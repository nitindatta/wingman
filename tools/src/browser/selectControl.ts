import type { Locator, Page } from 'playwright-core';

type ListboxOption = {
  text: string;
  value: string;
  disabled: boolean;
  index?: number;
};

type ComboboxControlInfo = {
  textEntryCapable: boolean;
  tagName: string;
  role: string;
};

type ComboboxSurface = {
  selector: string;
  triggerSelector?: string;
  options: ListboxOption[];
};

export type SelectControlDeps = {
  elementIdSelector: (elementId: string) => string;
  safeClick: (page: Page, target: Locator, elementId: string) => Promise<void>;
  maybeWaitForTimeout: (page: Page, timeout: number) => Promise<void>;
  maybePressKey: (page: Page, key: string) => Promise<void>;
  maybeType: (page: Page, value: string) => Promise<void>;
  createError: (message: string, diagnostics?: Record<string, unknown> | null) => Error;
};

function buildPageEvaluationExpression<TArg>(
  fn: (arg: TArg) => unknown,
  arg: TArg,
): string {
  const source = String(fn).replace(/\b__name\d*\b/g, '__envoyName');
  return `
    (() => {
      const __envoyName = (value) => value;
      const fn = eval(${JSON.stringify(`(${source})`)});
      return fn(${JSON.stringify(arg)});
    })()
  `;
}

async function evaluateInPage<TResult, TArg>(
  page: Page,
  fn: (arg: TArg) => TResult,
  arg: TArg,
): Promise<TResult> {
  return page.evaluate(buildPageEvaluationExpression(fn, arg), arg) as Promise<TResult>;
}

const OPTION_ALIAS_GROUPS = [
  ['south australia', 'sa'],
  ['new south wales', 'nsw'],
  ['queensland', 'qld'],
  ['victoria', 'vic'],
  ['tasmania', 'tas'],
  ['western australia', 'wa'],
  ['northern territory', 'nt'],
  ['australian capital territory', 'act'],
  ['mobile', 'mobile phone', 'cell phone', 'cellular', 'smartphone'],
  ['home', 'home phone', 'landline'],
  ['work', 'work phone', 'business phone'],
] as const;

export async function selectExternalOption(
  page: Page,
  target: Locator,
  elementId: string,
  value: string,
  deps: SelectControlDeps,
): Promise<void> {
  const isNativeSelect = await target.evaluate(
    (node) => (node as Element).tagName?.toLowerCase() === 'select',
  ).catch(() => false);
  if (isNativeSelect) {
    await target.selectOption({ label: value }).catch(async () => {
      await target.selectOption({ value });
    });
    return;
  }

  await selectCustomOption(page, elementId, value, deps);
}

