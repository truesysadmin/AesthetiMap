import { Map as LibreMap, setWorkerUrl } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { detailFactor, zoomForSpan, spanForZoom, framedSheetSize, strokePx, scaleRatio, niceScaleBar, titleText, titleSize, formatCoordinates } from './preview-math.js';
import { LABEL_SIZE, LINE_WIDTH, shapes, resolveColor, labelOffset, estimatePosition } from './markers.js';
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

export function createPreview({ initial, config, onMapChange, onMarkersChange, onSelectMarker, onOutsideMarkers }) {
  setWorkerUrl(workerUrl);
  let state = initial;
  let map;
  let size = { width: 0, height: 0 };
  let guard = false;
  let flying = false;
  let failed = false;
  let metadataLoaded = false;
  let generating = false;
  let result = false;
  let selected = -1;
  let marksGroup;
  let drag;
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
    marksGroup = svgNode('g', { 'data-marks': '' });
    overlay.append(marksGroup);
    drawMarks();
  }

  function markerPosition(marker) {
    if (marker.lat == null) return { x: state.width * 36, y: state.height * 36 };
    if (map && !failed && size.width) {
      const point = map.project([marker.lon, marker.lat]);
      return { x: point.x / K / size.width * state.width * 72, y: point.y / K / size.height * state.height * 72 };
    }
    return estimatePosition(marker, state);
  }

  function drawMarks() {
    if (!marksGroup) return;
    const W = state.width * 72, H = state.height * 72, scale = Math.min(state.width, state.height) / 12;
    const markers = state.markers || [], positions = markers.map(markerPosition);
    const outside = positions.map(point => point.x < 0 || point.y < 0 || point.x > W || point.y > H);
    marksGroup.replaceChildren();
    const labels = svgNode('g', { 'pointer-events': 'none' });
    if (state.connect && markers.length >= 2) {
      marksGroup.append(svgNode('path', { d: positions.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' '),
        fill: 'none', stroke: state.colors.text, opacity: 0.75, 'stroke-width': LINE_WIDTH * scale,
        'stroke-linecap': 'round', 'stroke-dasharray': `${0.01 * LINE_WIDTH * scale} ${2.6 * LINE_WIDTH * scale}` }));
    }
    markers.forEach((marker, index) => {
      if (outside[index]) return;
      const shape = shapes[marker.type], point = positions[index], color = resolveColor(marker.color, state.colors);
      // A large unselected mark would block dragging the map; it is picked in the panel.
      const interactive = !generating && !result && (marker.size <= 160 || selected === index);
      const group = svgNode('g', { transform: `translate(${point.x} ${point.y})`, 'data-marker': index,
        class: `${interactive ? 'mark-interactive' : ''}${selected === index && interactive ? ' mark-selected' : ''}`,
        ...(interactive ? { tabindex: 0, role: 'button', 'aria-label': `${shape.name} mark${marker.label ? `, ${marker.label}` : ''}` } : {}) });
      const factor = marker.size * scale / 100;
      const pathAttrs = { d: shape.d, transform: `scale(${factor}) translate(${-shape.anchor[0]} ${-shape.anchor[1]})`, fill: color, opacity: marker.opacity };
      if (marker.opacity >= 0.6 && marker.size <= 160) group.append(svgNode('path', { ...pathAttrs, fill: 'none', stroke: state.colors.bg,
        'stroke-width': 0.07 * marker.size * scale / factor, 'stroke-linejoin': 'round' }));
      group.append(svgNode('path', pathAttrs));
      if (marker.label) labels.append(svgNode('text', { x: point.x, y: point.y + labelOffset(shape, marker.size, scale),
        fill: color, 'font-family': 'Roboto', 'font-size': LABEL_SIZE * scale, 'font-weight': 400, 'text-anchor': 'middle',
        stroke: state.colors.bg, 'stroke-width': 3 * scale, 'stroke-linejoin': 'round', 'paint-order': 'stroke fill' }, marker.label));
      if (interactive) {
        const least = 32 * W / Math.max(size.width, 1), hit = Math.max(least, marker.size * scale);
        // The hit box includes the whole shape and remains at least 32 screen pixels.
        const hitX = -shape.anchor[0] * factor - Math.max(0, hit - marker.size * scale) / 2;
        const hitY = -shape.anchor[1] * factor - Math.max(0, hit - marker.size * scale) / 2;
        group.append(svgNode('rect', { x: hitX, y: hitY, width: hit, height: hit, fill: 'transparent' }),
          svgNode('rect', { x: hitX - 3, y: hitY - 3, width: hit + 6, height: hit + 6, rx: 4, class: 'mark-focus' }));
      }
      marksGroup.append(group);
    });
    marksGroup.append(labels);
    onOutsideMarkers?.(outside);
  }

  function editMarker(index, changes) {
    state = { ...state, markers: state.markers.map((marker, i) => i === index ? { ...marker, ...changes } : marker) };
    drawMarks(); onMarkersChange?.(state.markers);
  }
  overlay.addEventListener('pointerdown', event => {
    const target = event.target.closest('[data-marker]');
    if (!target || generating || result) return;
    const index = Number(target.dataset.marker);
    selected = index; onSelectMarker?.(index);
    if (!map || failed) return;
    event.preventDefault(); event.stopPropagation();
    const point = markerPosition(state.markers[index]), rect = sheet.getBoundingClientRect();
    drag = { index, pointerId: event.pointerId, panEnabled: map.dragPan.isEnabled(),
      offsetX: point.x / (state.width * 72) * rect.width - (event.clientX - rect.left),
      offsetY: point.y / (state.height * 72) * rect.height - (event.clientY - rect.top) };
    map.dragPan.disable(); overlay.setPointerCapture(event.pointerId);
  });
  overlay.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    event.preventDefault(); event.stopPropagation();
    const rect = sheet.getBoundingClientRect();
    const place = map.unproject([(event.clientX - rect.left + drag.offsetX) * K, (event.clientY - rect.top + drag.offsetY) * K]).wrap();
    editMarker(drag.index, { lat: Number(Math.max(-85, Math.min(85, place.lat)).toFixed(5)), lon: Number(place.lng.toFixed(5)) });
  });
  function endDrag() {
    if (!drag) return;
    if (overlay.hasPointerCapture(drag.pointerId)) overlay.releasePointerCapture(drag.pointerId);
    if (drag.panEnabled) map.dragPan.enable();
    const index = drag.index; drag = undefined;
    marksGroup?.querySelector(`[data-marker="${index}"]`)?.focus();
  }
  overlay.addEventListener('pointerup', endDrag);
  overlay.addEventListener('pointercancel', endDrag);
  overlay.addEventListener('keydown', event => {
    const target = event.target.closest('[data-marker]');
    if (!target || generating || result) return;
    const index = Number(target.dataset.marker);
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault(); state = { ...state, markers: state.markers.filter((_, i) => i !== index) };
      onMarkersChange?.(state.markers); drawMarks(); return;
    }
    if (['Enter', ' '].includes(event.key)) { event.preventDefault(); selected = index; onSelectMarker?.(index); drawMarks(); marksGroup?.querySelector(`[data-marker="${index}"]`)?.focus(); }
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
    if (!moves[event.key]) return;
    event.preventDefault();
    const marker = state.markers[index], [x, y] = moves[event.key], lat = marker.lat ?? latitude(), lon = marker.lon ?? state.longitude ?? 30.5234;
    editMarker(index, { lat: Number(Math.max(-85, Math.min(85, lat + y * state.span * 0.01 / 111320)).toFixed(5)),
      lon: Number(Math.max(-180, Math.min(180, lon + x * state.span * 0.01 / (111320 * Math.cos(lat * Math.PI / 180)))).toFixed(5)) });
    marksGroup?.querySelector(`[data-marker="${index}"]`)?.focus();
  });

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
      map.on('move', drawMarks);
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
      if (value) endDrag();
      drawMarks();
      sheet.classList.toggle('generating', value);
      canvas.inert = value;
      if (value && map) map.stop();
    },
    setSelected(index) { selected = index; drawMarks(); },
    setResult(value) { result = value; if (value) endDrag(); drawMarks(); },
    destroy() { observer.disconnect(); window.removeEventListener('resize', resize); map?.remove(); },
  };
}
