import '@fontsource/instrument-serif/400.css';
import { galleryPalette } from './palette.js';
import '@fontsource/instrument-sans/400.css';
import '@fontsource/instrument-sans/500.css';
import '@fontsource/instrument-sans/600.css';
import '@fontsource/ibm-plex-mono/400.css';
import { svgNode } from './theme-thumbnail.js';
import { parseCoordinate } from './preview-math.js';

const $ = id => document.getElementById(id);
const flags = ['no_title', 'no_coords', 'gradient_tb', 'gradient_lr', 'show_buildings', 'show_contours', 'show_heart'];
const inputId = key => key.replaceAll('_', '-');
const posterPathPattern = /^\/api\/posters\/[^/?#\\]+\.(png|svg|pdf)$/;
const fallbackColors = { bg: '#F5EDE4', text: '#8B4513', gradient_color: '#F5EDE4', water: '#A8C4C4', parks: '#E8E0D0',
  building: '#E5C4B0', road_motorway: '#A0522D', road_primary: '#B8653A', road_secondary: '#C9846A',
  road_tertiary: '#D9A08A', road_residential: '#E5C4B0', road_default: '#D9A08A' };
const defaults = { city: 'Kyiv', country: 'Ukraine', latitude: 50.4501, longitude: 30.5234, span: 20000,
  width: 12, height: 16, theme: 'terracotta', format: 'png', text_position: 'bottom',
  ...Object.fromEntries(flags.map(key => [key, false])) };

async function httpError(response) {
  let message = `Request failed (${response.status})`;
  if (response.status === 503) {
    const body = await response.json().catch(() => ({}));
    if (typeof body?.detail === 'string') message = body.detail;
  } else if (response.status === 422) message = 'Please check the form values.';
  else if (response.status === 429) message = 'Too many requests. Please wait a minute.';
  return new Error(message);
}

async function init() {
  const params = new URLSearchParams(location.search);
  let state = { ...defaults, colors: fallbackColors };
  let preview;
  let currentObjectUrl;
  const autocompleteCoordinates = { latitude: !params.size, longitude: !params.size };
  let editingTimer;
  let searchTimer;
  let searchController;
  let running = false;
  let editVersion = 0;
  $('current-year').textContent = new Date().getFullYear();
  if (params.size) {
    state.latitude = null;
    state.longitude = null;
    for (const key of ['city', 'country', 'theme', 'format', 'text_position']) {
      if (params.has(key)) state[key] = params.get(key).slice(0, 80);
    }
    for (const key of ['span', 'width', 'height']) {
      const value = params.get(key);
      if (!value?.trim() || !Number.isFinite(Number(value))) continue;
      const limits = { span: [500, 50000], width: [2, 40], height: [2, 40] }[key];
      state[key] = Math.min(limits[1], Math.max(limits[0], Number(value)));
    }
    state.latitude = parseCoordinate(params.get('latitude'), 85);
    state.longitude = parseCoordinate(params.get('longitude'), 180);
    for (const key of flags) state[key] = params.get(key) === 'true';
  }
  if (params.has('span')) $('span').step = '100';
  if (!['png', 'svg', 'pdf'].includes(state.format)) state.format = 'png';
  if (!['bottom', 'center', 'top'].includes(state.text_position)) state.text_position = 'bottom';
  for (const key of ['city', 'country', 'latitude', 'longitude', 'span', 'width', 'height', 'format', 'text_position']) {
    $(inputId(key)).value = state[key] ?? '';
    // Keep the coordinate text of a link as written.
    if (['latitude', 'longitude'].includes(key) && params.get(key)) $(key).value = params.get(key).slice(0, 40);
  }
  for (const key of flags) $(inputId(key)).checked = state[key];
  const syncSegments = () => {
    for (const key of ['format', 'text-position']) {
      document.querySelectorAll(`input[name="${key}-choice"]`).forEach(radio => { radio.checked = radio.value === $(key).value; });
    }
  };
  function applyGallery() {
    const palette = galleryPalette(state.colors);
    const root = document.documentElement.style;
    for (const [key, value] of Object.entries(palette)) {
      if (key === 'frame') {
        for (const [side, color] of Object.entries(value)) root.setProperty(`--frame-${side}`, color);
      } else root.setProperty(`--${(key === 'mat' ? 'matColor' : key).replace(/[A-Z]/g, char => `-${char.toLowerCase()}`)}`, value);
    }
    $('label-city').textContent = state.city;
    $('label-sub').textContent = `${state.country}, ${state.themeName || state.theme.replaceAll('_', ' ')}`;
  }
  applyGallery();
  syncSegments();
  const coverageLabel = () => { $('span-val').textContent = Number($('span').value) >= 1000 ? `${Number($('span').value) / 1000} km` : `${$('span').value} m`; };
  coverageLabel();

  function revokePosterUrl() {
    if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = undefined;
  }
  function edit() {
    editVersion++;
    $('sheet').classList.remove('has-result');
    $('preview-panel').classList.remove('has-result-panel');
    $('result-img').classList.add('hidden');
    $('result-img').removeAttribute('src');
    $('action-bar').classList.add('hidden');
    if (!running) $('preview-status').textContent = '';
    revokePosterUrl();
  }
  $('edit-btn').addEventListener('click', edit);

  function expired() {
    edit();
    $('preview-status').textContent = 'This poster has expired. Generate it again.';
  }
  $('result-img').addEventListener('error', expired);
  function showPoster(url, format, filename) {
    $('sheet').classList.toggle('has-result', format !== 'pdf');
    $('preview-panel').classList.add('has-result-panel');
    $('result-img').classList.toggle('hidden', format === 'pdf');
    if (format !== 'pdf') $('result-img').src = url;
    $('preview-status').textContent = format === 'pdf' ? 'Your PDF is ready' : '';
    $('download-btn').href = url;
    $('download-btn').download = filename;
    $('download-btn').textContent = `Download ${format.toUpperCase()}`;
    $('action-bar').classList.remove('hidden');
  }
  function setCopyLink(path) {
    const button = $('copy-link-btn');
    button.textContent = 'Copy link';
    button.onclick = async () => {
      const shareParams = new URLSearchParams(location.search);
      shareParams.set('img', path);
      try {
        await navigator.clipboard.writeText(`${location.origin}${location.pathname}?${shareParams}`);
        button.textContent = 'Copied';
        setTimeout(() => { button.textContent = 'Copy link'; }, 2000);
      } catch { $('preview-status').textContent = 'Could not copy the link'; }
    };
  }

  let config = { tiles_url: 'https://tiles.openfreemap.org/planet', max_span: 50000 };
  let themes = [{ id: 'terracotta', name: 'Terracotta', colors: fallbackColors }];
  const loaded = await Promise.allSettled([
    fetch('/api/config').then(async res => { if (!res.ok) throw await httpError(res); return res.json(); }),
    fetch('/api/themes').then(async res => { if (!res.ok) throw await httpError(res); return res.json(); }),
  ]);
  if (loaded[0].status === 'fulfilled') config = loaded[0].value;
  if (loaded[1].status === 'fulfilled' && loaded[1].value.themes?.length) themes = loaded[1].value.themes;
  $('span').max = config.max_span;
  state.span = Math.min(config.max_span, state.span);
  $('span').value = state.span;
  coverageLabel();
  if (!themes.some(theme => theme.id === state.theme)) state.theme = 'terracotta';
  if (!themes.some(theme => theme.id === state.theme)) state.theme = themes[0].id;
  $('theme').replaceChildren();
  for (const theme of themes) {
    const option = document.createElement('option');
    option.value = theme.id; option.textContent = theme.name;
    $('theme').append(option);
    const label = document.createElement('label');
    label.className = 'theme-option';
    label.title = `${theme.name}${theme.description ? ` — ${theme.description}` : ''}`;
    const radio = document.createElement('input');
    radio.type = 'radio'; radio.name = 'theme-choice'; radio.value = theme.id;
    radio.setAttribute('aria-label', theme.name);
    radio.checked = theme.id === state.theme;
    const colors = { ...fallbackColors, ...theme.colors };
    let artwork = thumbnail(colors);
    if (/^[a-z0-9_-]+$/.test(theme.id)) {
      artwork = document.createElement('img');
      artwork.alt = ''; artwork.loading = 'lazy'; artwork.decoding = 'async';
      artwork.width = 168; artwork.height = 224;
      artwork.addEventListener('error', () => artwork.replaceWith(thumbnail(colors)), { once: true });
      artwork.src = `/themes/${theme.id}.webp`;
    }
    label.append(radio, artwork);
    $('theme-grid').append(label);
  }
  $('theme').value = state.theme;
  function selectedTheme() {
    const theme = themes.find(item => item.id === $('theme').value) || themes[0];
    state.theme = theme.id;
    state.themeName = theme.name;
    state.colors = { ...fallbackColors, ...theme.colors };
    $('selected-theme').textContent = theme.name;
    applyGallery();
  }
  selectedTheme();

  function readForm() {
    state.city = $('city').value;
    state.country = $('country').value;
    state.latitude = parseCoordinate($('latitude').value, 85);
    state.longitude = parseCoordinate($('longitude').value, 180);
    for (const key of ['span', 'width', 'height']) {
      const el = $(key);
      const value = Number(el.value);
      const limits = { span: [500, config.max_span], width: [2, 40], height: [2, 40] }[key];
      if (el.value.trim() && Number.isFinite(value) && value >= limits[0] && value <= limits[1]) state[key] = value;
    }
    for (const key of flags) state[key] = $(inputId(key)).checked;
    state.format = $('format').value;
    state.text_position = $('text-position').value;
    selectedTheme(); coverageLabel();
  }
  const update = (fly = false) => { readForm(); preview?.update({ ...state }, { fly }); };
  $('generate-form').addEventListener('input', event => {
    if (event.target.name === 'theme-choice') $('theme').value = event.target.value;
    if (event.target.name === 'format-choice') $('format').value = event.target.value;
    if (event.target.name === 'text-position-choice') $('text-position').value = event.target.value;
    if (['latitude', 'longitude'].includes(event.target.id)) autocompleteCoordinates[event.target.id] = false;
    if (['city', 'country'].includes(event.target.id)) {
      for (const key of ['latitude', 'longitude']) {
        if (autocompleteCoordinates[key]) { $(key).value = ''; state[key] = null; }
        autocompleteCoordinates[key] = false;
      }
    }
    edit();
    $('panel-error').textContent = '';
    coverageLabel();
    clearTimeout(editingTimer);
    editingTimer = setTimeout(() => update(), 150);
  });
  // Keep select-based callers and old links compatible with segmented radios.
  $('generate-form').addEventListener('change', event => {
    if (['format', 'text-position', 'theme'].includes(event.target.id)) { syncSegments(); edit(); update(); }
  });

  // Loading the map bundle must not delay the generation form.
  import('./preview.js').then(({ createPreview }) => {
    readForm();
    preview = createPreview({ initial: { ...state }, config, onMapChange(values) {
      clearTimeout(editingTimer);
      autocompleteCoordinates.latitude = false; autocompleteCoordinates.longitude = false;
      for (const key of ['latitude', 'longitude']) $(key).value = values[key].toFixed(4);
      $('span').step = '100'; $('span').value = values.span;
      state = { ...state, ...values }; coverageLabel(); edit();
    } });
    preview.setGenerating(running);
  }).catch(error => {
    console.warn('Preview unavailable', error);
    $('sheet').style.backgroundColor = state.colors.bg;
    $('preview-note').textContent = 'Preview is unavailable. You can still create the poster';
    $('preview-note').classList.remove('hidden');
  });

  const list = $('autocomplete-list');
  const closeList = () => { list.classList.add('hidden'); $('city').setAttribute('aria-expanded', 'false'); };
  function choose(place) {
    $('city').value = String(place.name).slice(0, 80);
    $('country').value = String(place.country || '').slice(0, 80);
    $('latitude').value = place.latitude; $('longitude').value = place.longitude;
    autocompleteCoordinates.latitude = true; autocompleteCoordinates.longitude = true;
    clearTimeout(editingTimer); edit(); closeList(); update(true);
  }
  async function geocode(query, signal) {
    const response = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=5&language=en&format=json`, { signal });
    if (!response.ok) throw await httpError(response);
    return (await response.json()).results || [];
  }
  $('city').addEventListener('input', () => {
    clearTimeout(searchTimer); searchController?.abort(); closeList();
    const query = $('city').value;
    if (query.length < 2) return;
    searchTimer = setTimeout(async () => {
      searchController = new AbortController();
      try {
        const places = await geocode(query, searchController.signal);
        if ($('city').value !== query) return;
        list.replaceChildren();
        for (const place of places) {
          const item = document.createElement('button');
          item.type = 'button'; item.className = 'autocomplete-item';
          item.setAttribute('role', 'option');
          item.textContent = `${place.name}${place.admin1 ? `, ${place.admin1}` : ''} (${place.country || ''})`;
          item.addEventListener('click', () => choose(place));
          list.append(item);
        }
        list.classList.toggle('hidden', !places.length);
        $('city').setAttribute('aria-expanded', String(!!places.length));
      } catch (error) { if (error.name !== 'AbortError') console.warn('Geocoding unavailable', error); }
    }, 300);
  });
  $('city').addEventListener('keydown', event => {
    if (event.key === 'Escape') closeList();
    if (event.key === 'ArrowDown' && !list.classList.contains('hidden')) { event.preventDefault(); list.firstElementChild?.focus(); }
  });
  list.addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeList(); $('city').focus(); }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      (event.key === 'ArrowDown' ? document.activeElement.nextElementSibling : document.activeElement.previousElementSibling)?.focus();
    }
  });
  document.addEventListener('click', event => { if (!list.contains(event.target) && event.target !== $('city')) closeList(); });
  if (params.size && !params.get('latitude') && !params.get('longitude') && state.city) {
    const version = editVersion;
    const country = state.country;
    geocode(state.city).then(places => {
      // A namesake in another country would make the wrong map.
      const place = places.find(item => !country || item.country?.toLowerCase() === country.toLowerCase());
      if (place && version === editVersion) {
        $('latitude').value = place.latitude; $('longitude').value = place.longitude;
        autocompleteCoordinates.latitude = true; autocompleteCoordinates.longitude = true; update();
      }
    }).catch(error => { console.warn('Geocoding unavailable', error); });
  }

  const sharedPoster = params.get('img');
  if (sharedPoster && posterPathPattern.test(sharedPoster) && editVersion === 0) {
    const format = sharedPoster.split('.').pop();
    showPoster(sharedPoster, format, `${state.city}_${state.theme}.${format}`);
    setCopyLink(sharedPoster);
    if (format === 'pdf') {
      const version = editVersion;
      fetch(sharedPoster).then(res => { if (!res.ok && editVersion === version) expired(); })
        .catch(() => { if (editVersion === version) expired(); });
    }
  }

  $('generate-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (running) return;
    clearTimeout(editingTimer); update(); edit();
    const reqData = Object.fromEntries(Object.keys(defaults).filter(key => !['latitude', 'longitude'].includes(key)).map(key => [key, state[key]]));
    // Preserve the backend's support for coordinate strings and its error messages.
    if ($('latitude').value.trim() && $('longitude').value.trim()) {
      reqData.latitude = $('latitude').value; reqData.longitude = $('longitude').value;
    }
    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(reqData)) searchParams.set(key, value);
    history.replaceState({}, '', `${location.pathname}?${searchParams}`);
    running = true;
    $('submit-btn').disabled = true;
    $('submit-btn').querySelector('.btn-text').textContent = 'Creating poster';
    $('panel-error').textContent = '';
    $('preview-status').textContent = 'Connecting';
    $('progress-bar').style.width = '0%';
    $('progress-track').classList.remove('hidden');
    $('progress-track').setAttribute('aria-valuenow', '0');
    preview?.setGenerating(true);
    $('sheet').classList.add('generating');
    const version = editVersion;
    try {
      const response = await fetch('/api/generate_map_stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(reqData) });
      if (!response.ok) throw await httpError(response);
      if (!response.body) throw new Error('The connection was lost. Please try again.');
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let chunk = '', completed = false;
      async function handleLine(line) {
        if (!line.trim()) return;
        let data;
        try { data = JSON.parse(line); } catch (error) { console.warn('Invalid stream JSON', error); return; }
        if (data.type === 'progress') {
          $('preview-status').textContent = data.message;
          const percent = Number(data.percent);
          if (data.percent != null && Number.isFinite(percent)) {
            const value = Math.min(100, Math.max(0, percent));
            $('progress-bar').style.width = `${value}%`;
            $('progress-track').setAttribute('aria-valuenow', value);
          }
        } else if (data.type === 'log') console.log(data.message);
        else if (data.type === 'error') throw new Error(data.message);
        else if (data.type === 'done') {
          if (typeof data.url !== 'string' || !posterPathPattern.test(data.url)) throw new Error('Invalid poster URL.');
          $('preview-status').textContent = 'Downloading poster to your browser';
          const imageResponse = await fetch(data.url);
          if (!imageResponse.ok) throw await httpError(imageResponse);
          const blob = await imageResponse.blob();
          if (version === editVersion) {
            revokePosterUrl(); currentObjectUrl = URL.createObjectURL(blob);
            showPoster(currentObjectUrl, reqData.format, `${reqData.city}_${reqData.theme}.${reqData.format}`);
            setCopyLink(data.url);
          } else $('preview-status').textContent = '';
          completed = true;
        }
      }
      try {
        while (!completed) {
          const { value, done } = await reader.read();
          chunk += decoder.decode(value, { stream: !done });
          const lines = chunk.split('\n'); chunk = lines.pop();
          if (done && chunk.trim()) lines.push(chunk);
          for (const line of lines) { await handleLine(line); if (completed) break; }
          if (done) break;
        }
        if (!completed) throw new Error('The connection was lost. Please try again.');
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
    } catch (error) {
      $('panel-error').textContent = error.message;
      $('preview-status').textContent = '';
      $('action-bar').classList.add('hidden');
    } finally {
      running = false;
      preview?.setGenerating(false);
      $('sheet').classList.remove('generating');
      $('progress-track').classList.add('hidden');
      $('submit-btn').disabled = false;
      $('submit-btn').querySelector('.btn-text').textContent = 'Create poster';
    }
  });
}

function thumbnail(colors) {
  const svg = svgNode('svg', { viewBox: '0 0 36 48', 'aria-hidden': 'true' });
  svg.append(svgNode('rect', { width: 36, height: 48, fill: colors.bg }),
    svgNode('path', { d: 'M24 0L20 12 26 24 22 34 36 38V0Z', fill: colors.water }),
    svgNode('path', { d: 'M2 4H12L15 14 4 18Z', fill: colors.parks }));
  ['M0 30L36 8', 'M8 0L20 36', 'M0 20L36 28', 'M2 36L32 0', 'M0 10L20 18', 'M12 22L8 36', 'M16 0L36 14', 'M24 30L36 34'].forEach((d, i) => {
    const keys = ['motorway', 'primary', 'secondary', 'tertiary', 'residential', 'default', 'residential', 'default'];
    svg.append(svgNode('path', { d, stroke: colors[`road_${keys[i]}`], 'stroke-width': [1.2, 1, 0.8, 0.6, 0.4, 0.4, 0.4, 0.4][i], fill: 'none' }));
  });
  svg.append(svgNode('rect', { x: 8, y: 39, width: 20, height: 1, fill: colors.text }),
    svgNode('rect', { x: 12, y: 42, width: 12, height: 0.5, fill: colors.text }));
  return svg;
}
init().catch(error => { console.error(error); $('panel-error').textContent = 'Could not start the form. Please reload the page.'; });
