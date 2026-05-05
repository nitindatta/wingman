import { existsSync } from 'node:fs';
import type { Locator, Page } from 'playwright-core';
import { observeExternalApplyPage } from './externalApplyObserver.js';
import { selectExternalOption } from './selectControl.js';
import { saveSnapshot } from './snapshot.js';

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
  artifacts?: ActionArtifact[];
  diagnostics?: Record<string, unknown> | null;
};

export type ActionArtifact = {
  type: string;
  path: string;
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

class ActionExecutionError extends Error {
  diagnostics: Record<string, unknown> | null;

  constructor(message: string, diagnostics?: Record<string, unknown> | null) {
    super(message);
    this.name = 'ActionExecutionError';
    this.diagnostics = diagnostics ?? null;
  }
}

const SELECT_CONTROL_DEPS = {
  elementIdSelector,
  safeClick,
  maybeWaitForTimeout,
  maybePressKey,
  maybeType,
  createError: (message: string, diagnostics?: Record<string, unknown> | null) =>
    new ActionExecutionError(message, diagnostics),
};

type ActionDriver = (
  page: Page,
  target: Locator,
  action: ExternalApplyAction,
  previousUrl: string,
) => Promise<void>;

const ACTION_DRIVERS: Record<ExternalApplyActionType, ActionDriver> = {
  fill_text: async (_page, target, action) => {
    if (action.value == null) throw new Error('fill_text requires value');
    await target.fill(action.value);
    await blurLocator(target);
  },
  select_option: async (page, target, action) => {
    if (action.value == null) throw new Error('select_option requires value');
    await selectExternalOption(page, target, action.element_id, action.value, SELECT_CONTROL_DEPS);
  },
  set_checkbox: async (page, target, action) => {
    await setCheckboxValue(page, target, truthyFormValue(action.value), action.element_id);
  },
  set_radio: async (page, _target, action) => {
    if (action.value == null) throw new Error('set_radio requires value');
    await clickRadioOption(page, action.element_id, action.value);
  },
  upload_file: async (_page, target, action) => {
    if (!action.value) throw new Error('upload_file requires file path');
    if (!existsSync(action.value)) throw new Error(`file does not exist: ${action.value}`);
    await target.setInputFiles(action.value);
  },
  click: async (page, target, action, previousUrl) => {
    await safeClick(page, target, action.element_id);
    await waitForPossibleNavigation(page, previousUrl);
  },
};

export async function executeExternalApplyAction(
  page: Page,
  action: ExternalApplyAction,
): Promise<ExternalApplyActionResult> {
  const previousUrl = page.url();
  const beforeObservation = await observeExternalApplyPage(page).catch(() => null);
  const target = page.locator(elementIdSelector(action.element_id)).first();
  const exists = await target.count().catch(() => 0);
  if (!exists) {
    return actionResult(action, {
      ok: false,
      message: `Element not found for id ${action.element_id}`,
      newUrl: page.url(),
      previousUrl,
      diagnostics: buildObservationDiagnostics(action, beforeObservation, null),
    });
  }

  try {
    await preparePageForInteraction(page, action);
    await ACTION_DRIVERS[action.action_type](page, target, action, previousUrl);
    await settleAfterInteraction(page, action);
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
    const diagnostics = mergeDiagnostics(
      buildObservationDiagnostics(action, beforeObservation, observation),
      err instanceof ActionExecutionError ? err.diagnostics : null,
    );
    const artifacts = await captureFailureArtifacts(page, action);
    return actionResult(action, {
      ok: false,
      message: err instanceof Error ? err.message : String(err),
      errors: observation?.errors ?? [],
      newUrl: page.url(),
      previousUrl,
      artifacts,
      diagnostics,
    });
  }
}

async function preparePageForInteraction(page: Page, action: ExternalApplyAction): Promise<void> {
  await dismissTransientUi(page, action.element_id, { includeOwnedSurfaces: false });
}

async function settleAfterInteraction(page: Page, action: ExternalApplyAction): Promise<void> {
  await maybeWaitForLoadState(page, 'domcontentloaded', 1_500);
  if (action.action_type !== 'click') {
    await maybeWaitForLoadState(page, 'networkidle', 750);
    await maybeWaitForTimeout(page, 120);
    await dismissTransientUi(page, action.element_id, { includeOwnedSurfaces: true });
  }
  await maybeWaitForTimeout(page, 180);
}

async function waitForPossibleNavigation(page: Page, previousUrl: string): Promise<void> {
  await Promise.race([
    page.waitForURL((url) => url.toString() !== previousUrl, { timeout: 8_000 }).catch(() => {}),
    maybeWaitForLoadState(page, 'domcontentloaded', 8_000),
    maybeWaitForTimeout(page, 1_000),
  ]);
}

async function setCheckboxValue(
  page: Page,
  target: ReturnType<Page['locator']>,
  desiredChecked: boolean,
  elementId: string,
): Promise<void> {
  const state = await describeCheckboxTarget(target);
  if (state.nativeCheckbox) {
    try {
      if (desiredChecked) await target.check();
      else await target.uncheck();
    } catch {
      await dismissTransientUi(page, elementId, { includeOwnedSurfaces: true });
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
    }
    return;
  }
  if (state.ariaCheckbox) {
    if (state.checked !== desiredChecked) {
      await safeClick(page, target, elementId);
    }
    return;
  }
  try {
    if (desiredChecked) await target.check();
    else await target.uncheck();
  } catch {
    await dismissTransientUi(page, elementId, { includeOwnedSurfaces: true });
    if (desiredChecked) await target.check();
    else await target.uncheck();
  }
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

function escapeCssString(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

async function captureFailureArtifacts(
  page: Page,
  action: ExternalApplyAction,
): Promise<ActionArtifact[]> {
  const tag = `${action.action_type}-${sanitizeArtifactToken(action.element_id)}`;
  const artifacts: ActionArtifact[] = [];
  const screenshotPath = await saveSnapshot(page, 'screenshot', `${tag}-failed`).catch(() => null);
  if (screenshotPath) {
    artifacts.push({ type: 'screenshot', path: screenshotPath });
  }
  const domPath = await saveSnapshot(page, 'dom', `${tag}-failed`).catch(() => null);
  if (domPath) {
    artifacts.push({ type: 'dom', path: domPath });
  }
  return artifacts;
}

function sanitizeArtifactToken(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, '-');
}

function buildObservationDiagnostics(
  action: ExternalApplyAction,
  beforeObservation: Awaited<ReturnType<typeof observeExternalApplyPage>> | null,
  afterObservation: Awaited<ReturnType<typeof observeExternalApplyPage>> | null,
): Record<string, unknown> | null {
  const beforeField = beforeObservation?.fields.find((field) => field.element_id === action.element_id);
  const afterField = afterObservation?.fields.find((field) => field.element_id === action.element_id);
  const diagnostics: Record<string, unknown> = {
    page_url_before: beforeObservation?.url ?? null,
    page_type_before: beforeObservation?.page_type ?? null,
    page_url_after: afterObservation?.url ?? null,
    page_type_after: afterObservation?.page_type ?? null,
    field_label: beforeField?.label ?? afterField?.label ?? null,
    field_type: beforeField?.field_type ?? afterField?.field_type ?? null,
    current_value_before: beforeField?.current_value ?? null,
    current_value_after: afterField?.current_value ?? null,
    closed_state_options_before: beforeField?.options ?? [],
    closed_state_options_after: afterField?.options ?? [],
    visible_errors_before: beforeObservation?.errors ?? [],
    visible_errors_after: afterObservation?.errors ?? [],
  };
  return Object.values(diagnostics).some((value) => (
    value != null && (!(Array.isArray(value)) || value.length > 0)
  )) ? diagnostics : null;
}

function mergeDiagnostics(
  ...entries: Array<Record<string, unknown> | null | undefined>
): Record<string, unknown> | null {
  const merged: Record<string, unknown> = {};
  for (const entry of entries) {
    if (!entry) {
      continue;
    }
    for (const [key, value] of Object.entries(entry)) {
      merged[key] = value;
    }
  }
  return Object.keys(merged).length > 0 ? merged : null;
}

async function clickRadioOption(page: Page, elementId: string, value: string): Promise<void> {
  const radios = page.locator(`${elementIdSelector(elementId)} input[type="radio"]`);
  let count = await radios.count();
  let radioGroup = radios;
  if (count) {
    const expanded = await expandNativeRadioGroupByName(page, radios, count);
    radioGroup = expanded.radios;
    count = expanded.count;
  }
  if (count) {
    const candidates = radioValueCandidates(value);
    const entries: { radio: ReturnType<typeof radioGroup.nth>; label: string; inputValue: string; inputId: string }[] = [];
    for (let index = 0; index < count; index += 1) {
      const radio = radioGroup.nth(index);
      const { label, inputValue, inputId } = await radio.evaluate((node) => {
        const input = node as HTMLInputElement;
        const explicit = input.id ? document.querySelector(`label[for="${input.id}"]`) : null;
        const wrapping = input.closest('label');
        return {
          label: (explicit?.textContent ?? wrapping?.textContent ?? '').trim(),
          inputValue: (input.value ?? '').trim(),
          inputId: input.id ?? '',
        };
      });
      entries.push({ radio, label, inputValue, inputId });
    }

    for (const candidate of candidates) {
      for (const entry of entries) {
        const labelLower = entry.label.toLowerCase();
        const valueLower = entry.inputValue.toLowerCase();
        if (labelLower === candidate || valueLower === candidate) {
          await setNativeRadioEntry(page, entry.radio, entry.inputId, elementId);
          return;
        }
      }
    }

    for (const candidate of candidates) {
      for (const entry of entries) {
        if (entry.label.toLowerCase().includes(candidate)) {
          await setNativeRadioEntry(page, entry.radio, entry.inputId, elementId);
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
        await safeClick(page, entry.radio, elementId);
        return;
      }
    }
  }

  for (const candidate of candidates) {
    for (const entry of entries) {
      if (entry.label.toLowerCase().includes(candidate)) {
        await safeClick(page, entry.radio, elementId);
        return;
      }
    }
  }

  const available = entries.map((entry) => entry.label || entry.inputValue).filter(Boolean).join(', ');
  throw new Error(`No radio option matching "${value}"${available ? ` (options: ${available})` : ''}`);
}

async function expandNativeRadioGroupByName(
  page: Page,
  scopedRadios: Locator,
  scopedCount: number,
): Promise<{ radios: Locator; count: number }> {
  const rawGroupName = await scopedRadios.nth(0).evaluate((node) => {
    const input = node as HTMLInputElement;
    return (input.name ?? '').trim();
  }).catch(() => '');
  const groupName = typeof rawGroupName === 'string' ? rawGroupName.trim() : '';
  if (!groupName) {
    return { radios: scopedRadios, count: scopedCount };
  }

  const groupRadios = page.locator(`input[type="radio"][name="${escapeCssString(groupName)}"]`);
  const groupCount = await groupRadios.count().catch(() => 0);
  if (groupCount > scopedCount) {
    return { radios: groupRadios, count: groupCount };
  }
  return { radios: scopedRadios, count: scopedCount };
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

async function setNativeRadioEntry(
  page: Page,
  radio: Locator,
  inputId: string,
  elementId: string,
): Promise<void> {
  try {
    await radio.check();
    return;
  } catch (error) {
    if (!looksRecoverableClickError(error)) {
      throw error;
    }
  }

  await dismissTransientUi(page, elementId, { includeOwnedSurfaces: true });

  if (await clickAssociatedRadioLabel(page, radio, inputId, elementId)) {
    return;
  }

  await radio.evaluate((node) => {
    const input = node as HTMLInputElement;
    input.checked = true;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function clickAssociatedRadioLabel(
  page: Page,
  radio: Locator,
  inputId: string,
  elementId: string,
): Promise<boolean> {
  if (inputId) {
    const explicitLabel = page.locator(`label[for="${escapeCssIdent(inputId)}"]`).first();
    const explicitCount = await explicitLabel.count().catch(() => 0);
    if (explicitCount) {
      await safeClick(page, explicitLabel, elementId);
      return true;
    }
  }

  const nestedLocator = radio as Locator & { locator?: Locator['locator'] };
  if (typeof nestedLocator.locator === 'function') {
    const wrappingLabel = nestedLocator.locator('xpath=ancestor::label[1]').first();
    const wrappingCount = await wrappingLabel.count().catch(() => 0);
    if (wrappingCount) {
      await safeClick(page, wrappingLabel, elementId);
      return true;
    }
  }

  return false;
}

async function safeClick(page: Page, target: Locator, elementId: string): Promise<void> {
  try {
    await target.click();
  } catch (error) {
    if (!looksRecoverableClickError(error)) {
      throw error;
    }
    await dismissTransientUi(page, elementId, { includeOwnedSurfaces: true });
    await target.click();
  }
}

function looksRecoverableClickError(error: unknown): boolean {
  const message = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  return (
    message.includes('intercepts pointer events')
    || message.includes('pointer events intercept')
    || message.includes('outside of the viewport')
    || message.includes('another element would receive the click')
    || message.includes('element is not attached to the dom')
  );
}

async function dismissTransientUi(
  page: Page,
  elementId: string,
  options: { includeOwnedSurfaces: boolean },
): Promise<void> {
  const hasTransientUi = await detectTransientUi(page, elementId, options.includeOwnedSurfaces);
  if (!hasTransientUi) {
    return;
  }
  await maybePressKey(page, 'Escape');
  await maybeWaitForTimeout(page, 80);
  if (await detectTransientUi(page, elementId, options.includeOwnedSurfaces)) {
    await maybeMouseClick(page, 8, 8);
    await maybeWaitForTimeout(page, 80);
  }
}

async function detectTransientUi(
  page: Page,
  elementId: string,
  includeOwnedSurfaces: boolean,
): Promise<boolean> {
  const evaluator = (page as unknown as {
    evaluate?: <TArg, TResult>(fn: (arg: TArg) => TResult, arg: TArg) => Promise<TResult>;
  }).evaluate;
  if (!evaluator) {
    return false;
  }

  return evaluator(
    ({ targetSelector, includeOwned }) => {
      const target = document.querySelector(targetSelector);
      const isVisible = (node: Element): boolean => {
        if (!(node instanceof window.HTMLElement)) return false;
        if (node.hidden || node.getAttribute('aria-hidden') === 'true') return false;
        const style = window.getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
      };

      const active = document.activeElement;
      if (active instanceof window.HTMLElement && active !== document.body && (!target || !target.contains(active))) {
        active.blur();
      }

      const surfaces = Array.from(
        document.querySelectorAll(
          '[role="listbox"], [role="menu"], [data-popper-placement], [class*="popover"], [class*="dropdown"], [class*="menu"]',
        ),
      ).filter(isVisible);

      return surfaces.some((surface) => {
        if (!includeOwned && target && (surface.contains(target) || target.contains(surface))) {
          return false;
        }
        return true;
      });
    },
    {
      targetSelector: elementIdSelector(elementId),
      includeOwned: includeOwnedSurfaces,
    },
  ).catch(() => false);
}

async function blurLocator(target: Locator): Promise<void> {
  await target.evaluate((node) => {
    if (node instanceof HTMLElement) {
      node.blur();
    }
  }).catch(() => {});
}

async function maybeWaitForLoadState(
  page: Page,
  state: 'domcontentloaded' | 'networkidle',
  timeout: number,
): Promise<void> {
  const waiter = (page as unknown as {
    waitForLoadState?: (state: 'domcontentloaded' | 'networkidle', options?: { timeout?: number }) => Promise<void>;
  }).waitForLoadState;
  if (!waiter) {
    return;
  }
  await waiter.call(page, state, { timeout }).catch(() => {});
}

async function maybeWaitForTimeout(page: Page, timeout: number): Promise<void> {
  await page.waitForTimeout(timeout).catch(() => {});
}

async function maybePressKey(page: Page, key: string): Promise<void> {
  const keyboard = (page as unknown as {
    keyboard?: { press?: (key: string) => Promise<void> };
  }).keyboard;
  if (!keyboard?.press) {
    return;
  }
  await keyboard.press(key).catch(() => {});
}

async function maybeType(page: Page, value: string): Promise<void> {
  const keyboard = (page as unknown as {
    keyboard?: { type?: (text: string) => Promise<void> };
  }).keyboard;
  if (!keyboard?.type) {
    return;
  }
  await keyboard.type(value).catch(() => {});
}

async function maybeMouseClick(page: Page, x: number, y: number): Promise<void> {
  const mouse = (page as unknown as {
    mouse?: { click?: (x: number, y: number) => Promise<void> };
  }).mouse;
  if (!mouse?.click) {
    return;
  }
  await mouse.click(x, y).catch(() => {});
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
    artifacts?: ActionArtifact[];
    diagnostics?: Record<string, unknown> | null;
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
    artifacts: options.artifacts,
    diagnostics: options.diagnostics ?? null,
  };
}