async function selectCustomOption(
  page: Page,
  elementId: string,
  value: string,
  deps: SelectControlDeps,
): Promise<void> {
  const combobox = page.locator(deps.elementIdSelector(elementId)).first();
  const control = await describeComboboxControl(combobox);
  const diagnostics: Record<string, unknown> = {
    requested_value: value,
    requested_value_normalized: normalizeOptionText(value),
    text_entry_capable: control.textEntryCapable,
    control_tag: control.tagName,
    control_role: control.role,
  };

  await deps.safeClick(page, combobox, elementId);
  if (control.textEntryCapable) {
    await combobox.focus().catch(() => {});
  }

  const allowsForgivingFallback = await allowsForgivingComboboxFallback(page, elementId, deps);
  diagnostics.allows_forgiving_fallback = allowsForgivingFallback;

  if (!control.textEntryCapable) {
    const locatorResult = await tryClickOptionViaLocator(page, value, allowsForgivingFallback, deps);
    diagnostics.locator_result = locatorResult;
    if (locatorResult === 'selected') {
      return;
    }

    const ownedSurface = await resolveOwnedComboboxSurface(page, elementId, deps, 8, 120);
    diagnostics.owned_options = listboxOptionLabels(ownedSurface?.options ?? []);
    const ownedSelection = ownedSurface
      ? await trySelectFromComboboxSurface(page, elementId, value, ownedSurface, allowsForgivingFallback, diagnostics, deps)
      : 'unavailable';
    if (ownedSelection === 'selected') {
      return;
    }
    if (ownedSelection === 'failed') {
      throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
    }

    const initialSurface = await resolveComboboxSurface(page, elementId, deps, 12, 150);
    diagnostics.initial_options = listboxOptionLabels(initialSurface?.options ?? []);
    const initialSelection = initialSurface
      ? await trySelectFromComboboxSurface(page, elementId, value, initialSurface, allowsForgivingFallback, diagnostics, deps)
      : 'unavailable';
    if (initialSelection === 'selected') {
      return;
    }
    if (initialSelection === 'failed') {
      throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
    }

    const lateOwnedSurface = await resolveOwnedComboboxSurface(
      page,
      elementId,
      deps,
      8,
      120,
      { requireExpanded: false },
    );
    diagnostics.late_owned_options = listboxOptionLabels(lateOwnedSurface?.options ?? []);
    const lateOwnedSelection = lateOwnedSurface
      ? await trySelectFromComboboxSurface(page, elementId, value, lateOwnedSurface, allowsForgivingFallback, diagnostics, deps)
      : 'unavailable';
    if (lateOwnedSelection === 'selected') {
      return;
    }
    if (lateOwnedSelection === 'failed') {
      throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
    }

    throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
  }

  const ownedSurface = await resolveOwnedComboboxSurface(
    page,
    elementId,
    deps,
    6,
    80,
    { requireExpanded: false },
  );
  diagnostics.owned_options = listboxOptionLabels(ownedSurface?.options ?? []);
  if (!ownedSurface) {
    diagnostics.owned_lookup = await describeOwnedComboboxLookup(page, elementId, deps);
  }
  const ownedSelection = ownedSurface
    ? await trySelectFromComboboxSurface(page, elementId, value, ownedSurface, allowsForgivingFallback, diagnostics, deps)
    : 'unavailable';
  if (ownedSelection === 'selected') {
    return;
  }
  if (ownedSelection === 'failed') {
    throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
  }

  const initialSurface = await resolveComboboxSurface(page, elementId, deps, 8, 80);
  diagnostics.initial_options = listboxOptionLabels(initialSurface?.options ?? []);
  const initialSelection = initialSurface
    ? await trySelectFromComboboxSurface(page, elementId, value, initialSurface, allowsForgivingFallback, diagnostics, deps)
    : 'unavailable';
  if (initialSelection === 'selected') {
    return;
  }
  if (initialSelection === 'failed') {
    throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
  }

  await combobox.fill(value).catch(async () => {
    await deps.safeClick(page, combobox, elementId);
    await deps.maybeType(page, value);
  });
  await deps.maybeWaitForTimeout(page, 250);

  const typedSurface = await resolveComboboxSurface(page, elementId, deps, 8, 120);
  diagnostics.typed_options = listboxOptionLabels(typedSurface?.options ?? []);
  const typedSelection = typedSurface
    ? await trySelectFromComboboxSurface(page, elementId, value, typedSurface, allowsForgivingFallback, diagnostics, deps)
    : 'unavailable';
  if (typedSelection === 'selected') {
    return;
  }
  if (typedSelection === 'failed') {
    throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
  }

  await deps.maybePressKey(page, 'Enter');
  await deps.maybeWaitForTimeout(page, 150);
  const resolved = await readComboboxDisplayValue(page, elementId, deps);
  diagnostics.resolved_display_value = resolved;
  if (resolved && (allowsForgivingFallback || valueMatchesSelection(resolved, value))) {
    return;
  }
  throw deps.createError(`No combobox option matching "${value}"`, diagnostics);
}

async function describeComboboxControl(target: Locator): Promise<ComboboxControlInfo> {
  return target.evaluate((node) => {
    const el = node as HTMLElement;
    const tagName = el.tagName?.toLowerCase() ?? '';
    const role = (el.getAttribute('role') ?? '').toLowerCase();
    return {
      textEntryCapable: tagName === 'input'
        || tagName === 'textarea'
        || role === 'combobox'
        || el.isContentEditable,
      tagName,
      role,
    };
  }).catch(() => ({
    textEntryCapable: false,
    tagName: '',
    role: '',
  }));
}

async function tryClickOptionViaLocator(
  page: Page,
  value: string,
  allowsForgivingFallback: boolean,
  deps: SelectControlDeps,
): Promise<'selected' | 'unavailable' | 'failed'> {
  await deps.maybeWaitForTimeout(page, 300);

  const allOptions = page.locator('[role="listbox"] [role="option"], [role="listbox"] li');
  if (typeof allOptions.nth !== 'function') {
    return 'unavailable';
  }
  const count = await allOptions.count().catch(() => 0);
  if (!count) return 'unavailable';

  const targetVariants = optionMatchVariants(value);
  const optionTexts: string[] = [];
  for (let i = 0; i < count; i++) {
    const text = await allOptions.nth(i).textContent().catch(() => '');
    optionTexts.push(text ?? '');
  }

  for (let i = 0; i < count; i++) {
    const text = optionTexts[i] ?? '';
    const optVariants = optionMatchVariants(text);
    if (hasVariantIntersection(targetVariants, optVariants)) {
      const clicked = await allOptions.nth(i).click({ timeout: 2000 }).then(() => true).catch(() => false);
      return clicked ? 'selected' : 'failed';
    }
  }

  const targetNorm = normalizeOptionText(value);
  for (let i = 0; i < count; i++) {
    const text = optionTexts[i] ?? '';
    const optNorm = normalizeOptionText(text);
    if (optNorm.includes(targetNorm) || targetNorm.includes(optNorm)) {
      const clicked = await allOptions.nth(i).click({ timeout: 2000 }).then(() => true).catch(() => false);
      return clicked ? 'selected' : 'failed';
    }
  }

  if (allowsForgivingFallback) {
    for (let i = 0; i < count; i++) {
      const text = optionTexts[i] ?? '';
      if (!isPlaceholderOption(text)) {
        const clicked = await allOptions.nth(i).click({ timeout: 2000 }).then(() => true).catch(() => false);
        return clicked ? 'selected' : 'failed';
      }
    }
  }

  return 'failed';
}

