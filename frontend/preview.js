import { Map as LibreMap, setWorkerUrl } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { detailFactor, zoomForSpan, spanForZoom, framedSheetSize, strokePx, scaleRatio, niceScaleBar, titleText, titleSize, formatCoordinates } from './preview-math.js';
import { buildStyle } from './map-style.js';
import '@fontsource/roboto/latin-300.css';
import '@fontsource/roboto/latin-400.css';
import '@fontsource/roboto/latin-700.css';
import '@fontsource/roboto/latin-ext-300.css';
import '@fontsource/roboto/latin-ext-400.css';
import '@fontsource/roboto/latin-ext-700.css';
import '@fontsource/roboto/cyrillic-300.css';
import '@fontsource/roboto/cyrillic-400.css';
import '@fontsource/roboto/cyrillic-700.css';
import '@fontsource/roboto/greek-300.css';
import '@fontsource/roboto/greek-400.css';
import '@fontsource/roboto/greek-700.css';

const NS = 'http://www.w3.org/2000/svg';
const byId = id => document.getElementById(id);
export function svgNode(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function createPreview({ initial, config, onMapChange }) {
  setWorkerUrl(workerUrl);
  let state = initial;
  let map;
  let size = { width: 0, height: 0 };
  let guard = false;
  let flying = false;
  let failed = false;
  let metadataLoaded = false;
  let generating = false;
  // The map is drawn K times larger and scaled down, so that a small sheet still loads detailed tiles.
  let K = 3;
  let styleKey;
  let styleReady = false;
  const sheet = byId('sheet');
  const canvas = byId('map-canvas');
  const overlay = byId('lettering');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const latitude = () => Math.max(-85, Math.min(85, state.latitude ?? 50.4501));
  const longSide = () => Math.max(size.width, size.height) * K;
  const currentStyleKey = () => JSON.stringify([state.colors, state.show_buildings, size.width, state.width, state.height]);
  const mapStyle = () => buildStyle(state.colors, { tilesUrl: config.tiles_url,
    stroke: pt => strokePx(pt * Math.min(state.width, state.height) / 12, size.width * K, state.width),
    showBuildings: state.show_buildings });

  function note() {
    const unlocated = state.latitude == null || state.longitude == null;
    sheet.classList.toggle('unlocated', unlocated);
    sheet.classList.toggle('unavailable', failed);
    const message = unlocated ? 'Pick a city from the list to see it here'
      : failed ? 'Preview is unavailable. You can still create the poster' : '';
    byId('preview-note').textContent = message;
    byId('preview-note').classList.toggle('hidden', !message);
  }

  function lettering() {
    const { width, height, colors } = state;
    const W = width * 72, H = height * 72, scale = Math.min(width, height) / 12;
    overlay.setAttribute('viewBox', `0 0 ${W} ${H}`);
    overlay.replaceChildren();
    const defs = svgNode('defs');
    overlay.append(defs);
    const sides = [...(state.gradient_tb ? ['top', 'bottom'] : []), ...(state.gradient_lr ? ['left', 'right'] : [])];
    for (const side of sides) {
      const vertical = side === 'top' || side === 'bottom';
      const reversed = side === 'bottom' || side === 'right';
      const gradient = svgNode('linearGradient', { id: `fade-${side}`, x1: vertical ? '0%' : reversed ? '100%' : '0%',
        y1: vertical && reversed ? '100%' : '0%', x2: vertical ? '0%' : reversed ? '0%' : '100%',
        y2: vertical && !reversed ? '100%' : '0%' });
      gradient.append(svgNode('stop', { offset: '0%', 'stop-color': colors.gradient_color }),
        svgNode('stop', { offset: '100%', 'stop-color': colors.gradient_color, 'stop-opacity': 0 }));
      defs.append(gradient);
      overlay.append(svgNode('rect', { x: side === 'right' ? W * 0.75 : 0,
        y: side === 'bottom' ? H * 0.75 : 0, width: vertical ? W : W * 0.25,
        height: vertical ? H * 0.25 : H, fill: `url(#fade-${side})` }));
    }
    const positions = { bottom: [0.14, 0.125, 0.10, 0.07], top: [0.88, 0.862, 0.83, 0.80], center: [0.56, 0.542, 0.51, 0.48] };
    const [cityY, sepY, countryY, coordsY] = positions[state.text_position];
    const text = (content, x, y, fontSize, weight, extra = {}) => {
      const node = svgNode('text', { x, y, fill: colors.text, 'font-family': 'Roboto',
        'font-size': fontSize, 'font-weight': weight, 'text-anchor': 'middle', ...extra }, content);
      node.setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
      overlay.append(node);
    };
    if (!state.no_title) {
      text(titleText(state.city), W / 2, H * (1 - cityY), titleSize(state.city, width, height), 700);
      overlay.append(svgNode('line', { x1: W * 0.4, x2: W * 0.6, y1: H * (1 - sepY), y2: H * (1 - sepY),
        stroke: colors.text, 'stroke-width': scale }));
      text(state.country.toUpperCase(), W / 2, H * (1 - countryY), 22 * scale, 300);
    }
    if (!state.no_coords && state.latitude != null && state.longitude != null) {
      text(formatCoordinates(state.latitude, state.longitude), W / 2, H * (1 - coordsY), 14 * scale, 400, { opacity: 0.7 });
    }
    text('© OpenMapTiles © OpenStreetMap contributors · aesthetimap.rastiegaiev.com', W * 0.98, H * 0.98,
      8 * scale, 300, { 'text-anchor': 'end', opacity: 0.7 });
    if (state.show_heart) text('♥', W / 2, H / 2, 50 * scale, 400, { fill: '#FF2A2A', 'dominant-baseline': 'central' });
  }

  function caption() {
    const spanLabel = state.span >= 1000 ? `${state.span / 1000} km` : `${state.span} m`;
    byId('caption-figures').textContent = `${state.width} × ${state.height} in   ${spanLabel}   1:${scaleRatio(state.span, state.width, state.height).toLocaleString('en-US')}`;
    byId('detail-note').textContent = state.span > 12000 ? 'The finished poster has finer streets' : '';
    byId('contours-note').textContent = state.show_contours ? 'Contours appear in the finished poster' : '';
    const ruler = byId('scale-ruler');
    const bar = niceScaleBar(state.span, state.width, state.height, size.width);
    const barWidth = bar.pixels;
    ruler.setAttribute('viewBox', `0 0 ${barWidth + 8} 32`);
    ruler.setAttribute('width', barWidth + 8);
    ruler.setAttribute('height', 32);
    ruler.setAttribute('aria-label', bar.label);
    ruler.replaceChildren(svgNode('path', { d: `M4 16v8m0-4h${barWidth}m0-4v8`, fill: 'none', stroke: 'currentColor' }),
      svgNode('text', { x: 4, y: 12, fill: 'currentColor', 'font-size': 12 }, bar.label));
  }

  function limits() {
    if (!map) return;
    const lat = latitude();
    // Reset both ends first so a large aspect-ratio change cannot cross old limits.
    map.setMinZoom(-2);
    map.setMaxZoom(24);
    map.setMinZoom(zoomForSpan(config.max_span, lat, longSide()));
    map.setMaxZoom(zoomForSpan(500, lat, longSide()));
  }

  function syncMap(fly = false) {
    if (!map || !size.width) return;
    guard = true;
    limits();
    const key = currentStyleKey();
    // Replacing a style before it has loaded would throw away its tile requests.
    if (key !== styleKey && styleReady) {
      styleKey = key;
      map.setStyle(mapStyle(), { diff: true });
    }
    const camera = { center: [state.longitude ?? 30.5234, latitude()], zoom: zoomForSpan(state.span, latitude(), longSide()) };
    flying = false;
    if (fly && !reducedMotion.matches) {
      map.stop();
      flying = true;
      map.flyTo({ ...camera, duration: 600 });
    } else map.jumpTo(camera);
    guard = false;
  }

  function startMap() {
    try {
      styleKey = currentStyleKey();
      map = new LibreMap({ container: canvas, style: mapStyle(), center: [state.longitude ?? 30.5234, latitude()],
        zoom: zoomForSpan(state.span, latitude(), longSide()), pixelRatio: devicePixelRatio / K,
        attributionControl: false, dragRotate: false, touchPitch: false, pitchWithRotate: false,
        maxPitch: 0,
        cooperativeGestures: matchMedia('(max-width: 959px), (pointer: coarse)').matches });
      map.keyboard.disableRotation();
      map.touchZoomRotate.disableRotation();
      map.on('sourcedata', event => {
        if (event.sourceId === 'map' && event.sourceDataType === 'metadata') {
          metadataLoaded = true;
          failed = false;
          note();
        }
      });
      map.on('error', event => {
        // Tile errors carry a tile object. A failed source before metadata is fatal.
        if (event.sourceId === 'map' && !event.tile && !metadataLoaded) { failed = true; note(); }
      });
      map.once('load', () => { styleReady = true; syncMap(); });
      map.once('idle', () => performance.mark('preview-idle'));
      map.on('webglcontextlost', () => { failed = true; note(); });
      // Wheel zoom ends without an original event, so our own moves are marked instead.
      map.on('moveend', () => {
        if (guard || generating) return;
        if (flying) { flying = false; return; }
        const center = map.getCenter().wrap();
        const lat = Number(center.lat.toFixed(4));
        const lon = Number(center.lng.toFixed(4));
        const span = Math.min(config.max_span, Math.max(500, Math.round(spanForZoom(map.getZoom(), lat, longSide()) / 100) * 100));
        state = { ...state, latitude: lat, longitude: lon, span };
        onMapChange({ latitude: lat, longitude: lon, span });
        syncMap();
        lettering(); caption(); note();
      });
      syncMap();
    } catch (error) {
      console.warn('Preview unavailable', error);
      failed = true;
      note();
    }
  }

  function resize() {
    const stage = byId('preview-stage');
    const padding = getComputedStyle(stage);
    const next = framedSheetSize(Math.max(0, stage.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight)),
      Math.max(0, stage.clientHeight - parseFloat(padding.paddingTop) - parseFloat(padding.paddingBottom)), state.width, state.height);
    if (!next.width || !next.height) return;
    size = next;
    sheet.style.width = `${size.width}px`;
    sheet.style.height = `${size.height}px`;
    const wrap = byId('sheet-wrap');
    wrap.style.setProperty('--mat', `${size.mat}px`);
    wrap.style.setProperty('--frame', `${size.frame}px`);
    wrap.style.width = `${size.width + 2 * (size.mat + size.frame)}px`;
    wrap.style.height = `${size.height + 2 * (size.mat + size.frame)}px`;
    K = detailFactor(size.width, size.height);
    canvas.style.setProperty('--k', K);
    canvas.style.width = `${size.width * K}px`;
    canvas.style.height = `${size.height * K}px`;
    canvas.style.transform = `scale(${1 / K})`;
    sheet.style.backgroundColor = state.colors.bg;
    if (!map && !failed) startMap();
    else if (map) {
      guard = true;
      map.setPixelRatio(devicePixelRatio / K);
      map.resize();
      syncMap();
      guard = false;
    }
    lettering(); caption(); note();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(byId('preview-stage'));
  window.addEventListener('resize', resize);
  resize();
  return {
    update(next, { fly = false } = {}) {
      const dimensionsChanged = state.width !== next.width || state.height !== next.height;
      state = next;
      sheet.style.backgroundColor = state.colors.bg;
      if (dimensionsChanged) resize();
      else { syncMap(fly); lettering(); caption(); note(); }
    },
    setGenerating(value) {
      generating = value;
      sheet.classList.toggle('generating', value);
      canvas.inert = value;
      if (value && map) map.stop();
    },
    destroy() { observer.disconnect(); window.removeEventListener('resize', resize); map?.remove(); },
  };
}
