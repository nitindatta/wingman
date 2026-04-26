import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import {
  collectExternalApplyObservation,
  evaluateExternalApplyObservation,
  normalizeInjectedNameHelpers,
} from './externalApplyObserver.js';

function withDom<T>(html: string, fn: () => T): T {
  const dom = new JSDOM(html, { url: 'https://ats.example/apply' });
  const previousWindow = (globalThis as { window?: Window }).window;
  const previousDocument = (globalThis as { document?: Document }).document;
  (globalThis as { window?: Window }).window = dom.window as unknown as Window;
  (globalThis as { document?: Document }).document = dom.window.document;
  try {
    return fn();
  } finally {
    if (previousWindow) (globalThis as { window?: Window }).window = previousWindow;
    else delete (globalThis as { window?: Window }).window;
    if (previousDocument) (globalThis as { document?: Document }).document = previousDocument;
    else delete (globalThis as { document?: Document }).document;
  }
}

describe('collectExternalApplyObservation', () => {
  it('extracts fields, uploads, buttons, links, and errors from a generic apply page', () => {
    const observation = withDom(
      `
      <html>
        <head><title>Apply now</title></head>
        <body>
          <form>
            <label for="name">Full name *</label>
            <input id="name" name="name" required value="Nitin Datta" />
            <label for="email">Email</label>
            <input id="email" type="email" />
            <label for="resume">Resume</label>
            <input id="resume" type="file" />
            <label for="country">Country</label>
            <select id="country"><option>Australia</option><option>New Zealand</option></select>
            <div role="alert">Email is required</div>
            <button type="submit">Continue</button>
            <a href="/privacy">Privacy policy</a>
          </form>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.url).toBe('https://ats.example/apply');
    expect(observation.title).toBe('Apply now');
    expect(observation.page_type).toBe('resume_upload');
    expect(observation.fields.map((field) => field.label)).toContain('Full name *');
    expect(observation.fields.find((field) => field.label === 'Full name *')?.required).toBe(true);
    expect(observation.uploads).toHaveLength(1);
    expect(observation.buttons[0]?.label).toBe('Continue');
    expect(observation.links[0]?.label).toBe('Privacy policy');
    expect(observation.errors).toContain('Email is required');
  });

  it('collapses radio groups into one observed field with options', () => {
    const observation = withDom(
      `
      <fieldset>
        <legend>Do you have working rights?</legend>
        <input id="rights_yes" type="radio" name="rights" value="yes" />
        <label for="rights_yes">Yes</label>
        <input id="rights_no" type="radio" name="rights" value="no" />
        <label for="rights_no">No</label>
      </fieldset>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.label).toBe('Do you have working rights?');
    expect(observation.fields[0]?.field_type).toBe('radio');
    expect(observation.fields[0]?.options).toEqual(['Yes', 'No']);
  });

  it('observes custom aria radio groups with labels and selected value', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <div role="radiogroup" aria-labelledby="worked_label" aria-required="true">
            <span id="worked_label">Have you previously worked at SVHA?</span>
            <div role="radio" aria-checked="false" aria-label="Yes"></div>
            <div role="radio" aria-checked="true" aria-label="No"></div>
          </div>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.label).toBe('Have you previously worked at SVHA?');
    expect(observation.fields[0]?.field_type).toBe('radio');
    expect(observation.fields[0]?.options).toEqual(['Yes', 'No']);
    expect(observation.fields[0]?.current_value).toBe('No');
    expect(observation.fields[0]?.required).toBe(true);
  });

  it('observes aria comboboxes as select fields', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <label id="device_label">Phone Device Type</label>
          <div
            role="combobox"
            aria-labelledby="device_label"
            aria-required="true"
            aria-controls="device_listbox"
            aria-expanded="true"
          >
            Mobile
          </div>
          <ul id="device_listbox" role="listbox">
            <li role="option">Mobile</li>
            <li role="option">Home</li>
            <li role="option">Work</li>
          </ul>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.label).toBe('Phone Device Type');
    expect(observation.fields[0]?.field_type).toBe('select');
    expect(observation.fields[0]?.options).toEqual(['Mobile', 'Home', 'Work']);
    expect(observation.fields[0]?.current_value).toBe('Mobile');
    expect(observation.fields[0]?.required).toBe(true);
  });

  it('filters accessibility skip and jump-menu actions from observed buttons and links', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <a href="" id="accessibilitySkipToMainContent" data-automation-id="accessibilitySkipToMainContent">
            Skip to main content
          </a>
          <button aria-label="Close jump menu">Close</button>
          <a href="/apply/start">Continue application</a>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.links.map((link) => link.label)).toEqual(['Continue application']);
    expect(observation.buttons).toHaveLength(0);
  });

  it('observes required custom role checkboxes as checkbox fields', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <div class="question">
            <div
              id="terms"
              role="checkbox"
              aria-required="true"
              aria-checked="false"
              aria-labelledby="terms_label"
            ></div>
            <span id="terms_label">I agree to the Terms and Conditions</span>
          </div>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.field_type).toBe('checkbox');
    expect(observation.fields[0]?.label).toBe('I agree to the Terms and Conditions');
    expect(observation.fields[0]?.required).toBe(true);
  });

  it('observes visually hidden native checkboxes when their label is visible', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <div class="question">
            <label for="input-9">I agree to the Terms and Conditions</label>
            <input
              id="input-9"
              type="checkbox"
              aria-checked="false"
              aria-invalid="true"
              style="opacity: 0"
            />
          </div>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.field_type).toBe('checkbox');
    expect(observation.fields[0]?.label).toBe('I agree to the Terms and Conditions');
  });

  it('keeps password pages as login instead of captcha when a human-verification hint is present', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <h1>Sign in</h1>
          <p>Complete captcha later if needed.</p>
          <label for="email">Email</label>
          <input id="email" type="email" />
          <label for="password">Password</label>
          <input id="password" type="password" />
          <button type="submit">Continue</button>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.page_type).toBe('login');
  });

  it('evaluates observer source that contains runtime-injected __name helper calls', () => {
    const observation = withDom(
      '<html><head><title>Apply</title></head><body><button>Continue</button></body></html>',
      () => evaluateExternalApplyObservation(`
        function collectExternalApplyObservation() {
          const read = __name27(() => ({
            url: window.location.href,
            title: document.title,
            page_type: 'unknown',
            visible_text: document.body.textContent || '',
            fields: [],
            buttons: [],
            links: [],
            uploads: [],
            errors: [],
            screenshot_ref: null,
          }), 'read');
          return read();
        }
      `),
    );

    expect(observation.url).toBe('https://ats.example/apply');
    expect(observation.title).toBe('Apply');
  });

  it('normalises any TS injected __name helper suffix', () => {
    expect(normalizeInjectedNameHelpers('__name(fn); __name9(fn); __name27(fn);')).toBe(
      '__envoyName(fn); __envoyName(fn); __envoyName(fn);',
    );
  });
});
