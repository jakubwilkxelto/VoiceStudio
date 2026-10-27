import { chromium } from 'playwright';
import assert from 'node:assert/strict';

// Saving a designed voice confirms it: the form only clears its name field, so
// without the toast the save looked like nothing happened.
const BASE = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  const created = [];
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path === '/api/profiles' && request.method() === 'POST') {
        const profile = {
          id: 'designed',
          name: 'Release voice',
          kind: 'design',
          language: 'English',
        };
        created.push(profile);
        return route.fulfill({ json: profile });
      }
      const fixtures = {
        '/api/health': { status: 'ok' },
        '/api/setup/status': { ready: true },
        '/api/models/install/status': { jobs: [] },
        '/api/profiles': created,
        '/api/history': [],
        '/api/projects': [],
        '/api/export/history': [],
        '/api/longform/jobs': { jobs: [] },
      };
      return path in fixtures
        ? route.fulfill({ json: fixtures[path] })
        : route.fulfill({ status: 404, json: { detail: 'Not mocked' } });
    },
  );
  await page.goto(`${BASE}/#/design`);
  await page.getByRole('textbox', { name: 'Profile name' }).fill('Release voice');
  await page.getByRole('button', { name: 'Save as voice profile' }).click();
  await page.locator('[data-sonner-toast]', { hasText: 'Voice profile saved.' }).waitFor();
  assert.equal(created.length, 1);
  assert.equal(await page.getByRole('textbox', { name: 'Profile name' }).inputValue(), '');
  console.log('design save profile smoke passed');
} finally {
  await browser.close();
}
