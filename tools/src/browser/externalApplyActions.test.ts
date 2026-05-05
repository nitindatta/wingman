import { JSDOM } from 'jsdom';
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

vi.mock('./snapshot.js', () => ({
  saveSnapshot: vi.fn(async (_page: unknown, kind: string) => `C:/tmp/${kind}.artifact`),
}));

function expectEvaluateArg(evaluate: ReturnType<typeof vi.fn>, expected: Record<string, unknown>): void {
  expect(evaluate.mock.calls.some(([, arg]) => (
    arg != null
    && typeof arg === 'object'
    && Object.entries(expected).every(([key, value]) => (arg as Record<string, unknown>)[key] === value)
  ))).toBe(true);
}

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

  it('waits for live combobox options and falls back to the first usable source option', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('cannot fill button combobox');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'targetSelector' in value).length;
        if (call <= 2) {
          return null;
        }
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'Select One', value: '', disabled: true },
            { text: 'Indeed', value: 'indeed', disabled: false },
            { text: 'Jora', value: 'jora', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'selector' in value).length;
        if (call === 1) {
          return true;
        }
        return 'Indeed';
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'indeed';
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_19', value: 'SEEK' },
    );

    expect(result.ok).toBe(true);
    expect(click).toHaveBeenCalledTimes(1);
    expect(focus).not.toHaveBeenCalled();
    expect(fill).not.toHaveBeenCalled();
    expectEvaluateArg(evaluate, { wantText: 'indeed' });
  });

  it('waits longer for slow button-backed combobox popups before failing', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('cannot fill button combobox');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
          tagName: 'button',
          role: '',
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'targetSelector' in value).length;
        if (call <= 25) {
          return null;
        }
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'Select One', value: '', disabled: true },
            { text: 'Indeed', value: 'indeed', disabled: false },
            { text: 'Seek', value: 'seek', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'seek';
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'selector' in value).length;
        return call === 1 ? false : 'Seek';
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_11', value: 'SEEK' },
    );

    expect(result.ok).toBe(true);
    expect(click).toHaveBeenCalledTimes(1);
    expect(focus).not.toHaveBeenCalled();
    expect(fill).not.toHaveBeenCalled();
    expectEvaluateArg(evaluate, { wantText: 'seek' });
  });

  it('selects from an expanded button-backed combobox via its owned listbox', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('cannot fill button combobox');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
          tagName: 'button',
          role: '',
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && arg.ownedOnly === true) {
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'Select One', value: '', disabled: true },
            { text: 'Australian Capital Territory', value: 'act', disabled: false },
            { text: 'South Australia', value: '72de81fc3e6c414ebfbc8bb8f7a2c2c8', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        return null;
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'selector' in value).length;
        if (call === 1) {
          return true;
        }
        return 'South Australia';
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'south australia';
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_23', value: 'South Australia' },
    );

    expect(result.ok).toBe(true);
    expect(click).toHaveBeenCalledTimes(1);
    expect(focus).not.toHaveBeenCalled();
    expect(fill).not.toHaveBeenCalled();
    expectEvaluateArg(evaluate, { ownedOnly: true });
    expectEvaluateArg(evaluate, { wantText: 'south australia' });
  });

  it('selects a hidden PageUp-style combobox option by visible label and data-value prefix', async () => {
    const dom = new JSDOM(
      `
      <html>
        <body>
          <label id="lbl9574" for="q9574" class="col-sm-12 col-md-5">
            Are you currently authorised to work in Australia?<span class="asterisk">*</span>
          </label>
          <div id="q9574" class="input-group cb pu-select">
            <input
              id="q9574-edit"
              class="q9574_edit form-control dropdownEdit"
              autocomplete="off"
              type="text"
              tabindex="0"
              role="combobox"
              aria-labelledby="lbl9574"
              aria-activedescendant="q9574--ID"
              aria-autocomplete="both"
              aria-owns="q9574-list"
              aria-expanded="false"
              aria-describedby="q9574-current-value"
              aria-controls="q9574-list"
              aria-required="true"
              data-envoy-apply-id="field_9"
              value=""
            />
            <div id="q9574-button-label" class="hidden">Open list</div>
            <span class="input-group-btn">
              <button id="q9574-button" aria-labelledby="q9574-button-label" aria-controls="q9574-list" tabindex="-1" class="btn" type="button">
                <span class="caret"></span>
              </button>
            </span>
            <input type="hidden" name="q9574" id="q9574-postback" class="hidden dropdownvalue" value="" />
            <span id="q9574-current-value" class="sr-only"></span>
            <ul id="q9574-list" class="cb_list" tabindex="-1" role="listbox" aria-expanded="false" style="display:none">
              <li role="option" id="q9574--ID" data-value="" class="cb_option selected">Select</li>
              <li role="option" id="q9574-Yes-Iamapermanentresident/citizen||28682|-ID" data-value="Yes - I am a permanent resident / citizen||28682|" class="cb_option">
                Yes - I am a permanent resident / citizen
              </li>
              <li role="option" id="q9574-No-Irequiresponsorship||28684|-ID" data-value="No - I require sponsorship||28684|" class="cb_option">
                No - I require sponsorship
              </li>
            </ul>
          </div>
        </body>
      </html>
      `,
      { url: 'https://pageup.example/apply' },
    );
    const document = dom.window.document;
    const combobox = document.querySelector<HTMLInputElement>('[data-envoy-apply-id="field_9"]');
    const hidden = document.querySelector<HTMLInputElement>('#q9574-postback');
    const listbox = document.querySelector<HTMLElement>('#q9574-list');
    let dropdownOpened = false;
    document.querySelector<HTMLButtonElement>('#q9574-button')?.addEventListener('click', () => {
      dropdownOpened = true;
      if (!listbox || !combobox) return;
      listbox.style.display = 'block';
      listbox.setAttribute('aria-expanded', 'true');
      combobox.setAttribute('aria-expanded', 'true');
    });

    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click: vi.fn(async () => {}),
      focus: vi.fn(async () => {}),
      fill: vi.fn(async () => {}),
      evaluate: vi.fn(async (fn: (node: Element) => unknown) => fn(combobox as Element)),
    };
    const page = {
      url: () => 'https://pageup.example/apply',
      locator: () => target,
      evaluate: vi.fn(async (fn: (arg: never) => unknown, arg: never) => {
        const previousWindow = (globalThis as { window?: Window }).window;
        const previousDocument = (globalThis as { document?: Document }).document;
        const previousHTMLElement = (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement;
        (globalThis as { window?: Window }).window = dom.window as unknown as Window;
        (globalThis as { document?: Document }).document = document;
        (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement =
          dom.window.HTMLElement as unknown as typeof HTMLElement;
        try {
          if (typeof fn === 'string') {
            return eval(fn);
          }
          const source = String(fn).replace(
            /=>\s*\{/,
            '=> { const __envoyInjectedName = __name((value) => value, "__envoyInjectedName"); void __envoyInjectedName;',
          );
          const evaluated = eval(`(${source})`) as (value: never) => unknown;
          return evaluated(arg);
        } finally {
          if (previousWindow) (globalThis as { window?: Window }).window = previousWindow;
          else delete (globalThis as { window?: Window }).window;
          if (previousDocument) (globalThis as { document?: Document }).document = previousDocument;
          else delete (globalThis as { document?: Document }).document;
          if (previousHTMLElement) (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement = previousHTMLElement;
          else delete (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement;
        }
      }),
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      {
        action_type: 'select_option',
        element_id: 'field_9',
        value: 'Yes - I am a permanent resident / citizen',
      },
    );

    expect(result.message).toBe('action executed');
    expect(result.ok).toBe(true);
    expect(dropdownOpened).toBe(true);
    expect(target.fill).not.toHaveBeenCalled();
    expect(combobox?.value).toBe('Yes - I am a permanent resident / citizen');
    expect(combobox?.getAttribute('aria-expanded')).toBe('false');
    expect(hidden?.value).toBe('Yes - I am a permanent resident / citizen||28682|');
    expect(document.querySelector('#q9574--ID')?.className).toBe('cb_option');
    expect(document.querySelector('#q9574-Yes-Iamapermanentresident\\/citizen\\|\\|28682\\|-ID')?.className).toBe(
      'cb_option selected',
    );
  });

  it('selects an owned PageUp combobox even when the input itself is hidden', async () => {
    const dom = new JSDOM(
      `
      <html>
        <body>
          <table>
            <tr>
              <td id="r_lLKP_LanguageProficiency_SpeakingID_1" style="display:none">
                <label
                  id="lLKP_LanguageProficiency_SpeakingID_1_label"
                  for="lLKP_LanguageProficiency_SpeakingID_1"
                  style="display:none;"
                >
                  Language 1 Speaking proficiency
                </label>
                <div id="lLKP_LanguageProficiency_SpeakingID_1" class="input-group cb pu-select">
                  <input
                    autocomplete="off"
                    id="lLKP_LanguageProficiency_SpeakingID_1-edit"
                    class="form-control dropdownEdit"
                    type="text"
                    tabindex="0"
                    role="combobox"
                    aria-labelledby="lLKP_LanguageProficiency_SpeakingID_1_label"
                    aria-activedescendant="lLKP_LanguageProficiency_SpeakingID_1--ID"
                    aria-autocomplete="both"
                    aria-owns="lLKP_LanguageProficiency_SpeakingID_1-list"
                    aria-expanded="false"
                    aria-controls="lLKP_LanguageProficiency_SpeakingID_1-list"
                    aria-required="False"
                    data-envoy-apply-id="field_10"
                    value=""
                  />
                  <div id="lLKP_LanguageProficiency_SpeakingID_1-button-label" class="hidden">Open list</div>
                  <span class="input-group-btn">
                    <button
                      id="lLKP_LanguageProficiency_SpeakingID_1-button"
                      aria-labelledby="lLKP_LanguageProficiency_SpeakingID_1-button-label"
                      aria-controls="lLKP_LanguageProficiency_SpeakingID_1-list"
                      tabindex="-1"
                      class="btn"
                      type="button"
                    >
                      <span class="caret"></span>
                    </button>
                  </span>
                  <input
                    type="hidden"
                    name="lLKP_LanguageProficiency_SpeakingID_1"
                    id="lLKP_LanguageProficiency_SpeakingID_1-postback"
                    class="hidden dropdownvalue"
                    value=""
                  />
                  <span id="lLKP_LanguageProficiency_SpeakingID_1-current-value" class="sr-only"></span>
                  <ul
                    id="lLKP_LanguageProficiency_SpeakingID_1-list"
                    class="cb_list"
                    tabindex="-1"
                    role="listbox"
                    aria-expanded="false"
                    style="display:none"
                  >
                    <li role="option" id="lLKP_LanguageProficiency_SpeakingID_1--ID" data-value="" class="cb_option selected">Select</li>
                    <li role="option" id="lLKP_LanguageProficiency_SpeakingID_1-4-ID" data-value="4" class="cb_option">None</li>
                    <li role="option" id="lLKP_LanguageProficiency_SpeakingID_1-1-ID" data-value="1" class="cb_option">Basic</li>
                    <li role="option" id="lLKP_LanguageProficiency_SpeakingID_1-3-ID" data-value="3" class="cb_option">Intermediate</li>
                    <li role="option" id="lLKP_LanguageProficiency_SpeakingID_1-5-ID" data-value="5" class="cb_option">Proficient</li>
                    <li role="option" id="lLKP_LanguageProficiency_SpeakingID_1-2-ID" data-value="2" class="cb_option">Fluent</li>
                  </ul>
                </div>
              </td>
            </tr>
          </table>
        </body>
      </html>
      `,
      { url: 'https://pageup.example/apply' },
    );
    const document = dom.window.document;
    const combobox = document.querySelector<HTMLInputElement>('[data-envoy-apply-id="field_10"]');
    const hidden = document.querySelector<HTMLInputElement>('#lLKP_LanguageProficiency_SpeakingID_1-postback');
    const listbox = document.querySelector<HTMLElement>('#lLKP_LanguageProficiency_SpeakingID_1-list');
    let dropdownOpened = false;
    document.querySelector<HTMLButtonElement>('#lLKP_LanguageProficiency_SpeakingID_1-button')?.addEventListener('click', () => {
      dropdownOpened = true;
      if (!listbox || !combobox) return;
      listbox.style.display = 'block';
      listbox.setAttribute('aria-expanded', 'true');
      combobox.setAttribute('aria-expanded', 'true');
    });

    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click: vi.fn(async () => {
        throw new Error('hidden input should not be clicked directly');
      }),
      focus: vi.fn(async () => {}),
      fill: vi.fn(async () => {
        throw new Error('hidden input should not be filled directly');
      }),
      evaluate: vi.fn(async (fn: (node: Element) => unknown) => fn(combobox as Element)),
    };
    const page = {
      url: () => 'https://pageup.example/apply',
      locator: () => target,
      evaluate: vi.fn(async (fn: (arg: never) => unknown, arg: never) => {
        const previousWindow = (globalThis as { window?: Window }).window;
        const previousDocument = (globalThis as { document?: Document }).document;
        const previousHTMLElement = (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement;
        (globalThis as { window?: Window }).window = dom.window as unknown as Window;
        (globalThis as { document?: Document }).document = document;
        (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement =
          dom.window.HTMLElement as unknown as typeof HTMLElement;
        try {
          if (typeof fn === 'string') {
            return eval(fn);
          }
          return fn(arg);
        } finally {
          if (previousWindow) (globalThis as { window?: Window }).window = previousWindow;
          else delete (globalThis as { window?: Window }).window;
          if (previousDocument) (globalThis as { document?: Document }).document = previousDocument;
          else delete (globalThis as { document?: Document }).document;
          if (previousHTMLElement) (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement = previousHTMLElement;
          else delete (globalThis as { HTMLElement?: typeof HTMLElement }).HTMLElement;
        }
      }),
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      {
        action_type: 'select_option',
        element_id: 'field_10',
        value: 'Proficient',
      },
    );

    expect(result.ok).toBe(true);
    expect(target.click).not.toHaveBeenCalled();
    expect(target.fill).not.toHaveBeenCalled();
    expect(dropdownOpened).toBe(true);
    expect(combobox?.value).toBe('Proficient');
    expect(hidden?.value).toBe('5');
    expect(document.querySelector('#lLKP_LanguageProficiency_SpeakingID_1-5-ID')?.className).toBe('cb_option selected');
  });

  it('falls through to the owned listbox when a global listbox does not match', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('cannot fill button combobox');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
          tagName: 'button',
          role: '',
        })),
    };
    const unrelatedOptions = ['English', 'French'].map((text) => ({
      textContent: vi.fn(async () => text),
      click: vi.fn(async () => {}),
    }));
    const globalOptions = {
      count: vi.fn(async () => unrelatedOptions.length),
      nth: (index: number) => unrelatedOptions[index],
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && arg.ownedOnly === true) {
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'Indeed', value: 'indeed', disabled: false },
            { text: 'Seek', value: 'e5449715676d0127bb45880c1f4a6d39', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'seek';
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'selector' in value).length;
        return call === 1 ? false : 'Seek';
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: (selector: string) => (selector.includes('[role="listbox"]') ? globalOptions : target),
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_19', value: 'Seek' },
    );

    expect(result.ok).toBe(true);
    expect(focus).not.toHaveBeenCalled();
    expect(unrelatedOptions[0]?.click).not.toHaveBeenCalled();
    expectEvaluateArg(evaluate, { ownedOnly: true });
    expectEvaluateArg(evaluate, { wantText: 'seek' });
  });

  it('recovers when a button-backed combobox owned listbox appears only in a late final pass', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('cannot fill button combobox');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
          tagName: 'button',
          role: '',
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && arg.ownedOnly === true && arg.requireExpanded === false) {
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'Select One', value: '', disabled: true },
            { text: 'Indeed', value: 'indeed', disabled: false },
            { text: 'Seek', value: 'e5449715676d0127bb45880c1f4a6d39', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        return null;
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'selector' in value).length;
        if (call === 1) {
          return true;
        }
        return 'Seek';
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'seek';
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_19', value: 'seek' },
    );

    expect(result.ok).toBe(true);
    expectEvaluateArg(evaluate, {
      ownedOnly: true,
      requireExpanded: false,
    });
    expectEvaluateArg(evaluate, { wantText: 'seek' });
  });

  it('falls back to the first usable salutation option when the configured value is unavailable', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('cannot fill button combobox');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'Select One', value: '', disabled: true },
            { text: 'Mx', value: 'mx', disabled: false },
            { text: 'Dr', value: 'dr', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'selector' in value).length;
        if (call === 1) {
          return true;
        }
        return 'Mx';
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'mx';
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_22', value: 'Mr' },
    );

    expect(result.ok).toBe(true);
    expect(click).toHaveBeenCalledTimes(1);
    expect(focus).not.toHaveBeenCalled();
    expect(fill).not.toHaveBeenCalled();
    expectEvaluateArg(evaluate, { wantText: 'mx' });
  });

  it('matches strict state selects through shared aliases like South Australia and SA', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('cannot fill button combobox');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'ACT', value: 'act', disabled: false },
            { text: 'SA', value: 'sa', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        const call = evaluate.mock.calls.filter(([, value]) => value && typeof value === 'object' && 'selector' in value).length;
        if (call === 1) {
          return true;
        }
        return 'SA';
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'sa';
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_23', value: 'South Australia' },
    );

    expect(result.ok).toBe(true);
    expect(click).toHaveBeenCalledTimes(1);
    expect(focus).not.toHaveBeenCalled();
    expect(fill).not.toHaveBeenCalled();
    expectEvaluateArg(evaluate, { wantText: 'sa' });
  });

  it('does not type into button-backed comboboxes and fails strict selects when the chosen value does not stick', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('button-backed comboboxes should not be filled');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'Australian Capital Territory', value: 'act', disabled: false },
            { text: 'South Australia', value: 'sa', disabled: false },
          ],
        };
      }
      if (arg && typeof arg === 'object' && 'wantText' in arg) {
        return arg.wantText === 'south australia';
      }
      if (arg && typeof arg === 'object' && 'selector' in arg) {
        return false;
      }
      return 'Australian Capital Territory';
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_22', value: 'South Australia' },
    );

    expect(result.ok).toBe(false);
    expect(result.message).toContain('did not stick');
    expect(click).toHaveBeenCalledTimes(1);
    expect(focus).not.toHaveBeenCalled();
    expect(fill).not.toHaveBeenCalled();
  });

  it('captures artifacts and live combobox options when a select fails to match', async () => {
    const click = vi.fn(async () => {});
    const focus = vi.fn(async () => {});
    const fill = vi.fn(async () => {
      throw new Error('button-backed comboboxes should not be filled');
    });
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      click,
      focus,
      fill,
      evaluate: vi
        .fn()
        .mockImplementationOnce(async () => false)
        .mockImplementationOnce(async () => ({
          textEntryCapable: false,
        })),
    };

    const evaluate = vi.fn(async (_fn: unknown, arg: any) => {
      if (arg && typeof arg === 'object' && 'targetSelector' in arg) {
        return {
          selector: '[data-envoy-active-listbox="true"]',
          options: [
            { text: 'NSW', value: 'nsw', disabled: false },
            { text: 'VIC', value: 'vic', disabled: false },
          ],
        };
      }
      return false;
    });

    const page = {
      url: () => 'https://ats.example/apply',
      locator: () => target,
      evaluate,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'select_option', element_id: 'field_22', value: 'South Australia' },
    );

    expect(result.ok).toBe(false);
    expect(result.message).toContain('No combobox option matching "South Australia"');
    expect(result.artifacts).toEqual([
      { type: 'screenshot', path: 'C:/tmp/screenshot.artifact' },
      { type: 'dom', path: 'C:/tmp/dom.artifact' },
    ]);
    expect(result.diagnostics).toMatchObject({
      requested_value: 'South Australia',
      initial_options: ['NSW', 'VIC'],
      text_entry_capable: false,
    });
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

  it('selects sibling native radio options that share the tagged radio name', async () => {
    const uploadCheck = vi.fn(async () => {});
    const scopedRadios = {
      count: vi.fn(async () => 1),
      nth: vi.fn(() => ({
        evaluate: vi.fn(async () => 'optionsCoverLetter'),
      })),
    };
    const groupEntries = [
      {
        check: vi.fn(async () => {}),
        evaluate: vi.fn(async () => ({ label: 'No cover letter', inputValue: '0', inputId: '' })),
      },
      {
        check: uploadCheck,
        evaluate: vi.fn(async () => ({
          label: 'Upload my cover letter from my computer',
          inputValue: '1',
          inputId: '',
        })),
      },
      {
        check: vi.fn(async () => {}),
        evaluate: vi.fn(async () => ({ label: 'Write or paste my cover letter', inputValue: '2', inputId: '' })),
      },
    ];
    const groupRadios = {
      count: vi.fn(async () => groupEntries.length),
      nth: (index: number) => groupEntries[index],
    };
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      evaluate: vi.fn(async () => ({ nativeCheckbox: false, ariaCheckbox: false, checked: false })),
    };
    const page = {
      url: () => 'https://ats.example/apply',
      locator: (selector: string) => {
        if (selector === '[data-envoy-apply-id="field_2"] input[type="radio"]') return scopedRadios;
        if (selector === 'input[type="radio"][name="optionsCoverLetter"]') return groupRadios;
        return target;
      },
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'set_radio', element_id: 'field_2', value: 'Upload my cover letter from my computer' },
    );

    expect(result.ok).toBe(true);
    expect(uploadCheck).toHaveBeenCalledTimes(1);
  });

  it('dismisses transient popovers before selecting the next radio option', async () => {
    const radioCheck = vi.fn(async () => {});
    const radioEntries = [
      {
        check: radioCheck,
        evaluate: vi.fn(async () => ({ label: 'No', inputValue: 'false', inputId: 'dm287' })),
      },
    ];
    const nativeRadios = {
      count: vi.fn(async () => radioEntries.length),
      nth: (index: number) => radioEntries[index],
    };
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      evaluate: vi.fn(async () => ({ nativeCheckbox: false, ariaCheckbox: false, checked: false })),
    };
    const keyboard = { press: vi.fn(async () => {}) };
    const mouse = { click: vi.fn(async () => {}) };
    const page = {
      url: () => 'https://ats.example/apply',
      locator: (selector: string) => {
        if (selector.endsWith('input[type="radio"]')) return nativeRadios;
        return target;
      },
      evaluate: vi
        .fn()
        .mockResolvedValueOnce(true)
        .mockResolvedValueOnce(false)
        .mockResolvedValueOnce(false),
      keyboard,
      mouse,
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'set_radio', element_id: 'field_1', value: 'No' },
    );

    expect(result.ok).toBe(true);
    expect(keyboard.press).toHaveBeenCalledWith('Escape');
    expect(radioCheck).toHaveBeenCalledTimes(1);
  });

  it('falls back to clicking the associated label when native radio interaction is intercepted', async () => {
    const radioCheck = vi.fn(async () => {
      throw new Error('pointer events intercept the action');
    });
    const explicitLabelClick = vi.fn(async () => {});
    const radioEntries = [
      {
        check: radioCheck,
        evaluate: vi.fn(async () => ({ label: 'No', inputValue: 'false', inputId: 'dm287' })),
      },
    ];
    const nativeRadios = {
      count: vi.fn(async () => radioEntries.length),
      nth: (index: number) => radioEntries[index],
    };
    const explicitLabel = {
      first: () => explicitLabel,
      count: vi.fn(async () => 1),
      click: explicitLabelClick,
    };
    const target = {
      first: () => target,
      count: vi.fn(async () => 1),
      evaluate: vi.fn(async () => ({ nativeCheckbox: false, ariaCheckbox: false, checked: false })),
    };
    const page = {
      url: () => 'https://ats.example/apply',
      locator: (selector: string) => {
        if (selector.endsWith('input[type="radio"]')) return nativeRadios;
        if (selector === 'label[for="dm287"]') return explicitLabel;
        return target;
      },
      evaluate: vi.fn(async () => false),
      waitForTimeout: vi.fn(async () => {}),
    };

    const result = await executeExternalApplyAction(
      page as never,
      { action_type: 'set_radio', element_id: 'field_1', value: 'No' },
    );

    expect(result.ok).toBe(true);
    expect(radioCheck).toHaveBeenCalledTimes(1);
    expect(explicitLabelClick).toHaveBeenCalledTimes(1);
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
      })) as any;
    evaluate
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
    expect(check).toHaveBeenCalledTimes(2);
    expect(click).not.toHaveBeenCalled();
  });
});
