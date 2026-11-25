export const ROAD_CLASSES = {
  default: ['service', 'track', 'path', 'busway', 'bus_guideway'],
  residential: ['minor'], tertiary: ['tertiary'], secondary: ['secondary'],
  primary: ['trunk', 'primary'], motorway: ['motorway'],
};
export const ROAD_WIDTHS = { default: 0.4, residential: 0.4, tertiary: 0.6, secondary: 0.8, primary: 1, motorway: 1.2 };
const eq = (property, value) => ['==', ['get', property], value];
const notIntermittent = ['!', ['in', ['get', 'intermittent'], ['literal', [1, '1']]]];
const surfaceWater = ['all', ['!=', ['get', 'brunnel'], 'tunnel'], notIntermittent];
export function buildStyle(colors, { tilesUrl, stroke, showBuildings }) {
  const line = (id, sourceLayer, filter, color, width) => ({
    id, type: 'line', source: 'map', 'source-layer': sourceLayer,
    filter: ['all', ['==', ['geometry-type'], 'LineString'], filter],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': color, 'line-width': stroke(width) },
  });
  return {
    version: 8, sources: { map: { type: 'vector', url: tilesUrl } }, layers: [
      { id: 'background', type: 'background', paint: { 'background-color': colors.bg } },
      { id: 'water', type: 'fill', source: 'map', 'source-layer': 'water',
        filter: ['all', surfaceWater, ['!=', ['get', 'class'], 'swimming_pool']],
        paint: { 'fill-color': colors.water, 'fill-antialias': false } },
      ...Object.entries({ river: 1, canal: 0.6, stream: 0.3 }).map(([kind, width]) =>
        line(`waterway-${kind}`, 'waterway', ['all', surfaceWater, eq('class', kind)], colors.water, width)),
      { id: 'parks', type: 'fill', source: 'map', 'source-layer': 'landcover',
        filter: ['all', eq('class', 'grass'), ['in', ['get', 'subclass'], ['literal', ['park', 'grass', 'village_green', 'recreation_ground']]]],
        paint: { 'fill-color': colors.parks, 'fill-antialias': false } },
      ...Object.entries(ROAD_CLASSES).map(([kind, classes]) => line(`road-${kind}`, 'transportation',
        ['in', ['get', 'class'], ['literal', classes]], colors[`road_${kind}`] || colors.road_tertiary, ROAD_WIDTHS[kind])),
      { id: 'buildings', type: 'fill', source: 'map', 'source-layer': 'building',
        layout: { visibility: showBuildings ? 'visible' : 'none' },
        paint: { 'fill-color': colors.building || colors.road_residential } },
    ],
  };
}
