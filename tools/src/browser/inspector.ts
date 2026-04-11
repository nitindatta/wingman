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

  // Detect external redirect
  const is_external_portal = !url.includes('seek.com.au');
  if (is_external_portal) {
    return {
      ok: true,
      step: {
        page_url: url,
        page_type: 'external_redirect',
        step_index: null,
        total_steps_estimate: null,
        is_external_portal: true,
        portal_type: detectPortalType(url),
        fields: [],
        visible_actions: [],
      },
    };
  }

  // Detect confirmation page
  const pageText = await page.evaluate(() => document.body.innerText).catch(() => '');
  if (
    /application (submitted|received|successful)/i.test(pageText) ||
    /thank you for applying/i.test(pageText)
  ) {
    return {
      ok: true,
      step: {
        page_url: url,
        page_type: 'confirmation',
        step_index: null,
        total_steps_estimate: null,
        is_external_portal: false,
        portal_type: null,
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

  // Extract form fields
  const fields = await extractFields(page);

  // Extract visible action buttons
  const actionButtons = await page
    .locator('button[type="submit"], button[type="button"], input[type="submit"]')
    .all();
  const visible_actions: string[] = [];
  for (const btn of actionButtons) {
    const raw = (await btn.textContent())?.trim() ?? '';
    // Strip zero-width characters injected by some UIs (U+2060 word joiner, etc.)
    const text = raw.replace(/[\u2060\u200b\u200c\u200d\uFEFF]/g, '').trim();
    if (text && !visible_actions.includes(text)) visible_actions.push(text);
  }

  return {
    ok: true,
    step: {
      page_url: url,
      page_type: 'form',
      step_index,
      total_steps_estimate,
      is_external_portal: false,
      portal_type: null,
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
        // The id variable above may be the React-generated per-element id; override with group name.
        const groupName = (input as HTMLInputElement).name || id;
        // Collect all labels in this group
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
        // Override id to group name so duplicates collapse in dedup step
        fields.push({
          id: groupName,
          label,
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
