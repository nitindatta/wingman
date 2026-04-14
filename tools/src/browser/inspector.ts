/**
 * Inspects the current apply step in the browser and returns structured field data.
 * Called by inspect_apply_step and fill_and_continue routes.
 */

import type { Page } from 'playwright-core';

export type FieldInfo = {
  id: string;
  label: string;
  field_type: 'text' | 'email' | 'phone' | 'textarea' | 'select' | 'radio' | 'checkbox' | 'file' | 'unknown';
  required: boolean;
  current_value: string | null;
  options: string[] | null;
  max_length: number | null;
};

export type StepInfo = {
  page_url: string;
  page_type: 'form' | 'confirmation' | 'external_redirect' | 'unknown';
  step_index: number | null;
  total_steps_estimate: number | null;
  is_external_portal: boolean;
  portal_type: string | null;
  fields: FieldInfo[];
  visible_actions: string[];
};

export type InspectResult =
  | { ok: true; step: StepInfo }
  | { ok: false; reason: string };

export async function inspectStep(page: Page): Promise<InspectResult> {
  const url = page.url();
  // External if we're off seek.com.au entirely, or still on SEEK's own /apply/external redirect stub
  const is_external_portal =
    !url.includes('seek.com.au') ||
    (url.includes('seek.com.au') && url.includes('/apply/external'));
  const portal_type = is_external_portal ? detectPortalType(url) : null;

  // Wait for the page to settle on external portals (they often render async)
  if (is_external_portal) {
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
  }

  // Detect confirmation page (works on any domain)
  const pageText = await page.evaluate(() => document.body.innerText).catch(() => '');
  if (
    /application (submitted|received|successful|complete)/i.test(pageText) ||
    /thank you for applying/i.test(pageText) ||
    /your application has been (submitted|received)/i.test(pageText)
  ) {
    return {
      ok: true,
      step: {
        page_url: url,
        page_type: 'confirmation',
        step_index: null,
        total_steps_estimate: null,
        is_external_portal,
        portal_type,
        fields: [],
        visible_actions: [],
      },
    };
  }

  // Extract step progress (e.g. "Step 2 of 4")
  let step_index: number | null = null;
  let total_steps_estimate: number | null = null;
  const stepText = await page
    .locator('[data-automation="progress-indicator"], [data-testid="progress"]')
    .first()
    .textContent()
    .catch(() => null);
  if (stepText) {
    const m = stepText.match(/(\d+)\s*(?:of|\/)\s*(\d+)/i);
    if (m && m[1] && m[2]) { step_index = parseInt(m[1]); total_steps_estimate = parseInt(m[2]); }
  }
  // Fallback: look in page text
  if (!step_index) {
    const m = pageText.match(/step\s+(\d+)\s+of\s+(\d+)/i);
    if (m && m[1] && m[2]) { step_index = parseInt(m[1]); total_steps_estimate = parseInt(m[2]); }
  }

  // Extract form fields (generic — works on any site using standard HTML inputs)
  const fields = await extractFields(page);

  // Extract visible action buttons
  const _noisePatterns = /^(open app|get app|download|sign in|log in|back|cancel|close|dismiss)$/i;
  const actionButtons = await page
    .locator('button[type="submit"], button[type="button"], input[type="submit"]')
    .all();
  const visible_actions: string[] = [];
  for (const btn of actionButtons) {
    const raw = (await btn.textContent())?.trim() ?? '';
    const text = raw.replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').trim();
    if (text && !visible_actions.includes(text) && !_noisePatterns.test(text))
      visible_actions.push(text);
  }

  // External portal with no extractable fields — the ATS uses non-standard rendering
  // (e.g. Workday shadow DOM). Fall back to manual.
  if (is_external_portal && fields.length === 0 && visible_actions.length === 0) {
    return {
      ok: true,
      step: {
        page_url: url,
        page_type: 'external_redirect',
        step_index: null,
        total_steps_estimate: null,
        is_external_portal: true,
        portal_type,
        fields: [],
        visible_actions: [],
      },
    };
  }

  return {
    ok: true,
    step: {
      page_url: url,
      page_type: 'form',
      step_index,
      total_steps_estimate,
      is_external_portal,
      portal_type,
      fields,
      visible_actions,
    },
  };
}

