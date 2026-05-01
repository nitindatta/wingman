import type { Page } from 'playwright-core';

export type ExternalControlKind =
  | 'native_text'
  | 'native_select'
  | 'native_checkbox'
  | 'native_radio_group'
  | 'aria_checkbox'
  | 'aria_radio_group'
  | 'aria_combobox'
  | 'button_listbox'
  | 'prompt_select'
  | 'file_upload'
  | 'unknown';

export type ObservedField = {
  element_id: string;
  label: string;
  field_type: string;
  control_kind?: ExternalControlKind | null;
  required: boolean;
  current_value: string | null;
  options: string[];
  nearby_text: string;
  disabled: boolean;
  visible: boolean;
  invalid?: boolean;
  validation_message?: string | null;
};

export type ObservedAction = {
  element_id: string;
  label: string;
  kind: 'button' | 'link' | 'submit' | 'unknown';
  href: string | null;
  disabled: boolean;
  nearby_text: string;
};

export type PageObservation = {
  url: string;
  title: string;
  page_type: string;
  visible_text: string;
  fields: ObservedField[];
  buttons: ObservedAction[];
  links: ObservedAction[];
  uploads: ObservedField[];
  errors: string[];
  screenshot_ref: string | null;
};

export async function observeExternalApplyPage(page: Page): Promise<PageObservation> {
  await page.waitForLoadState('domcontentloaded', { timeout: 15_000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => {});
  return page.evaluate(buildExternalApplyObservationExpression());
}

export function buildExternalApplyObservationExpression(source = collectExternalApplyObservation.toString()): string {
  const wrappedSource = `(${normalizeInjectedNameHelpers(source)})`;
  return `
    (() => {
      const __envoyName = (value) => value;
      const collect = eval(${JSON.stringify(wrappedSource)});
      return collect();
    })()
  `;
}

export function evaluateExternalApplyObservation(source: string): PageObservation {
  const __envoyName = <T>(value: T): T => value;
  const collect = eval(`(${normalizeInjectedNameHelpers(source)})`) as () => PageObservation;
  void __envoyName;
  return collect();
}

export function normalizeInjectedNameHelpers(source: string): string {
  return source.replace(/\b__name\d*\b/g, '__envoyName');
}

export function collectExternalApplyObservation(): PageObservation {
  const cleanText = (value: string | null | undefined, max = 600): string =>
    (value ?? '').replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').replace(/\s+/g, ' ').trim().slice(0, max);

  const explicitLabelFor = (el: Element): HTMLLabelElement | null => {
    const id = (el as HTMLElement).id;
    if (!id) return null;
    return Array.from(document.querySelectorAll('label')).find((label) => label.htmlFor === id) ?? null;
  };

  const labelledByText = (el: Element): string => {
    const ids = (el.getAttribute('aria-labelledby') ?? '')
      .split(/\s+/)
      .map((value) => value.trim())
      .filter(Boolean);
    return cleanText(
      ids
        .map((id) => document.getElementById(id)?.textContent ?? '')
        .filter(Boolean)
        .join(' '),
      240,
    );
  };

  const isVisible = (el: Element): boolean => {
    if (!(el instanceof window.HTMLElement)) return false;
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  };

  const hasVisibleAssociatedLabel = (input: HTMLInputElement): boolean => {
    const labels = Array.from(document.querySelectorAll('label'));
    const explicit = input.id ? labels.find((label) => label.htmlFor === input.id) : null;
    const wrapping = input.closest('label');
    const fieldsetLegend = input.closest('fieldset')?.querySelector('legend');
    const containerLabel = nearestContainer(input).querySelector('label, [class*="label"], [class*="title"], [class*="heading"]');
    return [explicit, wrapping, fieldsetLegend, containerLabel].some((candidate) => candidate != null && isVisible(candidate));
  };

  const isObservableInput = (input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): boolean => {
    if (isVisible(input)) return true;
    if (!(input instanceof window.HTMLInputElement)) return false;
    const type = input.type?.toLowerCase() || 'text';
    if (!['checkbox', 'radio'].includes(type)) return false;
    return hasVisibleAssociatedLabel(input);
  };

  const isSelectPromptInput = (input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): boolean => {
    if (!(input instanceof window.HTMLInputElement)) return false;
    return Boolean(
      input.closest('[data-automation-id="multiSelectContainer"], [data-uxi-widget-type="multiselect"]')
      || input.getAttribute('data-uxi-widget-type') === 'selectinput',
    );
  };

  const assignElementId = (el: Element, prefix: string): string => {
    const attr = 'data-envoy-apply-id';
    const existing = el.getAttribute(attr);
    if (existing) return existing;
    const next = `${prefix}_${document.querySelectorAll(`[${attr}]`).length + 1}`;
    el.setAttribute(attr, next);
    return next;
  };

  const nearestContainer = (el: Element): Element =>
    el.closest('fieldset, [class*="question"], [class*="field"], [class*="form-group"], [class*="control"], [class*="input"], form, section, div')
    ?? el;

  const textNear = (el: Element, max = 320): string => cleanText(nearestContainer(el).textContent, max);

  const labelForInput = (input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): string => {
    const labels = Array.from(document.querySelectorAll('label'));
    const explicit = input.id ? labels.find((label) => label.htmlFor === input.id) : null;
    const wrapping = input.closest('label');
    const fieldsetLegend = input.closest('fieldset')?.querySelector('legend');
    const containerLabel = nearestContainer(input).querySelector('label, [class*="label"], [class*="title"], [class*="heading"]');
    return cleanText(
      explicit?.textContent
      ?? wrapping?.textContent
      ?? fieldsetLegend?.textContent
      ?? containerLabel?.textContent
      ?? input.getAttribute('aria-label')
      ?? input.getAttribute('placeholder')
      ?? input.name
      ?? input.id,
      240,
    );
  };

  const labelForElement = (el: Element): string =>
    cleanText(
      labelledByText(el)
      || explicitLabelFor(el)?.textContent
      || el.getAttribute('aria-label')
      || el.closest('label')?.textContent
      || el.closest('fieldset')?.querySelector('legend')?.textContent
      || nearestContainer(el).querySelector('label, [class*="label"], [class*="title"], [class*="heading"]')?.textContent
      || textNear(el, 240),
      240,
    );

  const normalizedSelectValue = (value: string | null | undefined): string | null => {
    const text = cleanText(value, 180);
    if (!text) return null;
    return /^(select one|choose one|search)$/i.test(text) ? null : text;
  };

  const optionLabelForElement = (el: Element): string =>
    cleanText(
      labelledByText(el)
      || el.getAttribute('aria-label')
      || el.textContent
      || (el as HTMLInputElement).value
      || '',
      160,
    );

  const optionLabelForListboxOption = (el: Element): string => {
    const dataValue = cleanText(el.getAttribute('data-value'), 240);
    const dataValueLabel = cleanText((dataValue.split('||')[0] ?? dataValue), 160);
    return optionLabelForElement(el) || dataValueLabel;
  };

  const controlledListboxesFor = (el: Element): HTMLElement[] => {
    const listboxes: HTMLElement[] = [];
    const add = (candidate: Element | null | undefined): void => {
      if (!(candidate instanceof window.HTMLElement)) return;
      if (!candidate.matches('[role="listbox"], ul, ol, [data-value]')) return;
      if (!listboxes.includes(candidate)) listboxes.push(candidate);
    };
    const addByIds = (ids: string | null): void => {
      (ids ?? '')
        .split(/\s+/)
        .map((id) => id.trim())
        .filter(Boolean)
        .forEach((id) => add(document.getElementById(id)));
    };

    addByIds(el.getAttribute('aria-controls'));
    addByIds(el.getAttribute('aria-owns'));
    const trigger = el.closest('[aria-controls], [aria-owns]') ?? el.parentElement?.querySelector('[aria-controls], [aria-owns]');
    if (trigger && trigger !== el) {
      addByIds(trigger.getAttribute('aria-controls'));
      addByIds(trigger.getAttribute('aria-owns'));
    }

    const container = nearestContainer(el);
    container.querySelectorAll<HTMLElement>('[role="listbox"], ul[id], ol[id]').forEach(add);
    let sibling = el.nextElementSibling;
    while (sibling) {
      add(sibling);
      sibling.querySelectorAll<HTMLElement>('[role="listbox"], ul[id], ol[id]').forEach(add);
      sibling = sibling.nextElementSibling;
    }
    return listboxes;
  };

  const listboxOptionsForControl = (el: Element): string[] => {
    const options = controlledListboxesFor(el).flatMap((listbox) =>
      Array.from(listbox.querySelectorAll<HTMLElement>('[role="option"], li, [data-value]'))
        .map(optionLabelForListboxOption)
        .filter(Boolean),
    );
    return Array.from(new Set(options));
  };

  const requiredFor = (input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): boolean => {
    const nearby = textNear(input, 180).toLowerCase();
    return input.required || input.getAttribute('aria-required') === 'true' || nearby.includes('required') || /\*\s*$/.test(labelForInput(input));
  };

  const requiredForElement = (el: Element): boolean => {
    const nearby = textNear(el, 180).toLowerCase();
    return el.getAttribute('aria-required') === 'true' || nearby.includes('required') || /\*\s*$/.test(labelForElement(el));
  };

  const validationStateFor = (el: Element, label: string): { invalid: boolean; validationMessage: string | null } => {
    const ariaInvalid = (el.getAttribute('aria-invalid') ?? '').toLowerCase() === 'true';
    const dataInvalid = (el.getAttribute('data-invalid') ?? '').toLowerCase() === 'true'
      || (el.getAttribute('data-state') ?? '').toLowerCase() === 'invalid';
    const native = el as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
    const nativeInvalid = 'validity' in native && native.willValidate === true && native.validity.valid === false;
    const ariaErrorText = (el.getAttribute('aria-errormessage') ?? '')
      .split(/\s+/)
      .map((id) => cleanText(document.getElementById(id)?.textContent, 220))
      .filter(Boolean)
      .join(' ');
    const message = cleanText(
      ('validationMessage' in native ? native.validationMessage : '')
      || ariaErrorText
      || el.getAttribute('data-error')
      || '',
      220,
    );
    return {
      invalid: ariaInvalid || dataInvalid || nativeInvalid,
      validationMessage: message || (ariaInvalid || dataInvalid ? `${label} is marked invalid.` : null),
    };
  };

  const looksLikeControlTranscript = (text: string): boolean => {
    const normalized = text.toLowerCase();
    if (/\b(open list|selected:|aria-|setupconditionalattributeitems|aattributeitems)\b/.test(normalized)) return true;
    if (text.length < 180) return false;
    const optionWords = (normalized.match(/\b(select|choose|option|yes|no|none|basic|intermediate|proficient|fluent)\b/g) ?? []).length;
    const validationWords = /\b(required|invalid|missing|must|cannot|can't|failed|error|enter a valid|please enter|please select)\b/.test(normalized);
    return optionWords >= 4 && !validationWords;
  };

  const shouldKeepErrorText = (text: string, source: 'candidate' | 'validation'): boolean => {
    if (!text) return false;
    if (source === 'validation') return true;
    if (looksLikeControlTranscript(text)) return false;
    if (text.length <= 180) return true;
    return /\b(required|invalid|missing|must|cannot|can't|failed|error|enter a valid|please enter|please select)\b/i.test(text);
  };

  const fieldTypeFor = (input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): string => {
    const tag = input.tagName.toLowerCase();
    if (tag === 'textarea') return 'textarea';
    if (tag === 'select') return 'select';
    const rawType = (input as HTMLInputElement).type?.toLowerCase() || 'text';
    if (rawType === 'tel') return 'phone';
    return rawType;
  };

  const controlKindForInput = (
    input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
    fieldType: string,
  ): ExternalControlKind => {
    if (input instanceof window.HTMLSelectElement) return 'native_select';
    if (input instanceof window.HTMLInputElement) {
      if (fieldType === 'checkbox') return 'native_checkbox';
      if (fieldType === 'file') return 'file_upload';
    }
    return 'native_text';
  };

  const optionLabelFor = (input: HTMLInputElement): string => {
    const labels = Array.from(document.querySelectorAll('label'));
    const explicit = input.id ? labels.find((label) => label.htmlFor === input.id) : null;
    const wrapping = input.closest('label');
    return cleanText(explicit?.textContent ?? wrapping?.textContent ?? input.value, 160);
  };

  const fields: ObservedField[] = [];
  const radioGroups = new Set<string>();
  const inputs = Array.from(document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select',
  ));

  for (const input of inputs) {
    if (!isObservableInput(input)) continue;
    if (isSelectPromptInput(input)) continue;
    if ((input.getAttribute('role') ?? '').toLowerCase() === 'combobox') continue;
    const type = fieldTypeFor(input);

    if (type === 'radio') {
      const radio = input as HTMLInputElement;
      const groupName = radio.name || radio.id;
      if (radioGroups.has(groupName)) continue;
      radioGroups.add(groupName);

      const groupRadios = inputs
        .filter((candidate): candidate is HTMLInputElement =>
          candidate instanceof window.HTMLInputElement && candidate.type === 'radio' && (candidate.name || candidate.id) === groupName);
      const container = radio.closest('fieldset') ?? nearestContainer(radio);
      const checked = groupRadios.find((candidate) => candidate.checked);
      const label = cleanText(container.querySelector('legend')?.textContent ?? labelForInput(radio), 240);
      const required = groupRadios.some((candidate) => candidate.required) || textNear(container).toLowerCase().includes('required');
      const invalid = required && !checked;
      fields.push({
        element_id: assignElementId(container, 'field'),
        label,
        field_type: 'radio',
        control_kind: 'native_radio_group',
        required,
        current_value: checked ? optionLabelFor(checked) : null,
        options: groupRadios.map(optionLabelFor).filter(Boolean),
        nearby_text: textNear(container),
        disabled: groupRadios.every((candidate) => candidate.disabled),
        visible: true,
        invalid,
        validation_message: invalid ? `${label} is required.` : null,
      });
      continue;
    }

    const options = input instanceof window.HTMLSelectElement
      ? Array.from(input.options).map((option) => cleanText(option.textContent, 160)).filter(Boolean)
      : [];
    const currentValue = input instanceof window.HTMLInputElement && input.type === 'checkbox'
      ? (input.checked ? 'checked' : null)
      : ((input as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value || null);
    const label = labelForInput(input);
    const validation = validationStateFor(input, label);

    fields.push({
      element_id: assignElementId(input, 'field'),
      label,
      field_type: type,
      control_kind: controlKindForInput(input, type),
      required: requiredFor(input),
      current_value: currentValue,
      options,
      nearby_text: textNear(input),
      disabled: input.disabled,
      visible: true,
      invalid: validation.invalid,
      validation_message: validation.validationMessage,
    });
  }

  const customCheckboxes = Array.from(document.querySelectorAll<HTMLElement>('[role="checkbox"]'));
  for (const checkbox of customCheckboxes) {
    if (!isVisible(checkbox)) continue;
    if (fields.some((field) => field.element_id === checkbox.getAttribute('data-envoy-apply-id'))) continue;
    const ariaChecked = (checkbox.getAttribute('aria-checked') ?? '').toLowerCase();
    const dataState = (checkbox.getAttribute('data-state') ?? '').toLowerCase();
    const label = labelForElement(checkbox);
    const required = requiredForElement(checkbox);
    const checked = ariaChecked === 'true' || dataState === 'checked';
    const validation = validationStateFor(checkbox, label);
    const invalid = validation.invalid || (required && !checked);
    fields.push({
      element_id: assignElementId(checkbox, 'field'),
      label,
      field_type: 'checkbox',
      control_kind: 'aria_checkbox',
      required,
      current_value: checked ? 'checked' : null,
      options: [],
      nearby_text: textNear(checkbox),
      disabled: checkbox.getAttribute('aria-disabled') === 'true' || checkbox.hasAttribute('disabled'),
      visible: true,
      invalid,
      validation_message: invalid ? validation.validationMessage ?? `${label} is required.` : null,
    });
  }

  const customRadioGroups = Array.from(document.querySelectorAll<HTMLElement>('[role="radiogroup"]'));
  for (const group of customRadioGroups) {
    if (!isVisible(group)) continue;
    if (group.querySelector('input[type="radio"]')) continue;
    const radios = Array.from(group.querySelectorAll<HTMLElement>('[role="radio"]')).filter(isVisible);
    if (!radios.length) continue;
    const checked = radios.find((radio) => (radio.getAttribute('aria-checked') ?? '').toLowerCase() === 'true');
    const label = labelForElement(group);
    const required = requiredForElement(group);
    const validation = validationStateFor(group, label);
    const invalid = validation.invalid || (required && !checked);
    fields.push({
      element_id: assignElementId(group, 'field'),
      label,
      field_type: 'radio',
      control_kind: 'aria_radio_group',
      required,
      current_value: checked ? optionLabelForElement(checked) : null,
      options: radios.map(optionLabelForElement).filter(Boolean),
      nearby_text: textNear(group),
      disabled: radios.every((radio) => radio.getAttribute('aria-disabled') === 'true' || radio.hasAttribute('disabled')),
      visible: true,
      invalid,
      validation_message: invalid ? validation.validationMessage ?? `${label} is required.` : null,
    });
  }

  const customComboboxes = Array.from(document.querySelectorAll<HTMLElement>('[role="combobox"]'));
  for (const combobox of customComboboxes) {
    if (!isVisible(combobox)) continue;
    const existingId = combobox.getAttribute('data-envoy-apply-id');
    if (existingId && fields.some((field) => field.element_id === existingId)) continue;
    const options = listboxOptionsForControl(combobox);
    const inputValue = combobox instanceof window.HTMLInputElement
      || combobox instanceof window.HTMLTextAreaElement
      || combobox instanceof window.HTMLSelectElement
      ? combobox.value
      : '';
    const currentValue = cleanText(
      combobox.getAttribute('aria-valuetext')
      || inputValue
      || combobox.textContent
      || '',
      160,
    );
    const label = labelForElement(combobox);
    const validation = validationStateFor(combobox, label);
    const required = combobox instanceof window.HTMLInputElement
      || combobox instanceof window.HTMLTextAreaElement
      || combobox instanceof window.HTMLSelectElement
      ? requiredFor(combobox)
      : requiredForElement(combobox);
    fields.push({
      element_id: assignElementId(combobox, 'field'),
      label,
      field_type: 'select',
      control_kind: 'aria_combobox',
      required,
      current_value: normalizedSelectValue(currentValue),
      options,
      nearby_text: textNear(combobox),
      disabled: combobox.getAttribute('aria-disabled') === 'true' || combobox.hasAttribute('disabled'),
      visible: true,
      invalid: validation.invalid,
      validation_message: validation.validationMessage,
    });
  }

  const buttonListboxControls = Array.from(document.querySelectorAll<HTMLElement>(
    'button[aria-haspopup="listbox"], [role="button"][aria-haspopup="listbox"]',
  ));
  for (const control of buttonListboxControls) {
    if (!isVisible(control)) continue;
    if (!explicitLabelFor(control) && !labelledByText(control)) continue;
    const existingId = control.getAttribute('data-envoy-apply-id');
    if (existingId && fields.some((field) => field.element_id === existingId)) continue;
    const options = listboxOptionsForControl(control);
    const label = labelForElement(control);
    const validation = validationStateFor(control, label);
    fields.push({
      element_id: assignElementId(control, 'field'),
      label,
      field_type: 'select',
      control_kind: 'button_listbox',
      required: requiredForElement(control) || /\brequired\b/i.test(control.getAttribute('aria-label') ?? ''),
      current_value: normalizedSelectValue(control.textContent || control.getAttribute('value') || ''),
      options,
      nearby_text: textNear(control),
      disabled: control.getAttribute('aria-disabled') === 'true' || control.hasAttribute('disabled'),
      visible: true,
      invalid: validation.invalid,
      validation_message: validation.validationMessage,
    });
  }

  const promptSelectInputs = Array.from(document.querySelectorAll<HTMLInputElement>(
    'input[data-uxi-widget-type="selectinput"]',
  ));
  for (const input of promptSelectInputs) {
    if (!isObservableInput(input)) continue;
    const existingId = input.getAttribute('data-envoy-apply-id');
    if (existingId && fields.some((field) => field.element_id === existingId)) continue;
    const container = input.closest('[data-automation-id="multiSelectContainer"], [data-uxi-widget-type="multiselect"]') ?? input;
    const selectedValue = cleanText(
      container.querySelector('[data-automation-id="promptOption"]')?.textContent
      || container.querySelector('[role="option"][title]')?.getAttribute('title')
      || container.querySelector('[role="option"]')?.textContent
      || container.querySelector('[data-automation-id="promptAriaInstruction"]')?.textContent
      || '',
      180,
    );
    const label = labelForInput(input);
    const validation = validationStateFor(input, label);
    fields.push({
      element_id: assignElementId(input, 'field'),
      label,
      field_type: 'select',
      control_kind: 'prompt_select',
      required: requiredFor(input),
      current_value: normalizedSelectValue(selectedValue),
      options: [],
      nearby_text: textNear(container),
      disabled: input.disabled || input.getAttribute('aria-disabled') === 'true',
      visible: true,
      invalid: validation.invalid,
      validation_message: validation.validationMessage,
    });
  }

  const actionLabel = (el: HTMLElement): string => {
    const candidates = [
      el.textContent,
      el instanceof window.HTMLInputElement ? el.value : '',
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
    ];
    return cleanText(candidates.find((candidate) => cleanText(candidate, 180)) ?? '', 180);
  };

  const isUtilityNavigationAction = (el: HTMLElement, label: string): boolean => {
    const combined = cleanText(
      [
        label,
        el.getAttribute('aria-label'),
        el.getAttribute('title'),
        el.getAttribute('id'),
        el.getAttribute('data-automation-id'),
        el instanceof window.HTMLAnchorElement ? el.getAttribute('href') : '',
      ].filter(Boolean).join(' '),
      240,
    ).toLowerCase();
    return /\b(skip to main content|skip navigation|accessibilityskiptomaincontent|close jump menu|jump menu)\b/.test(combined);
  };

  const buttons: ObservedAction[] = [];
  const buttonElements = Array.from(document.querySelectorAll<HTMLElement>(
    'button, input[type="submit"], input[type="button"], [role="button"]',
  ));
  for (const button of buttonElements) {
    if (!isVisible(button)) continue;
    const existingId = button.getAttribute('data-envoy-apply-id');
    if (existingId && fields.some((field) => field.element_id === existingId)) continue;
    const label = actionLabel(button);
    if (!label) continue;
    if (isUtilityNavigationAction(button, label)) continue;
    const inputType = button instanceof window.HTMLInputElement ? button.type.toLowerCase() : '';
    const kind = inputType === 'submit' || (button instanceof window.HTMLButtonElement && button.type === 'submit') ? 'submit' : 'button';
    buttons.push({
      element_id: assignElementId(button, 'button'),
      label,
      kind,
      href: null,
      disabled: button.hasAttribute('disabled') || button.getAttribute('aria-disabled') === 'true',
      nearby_text: textNear(button, 220),
    });
  }

  const links: ObservedAction[] = [];
  for (const link of Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href]'))) {
    if (!isVisible(link)) continue;
    const label = cleanText(link.textContent ?? link.getAttribute('aria-label') ?? link.href, 180);
    if (!label) continue;
    if (isUtilityNavigationAction(link, label)) continue;
    links.push({
      element_id: assignElementId(link, 'link'),
      label,
      kind: 'link',
      href: link.href,
      disabled: link.getAttribute('aria-disabled') === 'true',
      nearby_text: textNear(link, 220),
    });
  }

  const errors = Array.from(document.querySelectorAll<HTMLElement>(
    '[role="alert"], [aria-live], [class*="error"], [class*="invalid"], [data-testid*="error"], [id*="error"]',
  ))
    .filter(isVisible)
    .map((el) => cleanText(el.textContent, 260))
    .filter((text) => shouldKeepErrorText(text, 'candidate'))
    .filter(Boolean);
  for (const input of inputs) {
    if ('validationMessage' in input && input.validationMessage) {
      errors.push(cleanText(input.validationMessage, 220));
    }
  }
  for (const field of fields) {
    if (!field.invalid) continue;
    const message = cleanText(field.validation_message || `${field.label || 'A field'} is invalid.`, 260);
    if (shouldKeepErrorText(message, 'validation')) {
      errors.push(message);
    }
  }

  const visibleText = cleanText(document.body?.innerText ?? document.body?.textContent, 6000);
  const uploads = fields.filter((field) => field.field_type === 'file');
  const lowerText = visibleText.toLowerCase();
  const hasLoginFields = fields.some((field) => field.field_type === 'password' || /email|password/i.test(field.label));
  const hasCaptchaSignals = Boolean(
    document.querySelector(
      [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        '[data-sitekey]',
        'textarea[name="g-recaptcha-response"]',
        'textarea[name="h-captcha-response"]',
        '[title*="captcha" i]',
      ].join(', '),
    ),
  ) || /\bi am not a robot\b|\bi'm not a robot\b|\bverify you are human\b|\bhuman verification\b|\bsecurity check\b/.test(lowerText);
  const page_type =
    /sign in|log in|login/.test(lowerText) && hasLoginFields ? 'login'
    : hasCaptchaSignals && !hasLoginFields ? 'captcha'
    : /application (submitted|received|successful|complete)|thank you for applying/.test(lowerText) ? 'confirmation'
    : uploads.length > 0 ? 'resume_upload'
    : /review|summary|confirm/.test(lowerText) && buttons.some((button) => /submit|apply/i.test(button.label)) ? 'review'
    : fields.length > 0 ? 'form'
    : 'unknown';

  return {
    url: window.location.href,
    title: document.title,
    page_type,
    visible_text: visibleText,
    fields,
    buttons,
    links,
    uploads,
    errors: Array.from(new Set(errors)).slice(0, 12),
    screenshot_ref: null,
  };
}
