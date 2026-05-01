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
    expect(observation.fields.find((field) => field.label === 'Full name *')?.control_kind).toBe('native_text');
    expect(observation.fields.find((field) => field.label === 'Country')?.control_kind).toBe('native_select');
    expect(observation.uploads).toHaveLength(1);
    expect(observation.uploads[0]?.control_kind).toBe('file_upload');
    expect(observation.buttons[0]?.label).toBe('Continue');
    expect(observation.links[0]?.label).toBe('Privacy policy');
    expect(observation.errors).toContain('Email is required');
  });

  it('observes hidden PageUp-style file inputs when their upload widgets are visible', () => {
    const observation = withDom(
      `
      <html>
        <head><title>Resume - SA Water</title></head>
        <body>
          <form>
            <section class="question">
              <p>Please attach your resume*</p>
              <a href="/existing">nitin_datta_resume.docx (30 kb)</a>
              <button type="button">Delete</button>
              <input id="resumeFile" type="file" style="display:none" />
            </section>
            <section class="question">
              <p>Please attach your cover letter</p>
              <button type="button">Upload file</button>
              <input id="coverFile" type="file" style="display:none" />
            </section>
            <section class="question">
              <p>Please attach any other relevant documentation (optional)</p>
              <button type="button">Upload file</button>
              <input id="otherFile" type="file" style="display:none" />
            </section>
            <button type="submit">Continue</button>
          </form>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.page_type).toBe('resume_upload');
    expect(observation.uploads.map((upload) => upload.label)).toEqual([
      'Please attach your resume*',
      'Please attach your cover letter',
      'Please attach any other relevant documentation (optional)',
    ]);
    expect(observation.uploads.map((upload) => upload.control_kind)).toEqual([
      'file_upload',
      'file_upload',
      'file_upload',
    ]);
    expect(observation.uploads.find((upload) => upload.label === 'Please attach your resume*')?.required).toBe(true);
    expect(observation.uploads.find((upload) => upload.label === 'Please attach your cover letter')?.required).toBe(false);
  });

  it('uses the enclosing PageUp question text for nested hidden upload inputs', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <form>
            <section class="question">
              <p>Please attach your resume*</p>
              <div class="upload-control">
                <button type="button">Upload file</button>
                <input id="resumeFile" type="file" style="display:none" />
              </div>
            </section>
            <section class="question">
              <p>Please attach your cover letter</p>
              <div class="upload-control">
                <button type="button">Upload file</button>
                <input id="coverFile" type="file" style="display:none" />
              </div>
            </section>
          </form>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.uploads.map((upload) => upload.label)).toEqual([
      'Please attach your resume*',
      'Please attach your cover letter',
    ]);
    expect(observation.uploads[0]?.nearby_text).toContain('Please attach your resume*');
    expect(observation.uploads[1]?.nearby_text).toContain('Please attach your cover letter');
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
    expect(observation.fields[0]?.control_kind).toBe('native_radio_group');
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
    expect(observation.fields[0]?.control_kind).toBe('aria_radio_group');
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
    expect(observation.fields[0]?.control_kind).toBe('aria_combobox');
    expect(observation.fields[0]?.options).toEqual(['Mobile', 'Home', 'Work']);
    expect(observation.fields[0]?.current_value).toBe('Mobile');
    expect(observation.fields[0]?.required).toBe(true);
  });

  it('extracts hidden PageUp-style combobox options from an owned listbox', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <div class="input-group cb pu-select">
            <label for="q9574">Do you have working rights in Australia? *</label>
            <input
              id="q9574"
              role="combobox"
              aria-controls="q9574-list"
              aria-owns="q9574-list"
              aria-required="true"
              data-envoy-apply-id="field_1"
              value=""
            />
            <button id="q9574-button" aria-controls="q9574-list">Open</button>
            <input type="hidden" id="q9574-postback" name="q9574" />
            <ul id="q9574-list" role="listbox" style="display:none">
              <li role="option" data-value="">Select</li>
              <li role="option" data-value="Yes - I am a permanent resident / citizen||28682|">
                Yes - I am a permanent resident / citizen
              </li>
              <li role="option" data-value="Yes - I have a current work permit / visa||28683|">
                Yes - I have a current work permit / visa
              </li>
              <li role="option" data-value="No - I require sponsorship||28684|">
                No - I require sponsorship
              </li>
            </ul>
          </div>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.element_id).toBe('field_1');
    expect(observation.fields[0]?.label).toBe('Do you have working rights in Australia? *');
    expect(observation.fields[0]?.field_type).toBe('select');
    expect(observation.fields[0]?.control_kind).toBe('aria_combobox');
    expect(observation.fields[0]?.required).toBe(true);
    expect(observation.fields[0]?.options).toEqual([
      'Select',
      'Yes - I am a permanent resident / citizen',
      'Yes - I have a current work permit / visa',
      'No - I require sponsorship',
    ]);
  });

  it('does not observe PageUp conditional inputs inside hidden containers', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <form>
            <div class="form-group" style="display: none" id="r_9563">
              <label id="lbl9563" for="q9563">
                Please enter your notice period <span class="asterisk">*</span>
              </label>
              <input
                type="text"
                name="q9563"
                id="q9563"
                value=""
                aria-required="true"
                data-envoy-apply-id="field_1"
              />
            </div>
            <div class="form-group" id="r_9566">
              <label for="q9566_no">I will be on leave for some time during the next 6 months:</label>
              <input id="q9566_no" type="checkbox" checked />
              <label for="q9566_no">No</label>
            </div>
            <button type="submit">Continue</button>
          </form>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields.map((field) => field.label)).not.toContain('Please enter your notice period *');
    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.label).toBe('I will be on leave for some time during the next 6 months:');
    expect(observation.buttons[0]?.label).toBe('Continue');
  });

  it('does not report combobox option transcripts as validation errors', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <div class="field error-container">
            <label for="q9574">Are you currently authorised to work in Australia?*</label>
            <input
              id="q9574"
              role="combobox"
              aria-controls="q9574-list"
              aria-owns="q9574-list"
              value="Yes - I am a permanent resident / citizen"
            />
            <span>
              Are you currently authorised to work in Australia?* Open list
              Selected: Yes - I am a permanent resident / citizen Select
              Yes - I am a permanent resident / citizen
              Yes - I have a current work permit / visa
              No - I require sponsorship
              SetupConditionalAttributeItems(9575, 28683, 1, 9574);
              aAttributeItems[9574] = aC
            </span>
            <ul id="q9574-list" role="listbox" style="display:none">
              <li role="option" data-value="">Select</li>
              <li role="option" data-value="Yes - I am a permanent resident / citizen||28682|">
                Yes - I am a permanent resident / citizen
              </li>
              <li role="option" data-value="Yes - I have a current work permit / visa||28683|">
                Yes - I have a current work permit / visa
              </li>
              <li role="option" data-value="No - I require sponsorship||28684|">
                No - I require sponsorship
              </li>
            </ul>
          </div>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields[0]?.current_value).toBe('Yes - I am a permanent resident / citizen');
    expect(observation.errors).toEqual([]);
  });

  it('observes button-based listbox controls as select fields', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <div data-automation-id="formField-source">
            <label for="source--source">How Did You Hear About Us?*</label>
            <button
              id="source--source"
              name="source"
              type="button"
              aria-haspopup="listbox"
              aria-controls="source-options"
              aria-label="How Did You Hear About Us? Select One Required"
            >
              Select One
            </button>
            <ul id="source-options" role="listbox">
              <li role="option" aria-disabled="true">Select One</li>
              <li role="option">Indeed</li>
              <li role="option">Seek</li>
            </ul>
          </div>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.label).toBe('How Did You Hear About Us?*');
    expect(observation.fields[0]?.field_type).toBe('select');
    expect(observation.fields[0]?.control_kind).toBe('button_listbox');
    expect(observation.fields[0]?.required).toBe(true);
    expect(observation.fields[0]?.current_value).toBeNull();
    expect(observation.fields[0]?.options).toEqual(['Select One', 'Indeed', 'Seek']);
    expect(observation.buttons).toHaveLength(0);
  });

  it('marks invalid fields even when they already have values', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <form>
            <label for="postcode">Postcode *</label>
            <input
              id="postcode"
              name="postcode"
              required
              value="abc"
              aria-invalid="true"
              aria-errormessage="postcode-error"
            />
            <div id="postcode-error">Enter a valid postcode.</div>
            <button disabled>Save and Continue</button>
          </form>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.current_value).toBe('abc');
    expect(observation.fields[0]?.invalid).toBe(true);
    expect(observation.fields[0]?.validation_message).toBe('Enter a valid postcode.');
    expect(observation.errors).toContain('Enter a valid postcode.');
    expect(observation.buttons[0]?.disabled).toBe(true);
  });

  it('observes selected-item prompt widgets as select fields with current value', () => {
    const observation = withDom(
      `
      <html>
        <body>
          <div data-automation-id="formField-countryPhoneCode">
            <label for="phoneNumber--countryPhoneCode">Country Phone Code*</label>
            <div data-automation-id="multiSelectContainer">
              <div data-automation-id="multiselectInputContainer">
                <input
                  id="phoneNumber--countryPhoneCode"
                  aria-required="true"
                  data-uxi-widget-type="selectinput"
                  value=""
                />
                <div id="phone-hint">1 item selected, Australia (+61)</div>
              </div>
              <ul role="listbox" data-automation-id="selectedItemList">
                <li role="presentation">
                  <div role="option" aria-selected="false" title="Australia (+61)">
                    <p data-automation-id="promptOption">Australia (+61)</p>
                  </div>
                </li>
              </ul>
            </div>
          </div>
        </body>
      </html>
      `,
      () => collectExternalApplyObservation(),
    );

    expect(observation.fields).toHaveLength(1);
    expect(observation.fields[0]?.label).toBe('Country Phone Code*');
    expect(observation.fields[0]?.field_type).toBe('select');
    expect(observation.fields[0]?.control_kind).toBe('prompt_select');
    expect(observation.fields[0]?.required).toBe(true);
    expect(observation.fields[0]?.current_value).toBe('Australia (+61)');
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
    expect(observation.fields[0]?.control_kind).toBe('aria_checkbox');
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
    expect(observation.fields[0]?.control_kind).toBe('native_checkbox');
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
