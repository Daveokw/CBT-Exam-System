const { chromium } = require('playwright');

const APP_HEADING = 'CBT System';
const FAILURE_SCREENSHOT = 'keep_alive_screenshot.png';
const WAKE_CONTROL_PATTERN = /wake(?: up)?|back up|restart(?: this)? app|run(?: this)? app/i;

function validatedTarget(rawValue) {
  if (!rawValue) throw new Error('Pass the public Streamlit app URL to the availability check.');
  const target = new URL(rawValue);
  if (target.protocol !== 'https:' || !/^[a-z0-9-]+\.streamlit\.app$/.test(target.hostname)) {
    throw new Error('The target must be an HTTPS Streamlit Community Cloud URL.');
  }
  if (target.username || target.password || target.port || target.search || target.hash) {
    throw new Error('The target URL must not contain credentials, a port, query or fragment.');
  }
  return target.toString();
}

async function applicationIsReady(page) {
  for (const frame of page.frames()) {
    const heading = frame.getByRole('heading', { name: APP_HEADING, exact: true }).first();
    const container = frame.locator('[data-testid="stAppViewContainer"]').first();
    if (await heading.isVisible().catch(() => false)
        && await container.isVisible().catch(() => false)) return true;
  }
  return false;
}

async function visibleWakeControl(page) {
  for (const frame of page.frames()) {
    for (const role of ['button', 'link']) {
      const control = frame.getByRole(role, { name: WAKE_CONTROL_PATTERN }).first();
      if (await control.isVisible().catch(() => false)) return control;
    }
  }
  return null;
}

async function observedFrames(page) {
  const observations = [];
  for (const frame of page.frames()) {
    const body = await frame.locator('body').innerText().catch(() => '');
    observations.push(`${frame.url()} :: ${body.replace(/\s+/g, ' ').trim().slice(0, 160) || '[no body text]'}`);
  }
  return observations.join(' | ');
}

async function waitForState(page, timeoutMs, acceptWakeControl) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await applicationIsReady(page)) return null;
    if (acceptWakeControl) {
      const control = await visibleWakeControl(page);
      if (control) return control;
    }
    await page.waitForTimeout(1000);
  }
  throw new Error(`The CBT interface did not become ready. Observed frames: ${await observedFrames(page)}`);
}

async function openApp(page, target) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    let failure;
    let response;
    try {
      response = await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 30000 });
    } catch (error) {
      failure = error;
    }
    if (!failure) {
      if (!response || response.status() < 400) return;
      failure = new Error(`The app returned HTTP ${response.status()}.`);
      if (![403, 429, 502, 503, 504].includes(response.status())) throw failure;
    }
    if (attempt === 3) throw failure;
    console.log(`${failure.message} Retrying in 10 seconds (${attempt}/3).`);
    await page.waitForTimeout(10000);
  }
}

async function run() {
  let browser;
  let page;
  try {
    const target = validatedTarget(process.argv[2]);
    console.log('Starting the CBT System availability check.');
    browser = await chromium.launch({ headless: true });
    page = await browser.newPage();
    await openApp(page, target);
    const wakeControl = await waitForState(page, 120000, true);
    if (wakeControl) {
      console.log('The app is sleeping; requesting wake-up.');
      await wakeControl.click({ timeout: 10000 });
      await waitForState(page, 300000, false);
    }
    await page.waitForTimeout(15000);
    if (!(await applicationIsReady(page))) throw new Error('The interface became unavailable during verification.');
    console.log('The CBT interface is visible and responsive.');
  } catch (error) {
    console.error(`Availability check failed: ${error.message}`);
    process.exitCode = 1;
    if (page) {
      try {
        await page.screenshot({ path: FAILURE_SCREENSHOT, fullPage: true });
      } catch (screenshotError) {
        console.error(`Failure screenshot could not be saved: ${screenshotError.message}`);
      }
    }
  } finally {
    if (browser) await browser.close();
  }
}

if (require.main === module) run();
module.exports = { validatedTarget };
