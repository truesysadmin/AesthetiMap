import '@fontsource/outfit/300.css';
import '@fontsource/outfit/400.css';
import '@fontsource/outfit/600.css';
import '@fontsource/outfit/700.css';

document.addEventListener('DOMContentLoaded', () => {
  console.log('AesthetiMap UI v1.1 Loaded (Buildings & Topography)');
  const form = document.getElementById('generate-form');
  const themeSelect = document.getElementById('theme');
  const spanSlider = document.getElementById('span');
  const spanVal = document.getElementById('span-val');
  const submitBtn = document.getElementById('submit-btn');
  const btnText = submitBtn.querySelector('.btn-text');
  const loader = document.getElementById('loader');
  
  const previewPlaceholder = document.querySelector('.preview-placeholder');
  const resultImg = document.getElementById('result-img');
  const actionBar = document.getElementById('action-bar');
  const downloadBtn = document.getElementById('download-btn');

  document.getElementById('current-year').textContent = new Date().getFullYear();

  let currentObjectUrl;
  const posterPathPattern = /^\/api\/posters\/[^/?#\\]+\.(png|svg|pdf)$/;

  function revokePosterUrl() {
    if (currentObjectUrl) {
      URL.revokeObjectURL(currentObjectUrl);
      currentObjectUrl = undefined;
    }
  }

  async function httpError(response) {
    let message = `Request failed (${response.status})`;
    if (response.status === 503) {
      const body = await response.json().catch(() => ({}));
      if (typeof body?.detail === 'string') message = body.detail;
    } else if (response.status === 422) {
      message = 'Please check the form values.';
    } else if (response.status === 429) {
      message = 'Too many requests. Please wait a minute.';
    }
    return new Error(message);
  }

  function showMessage(message) {
    const paragraph = document.createElement('p');
    paragraph.textContent = message;
    previewPlaceholder.replaceChildren(paragraph);
    previewPlaceholder.classList.remove('hidden');
    resultImg.classList.add('hidden');
  }

  function showExpiredPoster() {
    showMessage('This poster has expired. Generate it again.');
    actionBar.classList.add('hidden');
  }

  resultImg.addEventListener('error', showExpiredPoster);

  function showPoster(url, format, filename) {
    if (format === 'pdf') {
      resultImg.removeAttribute('src');
      showMessage('Your PDF is ready.');
    } else {
      previewPlaceholder.classList.add('hidden');
      resultImg.src = url;
      resultImg.classList.remove('hidden');
    }
    downloadBtn.href = url;
    downloadBtn.download = filename;
    actionBar.classList.remove('hidden');
  }

  function setCopyLink(posterPath) {
    const copyBtn = document.getElementById('copy-link-btn');
    copyBtn.textContent = 'Copy Setup Link';
    copyBtn.onclick = (event) => {
      event.preventDefault();
      const shareParams = new URLSearchParams(window.location.search);
      shareParams.set('img', posterPath);
      const fullUrl = `${window.location.origin}${window.location.pathname}?${shareParams}`;
      navigator.clipboard.writeText(fullUrl).then(() => {
        copyBtn.textContent = 'Copied! ✨';
        setTimeout(() => { copyBtn.textContent = 'Copy Setup Link'; }, 2000);
      }).catch(console.error);
    };
  }

  // Pre-fill fields from URL params
  const params = new URLSearchParams(window.location.search);
  const textFields = ['city', 'country', 'latitude', 'longitude', 'span', 'width', 'height', 'format'];
  textFields.forEach(f => {
    if (params.has(f)) {
      const el = document.getElementById(f);
      if (!el) return;
      const value = params.get(f);
      if (['span', 'width', 'height'].includes(f)) {
        const number = Number(value);
        if (value.trim() && Number.isFinite(number)) {
          el.value = Math.min(Number(el.max), Math.max(Number(el.min), number));
        }
      } else {
        el.value = ['city', 'country'].includes(f) ? value.slice(0, 80) : value;
      }
    }
  });
  if (params.get('no_title') === 'true') {
    document.getElementById('no-title').checked = true;
  }
  if (params.get('no_coords') === 'true') {
    document.getElementById('no-coords').checked = true;
  }
  if (params.get('gradient_tb') === 'true') {
    document.getElementById('gradient-tb').checked = true;
  }
  if (params.get('gradient_lr') === 'true') {
    document.getElementById('gradient-lr').checked = true;
  }
  if (params.has('text_position')) {
    document.getElementById('text-position').value = params.get('text_position');
  }
  if (params.has('span')) {
    spanVal.textContent = spanSlider.value;
  }
  if (params.get('show_buildings') === 'true') {
    document.getElementById('show-buildings').checked = true;
  }
  if (params.get('show_contours') === 'true') {
    document.getElementById('show-contours').checked = true;
  }
  if (params.get('show_heart') === 'true') {
    document.getElementById('show-heart').checked = true;
  }

  // Shared links may only load posters served by this application.
  const sharedPoster = params.get('img');
  if (sharedPoster && posterPathPattern.test(sharedPoster)) {
    const format = sharedPoster.split('.').pop();
    const filename = `${document.getElementById('city').value}_${params.get('theme') || 'poster'}.${format}`;
    showPoster(sharedPoster, format, filename);
    setCopyLink(sharedPoster);
    if (format === 'pdf') {
      fetch(sharedPoster).then(response => {
        if (!response.ok) showExpiredPoster();
      }).catch(showExpiredPoster);
    }
  }

  spanSlider.addEventListener('input', (e) => {
    spanVal.textContent = e.target.value;
  });

  fetch('/api/themes')
    .then(res => res.json())
    .then(data => {
      themeSelect.replaceChildren();
      data.themes.forEach(theme => {
        const option = document.createElement('option');
        option.value = theme.id;
        option.textContent = theme.name;
        if (theme.description) {
            option.title = theme.description;
        }
        if (theme.id === 'terracotta') option.selected = true;
        themeSelect.appendChild(option);
      });
      // Set theme from URL *after* loading themes
      if (params.has('theme')) {
        themeSelect.value = params.get('theme');
      }
    })
    .catch(err => {
      console.error('Failed to load themes', err);
      const option = document.createElement('option');
      option.value = 'terracotta';
      option.textContent = 'Terracotta (Default)';
      themeSelect.replaceChildren(option);
    });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    revokePosterUrl();
    resultImg.removeAttribute('src');
    submitBtn.disabled = true;
    btnText.textContent = 'Working...';
    loader.classList.remove('loader-hidden');
    resultImg.classList.add('hidden');
    actionBar.classList.add('hidden');
    previewPlaceholder.classList.remove('hidden');
    
    // Create UI for streaming logs
    const progressContainer = document.createElement('div');
    progressContainer.className = 'progress-container';
    progressContainer.style.cssText = 'width: 80%; background: rgba(0,0,0,0.2); border-radius: 8px; overflow: hidden; height: 8px; margin-bottom: 1rem; box-shadow: inset 0 1px 3px rgba(0,0,0,0.2);';
    const progressBar = document.createElement('div');
    progressBar.id = 'progress-bar';
    progressBar.style.cssText = 'width: 0%; height: 100%; background: linear-gradient(90deg, var(--primary), var(--secondary)); transition: width 0.4s ease-out; border-radius: 8px;';
    progressContainer.append(progressBar);
    const progressText = document.createElement('p');
    progressText.id = 'progress-text';
    progressText.style.cssText = 'color: #a5b4fc; font-weight: 600; font-size: 1.1rem; text-align: center; max-width: 80%;';
    progressText.textContent = 'Initializing connection...';
    previewPlaceholder.replaceChildren(progressContainer, progressText);

    const reqData = {
      city: document.getElementById('city').value,
      country: document.getElementById('country').value,
      theme: document.getElementById('theme').value,
      span: parseInt(document.getElementById('span').value, 10),
      width: parseFloat(document.getElementById('width').value),
      height: parseFloat(document.getElementById('height').value),
      format: document.getElementById('format').value,
      no_title: document.getElementById('no-title').checked,
      no_coords: document.getElementById('no-coords').checked,
      gradient_tb: document.getElementById('gradient-tb').checked,
      gradient_lr: document.getElementById('gradient-lr').checked,
      text_position: document.getElementById('text-position').value,
      show_buildings: document.getElementById('show-buildings').checked,
      show_contours: document.getElementById('show-contours').checked,
      show_heart: document.getElementById('show-heart').checked
    };

    const lat = document.getElementById('latitude').value;
    const lon = document.getElementById('longitude').value;
    if (lat && lon) {
      reqData.latitude = lat;
      reqData.longitude = lon;
    }
    
    // Update the browser URL without reloading so the user always has a sharable link dynamically
    const searchParams = new URLSearchParams();
    Object.keys(reqData).forEach(key => searchParams.set(key, reqData[key]));
    window.history.replaceState({}, '', `${window.location.pathname}?${searchParams.toString()}`);

    try {
      const response = await fetch('/api/generate_map_stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reqData)
      });

      if (!response.ok) throw await httpError(response);

      if (!response.body) throw new Error('The connection was lost. Please try again.');
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let chunk = '';
      let completed = false;

      async function handleLine(line) {
        if (!line.trim()) return;
        let data;
        try {
          data = JSON.parse(line);
        } catch (error) {
          console.error('Invalid stream JSON:', error);
          return;
        }
        if (data.type === 'progress') {
          progressText.textContent = data.message;
          const percent = Number(data.percent);
          if (Number.isFinite(percent)) {
            progressBar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
          }
        } else if (data.type === 'log') {
          console.log(data.message);
        } else if (data.type === 'error') {
          throw new Error(data.message);
        } else if (data.type === 'done') {
          if (typeof data.url !== 'string' || !posterPathPattern.test(data.url)) {
            throw new Error('Invalid poster URL.');
          }
          progressText.textContent = 'Downloading poster to your browser...';
          const imgRes = await fetch(data.url);
          if (!imgRes.ok) throw await httpError(imgRes);
          const blob = await imgRes.blob();
          revokePosterUrl();
          currentObjectUrl = URL.createObjectURL(blob);
          showPoster(currentObjectUrl, reqData.format, `${reqData.city}_${reqData.theme}.${reqData.format}`);
          setCopyLink(data.url);
          completed = true;
        }
      }

      try {
        while (!completed) {
          const { value, done } = await reader.read();
          chunk += decoder.decode(value, { stream: !done });
          const lines = chunk.split('\n');
          chunk = lines.pop();
          if (done && chunk.trim()) lines.push(chunk);
          for (const line of lines) {
            await handleLine(line);
            if (completed) break;
          }
          if (done) break;
        }
        if (!completed) throw new Error('The connection was lost. Please try again.');
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    } catch (error) {
      console.error(error);
      const container = document.createElement('div');
      container.style.cssText = 'background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239,68,68,0.4); padding: 1.5rem; border-radius: 12px;';
      const heading = document.createElement('h3');
      heading.style.cssText = 'color: #ef4444; margin-bottom: 0.5rem;';
      heading.textContent = 'Generation Error';
      const message = document.createElement('p');
      message.style.cssText = 'color: #fca5a5; font-size: 0.9rem;';
      message.textContent = error.message;
      container.append(heading, message);
      previewPlaceholder.replaceChildren(container);
      previewPlaceholder.classList.remove('hidden');
      resultImg.classList.add('hidden');
      actionBar.classList.add('hidden');
    } finally {
      submitBtn.disabled = false;
      btnText.textContent = 'Generate Masterpiece';
      loader.classList.add('loader-hidden');
    }
  });

  // Autocomplete Logic
  const cityInput = document.getElementById('city');
  const countryInput = document.getElementById('country');
  const autocompleteList = document.getElementById('autocomplete-list');
  const latInput = document.getElementById('latitude');
  const lonInput = document.getElementById('longitude');
  let debounceTimeout;
  let autocompleteLatitude = false;
  let autocompleteLongitude = false;
  latInput.addEventListener('input', () => { autocompleteLatitude = false; });
  lonInput.addEventListener('input', () => { autocompleteLongitude = false; });
  function clearAutocompleteCoordinates() {
    if (autocompleteLatitude) latInput.value = '';
    if (autocompleteLongitude) lonInput.value = '';
    autocompleteLatitude = false;
    autocompleteLongitude = false;
  }
  countryInput.addEventListener('input', clearAutocompleteCoordinates);

  cityInput.addEventListener('input', function() {
    clearAutocompleteCoordinates();
    clearTimeout(debounceTimeout);
    const query = this.value;
    
    if (!query || query.length < 2) {
      autocompleteList.replaceChildren();
      autocompleteList.classList.add('hidden');
      return;
    }

    debounceTimeout = setTimeout(async () => {
      try {
        const res = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=5&language=en&format=json`);
        const data = await res.json();
        if (cityInput.value !== query) return;
        
        autocompleteList.replaceChildren();
        
        if (data.results && data.results.length > 0) {
          autocompleteList.classList.remove('hidden');
          
          data.results.forEach(place => {
            const item = document.createElement('div');
            item.className = 'autocomplete-item';
            
            const cityName = String(place.name).slice(0, 80);
            const countryName = String(place.country || '').slice(0, 80);
            const admin1 = place.admin1 ? `, ${place.admin1}` : '';
            
            const name = document.createElement('strong');
            name.textContent = cityName;
            const country = document.createElement('span');
            country.className = 'country-text';
            country.textContent = `${admin1} (${countryName})`;
            item.append(name, country);
            
            item.addEventListener('click', () => {
              cityInput.value = cityName;
              countryInput.value = countryName;
              latInput.value = place.latitude;
              lonInput.value = place.longitude;
              autocompleteLatitude = true;
              autocompleteLongitude = true;
              autocompleteList.replaceChildren();
              autocompleteList.classList.add('hidden');
            });
            
            autocompleteList.appendChild(item);
          });
        } else {
          autocompleteList.classList.add('hidden');
        }
      } catch (err) {
        console.error('Geocoding error:', err);
      }
    }, 300);
  });

  document.addEventListener('click', (e) => {
    if (e.target !== cityInput && e.target !== autocompleteList) {
      autocompleteList.classList.add('hidden');
    }
  });
});