async function resolveComboboxSurface(
  page: Page,
  elementId: string,
  deps: SelectControlDeps,
  attempts = 6,
  waitMs = 80,
): Promise<ComboboxSurface | null> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const surface = await evaluateInPage<ComboboxSurface | null, { targetSelector: string }>(
      page,
      ({ targetSelector }) => {
        function __name<T>(value: T): T {
          return value;
        }
        void __name;

        let target = document.querySelector(targetSelector);
        if (!(target instanceof HTMLElement)) {
          target = document.querySelector(
            'button[aria-haspopup="listbox"][aria-expanded="true"], [role="combobox"][aria-expanded="true"], [role="button"][aria-haspopup="listbox"][aria-expanded="true"]',
          );
          if (!(target instanceof HTMLElement)) return null;
        }

        const cleanText = (value: string | null | undefined): string =>
          (value ?? '').replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').replace(/\s+/g, ' ').trim();
        const idSelector = (value: string): string =>
          `[id="${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`;
        const controlsListbox = (controller: Element, listbox: HTMLElement): boolean => {
          if (!listbox.id) return false;
          return ['aria-controls', 'aria-owns'].some((attribute) => (
            (controller.getAttribute(attribute) ?? '').split(/\s+/).includes(listbox.id)
          ));
        };
        const controllerSelectorFor = (listbox: HTMLElement): string | undefined => {
          const controllers = Array.from(document.querySelectorAll('[aria-controls], [aria-owns]'))
            .filter((node) => node instanceof window.HTMLElement && controlsListbox(node, listbox)) as HTMLElement[];
          const preferred = controllers.find((node) => (
            node.matches('button, [role="button"], [aria-haspopup="listbox"]')
          )) ?? controllers.find((node) => node !== target) ?? controllers[0];
          return preferred?.id ? idSelector(preferred.id) : undefined;
        };

        const isVisible = (node: Element): boolean => {
          if (!(node instanceof window.HTMLElement)) return false;
          if (node.hidden) return false;
          const style = window.getComputedStyle(node);
          return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
        };

        const listboxes = Array.from(document.querySelectorAll('[role="listbox"]'))
          .filter(isVisible) as HTMLElement[];
        if (!listboxes.length) return null;

        const ownedId = target.getAttribute('aria-owns') || target.getAttribute('aria-controls')
          || (target.querySelector('[aria-controls]') as HTMLElement | null)?.getAttribute('aria-controls')
          || (target.querySelector('[aria-owns]') as HTMLElement | null)?.getAttribute('aria-owns') || '';
        const owned = ownedId ? document.getElementById(ownedId) : null;
        let chosen = owned instanceof HTMLElement && isVisible(owned) ? owned : null;
        if (!chosen) {
          if (listboxes.length === 1) {
            chosen = listboxes[0] ?? null;
          } else {
            const targetRect = target.getBoundingClientRect();
            const targetCenterX = targetRect.left + targetRect.width / 2;
            const targetCenterY = targetRect.top + targetRect.height / 2;
            chosen = listboxes
              .map((listbox) => {
                const surface = (listbox.closest('[data-popper-placement]') as HTMLElement | null) ?? listbox;
                const rect = surface.getBoundingClientRect();
                const dx = (rect.left + rect.width / 2) - targetCenterX;
                const dy = (rect.top + rect.height / 2) - targetCenterY;
                return { listbox, distance: Math.sqrt(dx * dx + dy * dy) };
              })
              .sort((left, right) => left.distance - right.distance)[0]?.listbox ?? null;
          }
        }
        if (!chosen) return null;

        document.querySelectorAll('[data-envoy-active-listbox="true"]').forEach((node) => {
          node.removeAttribute('data-envoy-active-listbox');
        });
        chosen.setAttribute('data-envoy-active-listbox', 'true');
        const options = Array.from(chosen.querySelectorAll('[role="option"], li, [data-value]')).map((option, index) => {
          const el = option as HTMLElement;
          return {
            text: cleanText(el.textContent),
            value: cleanText(el.getAttribute('data-value')),
            disabled: el.getAttribute('aria-disabled') === 'true',
            index,
          };
        });
        return {
          selector: chosen.id ? idSelector(chosen.id) : '[data-envoy-active-listbox="true"]',
          triggerSelector: controllerSelectorFor(chosen),
          options,
        };
      },
      { targetSelector: deps.elementIdSelector(elementId) },
    ).catch(() => null);

    if (surface && surface.options.length) {
      return surface;
    }
    if (attempt < attempts - 1) {
      await deps.maybeWaitForTimeout(page, waitMs);
    }
  }
  return null;
}

