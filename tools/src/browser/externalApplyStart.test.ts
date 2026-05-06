import { describe, expect, it } from 'vitest';
import { startGenericExternalApply, type GenericExternalStartPage } from './externalApplyStart.js';

describe('startGenericExternalApply', () => {
  it('opens unsupported provider jobs as external apply pages', async () => {
    const calls: string[] = [];
    const page: GenericExternalStartPage = {
      async goto(url) {
        calls.push(`goto:${url}`);
      },
      async waitForLoadState(state) {
        calls.push(`wait:${state}`);
      },
      async waitForTimeout(timeoutMs) {
        calls.push(`timeout:${timeoutMs}`);
      },
      url() {
        return 'https://jobs.example/view/123';
      },
    };

    const result = await startGenericExternalApply(
      page,
      'indeed',
      'https://jobs.example/view/123',
    );

    expect(calls).toEqual([
      'goto:https://jobs.example/view/123',
      'wait:networkidle',
      'timeout:1500',
    ]);
    expect(result).toEqual({
      apply_url: 'https://jobs.example/view/123',
      is_external_portal: true,
      portal_type: 'indeed',
    });
  });

  it('continues when networkidle waiting times out', async () => {
    const page: GenericExternalStartPage = {
      async goto() {},
      async waitForLoadState() {
        throw new Error('networkidle timeout');
      },
      async waitForTimeout() {},
      url() {
        return 'https://jobs.example/apply';
      },
    };

    await expect(
      startGenericExternalApply(page, 'indeed', 'https://jobs.example/apply'),
    ).resolves.toMatchObject({
      apply_url: 'https://jobs.example/apply',
      is_external_portal: true,
      portal_type: 'indeed',
    });
  });
});
