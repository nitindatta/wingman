import { existsSync } from 'node:fs';
import type { Page } from 'playwright-core';
import { observeExternalApplyPage } from './externalApplyObserver.js';

export type ExternalApplyActionType =
  | 'fill_text'
  | 'select_option'
  | 'set_checkbox'
  | 'set_radio'
  | 'upload_file'
  | 'click';

export type ExternalApplyAction = {
  action_type: ExternalApplyActionType;
  element_id: string;
  value?: string | null;
};

export type ExternalApplyActionResult = {
  ok: boolean;
  action_type: ExternalApplyActionType;
  element_id: string;
  message: string;
  value_after: string | null;
  navigated: boolean;
  new_url: string | null;
  errors: string[];
};

export function elementIdSelector(elementId: string): string {
  const escaped = elementId.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `[data-envoy-apply-id="${escaped}"]`;
}

export function truthyFormValue(value: string | null | undefined): boolean {
  return ['1', 'true', 'yes', 'y', 'checked', 'on'].includes((value ?? '').trim().toLowerCase());
}

type CheckboxTargetState = {
  nativeCheckbox: boolean;
  ariaCheckbox: boolean;
  checked: boolean;
};

export async function executeExternalApplyAction(
  page: Page,
  action: ExternalApplyAction,
): Promise<ExternalApplyActionResult> {
  const previousUrl = page.url();
  const target = page.locator(elementIdSelector(action.element_id)).first();
  const exists = await target.count().catch(() => 0);
  if (!exists) {
    return actionResult(action, {
      ok: false,
      message: `Element not found for id ${action.element_id}`,
      newUrl: page.url(),
      previousUrl,
    });
  }

  try {
    if (action.action_type === 'fill_text') {
      if (action.value == null) throw new Error('fill_text requires value');
      await target.fill(action.value);
    } else if (action.action_type === 'select_option') {
      if (action.value == null) throw new Error('select_option requires value');
      const isNativeSelect = await target.evaluate(
        (node) => (node as Element).tagName?.toLowerCase() === 'select',
      ).catch(() => false);
      if (isNativeSelect) {
        await target.selectOption({ label: action.value }).catch(async () => {
          await target.selectOption({ value: action.value ?? '' });
        });
      } else {
        await selectAriaComboboxOption(page, action.element_id, action.value);
      }
    } else if (action.action_type === 'set_checkbox') {
      await setCheckboxValue(target, truthyFormValue(action.value));
    } else if (action.action_type === 'set_radio') {
      if (action.value == null) throw new Error('set_radio requires value');
      await clickRadioOption(page, action.element_id, action.value);
    } else if (action.action_type === 'upload_file') {
      if (!action.value) throw new Error('upload_file requires file path');
      if (!existsSync(action.value)) throw new Error(`file does not exist: ${action.value}`);
      await target.setInputFiles(action.value);
    } else if (action.action_type === 'click') {
      await target.click();
      await Promise.race([
        page.waitForURL((url) => url.toString() !== previousUrl, { timeout: 8_000 }).catch(() => {}),
        page.waitForLoadState('domcontentloaded', { timeout: 8_000 }).catch(() => {}),
        page.waitForTimeout(1_000),
      ]);
    }

    await page.waitForTimeout(300);
    const observation = await observeExternalApplyPage(page).catch(() => null);
    const matchingField = observation?.fields.find((field) => field.element_id === action.element_id);
    return actionResult(action, {
      ok: true,
      message: 'action executed',
      valueAfter: matchingField?.current_value ?? null,
      errors: observation?.errors ?? [],
      newUrl: page.url(),
      previousUrl,
    });
  } catch (err) {
    const observation = await observeExternalApplyPage(page).catch(() => null);
    return actionResult(action, {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
      errors: observation?.errors ?? [],
      newUrl: page.url(),
      previousUrl,
    });
  }
}

async function setCheckboxValue(target: ReturnType<Page['locator']>, desiredChecked: boolean): Promise<void> {
  const state = await describeCheckboxTarget(target);
  if (state.nativeCheckbox) {
    try {
      if (desiredChecked) await target.check();
      else await target.uncheck();
    } catch {
      await target.evaluate((node, shouldBeChecked) => {
        const input = node as HTMLInputElement;
        input.checked = shouldBeChecked;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }, desiredChecked);
    }
    return;
  }
  if (state.ariaCheckbox) {
    if (state.checked !== desiredChecked) {
      await target.click();
    }
    return;
  }
  if (desiredChecked) await target.check();
  else await target.uncheck();
}