async function resolveOwnedComboboxSurface(
  page: Page,
  elementId: string,
  deps: SelectControlDeps,
  attempts = 12,
  waitMs = 100,
  options?: { requireExpanded?: boolean },
): Promise<ComboboxSurface | null> {
  const requireExpanded = options?.requireExpanded ?? true;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const surface = await evaluateInPage<ComboboxSurface | null, { targetSelector: string; ownedOnly: boolean; requireExpanded: boolean }>(
      page,
      ({ targetSelector, ownedOnly, requireExpanded: mustBeExpanded }) => {
        function __name<T>(value: T): T {
          return value;
        }
        void __name;

        void ownedOnly;
        let target = document.querySelector(targetSelector);
        if (!(target instanceof window.HTMLElement)) {
          target = document.querySelector(
            'button[aria-haspopup="listbox"][aria-expanded="true"], [role="combobox"][aria-expanded="true"], [role="button"][aria-haspopup="listbox"][aria-expanded="true"]',
          );
          if (!(target instanceof window.HTMLElement)) return null;
        }

        const trigger = (
          target.getAttribute('aria-controls') || target.getAttribute('aria-owns')
            ? target
            : (target.querySelector('[aria-controls], [aria-owns]') as HTMLElement | null) ?? target
        ) as HTMLElement;
        if (mustBeExpanded) {
          const expanded =
            (trigger.getAttribute('aria-expanded') ?? '').toLowerCase() === 'true' ||
            target.querySelector('[aria-expanded="true"]') !== null;
          if (!expanded) return null;
        }

        const cleanText = (value: string | null | undefined): string =>
          (value ?? '').replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').replace(/\s+/g, ' ').trim();
        const idSelector = (value: string): string =>
          `[id="${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`;
        const controlsListbox = (controller: Element, listbox: HTMLElement): boolean => {
          if (!listbox.id) return false;
          return ['aria-controls', 'aria-owns'].some((attribute) => (
            (controller.getAttribute(attribute) ?? '').split(/\s+/).includes(listbox.id)
          ));
        };
        const controllerSelectorFor = (listbox: HTMLElement): string | undefined => {
          const controllers = Array.from(document.querySelectorAll('[aria-controls], [aria-owns]'))
            .filter((node) => node instanceof window.HTMLElement && controlsListbox(node, listbox)) as HTMLElement[];
          const preferred = controllers.find((node) => (
            node.matches('button, [role="button"], [aria-haspopup="listbox"]')
          )) ?? controllers.find((node) => node !== target) ?? controllers[0];
          return preferred?.id ? idSelector(preferred.id) : undefined;
        };

        const optionDataFor = (listbox: HTMLElement) =>
          Array.from(listbox.querySelectorAll('[role="option"], li, [data-value]')).map((option, index) => {
            const el = option as HTMLElement;
            return {
              text: cleanText(el.textContent),
              value: cleanText(el.getAttribute('data-value')),
              disabled: el.getAttribute('aria-disabled') === 'true',
              index,
            };
          });
        const nearestContainer = (el: Element): Element =>
          el.closest('fieldset, [class*="question"], [class*="field"], [class*="form-group"], [class*="control"], [class*="input"], form, section, div')
          ?? el;
        const addCandidate = (candidates: HTMLElement[], candidate: Element | null | undefined): void => {
          if (!(candidate instanceof window.HTMLElement)) return;
          if (!candidate.matches('[role="listbox"], ul, ol, [data-value]')) return;
          if (!candidates.includes(candidate)) candidates.push(candidate);
        };
        const addByIds = (candidates: HTMLElement[], ids: string | null): void => {
          (ids ?? '')
            .split(/\s+/)
            .map((id) => id.trim())
            .filter(Boolean)
            .forEach((id) => addCandidate(candidates, document.getElementById(id)));
        };

        const candidates: HTMLElement[] = [];
        addByIds(candidates, trigger.getAttribute('aria-controls'));
        addByIds(candidates, trigger.getAttribute('aria-owns'));
        addByIds(candidates, target.getAttribute('aria-controls'));
        addByIds(candidates, target.getAttribute('aria-owns'));
        nearestContainer(target).querySelectorAll('[role="listbox"], ul[id], ol[id]').forEach((node) => {
          addCandidate(candidates, node);
        });
        let sibling = target.nextElementSibling;
        while (sibling) {
          addCandidate(candidates, sibling);
          sibling.querySelectorAll('[role="listbox"], ul[id], ol[id]').forEach((node) => {
            addCandidate(candidates, node);
          });
          sibling = sibling.nextElementSibling;
        }

        const owned = candidates.find((candidate) => optionDataFor(candidate).length > 0) ?? null;
        if (!owned) return null;

        const listboxOptions = optionDataFor(owned);
        if (!listboxOptions.length) {
          return null;
        }

        document.querySelectorAll('[data-envoy-active-listbox="true"]').forEach((node) => {
          node.removeAttribute('data-envoy-active-listbox');
        });
        owned.setAttribute('data-envoy-active-listbox', 'true');
        return {
          selector: owned.id ? idSelector(owned.id) : '[data-envoy-active-listbox="true"]',
          triggerSelector: controllerSelectorFor(owned),
          options: listboxOptions,
        };
      },
      {
        targetSelector: deps.elementIdSelector(elementId),
        ownedOnly: true,
        requireExpanded,
      },
    ).catch(() => null);

    if (surface && surface.options.length) {
      return surface;
    }
    if (attempt < attempts - 1) {
      await deps.maybeWaitForTimeout(page, waitMs);
    }
  }
  return null;
}

