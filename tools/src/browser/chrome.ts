import { chromium, type BrowserContext } from 'playwright-core';
import { existsSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

/**
 * Singleton Chrome launcher.
 *
 * Uses launchPersistentContext with a dedicated profile directory so provider
 * login cookies (SEEK, LinkedIn, etc.) persist across sessions without
 * touching the user's personal Chrome profile.
 *
 * Profile dir is resolved from BROWSER_PROFILE_DIR env var, falling back to
 * ../automation/browser-profile relative to the tools/ working directory.
 */

const CHROME_CANDIDATES = [
  // Windows
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  // macOS
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  // Linux
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium-browser',
  '/usr/bin/chromium',
];

const DEFAULT_PROFILE_DIR = process.env.BROWSER_PROFILE_DIR
  ? path.resolve(process.env.BROWSER_PROFILE_DIR)
  : path.resolve(process.cwd(), '..', 'automation', 'browser-profile');

export type ChromeLaunchOptions = {
  profileDir?: string;
  headless?: boolean;
};

let _context: BrowserContext | null = null;
let _profileDir: string = DEFAULT_PROFILE_DIR;

/**
 * Returns the shared BrowserContext, launching Chrome if not already running.
 * Subsequent calls return the same context — Chrome stays open between sessions
 * so that login cookies persist without re-authentication.
 */
export async function getOrLaunchChrome(options: ChromeLaunchOptions = {}): Promise<BrowserContext> {
  const profileDir = options.profileDir ?? DEFAULT_PROFILE_DIR;
  _profileDir = profileDir;

  if (_context) {
    // Verify the context is still usable
    try {
      await _context.pages(); // throws if browser was closed externally
      return _context;
    } catch {
      _context = null;
    }
  }

  const executablePath = CHROME_CANDIDATES.find((p) => existsSync(p));
  if (!executablePath) {
    throw new Error(
      `Could not find Google Chrome. Checked:\n  ${CHROME_CANDIDATES.join('\n  ')}\n` +
        'Please install Google Chrome and try again.',
    );
  }

  await mkdir(profileDir, { recursive: true });

  _context = await chromium.launchPersistentContext(profileDir, {
    executablePath,
    headless: options.headless ?? false,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });

  _context.on('close', () => {
    _context = null;
  });

  return _context;
}

/** Returns the profile dir in use (for diagnostics). */
export function getProfileDir(): string {
  return _profileDir;
}
