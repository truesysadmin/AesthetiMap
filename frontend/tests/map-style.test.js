import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildStyle, ROAD_CLASSES, ROAD_WIDTHS } from '../map-style.js';
const colors = JSON.parse(readFileSync(new URL('../../themes/terracotta.json', import.meta.url)));
const style = buildStyle(colors, { tilesUrl: 'https://example.test/tiles', stroke: pt => pt * 2, showBuildings: true });
const layer = id => style.layers.find(item => item.id === id);
// Evaluate the small expression subset used in our filters against representative tile features.
function evaluate(expression, properties, geometry = 'LineString') {
  if (!Array.isArray(expression)) return expression;
  const [operator, ...args] = expression;
  const values = () => args.map(arg => evaluate(arg, properties, geometry));
  if (operator === 'get') return properties[args[0]];
  if (operator === 'literal') return args[0];
  if (operator === 'geometry-type') return geometry;
  if (operator === 'all') return values().every(Boolean);
  if (operator === '!') return !values()[0];
  if (operator === '==') return values()[0] === values()[1];
  if (operator === '!=') return values()[0] !== values()[1];
  if (operator === 'in') return values()[1].includes(values()[0]);
  throw new Error(operator);
}
test('source, layer order, and palette match the renderer', () => {
  assert.deepEqual(style.sources.map, { type: 'vector', url: 'https://example.test/tiles' });
  assert.deepEqual(style.layers.map(item => item.id), ['background', 'water', 'waterway-river', 'waterway-canal', 'waterway-stream', 'parks',
    'road-default', 'road-residential', 'road-tertiary', 'road-secondary', 'road-primary', 'road-motorway', 'buildings']);
  assert.equal(layer('background').paint['background-color'], colors.bg);
  assert.equal(layer('water').paint['fill-color'], colors.water);
  assert.equal(layer('parks').paint['fill-color'], colors.parks);
  assert.equal(layer('buildings').paint['fill-color'], colors.building);
  assert.equal(layer('water').paint['fill-antialias'], false);
  assert.equal(layer('parks').paint['fill-antialias'], false);
});
test('exact tile road class mapping and point weights, lines only and round joins', () => {
  assert.deepEqual(ROAD_CLASSES, { default: ['service', 'track', 'path', 'busway', 'bus_guideway'], residential: ['minor'],
    tertiary: ['tertiary'], secondary: ['secondary'], primary: ['trunk', 'primary'], motorway: ['motorway'] });
  assert.deepEqual(ROAD_WIDTHS, { default: 0.4, residential: 0.4, tertiary: 0.6, secondary: 0.8, primary: 1, motorway: 1.2 });
  for (const [kind, classes] of Object.entries(ROAD_CLASSES)) {
    const road = layer(`road-${kind}`);
    assert.equal(road.paint['line-color'], colors[`road_${kind}`]);
    assert.equal(road.paint['line-width'], ROAD_WIDTHS[kind] * 2);
    assert.deepEqual(road.layout, { 'line-cap': 'round', 'line-join': 'round' });
    for (const cls of Object.values(ROAD_CLASSES).flat()) assert.equal(evaluate(road.filter, { class: cls }), classes.includes(cls));
    assert.equal(evaluate(road.filter, { class: classes[0] }, 'Polygon'), false);
    assert.equal(evaluate(road.filter, { class: 'rail' }), false);
  }
});
test('water excludes pools, tunnels, and both intermittent encodings', () => {
  const filter = layer('water').filter;
  assert.equal(evaluate(filter, { class: 'lake' }, 'Polygon'), true);
  for (const properties of [{ class: 'swimming_pool' }, { brunnel: 'tunnel' }, { intermittent: 1 }, { intermittent: '1' }]) {
    assert.equal(evaluate(filter, properties, 'Polygon'), false);
  }
  for (const [kind, pt] of Object.entries({ river: 1, canal: 0.6, stream: 0.3 })) {
    const waterway = layer(`waterway-${kind}`);
    assert.equal(waterway.paint['line-color'], colors.water);
    assert.equal(waterway.paint['line-width'], pt * 2);
    assert.equal(evaluate(waterway.filter, { class: kind }), true);
    assert.equal(evaluate(waterway.filter, { class: kind, brunnel: 'tunnel' }), false);
    assert.equal(evaluate(waterway.filter, { class: kind, intermittent: 1 }), false);
  }
});
test('parks use only the exact grass subclasses', () => {
  for (const subclass of ['park', 'grass', 'village_green', 'recreation_ground']) {
    assert.equal(evaluate(layer('parks').filter, { class: 'grass', subclass }, 'Polygon'), true);
    assert.equal(evaluate(layer('parks').filter, { class: 'wood', subclass }, 'Polygon'), false);
  }
  assert.equal(evaluate(layer('parks').filter, { class: 'grass', subclass: 'garden' }, 'Polygon'), false);
});
test('buildings toggle visibility; missing optional colors use renderer fallbacks', () => {
  const { building, road_default, ...minimal } = colors;
  const next = buildStyle(minimal, { tilesUrl: 'local', stroke: pt => pt, showBuildings: false });
  assert.equal(next.layers.at(-1).layout.visibility, 'none');
  assert.equal(layer('buildings').layout.visibility, 'visible');
  assert.equal(next.layers.at(-1).paint['fill-color'], colors.road_residential);
  assert.equal(next.layers.find(item => item.id === 'road-default').paint['line-color'], colors.road_tertiary);
});