async function describeOwnedComboboxLookup(
  page: Page,
  elementId: string,
  deps: SelectControlDeps,
): Promise<Record<string, unknown> | null> {
  return evaluateInPage<Record<string, unknown>, { targetSelector: string }>(
    page,
    ({ targetSelector }) => {
      function __name<T>(value: T): T {
        return value;
      }
      void __name;

      const cleanText = (value: string | null | undefined): string =>
        (value ?? '').replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').replace(/\s+/g, ' ').trim();
      const target = document.querySelector(targetSelector);
      if (!(target instanceof window.HTMLElement)) {
        return {
          target_found: false,
          target_selector: targetSelector,
          data_envoy_ids: Array.from(document.querySelectorAll('[data-envoy-apply-id]'))
            .map((node) => (node as HTMLElement).getAttribute('data-envoy-apply-id'))
            .filter(Boolean),
        };
      }

      const optionDataFor = (listbox: HTMLElement) =>
        Array.from(listbox.querySelectorAll('[role="option"], li, [data-value]')).map((option) => {
          const el = option as HTMLElement;
          return {
            text: cleanText(el.textContent),
            value: cleanText(el.getAttribute('data-value')),
            disabled: el.getAttribute('aria-disabled') === 'true',
          };
        });
      const nearestContainer = (el: Element): Element =>
        el.closest('fieldset, [class*="question"], [class*="field"], [class*="form-group"], [class*="control"], [class*="input"], form, section, div')
        ?? el;
      const listboxSummary = (node: Element) => {
        const el = node as HTMLElement;
        return {
          id: el.id || null,
          role: el.getAttribute('role'),
          class_name: el.className || null,
          display: window.getComputedStyle(el).display,
          option_count: optionDataFor(el).length,
          options: optionDataFor(el).map((option) => option.text || option.value).filter(Boolean).slice(0, 8),
        };
      };
      const ids = [
        target.getAttribute('aria-controls'),
        target.getAttribute('aria-owns'),
        (target.parentElement?.querySelector('[aria-controls]') as HTMLElement | null)?.getAttribute('aria-controls'),
        (target.parentElement?.querySelector('[aria-owns]') as HTMLElement | null)?.getAttribute('aria-owns'),
      ].filter(Boolean);
      const controlled = ids.map((id) => document.getElementById(String(id))).filter((node): node is HTMLElement => node instanceof window.HTMLElement);
      const container = nearestContainer(target);
      const containerListboxes = Array.from(container.querySelectorAll('[role="listbox"], ul[id], ol[id]'));

      return {
        target_found: true,
        target_tag: target.tagName.toLowerCase(),
        target_id: target.id || null,
        target_role: target.getAttribute('role'),
        target_class: target.className || null,
        aria_controls: target.getAttribute('aria-controls'),
        aria_owns: target.getAttribute('aria-owns'),
        parent_id: target.parentElement?.id || null,
        parent_class: target.parentElement?.className || null,
        controlled: controlled.map(listboxSummary),
        container_tag: (container as HTMLElement).tagName?.toLowerCase?.() ?? null,
        container_id: (container as HTMLElement).id || null,
        container_class: (container as HTMLElement).className || null,
        container_listboxes: containerListboxes.map(listboxSummary),
      };
    },
    { targetSelector: deps.elementIdSelector(elementId) },
  ).catch((error) => ({
    error: error instanceof Error ? error.message : String(error),
  }));
}

