import type { Page } from 'playwright-core';
import { describe, expect, it, vi } from 'vitest';
import { detectPortalType, isExternalPortalUrl, startApply } from './apply.js';

function createEasyApplyPage(modalVisible: boolean): Page {
  let currentUrl = 'about:blank';

  const easyApplyLocator = {
    or: vi.fn(() => easyApplyLocator),
    first: vi.fn(() => easyApplyLocator),
    click: vi.fn(async () => {}),
  };
  const modalLocator = {
    first: vi.fn(() => modalLocator),
    waitFor: vi.fn(async () => {
      if (!modalVisible) throw new Error('modal not visible');
    }),
    isVisible: vi.fn(async () => modalVisible),
    count: vi.fn(async () => (modalVisible ? 1 : 0)),
  };

  return {
    goto: vi.fn(async (url: string) => { currentUrl = url; }),
    waitForLoadState: vi.fn(async () => {}),
    waitForTimeout: vi.fn(async () => {}),
    url: vi.fn(() => currentUrl),
    locator: vi.fn(() => modalLocator),
    getByRole: vi.fn(() => easyApplyLocator),
  } as unknown as Page;
}

function createLinkedInApplyPageWithoutNavigation(): Page {
  let currentUrl = 'about:blank';

  const easyApplyLocator = {
    or: vi.fn(() => easyApplyLocator),
    first: vi.fn(() => easyApplyLocator),
    click: vi.fn(async () => {
      throw new Error('easy apply not found');
    }),
  };
  const applyLocator = {
    or: vi.fn(() => applyLocator),
    first: vi.fn(() => applyLocator),
    click: vi.fn(async () => {}),
  };
  const modalLocator = {
    first: vi.fn(() => modalLocator),
    waitFor: vi.fn(async () => {
      throw new Error('modal not visible');
    }),
    isVisible: vi.fn(async () => false),
    count: vi.fn(async () => 0),
  };

  return {
    goto: vi.fn(async (url: string) => { currentUrl = url; }),
    waitForLoadState: vi.fn(async () => {}),
    waitForTimeout: vi.fn(async () => {}),
    url: vi.fn(() => currentUrl),
    locator: vi.fn(() => modalLocator),
    context: vi.fn(() => ({ waitForEvent: vi.fn(async () => null) })),
    getByRole: vi.fn((_role: string, options?: { name?: RegExp }) => {
      const name = String(options?.name ?? '');
      return name.includes('easy apply') ? easyApplyLocator : applyLocator;
    }),
  } as unknown as Page;
}

describe('LinkedIn apply routing', () => {
  it('keeps LinkedIn job and Easy Apply pages inside the LinkedIn provider flow', () => {
    expect(isExternalPortalUrl('https://www.linkedin.com/jobs/view/4404477725/')).toBe(false);
    expect(isExternalPortalUrl('https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4404477725')).toBe(false);
    expect(isExternalPortalUrl('https://au.linkedin.com/jobs/view/4404477725')).toBe(false);
  });

  it('treats company apply destinations as external portals', () => {
    expect(isExternalPortalUrl('https://company.wd3.myworkdayjobs.com/job/123')).toBe(true);
    expect(isExternalPortalUrl('https://boards.greenhouse.io/company/jobs/123')).toBe(true);
  });

  it('detects common advertiser portal types', () => {
    expect(detectPortalType('https://company.wd3.myworkdayjobs.com/job/123')).toBe('workday');
    expect(detectPortalType('https://boards.greenhouse.io/company/jobs/123')).toBe('greenhouse');
  });

  it('starts LinkedIn Easy Apply only after the modal is visible', async () => {
    const page = createEasyApplyPage(true);

    await expect(startApply(page, 'https://www.linkedin.com/jobs/view/4404477725/')).resolves.toEqual({
      status: 'ok',
      apply_url: 'https://www.linkedin.com/jobs/view/4404477725/',
      is_external_portal: false,
      portal_type: null,
    });
  });

  it('does not report LinkedIn Easy Apply as started when the modal never opens', async () => {
    const page = createEasyApplyPage(false);

    await expect(startApply(page, 'https://www.linkedin.com/jobs/view/4404477725/')).resolves.toMatchObject({
      status: 'error',
      type: 'easy_apply_modal_not_found',
    });
  });

  it('does not route an Apply click as internal LinkedIn when no modal or external portal appears', async () => {
    const page = createLinkedInApplyPageWithoutNavigation();

    await expect(startApply(page, 'https://www.linkedin.com/jobs/view/4404477725/')).resolves.toMatchObject({
      status: 'error',
      type: 'linkedin_apply_not_started',
    });
  });
});
