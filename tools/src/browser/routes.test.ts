import { describe, expect, it, vi } from 'vitest';
import { linkedInPostSubmitFallbackStep, maybeAdoptNewExternalApplyPage } from './routes.js';

describe('browser routes external apply page handoff', () => {
  it('adopts a new company-site page after a successful click that does not navigate the current tab', async () => {
    const oldPage = makePage('https://au.indeed.com/viewjob?jk=abc');
    const newPage = makePage('https://company.example/jobs/123');
    const context = {
      pages: vi
        .fn()
        .mockReturnValueOnce([oldPage])
        .mockReturnValue([oldPage, newPage]),
    };
    oldPage.context.mockReturnValue(context);
    newPage.context.mockReturnValue(context);
    const session = { page: oldPage };

    const result = await maybeAdoptNewExternalApplyPage(
      session as never,
      { action_type: 'click', element_id: 'button_5' },
      {
        ok: true,
        action_type: 'click',
        element_id: 'button_5',
        message: 'action executed',
        value_after: null,
        navigated: false,
        new_url: 'https://au.indeed.com/viewjob?jk=abc',
        errors: [],
      },
      new Set([oldPage as never]),
    );

    expect(session.page).toBe(newPage);
    expect(newPage.bringToFront).toHaveBeenCalledTimes(1);
    expect(result.navigated).toBe(true);
    expect(result.new_url).toBe('https://company.example/jobs/123');
    expect(result.diagnostics).toMatchObject({
      page_handoff: 'new_page_after_click',
      previous_page_url: 'https://au.indeed.com/viewjob?jk=abc',
    });
  });

  it('keeps the current page when no new page appears', async () => {
    const oldPage = makePage('https://au.indeed.com/viewjob?jk=abc');
    oldPage.context.mockReturnValue({ pages: vi.fn(() => [oldPage]) });
    const session = { page: oldPage };

    const result = await maybeAdoptNewExternalApplyPage(
      session as never,
      { action_type: 'click', element_id: 'button_5' },
      {
        ok: true,
        action_type: 'click',
        element_id: 'button_5',
        message: 'action executed',
        value_after: null,
        navigated: false,
        new_url: 'https://au.indeed.com/viewjob?jk=abc',
        errors: [],
      },
      new Set([oldPage as never]),
    );

    expect(session.page).toBe(oldPage);
    expect(result.navigated).toBe(false);
    expect(result.new_url).toBe('https://au.indeed.com/viewjob?jk=abc');
  });
});

describe('LinkedIn post-submit fallback', () => {
  it('treats a closed Easy Apply modal with no submit button as a completed post-submit page', async () => {
    const page = makeLinkedInPostSubmitPage({
      url: 'https://www.linkedin.com/jobs/view/4404477725/',
      modalVisible: false,
      actions: ['Easy Apply', 'Save'],
      text: 'Lead Data Engineer Easy Apply Save',
    });

    const step = await linkedInPostSubmitFallbackStep(page as never, 'Submit application');

    expect(step).toMatchObject({
      page_url: 'https://www.linkedin.com/jobs/view/4404477725/',
      page_type: 'form',
      fields: [],
      visible_actions: ['Easy Apply', 'Save'],
    });
  });

  it('does not assume success when LinkedIn still shows a submit button', async () => {
    const page = makeLinkedInPostSubmitPage({
      url: 'https://www.linkedin.com/jobs/view/4404477725/',
      modalVisible: false,
      actions: ['Edit', 'Submit application'],
      text: 'Review your application Submit application',
    });

    await expect(linkedInPostSubmitFallbackStep(page as never, 'Submit application')).resolves.toBeNull();
  });
});

function makePage(url: string) {
  return {
    url: vi.fn(() => url),
    context: vi.fn(),
    isClosed: vi.fn(() => false),
    bringToFront: vi.fn(async () => {}),
    waitForLoadState: vi.fn(async () => {}),
    waitForTimeout: vi.fn(async () => {}),
  };
}

function makeLinkedInPostSubmitPage({
  url,
  modalVisible,
  actions,
  text,
}: {
  url: string;
  modalVisible: boolean;
  actions: string[];
  text: string;
}) {
  const actionLocators = actions.map((action) => ({
    textContent: vi.fn(async () => action),
  }));
  const pageLocator = {
    all: vi.fn(async () => actionLocators),
  };
  const modalLocator = {
    first: vi.fn(() => modalLocator),
    isVisible: vi.fn(async () => modalVisible),
  };

  return {
    url: vi.fn(() => url),
    waitForLoadState: vi.fn(async () => {}),
    waitForTimeout: vi.fn(async () => {}),
    evaluate: vi.fn(async () => text),
    locator: vi.fn((selector: string) => {
      if (selector.includes('easy-apply') || selector.includes('artdeco-modal')) {
        return modalLocator;
      }
      return pageLocator;
    }),
  };
}
