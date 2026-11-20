import { chromium } from 'playwright';

const browser = await chromium.launch({ channel: 'chromium', headless: true });
try {
  const page = await browser.newPage();
  let certificateFailure;
  try {
    await page.goto('https://localhost:8443', { waitUntil: 'domcontentloaded', timeout: 15_000 });
  } catch (error) {
    if (!/net::ERR_CERT_AUTHORITY_INVALID/.test(error.message)) throw error;
    certificateFailure = 'net::ERR_CERT_AUTHORITY_INVALID';
  }
  if (!certificateFailure) throw new Error('Fresh untrusted fixture certificate unexpectedly accepted');
  console.log(JSON.stringify({ origin: 'https://localhost:8443', certificateFailure, chromium: browser.version() }));
} finally {
  await browser.close();
}
