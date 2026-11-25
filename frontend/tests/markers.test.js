import test from 'node:test';
import assert from 'node:assert/strict';
import { shapes, defaultMarker, normalizeMarkers, encodeMarkers, decodeMarkers, resolveColor, markerBox, labelOffset, sizeForPosition, positionForSize, estimatePosition } from '../markers.js';

test('defaults are complete and each supplied shape is available', () => {
  assert.equal(Object.keys(shapes).length, 7);
  for (const type of Object.keys(shapes)) assert.deepEqual(defaultMarker(type), { type, lat: null, lon: null, size: 72, opacity: 1, color: 'text', label: '' });
  assert.equal(defaultMarker('missing').type, 'heart');
});
test('normalization drops malformed marks and coordinate pairs, clamps values, strips labels and extras', () => {
  assert.deepEqual(normalizeMarkers(null), []);
  const input = [null, {}, { type: 'unknown' }, { type: 'pin', lat: 1 }, { type: 'pin', lat: '1', lon: 2 }, { type: 'dot', lat: Infinity, lon: 2 },
    { type: 'heart', lat: 90, lon: -200, size: 900, opacity: 0, color: 'bad', label: '  Our\n home\u0000  ', extra: true }];
  assert.deepEqual(normalizeMarkers(input), [{ type: 'heart', lat: 85, lon: -180, size: 500, opacity: 0.15, color: 'text', label: 'Our home' }]);
  assert.equal(normalizeMarkers(Array(8).fill(defaultMarker())).length, 5);
  assert.equal(normalizeMarkers([{ type: 'pin', size: 1, opacity: 4, label: 'x'.repeat(50) }])[0].label.length, 40);
  assert.equal(normalizeMarkers([{ type: 'pin', size: NaN }])[0].size, 72);
});
test('URL JSON round trip, malformed input and old heart links', () => {
  const marks = [{ ...defaultMarker('flag'), lat: 50.12345, lon: 30.54321, label: 'Our home & garden', color: '#abcdef' }];
  const params = new URLSearchParams({ markers: encodeMarkers(marks), connect: 'true' });
  assert.deepEqual(decodeMarkers(new URLSearchParams(params.toString()).get('markers')), marks);
  for (const value of [null, '', '{', '{}', 'x'.repeat(4001)]) assert.deepEqual(decodeMarkers(value), []);
  assert.deepEqual(decodeMarkers(null, 'true'), [{ ...defaultMarker(), color: '#FF2A2A' }]);
  assert.equal(decodeMarkers(encodeMarkers(marks), true)[1].type, 'flag');
});
test('all palette tokens resolve to the active theme and custom colors survive', () => {
  const colors = { text: '#123456', road_motorway: '#654321', water: '#abcdef' };
  for (const token of Object.keys(colors)) {
    assert.equal(resolveColor(token, colors), colors[token]);
    assert.equal(normalizeMarkers([{ type: 'dot', color: token }])[0].color, token);
  }
  assert.equal(resolveColor('#C0392B', colors), '#C0392B');
  assert.equal(resolveColor('invalid', colors), colors.text);
});
test('path bounds, label ascent, pin and flag labels sit below their anchors', () => {
  assert.deepEqual(markerBox(shapes.heart), { x: 6, y: 8, width: 88, height: 82 });
  assert.equal(labelOffset(shapes.pin, 72), 15.25);
  assert.equal(labelOffset(shapes.flag, 500, 2), 30.5);
  assert.equal(labelOffset(shapes.heart, 100), 56.25);
});
test('nonlinear size scale covers endpoints and round trips', () => {
  assert.equal(sizeForPosition(0), 12);
  assert.ok(Math.abs(sizeForPosition(100) - 500) < 1e-10);
  for (const size of [12, 72, 160, 500]) assert.ok(Math.abs(sizeForPosition(positionForSize(size)) - size) < 1e-10);
});
test('fallback position uses center and longest sheet span', () => {
  const state = { width: 12, height: 16, latitude: 0, longitude: 0, span: 20000 };
  assert.deepEqual(estimatePosition(defaultMarker(), state), { x: 432, y: 576 });
  const point = estimatePosition({ ...defaultMarker(), lat: 2000 / 111320, lon: 2000 / 111320 }, state);
  assert.equal(point.x, 547.2); assert.equal(point.y, 460.8);
});

test('an old heart link with five marks stays within the limit', () => {
  const five = JSON.stringify(Array.from({ length: 5 }, () => defaultMarker('dot')));
  const marks = decodeMarkers(five, 'true');
  assert.equal(marks.length, 5);
  assert.equal(marks[0].type, 'heart');
});
