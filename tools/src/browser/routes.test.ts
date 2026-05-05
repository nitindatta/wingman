import { describe, expect, it, vi } from 'vitest';
import { maybeAdoptNewExternalApplyPage } from './routes.js';

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
