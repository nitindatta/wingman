/**
 * Provider-agnostic browser tool routes.
 *
 * Implements the contract from docs/design.md §6.2 and §6.4.
 * All responses use the standard tool envelope (ok / error / drift).
 * Agent/ drives all decisions; tools/ only executes browser operations.
 */

import type { FastifyInstance } from 'fastify';
import type { Locator, Page } from 'playwright-core';
import { existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { z } from 'zod';
import { ok, error, type ToolResponse } from '../envelope.js';
import { createSession, getSession, closeSession } from './sessions.js';
import { inspectStep, type StepInfo, type InspectOptions } from './inspector.js';
import { executeExternalApplyAction } from './externalApplyActions.js';
import { observeExternalApplyPage } from './externalApplyObserver.js';
import { startGenericExternalApply } from './externalApplyStart.js';
import { saveSnapshot } from './snapshot.js';
import { getOrLaunchChrome, getProfileDir } from './chrome.js';
import {
  isConfirmationPage as seekIsConfirmation,
  isExternalPortalUrl as seekIsExternalPortal,
  detectPortalType as seekDetectPortalType,
  startApply as seekStartApply,
} from '../providers/seek/apply.js';

const PROVIDER_LOGIN_URLS: Record<string, string> = {
  seek: 'https://www.seek.com.au/oauth/login/',
  linkedin: 'https://www.linkedin.com/login',
};
const FIELD_ACTION_TIMEOUT_MS = 5_000;

function sessionError(key: string) {
  return error('session_not_found', `no active session for key ${key}`);
}

function attributeValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

async function firstVisible(locator: Locator): Promise<Locator | null> {
  const count = await locator.count().catch(() => 0);
  for (let index = 0; index < Math.min(count, 20); index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) return candidate;
  }
  return null;
}

async function resolveFillTarget(page: Page, id: string): Promise<Locator | null> {
  if (id.startsWith('__lbl_')) {
    const labelKey = id.replace(/^__lbl_/, '').replace(/__$/, '').replace(/_/g, ' ');
    return firstVisible(page.getByLabel(labelKey, { exact: false }));
  }

  const escaped = attributeValue(id);
  const native = page.locator(`[id="${escaped}"], [name="${escaped}"]`);
  const visibleNative = await firstVisible(native);
  if (visibleNative) return visibleNative;

  const data = page.locator(`[data-testid="${escaped}"], [data-automation="${escaped}"]`);
  return firstVisible(data);
}

async function resolveAttachedTarget(page: Page, id: string): Promise<Locator | null> {
  if (id.startsWith('__lbl_')) {
    const labelKey = id.replace(/^__lbl_/, '').replace(/__$/, '').replace(/_/g, ' ');
    const label = page.getByLabel(labelKey, { exact: false });
    return (await label.count().catch(() => 0)) ? label.first() : null;
  }

  const escaped = attributeValue(id);
  const native = page.locator(`[id="${escaped}"], [name="${escaped}"]`);
  if (await native.count().catch(() => 0)) return native.first();

  const data = page.locator(`[data-testid="${escaped}"], [data-automation="${escaped}"]`);
  return (await data.count().catch(() => 0)) ? data.first() : null;
}

/** Return provider-specific inspect options so inspectStep stays generic. */
function inspectOptsFor(provider: string): InspectOptions {
  if (provider === 'seek') {
    return {
      isConfirmation: (text) => seekIsConfirmation(text),
      isExternalPortal: (url) => seekIsExternalPortal(url),
      detectPortalType: (url) => seekDetectPortalType(url),
    };
  }
  return {};
}

