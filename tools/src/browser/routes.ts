/**
 * Provider-agnostic browser tool routes.
 *
 * Implements the contract from docs/design.md §6.2 and §6.4.
 * All responses use the standard tool envelope (ok / error / drift).
 * Agent/ drives all decisions; tools/ only executes browser operations.
 */

import type { FastifyInstance } from 'fastify';
import { existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { z } from 'zod';
import { ok, error, type ToolResponse } from '../envelope.js';
import { createSession, getSession, closeSession } from './sessions.js';
import { inspectStep, type StepInfo } from './inspector.js';
import { saveSnapshot } from './snapshot.js';
import { getOrLaunchChrome, getProfileDir } from './chrome.js';

const PROVIDER_LOGIN_URLS: Record<string, string> = {
  seek: 'https://www.seek.com.au/oauth/login/',
  linkedin: 'https://www.linkedin.com/login',
};

function sessionError(key: string) {
  return error('session_not_found', `no active session for key ${key}`);
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

    const result = await inspectStep(session.page);
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
          // fill() then fire React-compatible events so controlled state updates
          await el.fill(value);
          await el.evaluate((node, val) => {
            const nativeSetter = Object.getOwnPropertyDescriptor(
              window.HTMLTextAreaElement.prototype, 'value'
            )?.set;
            nativeSetter?.call(node, val);
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
          }, value);
        } else {
          await el.fill(value);
        }
        filled_ids.push(id);
      } catch { failed_ids.push(id); }
    }

    // Click the action button — strip zero-width chars from label before matching
    const cleanLabel = parsed.data.action_label.replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').trim();
    const prevUrl = session.page.url();
    await session.page
      .getByRole('button', { name: cleanLabel, exact: false })
      .first()
      .click({ timeout: 30_000 });

    // SEEK is a SPA — waitForLoadState('domcontentloaded') never fires on step transitions.
    // Instead: wait for URL change OR for the button to disappear (step changed).
    // Fall back to a fixed 3s wait so we don't burn 30s every step.
    await Promise.race([
      session.page.waitForURL((url) => url.toString() !== prevUrl, { timeout: 5_000 }).catch(() => {}),
      session.page.waitForSelector(
        `button:not(:has-text("${cleanLabel}"))`,
        { state: 'attached', timeout: 5_000 }
      ).catch(() => {}),
      session.page.waitForTimeout(3_000),
    ]);

    // Inspect the new step
    const result = await inspectStep(session.page);
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

    // Navigate to the job page first, then find and click the Apply button
    await session.page.goto(parsed.data.job_url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await session.page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {});
    await session.page.waitForTimeout(1_500);

    // Check if we need to log in before we can even see the Apply button
    const preClickUrl = session.page.url();
    if (isLoginUrl(preClickUrl)) {
      return {
        status: 'needs_human' as const,
        data: { reason: 'auth_required', login_url: preClickUrl },
      };
    }

    // Click "Apply" button
    try {
      await session.page
        .getByRole('link', { name: /apply/i })
        .or(session.page.getByRole('button', { name: /apply/i }))
        .first()
        .click({ timeout: 10_000 });
    } catch {
      // Button not found — check if we got redirected to login before the click
      const urlNow = session.page.url();
      if (isLoginUrl(urlNow)) {
        return {
          status: 'needs_human' as const,
          data: { reason: 'auth_required', login_url: urlNow },
        };
      }
      return error('apply_button_not_found', `Could not find Apply button on ${urlNow}`);
    }

    await session.page.waitForLoadState('domcontentloaded', { timeout: 30_000 }).catch(() => {});
    await session.page.waitForTimeout(2_000);

    let applyUrl = session.page.url();

    // Redirected to login after clicking Apply
    if (isLoginUrl(applyUrl)) {
      return {
        status: 'needs_human' as const,
        data: { reason: 'auth_required', login_url: applyUrl },
      };
    }

    // SEEK's own /apply/external intermediate page — click through to the employer's actual portal
    if (applyUrl.includes('seek.com.au') && applyUrl.includes('/apply/external')) {
      try {
        // Try role-based selectors first, then fall back to any external href on the page
        const clicked = await session.page
          .getByRole('link', { name: /continue|apply|proceed|go to/i })
          .or(session.page.getByRole('button', { name: /continue|apply|proceed|go to/i }))
          .first()
          .click({ timeout: 6_000 })
          .then(() => true)
          .catch(() => false);

        if (!clicked) {
          // Last resort: find any anchor pointing off seek.com.au and click it
          const externalHref = await session.page.evaluate(() => {
            const a = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href]'))
              .find((el) => el.href && !el.href.includes('seek.com.au'));
            return a ? a.href : null;
          });
          if (externalHref) {
            await session.page.goto(externalHref, { waitUntil: 'domcontentloaded', timeout: 30_000 });
          }
        }

        await session.page.waitForLoadState('domcontentloaded', { timeout: 30_000 }).catch(() => {});
        await session.page.waitForTimeout(2_000);
      } catch {
        // Navigation failed — will be flagged as external below
      }
      applyUrl = session.page.url();
    }

    // External if we left seek.com.au, OR if click-through failed and we're still on /apply/external
    const is_external_portal =
      !applyUrl.includes('seek.com.au') ||
      (applyUrl.includes('seek.com.au') && applyUrl.includes('/apply/external'));
    const portal_type = is_external_portal ? detectPortalType(applyUrl) : null;

    return ok({ apply_url: applyUrl, is_external_portal, portal_type });
  });
}

function isLoginUrl(url: string): boolean {
  return (
    url.includes('/oauth/') ||
    url.includes('/sign-in') ||
    url.includes('/signin') ||
    url.includes('/login') ||
    url.includes('accounts.seek.com.au')
  );
}

function detectPortalType(url: string): string {
  if (url.includes('workday.com')) return 'workday';
  if (url.includes('myworkdayjobs.com')) return 'workday';
  if (url.includes('greenhouse.io')) return 'greenhouse';
  if (url.includes('lever.co')) return 'lever';
  if (url.includes('icims.com')) return 'icims';
  if (url.includes('successfactors.com')) return 'successfactors';
  if (url.includes('smartrecruiters.com')) return 'smartrecruiters';
  if (url.includes('jobvite.com')) return 'jobvite';
  if (url.includes('taleo.net')) return 'taleo';
  if (url.includes('bamboohr.com')) return 'bamboohr';
  return 'unknown';
}
