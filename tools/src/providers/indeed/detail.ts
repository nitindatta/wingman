import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { error, ok, type ToolResponse } from '../../envelope.js';
import { getOrLaunchChrome as launchChrome } from '../../browser/chrome.js';
import { parseDetail } from './parseDetail.js';

const BASE_URL = 'https://au.indeed.com';

const DetailRequestSchema = z.object({
  job_id: z.string().min(1),
});

export function registerIndeedDetailRoute(app: FastifyInstance): void {
  app.post('/tools/providers/indeed/job', async (request) => {
    const parsed = DetailRequestSchema.safeParse(request.body);
    if (!parsed.success) {
      return error('bad_request', parsed.error.message) satisfies ToolResponse<never>;
    }

    const { job_id } = parsed.data;
    const url = `${BASE_URL}/viewjob?jk=${job_id}`;

    const context = await launchChrome();
    const page = await context.newPage();
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
      await page.waitForLoadState('networkidle', { timeout: 30_000 }).catch(() => {});
      await page.waitForTimeout(2_000);

      const html = await page.content();
      const result = parseDetail(html, job_id, url);

      if (!result.ok) {
        return { status: 'drift' as const, drift: result.reason };
      }

      return ok({ job: result.detail });
    } finally {
      await page.close();
    }
  });
}