async function trySelectFromComboboxSurface(
  page: Page,
  elementId: string,
  value: string,
  surface: ComboboxSurface,
  allowsForgivingFallback: boolean,
  diagnostics: Record<string, unknown>,
  deps: SelectControlDeps,
): Promise<'selected' | 'unavailable' | 'failed'> {
  const target = value.trim().toLowerCase();
  const exact = findMatchingListboxOption(surface.options, target);
  if (exact) {
    diagnostics.dropdown_opened = await openComboboxSurface(page, surface, deps);
    if (!await clickSpecificComboboxOption(page, surface.selector, exact)) {
      diagnostics.selected_option = exact.text || exact.value;
      return 'failed';
    }
    if (await verifyComboboxSelection(page, elementId, exact, allowsForgivingFallback, deps)) {
      return 'selected';
    }
    diagnostics.selected_option = exact.text || exact.value;
    diagnostics.resolved_display_value = await readComboboxDisplayValue(page, elementId, deps);
    throw deps.createError(`Combobox selection did not stick for "${value}"`, diagnostics);
  }

  if (allowsForgivingFallback) {
    const fallback = firstUsableListboxOption(surface.options);
    if (!fallback) {
      return 'unavailable';
    }
    diagnostics.dropdown_opened = await openComboboxSurface(page, surface, deps);
    if (!await clickSpecificComboboxOption(page, surface.selector, fallback)) {
      diagnostics.selected_option = fallback.text || fallback.value;
      return 'failed';
    }
    if (await verifyComboboxSelection(page, elementId, fallback, true, deps)) {
      return 'selected';
    }
    diagnostics.selected_option = fallback.text || fallback.value;
    diagnostics.resolved_display_value = await readComboboxDisplayValue(page, elementId, deps);
    throw deps.createError(`Combobox fallback selection did not stick for "${value}"`, diagnostics);
  }

  return 'unavailable';
}

function findMatchingListboxOption(options: ListboxOption[], target: string): ListboxOption | null {
  return (
    options.find((option) => optionMatchesTarget(option, target) && !option.disabled)
    || options.find((option) => optionLooselyMatchesTarget(option, target) && !option.disabled)
    || null
  );
}

async function openComboboxSurface(
  page: Page,
  surface: ComboboxSurface,
  deps: SelectControlDeps,
): Promise<boolean> {
  const opened = await evaluateInPage<boolean, { listboxSelector: string; triggerSelector: string | null }>(
    page,
    ({ listboxSelector, triggerSelector }) => {
      function __name<T>(value: T): T {
        return value;
      }
      void __name;

      const listbox = document.querySelector(listboxSelector);
      if (!(listbox instanceof window.HTMLElement)) return false;
      const isVisible = (node: HTMLElement): boolean => {
        if (node.hidden) return false;
        const style = window.getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
      };
      if (isVisible(listbox)) return true;

      const id = listbox.id;
      const controlsListbox = (controller: Element): boolean => {
        if (!id) return false;
        return ['aria-controls', 'aria-owns'].some((attribute) => (
          (controller.getAttribute(attribute) ?? '').split(/\s+/).includes(id)
        ));
      };
      const explicitTrigger = triggerSelector ? document.querySelector(triggerSelector) : null;
      const controllers = [
        explicitTrigger,
        ...Array.from(document.querySelectorAll('[aria-controls], [aria-owns]'))
          .filter((node) => node instanceof window.HTMLElement && controlsListbox(node)),
      ].filter((node): node is HTMLElement => node instanceof window.HTMLElement);
      const trigger = controllers.find((node) => (
        node.matches('button, [role="button"], [aria-haspopup="listbox"]')
      )) ?? controllers[0];
      if (!trigger) return false;

      trigger.click();
      return true;
    },
    {
      listboxSelector: surface.selector,
      triggerSelector: surface.triggerSelector ?? null,
    },
  ).catch(() => false);

  if (opened) {
    await deps.maybeWaitForTimeout(page, 120);
  }
  return opened;
}

