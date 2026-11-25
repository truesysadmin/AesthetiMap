// CSS-independent color math. OKLCH lightness and chroma use the 0–1 scale.
const clamp = value => Math.min(1, Math.max(0, value));
const linear = value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
const encoded = value => value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055;
const channels = hex => [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255);
export function srgbToOklch(hex) {
  const [r, g, b] = channels(hex).map(linear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return { L, C: Math.hypot(a, bb), h: (Math.atan2(bb, a) * 180 / Math.PI + 360) % 360 };
}
export function oklchToSrgb({ L, C, h }) {
  const a = C * Math.cos(h * Math.PI / 180), b = C * Math.sin(h * Math.PI / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return '#' + [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s]
    .map(value => Math.round(clamp(encoded(value)) * 255).toString(16).padStart(2, '0')).join('');
}
export function luminance(hex) {
  const [r, g, b] = channels(hex).map(linear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(first, second) {
  const a = luminance(first), b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
export function galleryPalette(colors) {
  const background = srgbToOklch(colors.bg || '#ffffff');
  const dark = background.L < 0.45;
  const strongest = ['bg', 'water', 'parks', 'road_motorway', 'text'].map(key => srgbToOklch(colors[key] || '#ffffff'))
    .filter(color => color.C >= 0.02).sort((a, b) => b.C - a.C)[0] || { C: 0, h: 0 };
  // A dark sheet sets the mood of the wall itself; its bright roads are only accents.
  const source = dark && background.C >= 0.02 ? { C: Math.max(0.06, background.C), h: background.h } : strongest;
  // Yellow turns khaki when it gets pale or dark, sand and bronze do not.
  const h = source.h > 78 && source.h < 125 ? 72 : source.h;
  const C = Math.min(0.030, source.C * 0.35);
  const color = (L, chroma = C) => oklchToSrgb({ L, C: chroma, h });
  const base = srgbToOklch(dark ? '#2b2521'
    : !source.C || h >= 20 && h <= 115 ? '#a07a50' : '#5f4433');
  const frame = Object.fromEntries(Object.entries({ top: 0.05, right: -0.03, bottom: -0.06, left: 0.02 })
    .map(([side, delta]) => [side, oklchToSrgb({ ...base, L: clamp(base.L + delta) })]));
  let L = 0.40;
  const accentColor = lightness => color(lightness, Math.min(source.C, 0.10));
  while (L > 0 && contrast('#ffffff', accentColor(L)) < 5.5) L -= 0.02;
  const accent = source.C ? accentColor(L) : '#1f1d1b';
  const hover = source.C ? accentColor(L + 0.05) : oklchToSrgb({ ...srgbToOklch(accent), L: srgbToOklch(accent).L + 0.05 });
  return { wall: color(0.83), wallLight: color(0.93, C * 0.5), floor: color(0.915, C * 0.5),
    skirting: color(0.72), frame, mat: '#fdfcf9', matShade: '#e4dfd5', accent,
    accentHover: contrast('#ffffff', hover) >= 4.5 ? hover : accent, onAccent: '#ffffff', label: color(0.99, 0.004) };
}
