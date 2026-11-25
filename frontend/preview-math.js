// The renderer measures coverage along the longest side of the sheet.
const WORLD = 40075016.686;
export function zoomForSpan(span, lat, longSidePx) {
  return Math.log2(WORLD * Math.cos(lat * Math.PI / 180) * longSidePx / (512 * span));
}
export function spanForZoom(zoom, lat, longSidePx) {
  return WORLD * Math.cos(lat * Math.PI / 180) * longSidePx / (512 * 2 ** zoom);
}
export function sheetSize(availableW, availableH, width, height) {
  const factor = Math.max(0, Math.min(availableW / width, availableH / height));
  return { width: width * factor, height: height * factor };
}
export function strokePx(pt, sheetWidthPx, widthIn) {
  return pt * sheetWidthPx / (widthIn * 72);
}
export function scaleRatio(span, width, height) {
  return Number((span / (Math.max(width, height) * 0.0254)).toPrecision(3));
}
export function niceScaleBar(span, width, height, sheetWidthPx) {
  const horizontalSpan = span * width / Math.max(width, height);
  const exponent = Math.floor(Math.log10(horizontalSpan * 0.25));
  const candidates = [];
  for (let n = exponent - 1; n <= exponent + 1; n++) {
    for (const unit of [1, 2, 5]) candidates.push(unit * 10 ** n);
  }
  const inRange = candidates.filter(value => value / horizontalSpan >= 0.15 && value / horizontalSpan <= 0.35);
  // A few spans fall in the small gap between 2 and 5; choose the nearest nice length.
  const metres = (inRange.length ? inRange : candidates)
    .sort((a, b) => Math.abs(a / horizontalSpan - 0.25) - Math.abs(b / horizontalSpan - 0.25))[0];
  return { metres, pixels: metres / horizontalSpan * sheetWidthPx,
    label: metres >= 1000 ? `${metres / 1000} km` : `${metres} m` };
}
export function titleText(city) {
  const letters = Array.from(city).filter(char => /\p{L}/u.test(char));
  const latin = !letters.length || letters.filter(char => char.codePointAt(0) < 0x250).length / letters.length > 0.8;
  return latin ? Array.from(city.toUpperCase()).join('  ') : city;
}
export function titleSize(city, width, height) {
  const length = Array.from(city).length;
  return Math.max(length > 10 ? 60 * 10 / length : 60, 10) * Math.min(width, height) / 12;
}
// Accepts what people paste and what old links hold: 41.15, -8.61, 41°9'0"N, 8 36 36 W.
export function parseCoordinate(text, limit) {
  const match = /^\s*([+-]?\d+(?:\.\d+)?)(?:\s*[°:\s]\s*(\d+(?:\.\d+)?)?)?(?:\s*['′:\s]\s*(\d+(?:\.\d+)?)?)?\s*["″]?\s*([NSEW])?\s*$/i.exec(text ?? '');
  if (!match) return null;
  const [, degrees, minutes = 0, seconds = 0, side = ''] = match;
  if (minutes >= 60 || seconds >= 60) return null;
  const size = Math.abs(degrees) + minutes / 60 + seconds / 3600;
  const value = degrees.startsWith('-') || /[SW]/i.test(side) ? -size : size;
  return Number.isFinite(value) && Math.abs(value) <= limit ? value : null;
}
export function formatCoordinates(lat, lon) {
  return `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? 'N' : 'S'} / ${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? 'E' : 'W'}`;
}
