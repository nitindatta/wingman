/**
 * LinkedIn-specific apply flow helpers.
 *
 * Easy Apply is an internal LinkedIn modal flow and must stay in the regular
 * provider apply pipeline. Only advertiser/company-site destinations should be
 * handed to the external apply harness.
 */

import type { Page } from 'playwright-core';

export const LINKEDIN_EASY_APPLY_ROOT_SELECTOR = [
  '[data-test-modal-id="easy-apply-modal"]',
  '.jobs-easy-apply-modal',
  '.jobs-easy-apply-content',
  'div[role="dialog"][aria-modal="true"]',
  '.artdeco-modal',
].join(', ');

export type StartApplyResult =
  | { status: 'ok'; apply_url: string; is_external_portal: boolean; portal_type: string | null }
  | { status: 'needs_human'; reason: string; login_url: string }
  | { status: 'error'; type: string; message: string };

export function isExternalPortalUrl(url: string): boolean {
  return !isLinkedInUrl(url);
}

export function detectPortalType(url: string): string {
  if (isLinkedInUrl(url)) return 'linkedin';
  if (url.includes('pageuppeople.com')) return 'pageup';
  if (url.includes('workday.com') || url.includes('myworkdayjobs.com')) return 'workday';
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

export function isConfirmationPage(pageText: string): boolean {
  return (
    /application (submitted|sent)/i.test(pageText) ||
    /your application was sent/i.test(pageText) ||
    /application sent/i.test(pageText) ||
    /successfully applied/i.test(pageText)
  );
}

function isLinkedInUrl(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  const host = parsed.hostname.toLowerCase();
  return host === 'linkedin.com' || host.endsWith('.linkedin.com');
}

function isLoginUrl(url: string): boolean {
  return (
    isLinkedInUrl(url) &&
    (
      url.includes('/login') ||
      url.includes('/uas/login') ||
      url.includes('/checkpoint/') ||
      url.includes('/challenge/')
    )
  );
}

async function waitForLinkedInToSettle(page: Page): Promise<void> {
  await page.waitForLoadState('domcontentloaded', { timeout: 15_000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 8_000 }).catch(() => {});
  await page.waitForTimeout(1_000);
}

async function waitForEasyApplyModal(page: Page, timeout = 8_000): Promise<boolean> {
  const modal = page.locator(LINKEDIN_EASY_APPLY_ROOT_SELECTOR).first();
  await modal.waitFor({ state: 'visible', timeout }).catch(() => {});
  return modal.isVisible().catch(() => false);
}

async function clickAndAdoptPopup(page: Page, click: () => Promise<unknown>): Promise<boolean> {
  const popupPromise = page.context().waitForEvent('page', { timeout: 12_000 }).catch(() => null);
  await click();
  const popup = await popupPromise;
  if (!popup) return false;

  await popup.waitForLoadState('domcontentloaded', { timeout: 30_000 }).catch(() => {});
  await popup.waitForLoadState('networkidle', { timeout: 8_000 }).catch(() => {});
  await popup.waitForTimeout(750);
  const popupUrl = popup.url();
  if (popupUrl && popupUrl !== 'about:blank') {
    await page.goto(popupUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await popup.close().catch(() => {});
    await waitForLinkedInToSettle(page);
    return true;
  }

  await popup.close().catch(() => {});
  return false;
}

export async function startApply(page: Page, jobUrl: string): Promise<StartApplyResult> {
  await page.goto(jobUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await waitForLinkedInToSettle(page);

  if (isLoginUrl(page.url())) {
    return { status: 'needs_human', reason: 'auth_required', login_url: page.url() };
  }

  try {
    await page
      .getByRole('button', { name: /easy apply/i })
      .or(page.getByRole('link', { name: /easy apply/i }))
      .first()
      .click({ timeout: 10_000 });
    if (await waitForEasyApplyModal(page)) {
      return {
        status: 'ok',
        apply_url: page.url(),
        is_external_portal: false,
        portal_type: null,
      };
    }

    const urlNow = page.url();
    if (isLoginUrl(urlNow)) {
      return { status: 'needs_human', reason: 'auth_required', login_url: urlNow };
    }
    return {
      status: 'error',
      type: 'easy_apply_modal_not_found',
      message: `Clicked LinkedIn Easy Apply, but the Easy Apply form did not open on ${urlNow}`,
    };
  } catch {
    // Not an Easy Apply job, or the internal button did not render.
  }

  try {
    await clickAndAdoptPopup(page, () =>
      page
        .getByRole('link', { name: /^apply|apply on company website|company website/i })
        .or(page.getByRole('button', { name: /^apply|apply on company website|company website/i }))
        .first()
        .click({ timeout: 8_000 }),
    );
    await waitForLinkedInToSettle(page);
  } catch {
    const urlNow = page.url();
    if (isLoginUrl(urlNow)) {
      return { status: 'needs_human', reason: 'auth_required', login_url: urlNow };
    }
    return { status: 'error', type: 'apply_button_not_found', message: `Could not find Easy Apply or advertiser Apply button on ${urlNow}` };
  }

  const applyUrl = page.url();
  if (isLoginUrl(applyUrl)) {
    return { status: 'needs_human', reason: 'auth_required', login_url: applyUrl };
  }

  const is_external_portal = isExternalPortalUrl(applyUrl);
  if (!is_external_portal && !(await waitForEasyApplyModal(page, 1_500))) {
    return {
      status: 'error',
      type: 'linkedin_apply_not_started',
      message: `LinkedIn Apply did not open an Easy Apply form or navigate to an external application on ${applyUrl}`,
    };
  }

  return {
    status: 'ok',
    apply_url: applyUrl,
    is_external_portal,
    portal_type: is_external_portal ? detectPortalType(applyUrl) : null,
  };
}
