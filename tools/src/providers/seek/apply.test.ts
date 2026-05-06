import { describe, expect, it } from 'vitest';
import { chooseBestExternalApplyHref, detectPortalType, isConfirmationPage, isExternalPortalUrl } from './apply.js';

describe('SEEK external apply URL selection', () => {
  it('prefers Apply with SEEK over an advertiser external link when both are offered', () => {
    const seekApplyUrl = 'https://www.seek.com.au/job/123/apply';
    const advertiserUrl =
      'https://secure.dc2.pageuppeople.com/apply/889/aw/applicationForm/initApplication.asp?lJobID=530460&sLanguage=en';

    const selected = chooseBestExternalApplyHref([
      {
        href: advertiserUrl,
        label: 'Continue to advertiser',
        nearbyText: 'Continue to advertiser to complete your application',
      },
      {
        href: seekApplyUrl,
        label: 'Apply with SEEK',
        nearbyText: 'Use your SEEK profile and saved resume',
      },
    ]);

    expect(selected).toBe(seekApplyUrl);
  });

  it('prefers the real ATS application link over SEEK network footer links', () => {
    const pageupUrl =
      'https://secure.dc2.pageuppeople.com/apply/889/aw/applicationForm/initApplication.asp?lJobID=530460&sLanguage=en';

    const selected = chooseBestExternalApplyHref([
      {
        href: 'https://hk.jobsdb.com/',
        label: 'Jobsdb',
        nearbyText: 'International partners Bdjobs Jobstreet Jora SEEK',
      },
      {
        href: 'https://play.google.com/store/apps/details?id=com.seek',
        label: 'Google Play',
        nearbyText: 'Download our app',
      },
      {
        href: pageupUrl,
        label: 'Continue to advertiser',
        nearbyText: 'Continue to advertiser to complete your application',
      },
    ]);

    expect(selected).toBe(pageupUrl);
  });

  it('detects PageUp portals', () => {
    expect(detectPortalType('https://secure.dc2.pageuppeople.com/apply/889/aw/applicationForm/initApplication.asp')).toBe('pageup');
  });

  it('keeps SEEK internal apply pages inside the SEEK provider flow', () => {
    expect(isExternalPortalUrl('https://au.seek.com/job/91685860/apply/profile')).toBe(false);
    expect(isExternalPortalUrl('https://au.seek.com/job/91685860/apply/role-requirements')).toBe(false);
    expect(isExternalPortalUrl('https://www.seek.com.au/job/123/apply')).toBe(false);
  });

  it('still treats SEEK external advertiser interstitials and ATS hosts as external', () => {
    expect(isExternalPortalUrl('https://au.seek.com/job/91685860/apply/external')).toBe(true);
    expect(isExternalPortalUrl('https://www.seek.com.au/job/123/apply/external')).toBe(true);
    expect(isExternalPortalUrl('https://secure.dc2.pageuppeople.com/apply/889/aw/applicationForm/initApplication.asp')).toBe(true);
  });

  it('treats SEEK success URLs as confirmation even when the page offers post-submit actions', () => {
    expect(
      isConfirmationPage(
        'Show strong interest',
        'https://au.seek.com/job/91854803/apply/success',
      ),
    ).toBe(true);
  });
});
