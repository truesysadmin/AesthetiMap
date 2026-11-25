import test from 'node:test';
import assert from 'node:assert/strict';
import { zoomForSpan, spanForZoom, sheetSize, strokePx, scaleRatio, niceScaleBar, titleText, titleSize, formatCoordinates, parseCoordinate, detailFactor } from '../preview-math.js';

for (const lat of [0, 43, 70]) {
  for (const [width, height] of [[12, 16], [16, 12], [12, 12]]) {
    test(`zoom/span round trip at latitude ${lat}, ${width} × ${height}`, () => {
      const sheet = sheetSize(800, 600, width, height);
      for (const span of [500, 1000, 20000, 50000]) {
        const pixels = Math.max(sheet.width, sheet.height) * 3;
        const zoom = zoomForSpan(span, lat, pixels);
        assert.ok(Math.abs(spanForZoom(zoom, lat, pixels) - span) < 1e-8);
        const resolution = 40075016.686 * Math.cos(lat * Math.PI / 180) / (512 * 2 ** zoom);
        assert.ok(Math.abs(resolution * pixels - span) < 1e-8);
      }
    });
  }
}

test('sheets fit available space and keep their print aspect ratio', () => {
  assert.deepEqual(sheetSize(800, 600, 12, 16), { width: 450, height: 600 });
  assert.deepEqual(sheetSize(800, 600, 16, 12), { width: 800, height: 600 });
  assert.deepEqual(sheetSize(800, 600, 12, 12), { width: 600, height: 600 });
  assert.deepEqual(sheetSize(0, 600, 12, 16), { width: 0, height: 0 });
});
test('point strokes and scale use physical print dimensions', () => {
  assert.equal(strokePx(1, 864, 12), 1);
  assert.equal(strokePx(0.4, 1296, 12), 0.6);
  assert.equal(scaleRatio(20000, 12, 16), 49200);
  assert.equal(scaleRatio(20000, 16, 12), 49200);
  assert.equal(scaleRatio(20000, 12, 12), 65600);
});
test('ruler uses horizontal coverage for every sheet orientation', () => {
  for (const [w, h] of [[12, 16], [16, 12], [12, 12]]) {
    for (const span of [500, 1000, 20000, 50000]) {
      const bar = niceScaleBar(span, w, h, 600);
      assert.ok(bar.pixels >= 90 && bar.pixels <= 210);
      assert.ok([1, 2, 5].includes(Number((bar.metres / 10 ** Math.floor(Math.log10(bar.metres))).toPrecision(1))));
      assert.equal(bar.pixels / 600, bar.metres / (span * w / Math.max(w, h)));
    }
  }
  assert.equal(niceScaleBar(20000, 12, 16, 600).label, '5 km');
  assert.ok(Number.isFinite(niceScaleBar(1400, 12, 12, 600).pixels));
});
test('title uses the renderer Latin threshold and two preserved spaces', () => {
  assert.equal(titleText('Kyiv'), 'K  Y  I  V');
  assert.equal(titleText('MieresEspaña'), 'M  I  E  R  E  S  E  S  P  A  Ñ  A');
  assert.equal(titleText('Київ'), 'Київ');
  assert.equal(titleText('Αθήνα'), 'Αθήνα');
  assert.equal(titleText('abcdЖ'), 'abcdЖ');
  assert.equal(titleText('abcdeЖ'), 'A  B  C  D  E  Ж');
  assert.equal(titleText('123'), '1  2  3');
  assert.equal(titleSize('Kyiv', 12, 16), 60);
  assert.equal(titleSize('a'.repeat(20), 12, 16), 30);
  assert.equal(titleSize('a'.repeat(100), 6, 8), 5);
});
test('coordinates match renderer output for both hemispheres', () => {
  assert.equal(formatCoordinates(50.4501, 30.5234), '50.4501° N / 30.5234° E');
  assert.equal(formatCoordinates(-43.12, -70), '43.1200° S / 70.0000° W');
  assert.equal(formatCoordinates(0, 0), '0.0000° N / 0.0000° E');
});

test('coordinate text in decimal and degree forms', () => {
  const near = (text, limit, expected) => assert.ok(Math.abs(parseCoordinate(text, limit) - expected) < 1e-9, text);
  near('41.1496', 85, 41.1496);
  near(' -8.611 ', 180, -8.611);
  near('8.611 W', 180, -8.611);
  near(`41°9'0"N`, 85, 41.15);
  near(`8°36'36"W`, 180, -8.61);
  near('8 36 36 W', 180, -8.61);
  near('-8 36 36', 180, -8.61);
  near('33°52′S', 85, -33 - 52 / 60);
  near('0', 85, 0);
  near('-0.5', 85, -0.5);
  for (const bad of ['', null, undefined, 'abc', '41,15', '86', '-85.1', '10 75', '1e5', '12.5.6']) assert.equal(parseCoordinate(bad, 85), null, String(bad));
  assert.equal(parseCoordinate('181', 180), null);
});

test('physical mat and frame proportions fit all sheet orientations', async () => {
  const { frameBorder, framedSheetSize } = await import('../preview-math.js');
  assert.deepEqual(frameBorder(12, 16), { mat: 1.02, frame: 0.312 });
  for (const [w, h] of [[12, 16], [16, 12], [12, 12], [2, 40]]) {
    for (const [availableW, availableH] of [[800, 600], [312, 400], [0, 600]]) {
      const size = framedSheetSize(availableW, availableH, w, h);
      const edge = 2 * (size.mat + size.frame);
      assert.ok(size.width + edge <= availableW + 1e-9);
      assert.ok(size.height + edge <= availableH + 1e-9);
      if (!availableW) { assert.deepEqual(size, { width: 0, height: 0, mat: 0, frame: 0 }); continue; }
      assert.ok(Math.abs(size.width / size.height - w / h) < 1e-10);
      assert.ok(Math.abs(size.mat / Math.min(size.width, size.height) - 0.085) < 1e-10);
      assert.ok(Math.abs(size.frame / Math.min(size.width, size.height) - 0.026) < 1e-10);
      assert.ok(Math.abs(size.width + edge - availableW) < 1e-9 || Math.abs(size.height + edge - availableH) < 1e-9);
    }
  }
});

test('small sheets get a larger detail factor within limits', () => {
  assert.equal(detailFactor(800, 900), 3);
  assert.ok(Math.abs(detailFactor(393, 525) * 525 - 2200) < 1e-9);
  assert.equal(detailFactor(100, 120), 6);
});
