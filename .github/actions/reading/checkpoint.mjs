import { saveCache } from '@actions/cache';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';

try {
  const prefix = process.env.WXREAD_CACHE_PREFIX;
  if (!process.env.ACTIONS_RUNTIME_TOKEN || !process.env.ACTIONS_RESULTS_URL || !prefix
      || !existsSync('.state/login.enc') || !existsSync('.state/cookies.enc')) throw new Error('missing runtime');
  const key = `${prefix}${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}-progress-${randomUUID()}`;
  const id = await saveCache(['.state/login.enc', '.state/cookies.enc', '.state/device.json'], key);
  if (!(id >= 0)) throw new Error('not saved');
} catch {
  // Never print SDK exception objects: they can contain authenticated URLs.
  process.exitCode = 1;
}