async function clickSpecificComboboxOption(
  page: Page,
  listboxSelector: string,
  option: ListboxOption,
): Promise<boolean> {
  return evaluateInPage<boolean, { sel: string; wantText: string; wantValue: string; optionIndex?: number }>(
    page,
    ({ sel, wantText, wantValue, optionIndex }) => {
      function __name<T>(value: T): T {
        return value;
      }
      void __name;

      const listbox = document.querySelector(sel);
      if (!listbox) return false;
      const normalize = (value: string | null | undefined): string =>
        (value ?? '')
          .replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '')
          .toLowerCase()
          .replace(/[^\p{L}\p{N}+]+/gu, ' ')
          .replace(/\s+/g, ' ')
          .trim();
      const options = Array.from(listbox.querySelectorAll('[role="option"], li, [data-value]'));
      const optionMatches = (candidate: Element): boolean => {
        const el = candidate as HTMLElement;
        const text = el.textContent ?? '';
        const value = el.getAttribute('data-value') ?? '';
        const valuePrefix = value.split('||')[0] ?? value;
        const disabled = el.getAttribute('aria-disabled') === 'true';
        if (disabled) return false;
        const available = [text, value, valuePrefix].map(normalize).filter(Boolean);
        const wanted = [wantText, wantValue].map(normalize).filter(Boolean);
        return wanted.some((target) => available.includes(target));
      };
      const indexMatch = typeof optionIndex === 'number' ? options[optionIndex] : null;
      const match = indexMatch && optionMatches(indexMatch) ? indexMatch : options.find(optionMatches);
      if (!match) return false;

      const el = match as HTMLElement;
      const cleanText = (value: string | null | undefined): string =>
        (value ?? '').replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').replace(/\s+/g, ' ').trim();
      const optionText = cleanText(el.textContent);
      const optionValue = cleanText(el.getAttribute('data-value'));
      const listboxId = (listbox as HTMLElement).id;
      const ownsListbox = (controller: Element): boolean => {
        if (!listboxId) return false;
        return ['aria-controls', 'aria-owns'].some((attribute) => (
          (controller.getAttribute(attribute) ?? '').split(/\s+/).includes(listboxId)
        ));
      };
      const dispatch = (node: HTMLElement, type: string): void => {
        const eventInit = { bubbles: true, cancelable: true, view: window };
        const EventCtor = type.startsWith('pointer') && 'PointerEvent' in window
          ? window.PointerEvent
          : window.MouseEvent;
        node.dispatchEvent(new EventCtor(type, eventInit));
      };
      const dispatchInputChange = (node: HTMLElement): void => {
        node.dispatchEvent(new window.Event('input', { bubbles: true }));
        node.dispatchEvent(new window.Event('change', { bubbles: true }));
      };

      el.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
      ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((eventName) => {
        dispatch(el, eventName);
      });

      const controllers = Array.from(document.querySelectorAll('[aria-controls], [aria-owns]'))
        .filter((node) => node instanceof window.HTMLElement && ownsListbox(node)) as HTMLElement[];
      controllers.forEach((controller) => {
        if (controller instanceof window.HTMLInputElement || controller instanceof window.HTMLTextAreaElement) {
          controller.value = optionText;
          dispatchInputChange(controller);
        }
        if (el.id) {
          controller.setAttribute('aria-activedescendant', el.id);
        }
        controller.setAttribute('aria-expanded', 'false');
      });

      const container = (listbox as HTMLElement).closest('[class*="input"], [class*="field"], [class*="control"], [class*="form-group"], fieldset, div')
        ?? (listbox as HTMLElement).parentElement;
      const listboxPrefix = listboxId.replace(/-list$/, '');
      const hiddenInputs = Array.from(container?.querySelectorAll('input[type="hidden"]') ?? [])
        .filter((node) => node instanceof window.HTMLInputElement) as HTMLInputElement[];
      const hiddenTarget = hiddenInputs.find((input) => (
        input.classList.contains('dropdownvalue')
        || input.id === `${listboxPrefix}-postback`
        || input.name === listboxPrefix
      )) ?? (hiddenInputs.length === 1 ? hiddenInputs[0] : null);
      if (hiddenTarget) {
        hiddenTarget.value = optionValue || optionText;
        dispatchInputChange(hiddenTarget);
      }

      options.forEach((candidate) => {
        (candidate as HTMLElement).classList.remove('selected');
      });
      el.classList.add('selected');
      (listbox as HTMLElement).setAttribute('aria-expanded', 'false');
      return true;
    },
    {
      sel: listboxSelector,
      wantText: normalizeOptionText(option.text),
      wantValue: normalizeOptionText(option.value),
      optionIndex: option.index,
    },
  ).catch(() => false);
}

async function verifyComboboxSelection(
  page: Page,
  elementId: string,
  expectedOption: ListboxOption,
  forgiving: boolean,
  deps: SelectControlDeps,
): Promise<boolean> {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const display = await readComboboxDisplayValue(page, elementId, deps);
    if (display) {
      if (forgiving) {
        return !isPlaceholderOption(display);
      }
      if (valueMatchesSelection(display, expectedOption.text) || valueMatchesSelection(display, expectedOption.value)) {
        return true;
      }
    }
    if (attempt < 3) {
      await deps.maybeWaitForTimeout(page, 90);
    }
  }
  return false;
}

