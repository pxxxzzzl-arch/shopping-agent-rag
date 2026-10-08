/* Record actual browser interactions with the local API; no prerecorded answers. */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { chromium } = require(process.env.SHOPPING_PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const base = process.argv[2] || 'http://127.0.0.1:8000';
  const out = path.resolve(process.argv[3] || 'docs/demo/media');
  const parsed = new URL(base);
  if (parsed.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(parsed.hostname))
    throw new Error('Demo recording only accepts a loopback HTTP service');
  const target = path.join(out, 'shopping-agent-demo.webm');
  if (fs.existsSync(target)) throw new Error('Refusing to overwrite an existing recording');
  fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({headless: true,
    ...(process.env.SHOPPING_CHROMIUM_EXECUTABLE ? {executablePath: process.env.SHOPPING_CHROMIUM_EXECUTABLE} : {})});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000},
    deviceScaleFactor: 1, locale: 'zh-CN',
    recordVideo: {dir: out, size: {width: 1440, height: 1000}}});
  const page = await context.newPage();
  const video = page.video();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const scenarios = [];
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  try {
    await page.goto(base, {waitUntil: 'networkidle'});
    await page.locator('#status').filter({hasText: '已连接'}).waitFor();
    if (!(await page.locator('#provider').innerText()).includes('hash'))
      throw new Error('Recording requires the offline hash demo; cloud evidence is shown separately');
    await page.screenshot({path: path.join(out, 'overview.png'), fullPage: true});
    await pause(3500);
    const steps = [
      ['product', 'product', 9000], ['followup', 'product', 7000],
      ['faq', 'faq', 7000], ['mixed', 'mixed', 9000], ['noanswer', 'product', 8000],
    ];
    for (const [name, expectedRoute, duration] of steps) {
      await page.locator('[data-case="' + name + '"]').click();
      await pause(700);
      const result = page.waitForResponse(response => response.url().endsWith('/api/v1/shop/recommend'));
      await page.locator('#submit').click();
      const response = await result;
      const data = await response.json();
      if (response.status() !== 200 || data.route !== expectedRoute || data.configured_embedding_provider !== 'hash')
        throw new Error('Unexpected live API result for ' + name);
      if (name === 'noanswer' && data.recommendations.length !== 0)
        throw new Error('Unsupported capability produced recommendations');
      if (name === 'product' && (!data.recommendations.length || data.recommendations.some(p => p.price > 500 || !p.evidence.length)))
        throw new Error('Product budget/evidence contract failed');
      if (name === 'followup') {
        const previous = new Set(scenarios[0].response.recommendations.map(p => p.product_id));
        if (!data.recommendations.length || data.recommendations.some(p => !previous.has(p.product_id)))
          throw new Error('Follow-up lost the recommended product context');
      }
      await page.locator('#submit').filter({hasText:'检索并核验'}).waitFor();
      await page.screenshot({path: path.join(out, name + '.png'), fullPage: true});
      scenarios.push({case: name, expected_route: expectedRoute, response: data});
      await pause(duration);
    }
    if (errors.length) throw new Error('Browser errors: ' + errors.join('; '));
  } finally {
    await context.close();
    await browser.close();
  }
  const recorded = await video.path();
  fs.renameSync(recorded, target);
  const html = path.resolve(__dirname, '../docs/demo/index.html');
  const info = {created_at: new Date().toISOString(), kind: 'actual_browser_recording',
    provider: 'hash', semantic_embedding: false, synthetic_data_only: true,
    new_cloud_calls_during_recording: 0, viewport: {width:1440,height:1000},
    html_sha256: crypto.createHash('sha256').update(fs.readFileSync(html)).digest('hex'),
    video_sha256: crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex'),
    scenarios, browser_errors: errors};
  fs.writeFileSync(path.join(out, 'recording-manifest.json'), JSON.stringify(info, null, 2) + '\n');
  console.log('Recorded ' + scenarios.length + ' real API interactions: ' + target);
})().catch(error => {console.error(String(error)); process.exitCode = 1;});
