import shapes from './marker-shapes.json' with { type: 'json' };
export { shapes };
// Points on a 12 inch wide sheet, the same as in markers.py.
export const LABEL_SIZE = 15;
export const LINE_WIDTH = 2.2;
const tokens = ['text', 'road_motorway', 'water'];
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
export function defaultMarker(type = 'heart') {
  return { type: Object.hasOwn(shapes, type) ? type : 'heart', lat: null, lon: null, size: 72, opacity: 1, color: 'text', label: '' };
}
export function normalizeMarkers(value) {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    if (!item || typeof item !== 'object' || !Object.hasOwn(shapes, item.type)) return [];
    const marker = { ...defaultMarker(item.type), ...item };
    if ((marker.lat == null) !== (marker.lon == null)) return [];
    if (marker.lat != null && (typeof marker.lat !== 'number' || !Number.isFinite(marker.lat) || typeof marker.lon !== 'number' || !Number.isFinite(marker.lon))) return [];
    if (marker.lat != null) { marker.lat = clamp(marker.lat, -85, 85); marker.lon = clamp(marker.lon, -180, 180); }
    const size = Number(marker.size), opacity = Number(marker.opacity);
    marker.size = Number.isFinite(size) ? clamp(size, 12, 500) : 72;
    marker.opacity = Number.isFinite(opacity) ? clamp(opacity, 0.15, 1) : 1;
    marker.color = tokens.includes(marker.color) || /^#[\da-f]{6}$/i.test(marker.color) ? marker.color : 'text';
    marker.label = typeof marker.label === 'string' ? Array.from(marker.label.replace(/[\p{Cc}\p{Cf}]/gu, '').trim()).slice(0, 40).join('') : '';
    return [{ type: marker.type, lat: marker.lat ?? null, lon: marker.lon ?? null, size: marker.size, opacity: marker.opacity, color: marker.color, label: marker.label }];
  }).slice(0, 5);
}
// URLSearchParams handles percent encoding; these functions handle only the JSON value.
export const encodeMarkers = value => JSON.stringify(normalizeMarkers(value));
export function decodeMarkers(value, showHeart = false) {
  let markers = [];
  if (typeof value === 'string' && value.length <= 4000) {
    try { markers = normalizeMarkers(JSON.parse(value)); } catch { /* Malformed links have no marks. */ }
  }
  return showHeart === true || showHeart === 'true' ? [{ ...defaultMarker(), color: '#FF2A2A' }, ...markers].slice(0, 5) : markers;
}
export function resolveColor(color, themeColors) {
  return tokens.includes(color) ? themeColors[color] : /^#[\da-f]{6}$/i.test(color) ? color : themeColors.text;
}
export function markerBox(shape) {
  const points = (shape.d.match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) || []).map(Number);
  const xs = points.filter((_, i) => i % 2 === 0), ys = points.filter((_, i) => i % 2 === 1);
  return { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
}
export function labelOffset(shape, size = 72, scale = 1) {
  const box = markerBox(shape);
  return ((box.y + box.height - shape.anchor[1]) * size / 100 + 4 + 0.75 * LABEL_SIZE) * scale;
}
export const sizeForPosition = position => 12 * (500 / 12) ** (position / 100);
export const positionForSize = size => 100 * Math.log(size / 12) / Math.log(500 / 12);
// Span follows the longest sheet dimension, as it does in the renderer.
export function estimatePosition(marker, state) {
  const W = state.width * 72, H = state.height * 72;
  if (marker.lat == null) return { x: W / 2, y: H / 2 };
  const metersPerDegree = 111320;
  const x = (marker.lon - (state.longitude ?? 30.5234)) * metersPerDegree * Math.cos((state.latitude ?? 50.4501) * Math.PI / 180);
  const y = (marker.lat - (state.latitude ?? 50.4501)) * metersPerDegree;
  return { x: W / 2 + x / state.span * Math.max(W, H), y: H / 2 - y / state.span * Math.max(W, H) };
}