async function describeCheckboxTarget(target: ReturnType<Page['locator']>): Promise<CheckboxTargetState> {
  return target.evaluate((node) => {
    const el = node as HTMLElement;
    const input = node as HTMLInputElement;
    const tag = el.tagName?.toLowerCase() ?? '';
    const type = input.type?.toLowerCase() ?? '';
    const role = (el.getAttribute('role') ?? '').toLowerCase();
    const ariaChecked = (el.getAttribute('aria-checked') ?? '').toLowerCase();
    const dataState = (el.getAttribute('data-state') ?? '').toLowerCase();
    return {
      nativeCheckbox: tag === 'input' && type === 'checkbox',
      ariaCheckbox: role === 'checkbox',
      checked: (tag === 'input' && type === 'checkbox' && input.checked)
        || ariaChecked === 'true'
        || dataState === 'checked',
    };
  });
}

function escapeCssIdent(ident: string): string {
  return ident.replace(/([^a-zA-Z0-9_-])/g, '\\$1');
}

async function selectAriaComboboxOption(page: Page, elementId: string, value: string): Promise<void> {
  const combobox = page.locator(elementIdSelector(elementId)).first();
  const listboxId = await combobox.evaluate((node) => {
    const el = node as HTMLElement;
    return el.getAttribute('aria-owns') || el.getAttribute('aria-controls') || '';
  });

  await combobox.click();
  await combobox.focus().catch(() => {});

  const target = value.trim().toLowerCase();
  const listboxSelector = listboxId
    ? `#${escapeCssIdent(listboxId)}`
    : '[role="listbox"]';

  const clicked = await page.evaluate(
    ({ sel, want }) => {
      const listbox = document.querySelector(sel);
      if (!listbox) return false;
      const options = Array.from(listbox.querySelectorAll('[role="option"], li, [data-value]'));
      const match =
        options.find((opt) => (opt.textContent ?? '').trim().toLowerCase() === want) ||
        options.find((opt) => (opt.textContent ?? '').trim().toLowerCase().includes(want)) ||
        options.find((opt) => {
          const dv = (opt as HTMLElement).getAttribute('data-value') ?? '';
          return dv.trim().toLowerCase() === want;
        });
      if (!match) return false;
      (match as HTMLElement).click();
      return true;
    },
    { sel: listboxSelector, want: target },
  ).catch(() => false);

  if (clicked) return;

  await combobox.fill(value).catch(async () => {
    await combobox.click();
    await page.keyboard.type(value);
  });
  await page.waitForTimeout(250);

  const clickedAfterType = await page.evaluate(
    ({ sel, want }) => {
      const listbox = document.querySelector(sel);
      if (!listbox) return false;
      const options = Array.from(listbox.querySelectorAll('[role="option"], li, [data-value]'));
      const match =
        options.find((opt) => (opt.textContent ?? '').trim().toLowerCase() === want) ||
        options.find((opt) => (opt.textContent ?? '').trim().toLowerCase().includes(want));
      if (!match) return false;
      (match as HTMLElement).click();
      return true;
    },
    { sel: listboxSelector, want: target },
  ).catch(() => false);

  if (clickedAfterType) return;

  await page.keyboard.press('Enter').catch(() => {});
  await page.waitForTimeout(150);
  const resolved = await combobox.evaluate((node) => (node as HTMLInputElement).value ?? '').catch(() => '');
  if (!resolved.trim()) {
    throw new Error(`No combobox option matching "${value}"`);
  }
}

