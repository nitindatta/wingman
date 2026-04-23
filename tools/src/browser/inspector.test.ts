import { describe, expect, it } from 'vitest';
import { markRequiredFromValidationText, type FieldInfo } from './inspector.js';

describe('markRequiredFromValidationText', () => {
  it('marks fields required from SEEK validation summary text', () => {
    const fields: FieldInfo[] = [
      {
        id: 'title',
        label: 'Title:',
        field_type: 'select',
        required: false,
        current_value: null,
        options: ['Select', 'Mr', 'Ms'],
        max_length: null,
      },
      {
        id: 'preferred-name',
        label: 'Preferred First Name:',
        field_type: 'text',
        required: false,
        current_value: null,
        options: null,
        max_length: null,
      },
    ];

    markRequiredFromValidationText(
      fields,
      'Before you can continue with the application, please address the following issues:\nTitle: - Please make a selection\nPreferred First Name: - Required field',
    );

    expect(fields.map((field) => field.required)).toEqual([true, true]);
  });
});
