/**
 * SEEK-specific apply flow helpers.
 *
 * Provider logic stays here; generic browser primitives live in tools/src/browser.
 */

import type { Page } from 'playwright-core';

export function isExternalPortalUrl(url: string): boolean {
  return (
    !url.includes('seek.com.au') ||
    (url.includes('seek.com.au') && url.includes('/apply/external'))
  );
}

export function isConfirmationPage(pageText: string): boolean {
  return (
    /application (submitted|received|successful|complete|sent)/i.test(pageText) ||
    /thank you for applying/i.test(pageText) ||
    /your application has been (submitted|received|sent)/i.test(pageText) ||
    /you('ve| have) (applied|submitted|sent your application)/i.test(pageText) ||
    /successfully applied/i.test(pageText) ||
    /application sent/i.test(pageText)
  );
}

export function isLoginUrl(url: string): boolean {
  return (
    url.includes('/oauth/') ||
    url.includes('/sign-in') ||
    url.includes('/signin') ||
    url.includes('/login') ||
    url.includes('accounts.seek.com.au')
  );
}

export function detectPortalType(url: string): string {
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

type ExternalHrefCandidate = {
  href: string;
  label: string;
  nearbyText?: string;
};

const SEEK_NETWORK_EXTERNAL_HOSTS = [
  'jobsdb.com',
  'jobstreet.com',
  'jora.com',
  'employer.seek.com',
  'seekpass.co',
  'gradconnection.com',
  'sidekicker.com',
  'go1.com',
  'futurelearn.com',
  'jobadder.com',
];

const NON_APPLY_HOSTS = [
  'apps.apple.com',
  'play.google.com',
  'facebook.com',
  'instagram.com',
  'twitter.com',
  'youtube.com',
  'medium.com',
];

export function chooseBestExternalApplyHref(candidates: ExternalHrefCandidate[]): string | null {
  const scored = candidates
    .map((candidate) => ({ href: candidate.href, score: scoreExternalApplyHref(candidate) }))
    .filter((candidate) => candidate.score > 0)
    .sort((a, b) => b.score - a.score);
  return scored[0]?.href ?? null;
}

export function scoreExternalApplyHref(candidate: ExternalHrefCandidate): number {
  let url: URL;
  try {
    url = new URL(candidate.href);
  } catch {
    return -100;
  }

  const host = url.hostname.toLowerCase();
  const text = `${candidate.label} ${candidate.nearbyText ?? ''}`.toLowerCase();

  if (host.endsWith('seek.com.au')) {
    return /\bapply\s+with\s+seek\b/.test(text) ? 1_000 : -100;
  }
  if (NON_APPLY_HOSTS.some((blockedHost) => host.includes(blockedHost))) return -100;
  if (SEEK_NETWORK_EXTERNAL_HOSTS.some((blockedHost) => host.includes(blockedHost))) return -50;

  let score = 0;
  if (/pageuppeople\.com|workday|myworkdayjobs|greenhouse|lever\.co|icims|successfactors|smartrecruiters|jobvite|taleo|bamboohr/.test(host)) {
    score += 80;
  }
  if (/apply|application|initapplication|candidate|career|recruit|jobid/i.test(candidate.href)) {
    score += 35;
  }
  if (/\b(apply|continue|proceed|application|advertiser|company site)\b/.test(text)) {
    score += 25;
  }
  if (/\b(privacy|terms|security|contact|help|about|investors|blog|app store|google play|social|career advice|saved jobs)\b/.test(text)) {
    score -= 30;
  }
  return score;
}

async function findBestExternalApplyHref(page: Page): Promise<string | null> {
  const candidates = await page.evaluate(() => {
    const cleanText = (value: string | null | undefined): string =>
      (value ?? '').replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').replace(/\s+/g, ' ').trim();
    const nearestText = (el: Element): string =>
      cleanText(el.closest('main, article, section, form, div')?.textContent).slice(0, 300);

    return Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href]')).map((el) => ({
      href: el.href,
      label: cleanText(el.textContent ?? el.getAttribute('aria-label') ?? el.href),
      nearbyText: nearestText(el),
    }));
  });
  return chooseBestExternalApplyHref(candidates);
}

async function waitForExternalUrlToSettle(page: Page): Promise<void> {
  let previous = '';
  let stableCount = 0;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    await page.waitForLoadState('domcontentloaded', { timeout: 5_000 }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: 4_000 }).catch(() => {});
    await page.waitForTimeout(750);
    const current = page.url();
    if (current === previous) {
      stableCount += 1;
      if (stableCount >= 2) return;
    } else {
      stableCount = 0;
      previous = current;
    }
  }
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
    await waitForExternalUrlToSettle(page);
    return true;
  }

  await popup.close().catch(() => {});
  return false;
}

export type StartApplyResult =
  | { status: 'ok'; apply_url: string; is_external_portal: boolean; portal_type: string | null }
  | { status: 'needs_human'; reason: string; login_url: string }
  | { status: 'error'; type: string; message: string };

export async function startApply(page: Page, jobUrl: string): Promise<StartApplyResult> {
  await page.goto(jobUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {});
  await page.waitForTimeout(1_500);

  if (isLoginUrl(page.url())) {
    return { status: 'needs_human', reason: 'auth_required', login_url: page.url() };
  }

  try {
    await clickAndAdoptPopup(page, () =>
      page
        .getByRole('link', { name: /apply/i })
        .or(page.getByRole('button', { name: /apply/i }))
        .first()
        .click({ timeout: 10_000 }),
    );
  } catch {
    const urlNow = page.url();
    if (isLoginUrl(urlNow)) {
      return { status: 'needs_human', reason: 'auth_required', login_url: urlNow };
    }
    return { status: 'error', type: 'apply_button_not_found', message: `Could not find Apply button on ${urlNow}` };
  }

  await page.waitForLoadState('domcontentloaded', { timeout: 30_000 }).catch(() => {});
  await page.waitForTimeout(2_000);

  let applyUrl = page.url();

  if (isLoginUrl(applyUrl)) {
    return { status: 'needs_human', reason: 'auth_required', login_url: applyUrl };
  }

  if (applyUrl.includes('seek.com.au') && applyUrl.includes('/apply/external')) {
    try {
      const externalHref = await findBestExternalApplyHref(page);
      if (externalHref) {
        await page.goto(externalHref, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      } else {
        await clickAndAdoptPopup(page, () =>
          page
            .getByRole('link', { name: /continue|apply|proceed|go to|company site|advertiser/i })
            .or(page.getByRole('button', { name: /continue|apply|proceed|go to|company site|advertiser/i }))
            .first()
            .click({ timeout: 6_000 }),
        ).catch(() => {});
      }

      await waitForExternalUrlToSettle(page);
    } catch {
      // Navigation failed - will be flagged as external below.
    }
    applyUrl = page.url();
  }

  const is_external_portal =
    !applyUrl.includes('seek.com.au') ||
    (applyUrl.includes('seek.com.au') && applyUrl.includes('/apply/external'));
  const portal_type = is_external_portal ? detectPortalType(applyUrl) : null;

  return { status: 'ok', apply_url: applyUrl, is_external_portal, portal_type };
}