async function clickRadioOption(page: Page, elementId: string, value: string): Promise<void> {
  const radios = page.locator(`${elementIdSelector(elementId)} input[type="radio"]`);
  const count = await radios.count();
  if (count) {
    const candidates = radioValueCandidates(value);
    const entries: { radio: ReturnType<typeof radios.nth>; label: string; inputValue: string }[] = [];
    for (let index = 0; index < count; index += 1) {
      const radio = radios.nth(index);
      const { label, inputValue } = await radio.evaluate((node) => {
        const input = node as HTMLInputElement;
        const explicit = input.id ? document.querySelector(`label[for="${input.id}"]`) : null;
        const wrapping = input.closest('label');
        return {
          label: (explicit?.textContent ?? wrapping?.textContent ?? '').trim(),
          inputValue: (input.value ?? '').trim(),
        };
      });
      entries.push({ radio, label, inputValue });
    }

    for (const candidate of candidates) {
      for (const entry of entries) {
        const labelLower = entry.label.toLowerCase();
        const valueLower = entry.inputValue.toLowerCase();
        if (labelLower === candidate || valueLower === candidate) {
          await entry.radio.click();
          return;
        }
      }
    }

    for (const candidate of candidates) {
      for (const entry of entries) {
        if (entry.label.toLowerCase().includes(candidate)) {
          await entry.radio.click();
          return;
        }
      }
    }

    const available = entries.map((entry) => entry.label || entry.inputValue).filter(Boolean).join(', ');
    throw new Error(`No radio option matching "${value}"${available ? ` (options: ${available})` : ''}`);
  }

  const roleRadios = page.locator(`${elementIdSelector(elementId)} [role="radio"]`);
  const roleCount = await roleRadios.count();
  if (!roleCount) {
    await page.locator(elementIdSelector(elementId)).first().click();
    return;
  }

  const candidates = radioValueCandidates(value);
  const entries: { radio: ReturnType<typeof roleRadios.nth>; label: string; inputValue: string }[] = [];
  for (let index = 0; index < roleCount; index += 1) {
    const radio = roleRadios.nth(index);
    const { label, inputValue } = await radio.evaluate((node) => {
      const el = node as HTMLElement;
      const ids = (el.getAttribute('aria-labelledby') ?? '').split(/\s+/).filter(Boolean);
      const labelledBy = ids.map((id) => document.getElementById(id)?.textContent ?? '').join(' ').trim();
      return {
        label: (el.getAttribute('aria-label') ?? labelledBy ?? el.textContent ?? '').trim(),
        inputValue: (el.getAttribute('data-value') ?? el.getAttribute('value') ?? '').trim(),
      };
    });
    entries.push({ radio, label, inputValue });
  }

  for (const candidate of candidates) {
    for (const entry of entries) {
      const labelLower = entry.label.toLowerCase();
      const valueLower = entry.inputValue.toLowerCase();
      if (labelLower === candidate || valueLower === candidate) {
        await entry.radio.click();
        return;
      }
    }
  }

  for (const candidate of candidates) {
    for (const entry of entries) {
      if (entry.label.toLowerCase().includes(candidate)) {
        await entry.radio.click();
        return;
      }
    }
  }

  const available = entries.map((entry) => entry.label || entry.inputValue).filter(Boolean).join(', ');
  throw new Error(`No radio option matching "${value}"${available ? ` (options: ${available})` : ''}`);
}

function radioValueCandidates(value: string): string[] {
  const raw = value.trim().toLowerCase();
  const truthy = new Set(['true', 'yes', 'y', '1', 'on', 'checked', 'agree', 'agreed', 'accept', 'accepted', 'confirm', 'confirmed', 'approved']);
  const falsy = new Set(['false', 'no', 'n', '0', 'off', 'decline', 'declined', 'disagree', 'reject', 'rejected']);
  const candidates = [raw];
  if (truthy.has(raw)) candidates.push('yes', 'true', 'y', '1');
  if (falsy.has(raw)) candidates.push('no', 'false', 'n', '0');
  return Array.from(new Set(candidates.filter(Boolean)));
}

function actionResult(
  action: ExternalApplyAction,
  options: {
    ok: boolean;
    message: string;
    valueAfter?: string | null;
    errors?: string[];
    newUrl: string;
    previousUrl: string;
  },
): ExternalApplyActionResult {
  return {
    ok: options.ok,
    action_type: action.action_type,
    element_id: action.element_id,
    message: options.message,
    value_after: options.valueAfter ?? null,
    navigated: options.newUrl !== options.previousUrl,
    new_url: options.newUrl,
    errors: options.errors ?? [],
  };
}
