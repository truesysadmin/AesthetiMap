import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { resolve, relative, isAbsolute, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as pause } from 'node:timers/promises';
import { chromium } from 'playwright';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const types = {
  '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css',
  '.html': 'text/html', '.json': 'application/json', '.png': 'image/png',
  '.webp': 'image/webp', '.svg': 'image/svg+xml', '.woff': 'font/woff',
  '.woff2': 'font/woff2', '.txt': 'text/plain',
};
let server, browser, origin;
let tileDelay = 0;
const location = '/?city=Kyiv&latitude=50.4501&longitude=30.5234';

async function bounded(work, ms, message) {
  let timer;
  try {
    return await Promise.race([
      work,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function alive(page, ms = 5000) {
  return bounded(page.evaluate(() => 1).then(() => true), ms, 'Renderer is unresponsive').catch(() => false);
}

before(async () => {
  assert.ok(await stat(resolve(dist, 'index.html')).then(info => info.isFile()).catch(() => false), 'run npm run build first');
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (url.pathname.startsWith('/api/')) { res.writeHead(404); res.end(); return; }
      const pathname = decodeURIComponent(url.pathname);
      const file = resolve(dist, pathname === '/' ? 'index.html' : `.${pathname}`);
      const within = relative(dist, file);
      if (within === '..' || within.startsWith(`..${sep}`) || isAbsolute(within)) {
        res.writeHead(403); res.end(); return;
      }
      const body = await readFile(file);
      res.writeHead(200, { 'Content-Type': types[extname(file)] || 'application/octet-stream' });
      res.end(body);
    } catch {
      res.writeHead(404); res.end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({
    headless: true, channel: process.env.E2E_CHANNEL || undefined,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
}, { timeout: 60000 });

after(async () => {
  try {
    if (browser) await bounded(browser.close(), 10000, 'Browser cleanup timed out');
  } finally {
    if (server?.listening) {
      const closed = new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      server.closeAllConnections();
      await closed;
    }
  }
}, { timeout: 15000 });

async function openPage(t) {
  const page = await bounded(browser.newPage({ viewport: { width: 1600, height: 1000 }, serviceWorkers: 'block' }), 10000, 'Could not open page');
  t.after(() => bounded(page.close(), 10000, 'Page cleanup timed out'));
  page.setDefaultTimeout(10000);
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === origin) return route.continue();
    if (url.href === 'https://tiles.openfreemap.org/planet') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        tilejson: '3.0.0', tiles: ['https://tiles.openfreemap.org/t/{z}/{x}/{y}.pbf'],
        minzoom: 0, maxzoom: 14, vector_layers: [],
      }) });
    }
    if (url.origin === 'https://tiles.openfreemap.org') {
      await pause(tileDelay);
      return route.fulfill({ status: 204, body: '' });
    }
    return route.abort();
  });
  await bounded(page.goto(`${origin}${location}`), 30000, 'Navigation timed out; the renderer may be frozen');
  return page;
}

async function center(page) {
  const box = await bounded(page.locator('#sheet').boundingBox(), 5000, 'Could not read sheet bounds; the renderer may be frozen');
  assert.ok(box, 'The sheet must be visible');
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function drag(page, point, steps = 12) {
  await bounded((async () => {
    await page.mouse.move(point.x, point.y);
    await page.mouse.down();
    for (let j = 1; j <= steps; j++) {
      await page.mouse.move(point.x - j * 10, point.y - j * 6);
      await pause(16);
    }
    await page.mouse.up();
  })(), 6000, 'Drag timed out; the renderer may be frozen');
}

async function wheel(page, count, delta) {
  for (let i = 0; i < count; i++) {
    await bounded(page.mouse.wheel(0, delta), 5000, 'Wheel input timed out; the renderer may be frozen');
    await pause(30);
  }
}

test('zooming in and dragging does not freeze the page', { timeout: 120000 }, async t => {
  tileDelay = 0;
  const page = await openPage(t);
  await bounded(page.waitForFunction(() => performance.getEntriesByName('preview-idle').length, null, { timeout: 30000 }), 31000, 'Map did not become idle; the renderer may be frozen');
  const point = await center(page);
  await bounded(page.mouse.move(point.x, point.y), 5000, 'Mouse input timed out');
  await wheel(page, 80, -300);
  await pause(1500);
  assert.ok(await alive(page), 'The renderer froze after zooming in');
  assert.equal(await bounded(page.locator('#span').inputValue(), 5000, 'Reading span timed out'), '500');
  for (let round = 0; round < 8; round++) {
    await drag(page, point);
    assert.ok(await alive(page), `The renderer froze after drag ${round + 1}`);
    await wheel(page, 4, round % 2 ? 300 : -300);
    await pause(500);
  }
  assert.ok(await alive(page), 'The renderer froze after repeated zooming and dragging');
  const latitude = Number.parseFloat(await bounded(page.locator('#latitude').inputValue(), 5000, 'Reading latitude timed out'));
  assert.ok(Number.isFinite(latitude) && Math.abs(latitude - 50.45) < 1, `Latitude drifted unexpectedly: ${latitude}`);
});

test('the loading bar shows while tiles load and hides when the map is idle', { timeout: 120000 }, async t => {
  tileDelay = 2000;
  t.after(() => { tileDelay = 0; });
  const page = await openPage(t);
  await bounded(page.waitForFunction(() => document.getElementById('map-loading').classList.contains('active'), null, { timeout: 10000 }), 11000, 'Loading bar did not appear; the renderer may be frozen');
  assert.equal(await bounded(page.locator('#sheet').getAttribute('aria-busy'), 5000, 'Reading busy state timed out'), 'true');
  await bounded(page.waitForFunction(() => !document.getElementById('map-loading').classList.contains('active')
    && !document.getElementById('sheet').hasAttribute('aria-busy'), null, { timeout: 20000 }), 21000, 'Loading bar did not clear when the map became idle');
  tileDelay = 0;
  await drag(page, await center(page), 1);
  await pause(2000);
  assert.ok(await alive(page), 'The renderer froze after a short pan');
  assert.equal(await bounded(page.locator('#map-loading').evaluate(element => element.classList.contains('active')), 5000, 'Reading loading state timed out'), false);
});