async function extractFields(page: Page): Promise<FieldInfo[]> {
  return page.evaluate(() => {
    const fields: Array<{
      id: string; label: string; field_type: string; required: boolean;
      current_value: string | null; options: string[] | null; max_length: number | null;
    }> = [];

    const inputs = document.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select',
    );

    let synthIndex = 0;
    for (const el of inputs) {
      const input = el as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

      // Find label first — we use it for stable synthetic IDs
      let label = '';
      const labelEl =
        document.querySelector(`label[for="${input.id}"]`) ??
        input.closest('label') ??
        input.closest('[class*="field"], [class*="form-group"], [class*="question"]')
          ?.querySelector('label, [class*="label"]');
      if (labelEl) label = labelEl.textContent?.trim() ?? '';
      if (!label && input.getAttribute('placeholder')) label = input.getAttribute('placeholder')!;
      if (!label && input.getAttribute('aria-label')) label = input.getAttribute('aria-label')!;

      // Stable ID: prefer native id/name, then data attrs, then label-based, then positional
      // Label-based IDs are prefixed __lbl_ so the filler knows to use getByLabel()
      const rawLabel = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
      const id = input.id || input.name
        || input.getAttribute('data-testid')
        || input.getAttribute('data-automation')
        || (rawLabel ? `__lbl_${rawLabel}__` : `__synth_${synthIndex++}__`);

      if (!label) label = id;

      const tagName = input.tagName.toLowerCase();
      let field_type: string;
      if (tagName === 'textarea') field_type = 'textarea';
      else if (tagName === 'select') field_type = 'select';
      else field_type = (input as HTMLInputElement).type || 'text';

      let current_value: string | null = null;
      let options: string[] | null = null;

      if (tagName === 'select') {
        current_value = (input as HTMLSelectElement).value || null;
        options = Array.from((input as HTMLSelectElement).options).map((o) => o.text.trim());
      } else if (field_type === 'radio') {
        // For radio buttons: use the group name as the ID so the whole group is one field.
        const groupName = (input as HTMLInputElement).name || id;

        // Question label comes from the group container, NOT the individual radio's label.
        // Individual labels are the option text (Yes/No). Look for:
        // 1. <legend> ancestor (standard fieldset pattern)
        // 2. A heading/label in the nearest [class*="question"/"field"/"group"] ancestor
        //    that is NOT itself a radio option label.
        let groupLabel = '';
        const fieldset = input.closest('fieldset');
        if (fieldset) {
          groupLabel = fieldset.querySelector('legend')?.textContent?.trim() ?? '';
        }
        if (!groupLabel) {
          const container = input.closest('[class*="question"],[class*="field"],[class*="form-group"]');
          if (container) {
            // Find first heading-like element that isn't a label for a radio button
            const heading = container.querySelector('legend, h1, h2, h3, h4, h5, [class*="label"]:not(label), [class*="title"], [class*="heading"]');
            groupLabel = heading?.textContent?.trim() ?? '';
            // Fallback: first <label> whose for= attribute doesn't match any radio in this group
            if (!groupLabel) {
              const firstLabel = container.querySelector('label');
              const firstLabelFor = firstLabel?.getAttribute('for') ?? '';
              const isOptionLabel = !!container.querySelector(`input[type="radio"][id="${firstLabelFor}"]`);
              if (!isOptionLabel) groupLabel = firstLabel?.textContent?.trim() ?? '';
            }
          }
        }
        // Last resort: use the individual input's label (old behaviour — will show "Yes"/"No")
        if (!groupLabel) groupLabel = label;

        // Collect all option labels in this group
        const groupOptions = Array.from(document.querySelectorAll<HTMLInputElement>(`input[name="${groupName}"]`))
          .map((r) => {
            const lbl = document.querySelector(`label[for="${r.id}"]`) ??
              r.closest('label') ??
              r.closest('[class*="field"],[class*="question"]')?.querySelector('label,[class*="label"]');
            return lbl?.textContent?.trim() ?? r.value ?? '';
          }).filter(Boolean);
        const checkedEl = document.querySelector<HTMLInputElement>(`input[name="${groupName}"]:checked`);
        const checkedLabel = checkedEl
          ? (document.querySelector(`label[for="${checkedEl.id}"]`)?.textContent?.trim() ?? checkedEl.value)
          : null;
        fields.push({
          id: groupName,
          label: groupLabel,
          field_type: 'radio',
          required: input.required,
          current_value: checkedLabel,
          options: groupOptions,
          max_length: null,
        });
        continue; // skip the generic push below
      } else {
        current_value = (input as HTMLInputElement).value || null;
      }

      const max_length = (input as HTMLInputElement).maxLength > 0
        ? (input as HTMLInputElement).maxLength
        : null;

      fields.push({
        id, label, field_type, required: input.required,
        current_value, options, max_length,
      });
    }

    // Dedupe by id, keep first occurrence
    const seen = new Set<string>();
    return fields.filter((f) => {
      if (seen.has(f.id)) return false;
      seen.add(f.id);
      return true;
    });
  }) as unknown as FieldInfo[];
}

function detectPortalType(url: string): string {
  if (url.includes('workday.com')) return 'workday';
  if (url.includes('greenhouse.io')) return 'greenhouse';
  if (url.includes('lever.co')) return 'lever';
  if (url.includes('icims.com')) return 'icims';
  return 'unknown';
}
