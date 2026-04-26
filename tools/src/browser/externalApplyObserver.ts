import type { Page } from 'playwright-core';

export type ObservedField = {
  element_id: string;
  label: string;
  field_type: string;
  required: boolean;
  current_value: string | null;
  options: string[];
  nearby_text: string;
  disabled: boolean;
  visible: boolean;
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
      || el.getAttribute('aria-label')
      || el.closest('label')?.textContent
      || el.closest('fieldset')?.querySelector('legend')?.textContent
      || nearestContainer(el).querySelector('label, [class*="label"], [class*="title"], [class*="heading"]')?.textContent
      || textNear(el, 240),
      240,
    );

  const optionLabelForElement = (el: Element): string =>
    cleanText(
      labelledByText(el)
      || el.getAttribute('aria-label')
      || el.textContent
      || (el as HTMLInputElement).value
      || '',
      160,
    );

  const requiredFor = (input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): boolean => {
    const nearby = textNear(input, 180).toLowerCase();
    return input.required || input.getAttribute('aria-required') === 'true' || nearby.includes('required') || /\*\s*$/.test(labelForInput(input));
  };

  const requiredForElement = (el: Element): boolean => {
    const nearby = textNear(el, 180).toLowerCase();
    return el.getAttribute('aria-required') === 'true' || nearby.includes('required') || /\*\s*$/.test(labelForElement(el));
  };

  const fieldTypeFor = (input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): string => {
    const tag = input.tagName.toLowerCase();
    if (tag === 'textarea') return 'textarea';
    if (tag === 'select') return 'select';
    const rawType = (input as HTMLInputElement).type?.toLowerCase() || 'text';
    if (rawType === 'tel') return 'phone';
    return rawType;
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
      fields.push({
        element_id: assignElementId(container, 'field'),
        label: cleanText(container.querySelector('legend')?.textContent ?? labelForInput(radio), 240),
        field_type: 'radio',
        required: groupRadios.some((candidate) => candidate.required) || textNear(container).toLowerCase().includes('required'),
        current_value: checked ? optionLabelFor(checked) : null,
        options: groupRadios.map(optionLabelFor).filter(Boolean),
        nearby_text: textNear(container),
        disabled: groupRadios.every((candidate) => candidate.disabled),
        visible: true,
      });
      continue;
    }

    const options = input instanceof window.HTMLSelectElement
      ? Array.from(input.options).map((option) => cleanText(option.textContent, 160)).filter(Boolean)
      : [];
    const currentValue = input instanceof window.HTMLInputElement && input.type === 'checkbox'
      ? (input.checked ? 'checked' : null)
      : ((input as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value || null);

    fields.push({
      element_id: assignElementId(input, 'field'),
      label: labelForInput(input),
      field_type: type,
      required: requiredFor(input),
      current_value: currentValue,
      options,
      nearby_text: textNear(input),
      disabled: input.disabled,
      visible: true,
    });
  }

  const customCheckboxes = Array.from(document.querySelectorAll<HTMLElement>('[role="checkbox"]'));
  for (const checkbox of customCheckboxes) {
    if (!isVisible(checkbox)) continue;
    if (fields.some((field) => field.element_id === checkbox.getAttribute('data-envoy-apply-id'))) continue;
    const ariaChecked = (checkbox.getAttribute('aria-checked') ?? '').toLowerCase();
    const dataState = (checkbox.getAttribute('data-state') ?? '').toLowerCase();
    fields.push({
      element_id: assignElementId(checkbox, 'field'),
      label: labelForElement(checkbox),
      field_type: 'checkbox',
      required: requiredForElement(checkbox),
      current_value: ariaChecked === 'true' || dataState === 'checked' ? 'checked' : null,
      options: [],
      nearby_text: textNear(checkbox),
      disabled: checkbox.getAttribute('aria-disabled') === 'true' || checkbox.hasAttribute('disabled'),
      visible: true,
    });
  }

  const customRadioGroups = Array.from(document.querySelectorAll<HTMLElement>('[role="radiogroup"]'));
  for (const group of customRadioGroups) {
    if (!isVisible(group)) continue;
    if (group.querySelector('input[type="radio"]')) continue;
    const radios = Array.from(group.querySelectorAll<HTMLElement>('[role="radio"]')).filter(isVisible);
    if (!radios.length) continue;
    const checked = radios.find((radio) => (radio.getAttribute('aria-checked') ?? '').toLowerCase() === 'true');
    fields.push({
      element_id: assignElementId(group, 'field'),
      label: labelForElement(group),
      field_type: 'radio',
      required: requiredForElement(group),
      current_value: checked ? optionLabelForElement(checked) : null,
      options: radios.map(optionLabelForElement).filter(Boolean),
      nearby_text: textNear(group),
      disabled: radios.every((radio) => radio.getAttribute('aria-disabled') === 'true' || radio.hasAttribute('disabled')),
      visible: true,
    });
  }

  const customComboboxes = Array.from(document.querySelectorAll<HTMLElement>('[role="combobox"]'));
  for (const combobox of customComboboxes) {
    if (!isVisible(combobox)) continue;
    const existingId = combobox.getAttribute('data-envoy-apply-id');
    if (existingId && fields.some((field) => field.element_id === existingId)) continue;
    const listboxId = combobox.getAttribute('aria-controls') || combobox.getAttribute('aria-owns') || '';
    const listbox = listboxId ? document.getElementById(listboxId) : null;
    const options = listbox
      ? Array.from(listbox.querySelectorAll<HTMLElement>('[role="option"], li, [data-value]'))
        .map(optionLabelForElement)
        .filter(Boolean)
      : [];
    const currentValue = cleanText(
      combobox.getAttribute('aria-valuetext')
      || combobox.getAttribute('aria-label')
      || combobox.textContent
      || '',
      160,
    );
    fields.push({
      element_id: assignElementId(combobox, 'field'),
      label: labelForElement(combobox),
      field_type: 'select',
      required: requiredForElement(combobox),
      current_value: currentValue || null,
      options,
      nearby_text: textNear(combobox),
      disabled: combobox.getAttribute('aria-disabled') === 'true' || combobox.hasAttribute('disabled'),
      visible: true,
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
    .filter(Boolean);
  for (const input of inputs) {
    if ('validationMessage' in input && input.validationMessage) {
      errors.push(cleanText(input.validationMessage, 220));
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
