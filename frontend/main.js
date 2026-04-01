document.addEventListener('DOMContentLoaded', () => {
  console.log('AesthetiMap UI v1.1 Loaded (Buildings & Topography)');
  const form = document.getElementById('generate-form');
  const themeSelect = document.getElementById('theme');
  const spanSlider = document.getElementById('span');
  const spanVal = document.getElementById('span-val');
  const submitBtn = document.getElementById('submit-btn');
  const btnText = submitBtn.querySelector('.btn-text');
  const loader = document.getElementById('loader');

  const authModal = document.getElementById('auth-modal');
  const authForm = document.getElementById('auth-form');
  const loginNavBtn = document.getElementById('login-nav-btn');
  const logoutNavBtn = document.getElementById('logout-nav-btn');
  const userDisplay = document.getElementById('user-display');
  const authError = document.getElementById('auth-error');
  const authCancelBtn = document.getElementById('auth-cancel-btn');
  const authSwitchBtn = document.getElementById('auth-switch-btn');
  
  let authMode = 'login';
  let currentTier = 'anonymous';

  // Auth Functions
  async function checkAuth() {
    const historyNavBtn = document.getElementById('history-nav-btn');
    const token = localStorage.getItem('aesthetimap_token');
    if (!token) {
        userDisplay.textContent = 'Anonymous';
        loginNavBtn.classList.remove('hidden');
        logoutNavBtn.classList.add('hidden');
        historyNavBtn.classList.add('hidden');
        currentTier = 'anonymous';
        return;
    }
    try {
        const res = await fetch('/api/users/me', { headers: { 'Authorization': `Bearer ${token}` } });
        if (res.ok) {
            const data = await res.json();
            userDisplay.innerHTML = `${data.email} <b>[${data.tier.toUpperCase()}]</b>`;
            loginNavBtn.classList.add('hidden');
            logoutNavBtn.classList.remove('hidden');
            historyNavBtn.classList.remove('hidden');
            currentTier = data.tier;
        } else {
            throw new Error("Invalid token");
        }
    } catch(e) {
        localStorage.removeItem('aesthetimap_token');
        checkAuth();
    }
  }

  async function loginRequest(email, password) {
      const formData = new URLSearchParams();
      formData.append('username', email);
      formData.append('password', password);
      const res = await fetch('/api/auth/token', {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: formData
      });
      if (!res.ok) {
          let errText = "Login failed";
          try { errText = (await res.json()).detail; } catch(e) {}
          throw new Error(errText);
      }
      const data = await res.json();
      localStorage.setItem('aesthetimap_token', data.access_token);
  }

  loginNavBtn.addEventListener('click', () => { authModal.classList.remove('hidden'); });
  authCancelBtn.addEventListener('click', () => { authModal.classList.add('hidden'); authError.textContent=''; });
  logoutNavBtn.addEventListener('click', () => { localStorage.removeItem('aesthetimap_token'); checkAuth(); });

  authSwitchBtn.addEventListener('click', () => {
    authMode = authMode === 'login' ? 'register' : 'login';
    document.getElementById('auth-title').textContent = authMode === 'login' ? 'Log In' : 'Register';
    authSwitchBtn.textContent = authMode === 'login' ? 'Need an account? Register' : 'Already have an account? Log In';
    authError.textContent = '';
  });

  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('auth-email').value;
    const password = document.getElementById('auth-password').value;
    authError.textContent = 'Working...';
    try {
        if (authMode === 'register') {
            const res = await fetch('/api/auth/register', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email, password})
            });
            if (!res.ok) {
                let errText = "Registration failed";
                try { errText = (await res.json()).detail; } catch(e) {}
                throw new Error(errText);
            }
        }
        await loginRequest(email, password);
        authModal.classList.add('hidden');
        authForm.reset();
        checkAuth();
    } catch(err) {
        authError.textContent = err.message;
    }
  });

  // History Modal Logic
  const historyModal = document.getElementById('history-modal');
  const historyCloseBtn = document.getElementById('history-close-btn');
  const historyList = document.getElementById('history-list');
  const historyLoading = document.getElementById('history-loading');
  const historyEmpty = document.getElementById('history-empty');

  document.getElementById('history-nav-btn').addEventListener('click', async () => {
      historyModal.classList.remove('hidden');
      historyLoading.classList.remove('hidden');
      historyList.innerHTML = '';
      historyList.classList.add('hidden');
      historyEmpty.classList.add('hidden');

      const token = localStorage.getItem('aesthetimap_token');
      try {
          const res = await fetch('/api/users/history', { headers: { 'Authorization': `Bearer ${token}` } });
          const data = await res.json();
          historyLoading.classList.add('hidden');
          if (data.history && data.history.length > 0) {
              historyList.classList.remove('hidden');
              data.history.forEach(item => {
                  const el = document.createElement('div');
                  el.style.cssText = "background: rgba(255,255,255,0.05); padding: 1rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); display: flex; justify-content: space-between; align-items: center;";
                  el.innerHTML = `
                    <div>
                        <div style="font-weight: 600; color: #e0e7ff; margin-bottom: 0.25rem;">${item.city_name}, ${item.country_name}</div>
                        <div style="font-size: 0.8rem; color: #9ca3af;">Theme: ${item.theme} &bull; ${new Date(item.created_at).toLocaleDateString()}</div>
                    </div>
                    <a href="/api/posters/${item.filename}" target="_blank" class="btn-generate" style="text-decoration: none; padding: 0.4rem 1rem; font-size: 0.85rem; border-radius: 6px; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center;">View Map</a>
                  `;
                  historyList.appendChild(el);
              });
          } else {
              historyEmpty.classList.remove('hidden');
          }
      } catch (err) {
          historyLoading.textContent = "Error loading history.";
      }
  });

  historyCloseBtn.addEventListener('click', () => { historyModal.classList.add('hidden'); });
  // Handle OAuth redirect token
  const urlParams = new URLSearchParams(window.location.search);
  const oauthToken = urlParams.get('token');
  if (oauthToken) {
      localStorage.setItem('aesthetimap_token', oauthToken);
      window.history.replaceState({}, document.title, window.location.pathname);
  }

  checkAuth();
  
  const previewPlaceholder = document.querySelector('.preview-placeholder');
  const resultImg = document.getElementById('result-img');
  const actionBar = document.getElementById('action-bar');
  const downloadBtn = document.getElementById('download-btn');

  document.getElementById('current-year').textContent = new Date().getFullYear();

  const formatSelect = document.getElementById('format');
  Array.from(formatSelect.options).forEach(opt => {
      if (['svg', 'pdf'].includes(opt.value)) opt.textContent += ' 🔒';
  });
  const poiEmoji = document.getElementById('poi-emoji');
  const poiSize = document.getElementById('poi-size');
  const poiSizeVal = document.getElementById('poi-size-val');
  const markerSizeGroup = document.getElementById('marker-size-group');

  poiEmoji.addEventListener('change', () => {
      if (poiEmoji.value) {
          markerSizeGroup.classList.remove('hidden');
      } else {
          markerSizeGroup.classList.add('hidden');
      }
  });

  poiSize.addEventListener('input', (e) => {
      poiSizeVal.textContent = e.target.value;
  });

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
  if (params.get('poi_emoji')) {
    poiEmoji.value = params.get('poi_emoji');
    markerSizeGroup.classList.remove('hidden');
  }
  if (params.get('poi_size')) {
    poiSize.value = params.get('poi_size');
    poiSizeVal.textContent = params.get('poi_size');
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
        if (theme.description) {
            option.title = theme.description;
        }
        if (['aurora_borealis', 'kintsugi'].includes(theme.id)) {
            option.textContent += ' 🔒';
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
      show_contours: document.getElementById('show-contours').checked,
      poi_emoji: poiEmoji.value || null,
      poi_size: parseInt(poiSize.value, 10)
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
      const headers = { 'Content-Type': 'application/json' };
      const token = localStorage.getItem('aesthetimap_token');
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch('/api/generate_map_stream', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(reqData)
      });

      if (!response.ok) {
        let errorDet = response.statusText;
        try {
            errorDet = (await response.json()).detail;
        } catch(e) {}
        throw new Error(errorDet);
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