export function registerBrowserRoutes(app: FastifyInstance): void {
  // ── launch_session ────────────────────────────────────────────────────────
  app.post('/tools/browser/launch_session', async (request) => {
    const parsed = z.object({ provider: z.string().min(1) }).safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session_key = await createSession(parsed.data.provider);
    return ok({ session_key, status: 'open' });
  });

  // ── open_url ──────────────────────────────────────────────────────────────
  app.post('/tools/browser/open_url', async (request) => {
    const parsed = z
      .object({ session_key: z.string(), url: z.string().url() })
      .safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    await session.page.goto(parsed.data.url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await session.page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {});
    await session.page.waitForTimeout(1_500);

    return ok({ page_url: session.page.url(), status: 'loaded' });
  });

  // ── inspect_apply_step ────────────────────────────────────────────────────
  app.post('/tools/browser/inspect_apply_step', async (request) => {
    const parsed = z.object({ session_key: z.string() }).safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    const result = await inspectStep(session.page, inspectOptsFor(session.provider));
    if (!result.ok) {
      // Take a snapshot for the drift signal
      const snapshotPath = await saveSnapshot(session.page, 'drift');
      return {
        status: 'drift' as const,
        drift: {
          parser_id: 'seek_apply_inspector_v1',
          expected: 'form fields with labels',
          observed: result.reason,
          page_snapshot: snapshotPath,
        },
      };
    }

    return ok(result.step);
  });

  app.post('/tools/browser/observe_external_apply', async (request) => {
    const parsed = z.object({ session_key: z.string() }).safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    try {
      const observation = await observeExternalApplyPage(session.page);
      return ok(observation);
    } catch (err) {
      const artifacts: Array<{ type: string; path: string }> = [];
      const screenshotPath = await saveSnapshot(session.page, 'screenshot').catch(() => null);
      if (screenshotPath) artifacts.push({ type: 'screenshot', path: screenshotPath });
      const domPath = await saveSnapshot(session.page, 'dom').catch(() => null);
      if (domPath) artifacts.push({ type: 'dom', path: domPath });

      const artifactMessage = artifacts.length
        ? ` artifacts=${artifacts.map((artifact) => `${artifact.type}:${artifact.path}`).join(', ')}`
        : '';
      return error('internal_error', `${String(err)}${artifactMessage}`, artifacts);
    }
  });

  app.post('/tools/browser/execute_external_apply_action', async (request) => {
    const parsed = z
      .object({
        session_key: z.string(),
        action: z.object({
          action_type: z.enum([
            'fill_text',
            'select_option',
            'set_checkbox',
            'set_radio',
            'upload_file',
            'click',
          ]),
          element_id: z.string().min(1),
          value: z.string().nullable().optional(),
        }),
      })
      .safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    const result = await executeExternalApplyAction(session.page, parsed.data.action);
    return ok(result);
  });

  // ── fill_fields ───────────────────────────────────────────────────────────
  app.post('/tools/browser/fill_fields', async (request) => {
    const parsed = z
      .object({
        session_key: z.string(),
        fields: z.array(z.object({ id: z.string(), value: z.string() })),
      })
      .safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    const filled_ids: string[] = [];
    const failed_ids: string[] = [];

    for (const { id, value } of parsed.data.fields) {
      try {
        const el = session.page.locator(`[id="${id}"], [name="${id}"]`).first();
        const count = await el.count();
        if (!count) { failed_ids.push(id); continue; }

        const tagName = await el.evaluate((e) => (e as HTMLElement).tagName.toLowerCase());
        const inputType = await el.evaluate((e) =>
          (e as HTMLInputElement).type?.toLowerCase() ?? 'text',
        );

        if (tagName === 'select') {
          await el.selectOption({ label: value }).catch(async () => {
            await el.selectOption({ value });
          });
        } else if (inputType === 'checkbox') {
          const shouldCheck = ['yes', 'true', '1'].includes(value.toLowerCase());
          if (shouldCheck) await el.check(); else await el.uncheck();
        } else if (inputType === 'radio') {
          const option = session.page
            .locator(`input[name="${id}"]`)
            .filter({ hasText: value });
          if (await option.count()) await option.check();
          else await el.check();
        } else if (inputType === 'file') {
          // Skip — assume pre-uploaded to SEEK profile
          failed_ids.push(id);
          continue;
        } else {
          await el.fill(value);
        }
        filled_ids.push(id);
      } catch {
        failed_ids.push(id);
      }
    }

    return ok({ filled_ids, failed_ids });
  });

  // ── click_action ──────────────────────────────────────────────────────────
  app.post('/tools/browser/click_action', async (request) => {
    const parsed = z
      .object({ session_key: z.string(), action_label: z.string() })
      .safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    const prevUrl = session.page.url();
    try {
      await session.page
        .getByRole('button', { name: parsed.data.action_label, exact: false })
        .first()
        .click({ timeout: 10_000 });
    } catch {
      // Try fallback: any clickable element with matching text
      await session.page
        .locator(`button, [role="button"], input[type="submit"]`)
        .filter({ hasText: parsed.data.action_label })
        .first()
        .click({ timeout: 10_000 });
    }

    await session.page.waitForLoadState('domcontentloaded', { timeout: 30_000 }).catch(() => {});
    await session.page.waitForTimeout(1_000);

    return ok({ navigated: session.page.url() !== prevUrl, new_page_url: session.page.url() });
  });

  // ── fill_and_continue (compound) ─────────────────────────────────────────
  app.post('/tools/apply/fill_and_continue', async (request) => {
    const parsed = z
      .object({
        session_key: z.string(),
        fields: z.array(z.object({ id: z.string(), value: z.string() })),
        action_label: z.string(),
      })
      .safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    // Fill all fields
    const filled_ids: string[] = [];
    const failed_ids: string[] = [];
    for (const { id, value } of parsed.data.fields) {
      try {
        // Resolve element: native id/name → data attrs → label-based (stable across re-renders)
        let el;
        if (id.startsWith('__lbl_')) {
          // Extract original label from __lbl_the_label__ pattern
          const labelKey = id.replace(/^__lbl_/, '').replace(/__$/, '').replace(/_/g, ' ');
          el = session.page.getByLabel(labelKey, { exact: false }).first();
        } else {
          el = session.page.locator(`[id="${id}"], [name="${id}"]`).first();
          if (!(await el.count())) {
            el = session.page.locator(`[data-testid="${id}"], [data-automation="${id}"]`).first();
          }
        }
        if (!(await el.count())) { failed_ids.push(id); continue; }

        const tagName = await el.evaluate((e) => (e as HTMLElement).tagName.toLowerCase());
        const inputType = await el.evaluate((e) => (e as HTMLInputElement).type ?? '');

        if (inputType === 'radio') {
          // Radio group: id is the group name. Use Playwright locator.click() (not DOM click)
          // so React synthetic event handlers fire properly.
          const radios = session.page.locator(`input[name="${id}"]`);
          const count = await radios.count();
          let matched = false;
          for (let i = 0; i < count; i++) {
            const radio = radios.nth(i);
            const radioId = await radio.getAttribute('id') ?? '';
            const labelText = await session.page.evaluate((rid) => {
              const lbl = document.querySelector(`label[for="${rid}"]`) ??
                document.querySelector(`[id="${rid}"]`)?.closest('label') ??
                document.querySelector(`[id="${rid}"]`)
                  ?.closest('[class*="field"],[class*="question"]')
                  ?.querySelector('label,[class*="label"]');
              return lbl?.textContent?.trim() ?? '';
            }, radioId);
            if (labelText.toLowerCase() === value.toLowerCase()) {
              await radio.click();
              matched = true;
              break;
            }
          }
          if (matched) filled_ids.push(id); else failed_ids.push(id);
          continue;
        } else if (inputType === 'checkbox') {
          const shouldCheck = ['yes', 'true', '1'].includes(value.toLowerCase());
          if (shouldCheck) await el.check(); else await el.uncheck();
        } else if (tagName === 'select') {
          await el.selectOption({ label: value }).catch(async () => el.selectOption({ value }));
        } else if (inputType === 'file') {
          failed_ids.push(id); continue;
        } else if (tagName === 'textarea') {
          // React controlled textareas track prior value via _valueTracker.
          // Without resetting the tracker, dispatching input fires but React
          // thinks nothing changed and skips the state update — causing
          // form validation to treat the textarea as empty.
          await el.scrollIntoViewIfNeeded().catch(() => {});
          await el.focus().catch(() => {});
          await el.evaluate((node, val) => {
            const ta = node as HTMLTextAreaElement & { _valueTracker?: { setValue(v: string): void } };
            const setter = Object.getOwnPropertyDescriptor(
              window.HTMLTextAreaElement.prototype,
              'value',
            )?.set;
            if (ta._valueTracker) ta._valueTracker.setValue('');
            setter?.call(ta, val);
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.dispatchEvent(new Event('change', { bubbles: true }));
          }, value);
          await el.evaluate((node) => (node as HTMLTextAreaElement).blur()).catch(() => {});
        } else {
          await el.fill(value);
        }
        filled_ids.push(id);
      } catch { failed_ids.push(id); }
    }

    // Click the action button — strip zero-width chars from label before matching
    const cleanLabel = parsed.data.action_label.replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').trim();
    const prevUrl = session.page.url();

    // Save a DOM snapshot before clicking so we can inspect button structure if needed.
    // This is especially useful for 0-field pages (profile review, final submit).
    await saveSnapshot(session.page, 'dom').catch(() => {});

    // Primary: getByRole (respects aria-label, accessible name).
    // Fallback 1: text-content locator (handles buttons where accessible name ≠ visible text).
    // Fallback 2: evaluate() direct DOM click — bypasses Playwright role resolution entirely.
    let actionBtn = session.page
      .getByRole('button', { name: cleanLabel, exact: false })
      .first();
    try {
      await actionBtn.click({ timeout: 10_000 });
    } catch {
      // Fallback 1: match any button/link whose text content contains the label
      actionBtn = session.page
        .locator('button, [role="button"], a[role="button"], input[type="submit"]')
        .filter({ hasText: cleanLabel })
        .first();
      const count = await actionBtn.count().catch(() => 0);
      if (count > 0) {
        await actionBtn.click({ timeout: 10_000 });
      } else {
        // Fallback 2: direct DOM querySelector by text — covers React portals, custom elements
        await session.page.evaluate((label) => {
          const all = Array.from(document.querySelectorAll<HTMLElement>('button, [role="button"], a'));
          const el = all.find((e) => (e.textContent ?? '').trim().toLowerCase().includes(label.toLowerCase()));
          if (!el) throw new Error(`No element found with text: ${label}`);
          el.click();
        }, cleanLabel);
        // Re-point actionBtn to something stable for the waitFor race below
        actionBtn = session.page.locator('body').first();
      }
    }

    // SEEK is a SPA — waitForLoadState('domcontentloaded') never fires on step transitions.
    // Wait for the button we just clicked to detach/hide (the page has moved on),
    // OR for the URL to change, OR fall back to a fixed 5s wait.
    //
    // NOTE: do NOT use `waitForSelector('button:not(:has-text(...))')` — that matches
    // any other button on the page immediately, resolving the race before SEEK has
    // actually navigated, which causes the inspector to see the pre-submit page.
    await Promise.race([
      session.page.waitForURL((url) => url.toString() !== prevUrl, { timeout: 8_000 }).catch(() => {}),
      actionBtn.waitFor({ state: 'detached', timeout: 6_000 }).catch(() => {}),
      actionBtn.waitFor({ state: 'hidden', timeout: 6_000 }).catch(() => {}),
      session.page.waitForTimeout(5_000),
    ]);

    // Inspect the new step using provider-specific options
    const result = await inspectStep(session.page, inspectOptsFor(session.provider));
    if (!result.ok) {
      const snapshotPath = await saveSnapshot(session.page, 'drift');
      return {
        status: 'drift' as const,
        drift: {
          parser_id: 'seek_apply_inspector_v1',
          expected: 'next form step or confirmation',
          observed: result.reason,
          page_snapshot: snapshotPath,
        },
      };
    }

    return ok({
      filled_ids,
      failed_ids,
      navigated: session.page.url() !== prevUrl,
      new_page_state: result.step,
    });
  });

  // ── take_snapshot ─────────────────────────────────────────────────────────
  app.post('/tools/browser/take_snapshot', async (request) => {
    const parsed = z
      .object({ session_key: z.string(), kind: z.enum(['screenshot', 'dom']) })
      .safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    const path = await saveSnapshot(session.page, parsed.data.kind);
    return ok({ artifact_path: path });
  });

  // ── close_session ─────────────────────────────────────────────────────────
  app.post('/tools/browser/close_session', async (request) => {
    const parsed = z.object({ session_key: z.string() }).safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const closed = await closeSession(parsed.data.session_key);
    return ok({ closed });
  });

  // ── open_for_login ────────────────────────────────────────────────────────
  // Opens the dedicated browser profile and navigates to the provider login page.
  // The user logs in manually; their session cookies are saved to the profile dir.
  app.post('/tools/browser/open_for_login', async (request) => {
    const parsed = z.object({ provider: z.string().min(1) }).safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const loginUrl = PROVIDER_LOGIN_URLS[parsed.data.provider];
    if (!loginUrl) {
      return error('unknown_provider', `No login URL known for provider: ${parsed.data.provider}`);
    }

    try {
      const context = await getOrLaunchChrome();
      const page = await context.newPage();
      await page.goto(loginUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      // Don't close the page — leave it open so the user can log in
      return ok({ provider: parsed.data.provider, login_url: loginUrl, profile_dir: getProfileDir() });
    } catch (err) {
      return error('launch_failed', String(err));
    }
  });

  // ── setup_status ──────────────────────────────────────────────────────────
  // Returns whether the browser profile directory exists and has stored cookies.
  app.get('/tools/setup/status', async () => {
    const profileDir = getProfileDir();
    const profileExists = existsSync(profileDir);

    // Chrome stores cookies at Default/Network/Cookies (Chromium) or Default/Cookies
    const cookiePaths = [
      path.join(profileDir, 'Default', 'Network', 'Cookies'),
      path.join(profileDir, 'Default', 'Cookies'),
    ];
    const hasCookies = cookiePaths.some((p) => {
      try { return existsSync(p) && statSync(p).size > 4096; } catch { return false; }
    });

    return ok({ profile_dir: profileDir, profile_exists: profileExists, has_cookies: hasCookies });
  });

  // ── start_apply (provider tool) ───────────────────────────────────────────
  app.post('/tools/providers/start_apply', async (request) => {
    const parsed = z
      .object({ provider: z.string(), job_url: z.string().url(), session_key: z.string() })
      .safeParse(request.body);
    if (!parsed.success) return error('bad_request', parsed.error.message);

    const session = getSession(parsed.data.session_key);
    if (!session) return sessionError(parsed.data.session_key);

    if (parsed.data.provider === 'seek') {
      try {
        const result = await seekStartApply(session.page, parsed.data.job_url);
        if (result.status === 'needs_human') {
          return { status: 'needs_human' as const, data: { reason: result.reason, login_url: result.login_url } };
        }
        if (result.status === 'error') {
          return error(result.type, result.message);
        }
        return ok({ apply_url: result.apply_url, is_external_portal: result.is_external_portal, portal_type: result.portal_type });
      } catch (err) {
        // Catch Playwright errors (network failure, timeout, etc.) so they return
        // a proper error envelope instead of crashing Fastify with HTTP 500.
        return error('navigation_failed', String(err));
      }
    }

    try {
      return ok(await startGenericExternalApply(session.page, parsed.data.provider, parsed.data.job_url));
    } catch (err) {
      return error('navigation_failed', String(err));
    }
  });
}
