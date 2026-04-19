import { JSDOM } from 'jsdom';
import { z } from 'zod';

/**
 * Selectors for Indeed job detail page (au.indeed.com/viewjob?jk=...).
 * The description lives in #jobDescriptionText which is stable across redesigns.
 */
const SELECTORS = {
  title: '[data-testid="jobsearch-JobInfoHeader-title"], h1.jobsearch-JobInfoHeader-title',
  company: '[data-testid="inlineHeader-companyName"] a, [data-testid="inlineHeader-companyName"]',
  location: '[data-testid="inlineHeader-companyLocation"]',
  salary: '[id="salaryInfoAndJobType"] .salary-snippet, [data-testid="attribute_snippet_testid"]',
  description: '#jobDescriptionText, .jobsearch-jobDescriptionText',
} as const;

export type IndeedJobDetail = {
  provider_job_id: string;
  title: string;
  company: string;
  location: string | null;
  salary: string | null;
  work_type: string | null;
  listed_at: string | null;
  description: string;
  classification: string | null;
  url: string;
};

const indeedJobDetailSchema = z.object({
  provider_job_id: z.string().min(1),
  title: z.string().min(1),
  company: z.string(),
  description: z.string().min(10),
  url: z.string().url(),
});

export type ParseDetailResult =
  | { ok: true; detail: IndeedJobDetail }
  | { ok: false; reason: string };

export function parseDetail(html: string, jobId: string, url: string): ParseDetailResult {
  const dom = new JSDOM(html);
  const doc = dom.window.document;

  const title = doc.querySelector(SELECTORS.title)?.textContent?.trim() ?? '';
  if (!title) {
    return { ok: false, reason: `no title found for job ${jobId} — selector drift?` };
  }

  const company = doc.querySelector(SELECTORS.company)?.textContent?.trim() ?? '';
  const location = doc.querySelector(SELECTORS.location)?.textContent?.trim() || null;
  const salary = doc.querySelector(SELECTORS.salary)?.textContent?.trim() || null;

  const descEl = doc.querySelector(SELECTORS.description);
  const description = descEl?.textContent?.trim() ?? '';
  if (!description) {
    return { ok: false, reason: `no description found for job ${jobId} — selector drift?` };
  }

  const detail: IndeedJobDetail = {
    provider_job_id: jobId,
    title,
    company,
    location,
    salary,
    work_type: null,
    listed_at: null,
    description,
    classification: null,
    url,
  };

  const validation = indeedJobDetailSchema.safeParse(detail);
  if (!validation.success) {
    return { ok: false, reason: `schema guard failed: ${validation.error.message}` };
  }

  return { ok: true, detail };
}
