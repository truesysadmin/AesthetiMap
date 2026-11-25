import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { srgbToOklch, oklchToSrgb, galleryPalette, contrast } from '../palette.js';

test('OKLCH known colors and sRGB round trips', () => {
  const white = srgbToOklch('#ffffff'), red = srgbToOklch('#ff0000');
  assert.ok(Math.abs(white.L - 1) < 1e-6);
  assert.ok(white.C < 1e-6);
  assert.ok(Math.abs(red.L - 0.628) < 0.001);
  assert.ok(Math.abs(red.C - 0.258) < 0.001);
  assert.ok(Math.abs(red.h - 29) < 0.3);
  for (const hex of ['#ffffff', '#000000', '#ff0000', '#00ff00', '#0000ff', '#a07a50', '#5f4433']) {
    assert.equal(oklchToSrgb(srgbToOklch(hex)), hex);
  }
  assert.equal(contrast('#ffffff', '#000000'), 21);
});
const directory = new URL('../../themes/', import.meta.url);
for (const file of readdirSync(directory).filter(name => name.endsWith('.json'))) {
  test(`gallery palette is accessible and valid for ${file}`, () => {
    const colors = JSON.parse(readFileSync(new URL(file, directory), 'utf8'));
    const palette = galleryPalette(colors);
    for (const value of Object.values(palette).flatMap(value => typeof value === 'object' ? Object.values(value) : [value])) {
      assert.match(value, /^#[a-f0-9]{6}$/);
    }
    assert.ok(contrast('#ffffff', palette.accent) >= 5.5);
    assert.ok(contrast('#ffffff', palette.accentHover) >= 4.5);
    for (const background of [palette.wall, palette.floor, palette.label]) assert.ok(contrast('#23201d', background) >= 4.5);
    assert.ok(contrast('#5d5750', palette.floor) >= 4.5);
    assert.ok(contrast('#766f67', '#ffffff') >= 4.5);
  });
}
test('neutral palettes use oak and the neutral accent; dark sheets use ebony', () => {
  const gray = { bg: '#ffffff', water: '#888888', parks: '#aaaaaa', text: '#222222', road_motorway: '#bbbbbb' };
  const palette = galleryPalette(gray);
  assert.equal(palette.accent, '#1f1d1b');
  const oak = srgbToOklch('#a07a50');
  assert.equal(palette.frame.top, oklchToSrgb({ ...oak, L: oak.L + 0.05 }));
  const ebony = srgbToOklch('#2b2521');
  assert.equal(galleryPalette({ ...gray, bg: '#000000' }).frame.bottom, oklchToSrgb({ ...ebony, L: ebony.L - 0.06 }));
});
test('a dark sheet sets the wall hue and yellow becomes sand', () => {
  const navy = { bg: '#0a1628', water: '#061020', parks: '#0f2235', text: '#d4af37', road_motorway: '#d4af37' };
  const hue = srgbToOklch(galleryPalette(navy).wall).h;
  assert.ok(hue > 220 && hue < 290, String(hue));
  const gold = { bg: '#fdfbf5', water: '#f0ede4', parks: '#f7f4ea', text: '#c8a84b', road_motorway: '#d4af37' };
  const sand = srgbToOklch(galleryPalette(gold).wall).h;
  assert.ok(sand > 60 && sand < 82, String(sand));
});
