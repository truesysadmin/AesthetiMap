import { shapes, defaultMarker, normalizeMarkers, resolveColor, estimatePosition, sizeForPosition, positionForSize } from './markers.js';
const NS = 'http://www.w3.org/2000/svg';
function node(tag, attrs = {}, text) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  if (text !== undefined) el.textContent = text;
  return el;
}
function icon(type, color) {
  const svg = document.createElementNS(NS, 'svg'); svg.setAttribute('viewBox', '0 0 100 100'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(NS, 'path'); path.setAttribute('d', shapes[type].d); path.setAttribute('fill', color); svg.append(path); return svg;
}
export function createMarksPanel({ getState, onChange, onSelect }) {
  const list = document.getElementById('marks-list'), add = document.getElementById('add-mark');
  const join = document.getElementById('connect-marks'), joinLabel = join.closest('label');
  let selected = -1;
  let outside = [];
  function change(index, changes, rerender = true) {
    const state = getState();
    const markers = state.markers.map((marker, i) => i === index ? { ...marker, ...changes } : marker);
    onChange(normalizeMarkers(markers), state.connect);
    if (rerender) render();
  }
  function remove(index) {
    const state = getState();
    const markers = state.markers.filter((_, i) => i !== index);
    selected = Math.min(selected, markers.length - 1);
    onChange(markers, state.connect); onSelect(selected); render();
  }
  function select(index) { selected = index; onSelect(index); render(); }
  function render() {
    const state = getState(); list.replaceChildren();
    state.markers.forEach((marker, index) => {
      const item = node('div', { class: `mark-item${index === selected ? ' selected' : ''}` });
      const row = node('div', { class: 'mark-row' });
      const pick = node('button', { type: 'button', class: 'mark-select', 'aria-expanded': String(index === selected) });
      const tile = node('span', { class: 'mark-tile' }); tile.append(icon(marker.type, resolveColor(marker.color, state.colors)));
      const name = node('span'); name.append(node('span', {}, marker.label || shapes[marker.type].name));
      const position = estimatePosition(marker, state);
      const isOutside = outside[index] ?? (position.x < 0 || position.x > state.width * 72 || position.y < 0 || position.y > state.height * 72);
      if (marker.lat != null && isOutside) name.append(node('small', { class: 'mark-outside' }, 'Outside the map'));
      pick.append(tile, name); pick.addEventListener('click', () => select(index));
      const del = node('button', { type: 'button', class: 'mark-remove', 'aria-label': `Remove ${marker.label || shapes[marker.type].name} mark` }, '×');
      del.addEventListener('click', () => remove(index)); row.append(pick, del); item.append(row);
      if (index === selected) {
        const editor = node('div', { class: 'mark-editor' });
        const picker = node('div', { class: 'mark-shapes', role: 'radiogroup', 'aria-label': 'Shape' });
        for (const [type, shape] of Object.entries(shapes)) {
          const button = node('button', { type: 'button', role: 'radio', 'aria-checked': String(marker.type === type), 'aria-label': shape.name, title: shape.name });
          button.append(icon(type, resolveColor(marker.color, state.colors))); button.onclick = () => change(index, { type });
          button.onkeydown = event => {
            if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
            event.preventDefault();
            const types = Object.keys(shapes), next = (types.indexOf(type) + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + types.length) % types.length;
            change(index, { type: types[next] }); list.querySelectorAll('[role="radio"]')[next]?.focus();
          };
          picker.append(button);
        }
        editor.append(picker);
        const label = node('label', { class: 'field' }); label.append(node('span', {}, 'Label'));
        const input = node('input', { type: 'text', maxlength: '40', value: marker.label, 'aria-label': 'Mark label' });
        input.oninput = event => {
          event.stopPropagation(); change(index, { label: input.value }, false);
          name.firstChild.textContent = input.value.trim() || shapes[marker.type].name;
        };
        input.onchange = () => render(); label.append(input); editor.append(label);
        function slider(title, min, max, value, format, apply) {
          const field = node('label', { class: 'field mark-slider' }); const heading = node('span');
          const output = node('output', { class: 'mark-value' }, format(value)); heading.append(document.createTextNode(title), output);
          const range = node('input', { type: 'range', min, max, step: '1', value, 'aria-label': title });
          range.oninput = event => { event.stopPropagation(); output.textContent = format(Number(range.value)); apply(Number(range.value)); };
          range.onchange = () => render(); field.append(heading, range); editor.append(field);
        }
        slider('Size', 0, 100, positionForSize(marker.size), value => `${Math.round(sizeForPosition(value))} pt`, value => change(index, { size: Math.round(sizeForPosition(value)) }, false));
        slider('Opacity', 15, 100, marker.opacity * 100, value => `${Math.round(value)}%`, value => change(index, { opacity: value / 100 }, false));
        const swatches = node('div', { class: 'mark-colors', role: 'group', 'aria-label': 'Mark color' });
        for (const [color, title] of [['text', 'Text'], ['road_motorway', 'Roads'], ['water', 'Water'], ['#C0392B', 'Red']]) {
          const button = node('button', { type: 'button', title, 'aria-label': title, 'aria-pressed': String(marker.color === color) });
          button.style.setProperty('--swatch', resolveColor(color, state.colors)); button.onclick = () => change(index, { color }); swatches.append(button);
        }
        const custom = node('input', { type: 'color', value: /^#/.test(marker.color) ? marker.color : resolveColor(marker.color, state.colors), 'aria-label': 'Custom mark color' });
        custom.oninput = event => { event.stopPropagation(); change(index, { color: custom.value }, false); }; custom.onchange = () => render(); swatches.append(custom); editor.append(swatches);
        if (marker.lat != null) { const center = node('button', { type: 'button', class: 'mark-center' }, 'Center'); center.onclick = () => change(index, { lat: null, lon: null }); editor.append(center); }
        item.append(editor);
      }
      list.append(item);
    });
    add.hidden = state.markers.length >= 5;
    document.getElementById('marks-limit').hidden = state.markers.length < 5;
    joinLabel.hidden = state.markers.length < 2; join.checked = state.connect;
  }
  add.onclick = () => {
    const state = getState(), centered = state.markers.some(marker => marker.lat == null);
    const marker = defaultMarker(centered ? 'pin' : 'heart');
    if (centered) {
      marker.lat = Math.min(85, (state.latitude ?? 50.4501) + state.span * 0.12 / 111320);
      marker.lon = Math.min(180, (state.longitude ?? 30.5234) + state.span * 0.12 / (111320 * Math.cos((state.latitude ?? 50.4501) * Math.PI / 180)));
    }
    selected = state.markers.length; onChange([...state.markers, marker], state.connect); onSelect(selected); render();
  };
  join.onchange = event => { event.stopPropagation(); onChange(getState().markers, join.checked); };
  render();
  return { render, select, remove, setOutside(values) { if (JSON.stringify(values) !== JSON.stringify(outside)) { outside = values; render(); } } };
}
