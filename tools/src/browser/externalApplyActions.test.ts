import { describe, expect, it, vi } from 'vitest';
import { executeExternalApplyAction, elementIdSelector, truthyFormValue } from './externalApplyActions.js';

vi.mock('./externalApplyObserver.js', () => ({
  observeExternalApplyPage: vi.fn(async () => ({
    url: 'https://ats.example/apply',
    title: 'Apply',
    page_type: 'form',
    visible_text: '',
    fields: [],
    buttons: [],
    links: [],
    uploads: [],
    errors: [],
    screenshot_ref: null,
  })),
}));

describe('external apply action helpers', () => {
  it('builds a safe data attribute selector for observed element ids', () => {
    expect(elementIdSelector('field_1')).toBe('[data-envoy-apply-id="field_1"]');
    expect(elementIdSelector('field_"x"')).toBe('[data-envoy-apply-id="field_\\"x\\""]');
  });

  it('normalises checkbox truthy values', () => {
    expect(truthyFormValue('yes')).toBe(true);
    expect(truthyFormValue('TRUE')).toBe(true);
    expect(truthyFormValue('checked')).toBe(true);
    expect(truthyFormValue('no')).toBe(false);
    expect(truthyFormValue(null)).toBe(false);
  });

  it('toggles custom aria checkboxes by clicking instead of calling native check', async () => {
    const click = vi.fn(async () => {});
    const check = vi.fn(async () => {});
    const uncheck = vi.fn(async () => {});
    const evaluate = vi.fn(async () => ({
      nativeCheckbox: false,
      ariaCheckbox: true,
      checked: false,
    }));
    const locator = {
      first: () => locator,
      count: vi.fn(async () => 1),
      evaluate,
      click,
      check,
      uncheck,
    };
    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => locator,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'set_checkbox', element_id: 'field_1', value: 'true' },
    );

    expect(result.ok).toBe(true);
    expect(click).toHaveBeenCalledTimes(1);
    expect(check).not.toHaveBeenCalled();
    expect(uncheck).not.toHaveBeenCalled();
  });

  it('selects custom aria radio options by clicking the matching role radio', async () => {
    const radioClick = vi.fn(async () => {});
    const groupClick = vi.fn(async () => {});
    const radioEntries = [
      {
        click: radioClick,
        evaluate: vi.fn(async () => ({ label: 'Yes', inputValue: '' })),
      },
      {
        click: vi.fn(async () => {}),
        evaluate: vi.fn(async () => ({ label: 'No', inputValue: '' })),
      },
    ];
    const roleRadios = {
      count: vi.fn(async () => radioEntries.length),
      nth: (index: number) => radioEntries[index],
    };
    const nativeRadios = {
      count: vi.fn(async () => 0),
      nth: vi.fn(),
    };
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click: groupClick,
      evaluate: vi.fn(async () => ({ nativeCheckbox: false, ariaCheckbox: false, checked: false })),
    };
    const page = {
      url: () => 'https://ats.example/apply',
      locator: (selector: string) => {
        if (selector.endsWith('input[type="radio"]')) return nativeRadios;
        if (selector.endsWith('[role="radio"]')) return roleRadios;
        return target;
      },
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'set_radio', element_id: 'field_1', value: 'Yes' },
    );

    expect(result.ok).toBe(true);
    expect(radioClick).toHaveBeenCalledTimes(1);
    expect(groupClick).not.toHaveBeenCalled();
  });

  it('falls back to setting hidden native checkboxes via DOM events when direct check fails', async () => {
    const click = vi.fn(async () => {});
    const check = vi.fn(async () => {
      throw new Error('element is not visible');
    });
    const uncheck = vi.fn(async () => {});
    const evaluate = vi
      .fn(async () => ({
        nativeCheckbox: true,
        ariaCheckbox: false,
        checked: false,
      }))
      .mockImplementationOnce(async () => ({
        nativeCheckbox: true,
        ariaCheckbox: false,
        checked: false,
      }))
      .mockImplementationOnce(async (fn: (node: unknown, desiredChecked: boolean) => boolean, desiredChecked: boolean) =>
        fn(
          {
            tagName: 'INPUT',
            type: 'checkbox',
            checked: false,
            dispatchEvent: () => true,
          },
          desiredChecked,
        ));
    const locator = {
      first: () => locator,
      count: vi.fn(async () => 1),
      evaluate,
      click,
      check,
      uncheck,
    };
    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => locator,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'set_checkbox', element_id: 'field_1', value: 'true' },
    );

    expect(result.ok).toBe(true);
    expect(check).toHaveBeenCalledTimes(1);
    expect(click).not.toHaveBeenCalled();
  });
});