async function readComboboxDisplayValue(
  page: Page,
  elementId: string,
  deps: SelectControlDeps,
): Promise<string> {
  return evaluateInPage<string, { selector: string }>(
    page,
    ({ selector }) => {
      function __name<T>(value: T): T {
        return value;
      }
      void __name;

      const target = document.querySelector(selector);
      if (!(target instanceof HTMLElement)) return '';

      const cleanText = (value: string | null | undefined): string =>
        (value ?? '').replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').replace(/\s+/g, ' ').trim();

      const input = target as HTMLInputElement;
      const ownText = cleanText(target.textContent);
      const ownValue = cleanText(input.value);
      const container = target.closest('[data-automation-id="multiSelectContainer"], [data-uxi-widget-type="multiselect"], [class*="field"], [class*="control"], [class*="input"], form, section, div') ?? target.parentElement;
      const promptText = cleanText(
        container?.querySelector('[data-automation-id="promptOption"], [role="option"][aria-selected="true"]')?.textContent,
      );

      const candidates = [promptText, ownText, ownValue];
      return candidates.find((candidate) => candidate && !/^(select|select one|choose|choose one|please select|please choose)$/i.test(candidate)) ?? '';
    },
    { selector: deps.elementIdSelector(elementId) },
  ).catch(() => '');
}

function valueMatchesSelection(actual: string, expected: string): boolean {
  const actualVariants = optionMatchVariants(actual);
  const expectedVariants = optionMatchVariants(expected);
  if (!actualVariants.size || !expectedVariants.size) {
    return false;
  }
  if (hasVariantIntersection(actualVariants, expectedVariants)) {
    return true;
  }
  return [...actualVariants].some((actualVariant) => (
    [...expectedVariants].some((expectedVariant) => (
      actualVariant.includes(expectedVariant) || expectedVariant.includes(actualVariant)
    ))
  ));
}

function listboxOptionLabels(options: ListboxOption[]): string[] {
  return options
    .map((option) => option.text || option.value)
    .map((value) => value.trim())
    .filter(Boolean);
}

async function allowsForgivingComboboxFallback(
  page: Page,
  elementId: string,
  deps: SelectControlDeps,
): Promise<boolean> {
  return evaluateInPage<boolean, { selector: string }>(
    page,
    ({ selector }) => {
      function __name<T>(value: T): T {
        return value;
      }
      void __name;

      const element = document.querySelector(selector);
      if (!(element instanceof HTMLElement)) return false;
      const id = element.id;
      const explicitLabel = id ? document.querySelector(`label[for="${id}"]`) : null;
      const text = [
        explicitLabel?.textContent ?? '',
        element.getAttribute('aria-label') ?? '',
        element.getAttribute('name') ?? '',
        element.textContent ?? '',
      ].join(' ').toLowerCase();
      return /\b(how did you hear|heard about|source|salutation|honorific|title|phone device type|device type)\b/.test(text);
    },
    { selector: deps.elementIdSelector(elementId) },
  ).catch(() => false);
}

function firstUsableListboxOption(options: ListboxOption[]): ListboxOption | null {
  return options.find((option) => !option.disabled && !isPlaceholderOption(option.text)) ?? null;
}

function isPlaceholderOption(value: string): boolean {
  const normalized = normalizeOptionText(value);
  return normalized === ''
    || ['select', 'select one', 'choose', 'choose one', 'please select', 'please choose'].includes(normalized);
}

function normalizeOptionText(value: string): string {
  return value
    .replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}+]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function optionMatchesTarget(option: ListboxOption, target: string): boolean {
  const targetVariants = optionMatchVariants(target);
  if (!targetVariants.size) {
    return false;
  }
  return hasVariantIntersection(optionMatchVariants(option.text), targetVariants)
    || hasVariantIntersection(optionMatchVariants(option.value), targetVariants);
}

function optionLooselyMatchesTarget(option: ListboxOption, target: string): boolean {
  const targetVariants = optionMatchVariants(target);
  if (!targetVariants.size) {
    return false;
  }
  return [...targetVariants].some((targetVariant) => (
    [...optionMatchVariants(option.text), ...optionMatchVariants(option.value)].some((optionVariant) => (
      optionVariant.includes(targetVariant) || targetVariant.includes(optionVariant)
    ))
  ));
}

function optionMatchVariants(value: string): Set<string> {
  const normalized = normalizeOptionText(value);
  if (!normalized) {
    return new Set();
  }
  const variants = new Set([normalized]);
  for (const group of OPTION_ALIAS_GROUPS) {
    const normalizedGroup = group.map((entry) => normalizeOptionText(entry));
    if (!normalizedGroup.includes(normalized)) {
      continue;
    }
    normalizedGroup.forEach((entry) => variants.add(entry));
  }
  return variants;
}

function hasVariantIntersection(left: Set<string>, right: Set<string>): boolean {
  for (const candidate of left) {
    if (right.has(candidate)) {
      return true;
    }
  }
  return false;
}
