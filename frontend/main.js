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

  // Pre-fill fields from URL params
  const params = new URLSearchParams(window.location.search);
  const textFields = ['city', 'country', 'latitude', 'longitude', 'span', 'width', 'height', 'format'];
  textFields.forEach(f => {
    if (params.has(f)) {
      const el = document.getElementById(f);
      if (el) el.value = params.get(f);
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
    spanVal.textContent = params.get('span');
  }
  if (params.get('show_buildings') === 'true') {
    document.getElementById('show-buildings').checked = true;
  }
  if (params.get('show_contours') === 'true') {
    document.getElementById('show-contours').checked = true;
  }

  // Pre-load image if shared link contains it
  if (params.has('img')) {
    previewPlaceholder.classList.add('hidden');
    resultImg.src = params.get('img');
    resultImg.classList.remove('hidden');
    
    downloadBtn.href = params.get('img');
    if (params.has('city') && params.has('theme')) {
        downloadBtn.download = `${params.get('city')}_${params.get('theme')}.${params.get('format') || 'png'}`;
    }
    actionBar.classList.remove('hidden');
  }

  spanSlider.addEventListener('input', (e) => {
    spanVal.textContent = e.target.value;
  });

  fetch('/api/themes')
    .then(res => res.json())
    .then(data => {
      themeSelect.innerHTML = '';
      data.themes.forEach(theme => {
        const option = document.createElement('option');
        option.value = theme.id;
        option.textContent = theme.name;
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
      themeSelect.innerHTML = '<option value="terracotta">Terracotta (Default)</option>';
    });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    submitBtn.disabled = true;
    btnText.textContent = 'Working...';
    loader.classList.remove('loader-hidden');
    resultImg.classList.add('hidden');
    actionBar.classList.add('hidden');
    previewPlaceholder.classList.remove('hidden');
    
    // Create UI for streaming logs
    previewPlaceholder.innerHTML = `
      <div class="progress-container" style="width: 80%; background: rgba(0,0,0,0.2); border-radius: 8px; overflow: hidden; height: 8px; margin-bottom: 1rem; box-shadow: inset 0 1px 3px rgba(0,0,0,0.2);">
         <div id="progress-bar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--primary), var(--secondary)); transition: width 0.4s ease-out; border-radius: 8px;"></div>
      </div>
      <p id="progress-text" style="color: #a5b4fc; font-weight: 600; font-size: 1.1rem; text-align: center; max-width: 80%;">Initializing connection...</p>
    `;
    const progressText = document.getElementById('progress-text');
    const progressBar = document.getElementById('progress-bar');

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
      show_contours: document.getElementById('show-contours').checked
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

      if (!response.ok) {
        throw new Error(response.statusText);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let chunk = "";

      while (true) {
        const {value, done} = await reader.read();
        if (value) {
          chunk += decoder.decode(value, {stream: true});
          const lines = chunk.split('\n');
          chunk = lines.pop(); // keep remainder
          
          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              const data = JSON.parse(line);
              
              if (data.type === 'ping') {
                continue; // Keep-alive
              }
              
              if (data.type === 'progress') {
                progressText.textContent = data.message;
                if (progressBar && data.percent !== undefined) {
                    progressBar.style.width = `${data.percent}%`;
                }
              } else if (data.type === 'log') {
                console.log(data.message);
              } else if (data.type === 'error') {
                throw new Error(data.message);
              } else if (data.type === 'done') {
                // Fetch the generated poster from the provided URL
                progressText.textContent = "Downloading poster to your browser...";
                const imgRes = await fetch(data.url);
                const blob = await imgRes.blob();
                const objectUrl = URL.createObjectURL(blob);
                
                previewPlaceholder.classList.add('hidden');
                resultImg.src = objectUrl;
                resultImg.classList.remove('hidden');
                
                downloadBtn.href = objectUrl;
                downloadBtn.download = `${reqData.city}_${reqData.theme}.${reqData.format}`;
                
                const copyBtn = document.getElementById('copy-link-btn');
                copyBtn.innerText = 'Copy Setup Link';
                copyBtn.onclick = (e) => {
                  e.preventDefault();
                  const shareParams = new URLSearchParams(window.location.search);
                  shareParams.set('img', data.url);
                  const fullUrl = window.location.origin + window.location.pathname + '?' + shareParams.toString();
                  navigator.clipboard.writeText(fullUrl).then(() => {
                    copyBtn.innerText = 'Copied! ✨';
                    setTimeout(() => copyBtn.innerText = 'Copy Setup Link', 2000);
                  });
                };
                
                actionBar.classList.remove('hidden');
              }
            } catch (err) {
              if (err.message) throw err;
            }
          }
        }
        if (done) break;
      }
    } catch (error) {
      console.error(error);
      previewPlaceholder.innerHTML = `
        <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239,68,68,0.4); padding: 1.5rem; border-radius: 12px;">
           <h3 style="color: #ef4444; margin-bottom: 0.5rem;">Generation Error</h3>
           <p style="color: #fca5a5; font-size: 0.9rem;">${error.message}</p>
        </div>`;
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

  cityInput.addEventListener('input', function() {
    clearTimeout(debounceTimeout);
    const query = this.value;
    
    if (!query || query.length < 2) {
      autocompleteList.innerHTML = '';
      autocompleteList.classList.add('hidden');
      return;
    }

    debounceTimeout = setTimeout(async () => {
      try {
        const res = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=5&language=en&format=json`);
        const data = await res.json();
        
        autocompleteList.innerHTML = '';
        
        if (data.results && data.results.length > 0) {
          autocompleteList.classList.remove('hidden');
          
          data.results.forEach(place => {
            const item = document.createElement('div');
            item.className = 'autocomplete-item';
            
            const cityName = place.name;
            const countryName = place.country || '';
            const admin1 = place.admin1 ? `, ${place.admin1}` : '';
            
            item.innerHTML = `<strong>${cityName}</strong><span class="country-text">${admin1} (${countryName})</span>`;
            
            item.addEventListener('click', () => {
              cityInput.value = cityName;
              countryInput.value = countryName;
              latInput.value = place.latitude;
              lonInput.value = place.longitude;
              autocompleteList.innerHTML = '';
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
