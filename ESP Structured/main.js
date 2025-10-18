// Configurable ESP base URL with auto-detect
const DEFAULT_ESP = "http://esp32.local";
let ESP_BASE = (function(){
  const raw = localStorage.getItem('esp_base');
  if(!raw || raw === 'null') return null;
  const v = raw.trim();
  return v.replace(/\/+$/,'');
})();

// Try to detect ESP automatically if not saved
async function detectEsp() {
  if(ESP_BASE) return ESP_BASE; // already saved
  const candidates = ["http://esp32.local", "http://nodemcu.local"];
  // Probe all candidates in parallel and pick the first that responds
  try{
    const probes = candidates.map(url =>
      fetchWithTimeout(url+"/pos", {}, 1200).then(()=>url).catch(()=>null)
    );
    const results = await Promise.all(probes);
    const found = results.find(r=>r);
    if(found){ ESP_BASE = found; localStorage.setItem('esp_base', found); console.log('Detected ESP at', found); appendLog('Detected ESP at '+found); return found; }
  }catch(e){ console.warn('Parallel detection error', e); }
  console.warn("Could not auto-detect ESP, falling back to default", DEFAULT_ESP);
  appendLog('Auto-detect failed, using default '+DEFAULT_ESP);
  return DEFAULT_ESP;
}

// Set/get helpers
function getEspBase(){
  if(ESP_BASE) return ESP_BASE;
  const raw = localStorage.getItem('esp_base');
  if(raw && raw !== 'null') return raw.replace(/\/+$/,'');
  return DEFAULT_ESP;
}
function setEspBase(url){
  if(!url){ ESP_BASE = null; localStorage.removeItem('esp_base'); return; }
  const v = String(url).trim();
  if(v.length === 0 || v === 'null'){ ESP_BASE = null; localStorage.removeItem('esp_base'); return; }
  const clean = v.replace(/\/+$/,'');
  ESP_BASE = clean;
  localStorage.setItem('esp_base', clean);
}

// Fetch helper with timeout and error logging
function fetchWithTimeout(url, opts={}, timeout=3000){
  return Promise.race([
    fetch(url, opts),
    new Promise((_, rej)=> setTimeout(()=> rej(new Error('timeout')), timeout))
  ]).catch(err => { console.error('Fetch error', url, err); throw err; });
}

// Control functions
async function move(dir){ const ESP32 = await detectEsp(); fetchWithTimeout(`${ESP32}/move?dir=${dir}`).then(r=>r.json()).then(d=>{document.getElementById('pos').textContent=`Pan: ${d.pan} | Tilt: ${d.tilt}`;}).catch(()=>{}); }
async function car(cmd){ const ESP32 = await detectEsp(); fetchWithTimeout(`${ESP32}/car?cmd=${cmd}`).catch(()=>{}); }
async function setGear(g){ const ESP32 = await detectEsp(); fetchWithTimeout(`${ESP32}/gear?value=${g}`).catch(()=>{}); document.getElementById('gear').textContent="Current Gear: "+g; }

// Enhance setGear UI: call update and pulse animation
async function setGear(g){
  const ESP32 = await detectEsp();
  fetchWithTimeout(`${ESP32}/gear?value=${g}`).catch(()=>{});
  updateGearUI(g);
  // pulse animation
  const btn = document.querySelector('#gearButtons button[data-gear="'+g+'"]');
  if(btn){ btn.classList.add('pulse'); setTimeout(()=>btn.classList.remove('pulse'), 600); }
  // persist gear selection
  try{ saveCurrentGear(g); }catch(e){}
}

// Button bindings
document.getElementById('forward').onclick=()=>car('forward');
document.getElementById('backward').onclick=()=>car('backward');
document.getElementById('left').onclick=()=>car('left');
document.getElementById('right').onclick=()=>car('right');
document.getElementById('stop').onclick=()=>car('stop');

document.getElementById('panLeft').onclick=()=>move('pan_left');
document.getElementById('panRight').onclick=()=>move('pan_right');
document.getElementById('tiltUp').onclick=()=>move('tilt_up');
document.getElementById('tiltDown').onclick=()=>move('tilt_down');

document.querySelectorAll('#gearButtons button').forEach(b=>{b.onclick=()=>setGear(b.dataset.gear);});

// Highlight active gear button
function updateGearUI(g){
  document.querySelectorAll('#gearButtons button').forEach(b=>{ b.classList.toggle('active', String(b.dataset.gear)===String(g)); });
  const gearDiv = document.getElementById('gear'); if(gearDiv) gearDiv.textContent = 'Current Gear: '+g;
}

// Disable arrow keys from scrolling the page (but still use them for control)
window.addEventListener('keydown', function(e){
  const keys = ['ArrowUp','ArrowDown','ArrowLeft','ArrowRight',' '];
  if(keys.includes(e.key)){
    e.preventDefault();
  }
}, {passive:false});

// Manual ESP input
function ensureEspInput(){
  if(document.getElementById('espSettingsPanel')) return;
  const div = document.createElement('div');
  div.id = 'espSettingsPanel';
  div.className = 'esp-settings-panel';
  div.innerHTML = `
    <div class="esp-row">
      <input id="espBaseInput" class="esp-input" placeholder="ESP base (http://IP)" value="${getEspBase()}">
      <button id="espSaveBtn" class="btn">Save</button>
    </div>
    <div class="esp-row esp-actions">
      <button id="espClearBtn" class="btn secondary">Clear saved ESP</button>
      <button id="espDetectBtn" class="btn detect">Detect</button>
      <button id="espResetBtn" class="btn reset">Reset Defaults</button>
      <button id="espDownloadBtn" class="btn download">Download Log</button>
      <button id="espToggleBtn" class="btn toggle">Collapse</button>
    </div>
    <div class="esp-meta">
      <span class="status-badge unknown"></span>
    </div>
    <div class="endpoints" aria-hidden="false"></div>
    <div class="diag-log" aria-live="polite"></div>
  `;
  document.body.appendChild(div);
  // Button handlers
  document.getElementById('espSaveBtn').onclick = ()=>{ 
    const val = document.getElementById('espBaseInput').value.trim();
    if(val && !/^https?:\/\//i.test(val)){ showToast('Please include http:// or https:// in the URL','error'); return; }
    setEspBase(val);
    showToast('ESP base saved','info');
    updateSettingsStatus('unknown');
  };
  document.getElementById('espClearBtn').onclick = ()=>{ setEspBase(null); document.getElementById('espBaseInput').value = ''; showToast('Saved ESP cleared','info'); updateSettingsStatus('unknown'); };
  document.getElementById('espDetectBtn').onclick = async ()=>{ 
    try{
      const found = await detectEsp(); document.getElementById('espBaseInput').value = found; showToast('Detected: '+found,'info'); updateSettingsStatus('unknown');
    }catch(e){ appendLog('Detect failed: '+(e.message||e)); showToast('Detect failed','error'); }
  };
  document.getElementById('espResetBtn').onclick = ()=>{
    // Reset saved settings (ESP base + gear)
    setEspBase(null);
    localStorage.removeItem(SAVED_GEAR_KEY);
    ESP_BASE = null;
    updateGearUI('1');
    document.getElementById('espBaseInput').value = '';
    showToast('Defaults restored','info');
    appendLog('Settings reset to defaults');
    updateSettingsStatus('unknown');
  };
  document.getElementById('espDownloadBtn').onclick = ()=>{
    // prepare log
    const text = diagEntries.join('\n');
    const blob = new Blob([text], {type:'text/plain'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'tank_diag_log.txt'; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    showToast('Log downloaded','info');
  };
  document.getElementById('espToggleBtn').onclick = ()=>{
    const panel = document.getElementById('espSettingsPanel');
    if(!panel) return;
    panel.classList.toggle('collapsed');
    const t = document.getElementById('espToggleBtn'); t.textContent = panel.classList.contains('collapsed') ? 'Expand' : 'Collapse';
  };
}
ensureEspInput();

// Ensure UI doesn't get overlapped by settings panel: reserve right-side space when panel visible
function adjustLayoutForPanel(){
  const panel = document.getElementById('espSettingsPanel');
  const cards = document.querySelector('.cards');
  if(!cards) return;
  if(panel && !panel.classList.contains('collapsed')){
    // measure panel width and add right margin to cards
    const rect = panel.getBoundingClientRect();
    const extra = Math.min(rect.width + 24, window.innerWidth * 0.35);
    cards.classList.add('with-panel-right');
    cards.style.marginRight = (extra) + 'px';
  } else {
    cards.classList.remove('with-panel-right');
    cards.style.marginRight = '';
  }
}
window.addEventListener('resize', adjustLayoutForPanel);
// call once
setTimeout(adjustLayoutForPanel, 200);

// Persist selected gear across reloads
const SAVED_GEAR_KEY = 'tank_current_gear';
function saveCurrentGear(g){ if(!g) return; localStorage.setItem(SAVED_GEAR_KEY, String(g)); }
function loadSavedGear(){ const g = localStorage.getItem(SAVED_GEAR_KEY); if(g){ updateGearUI(g); } }
// Restore saved gear on load
loadSavedGear();

// Toast helper
// Toast queue helper (limit concurrent toasts)
const TOAST_LIMIT = 3;
const toastQueue = [];
function showToast(msg, type='info', timeout=3500){
  const c = document.getElementById('toastContainer'); if(!c) return;
  // remove oldest if over limit
  if(toastQueue.length >= TOAST_LIMIT){ const old = toastQueue.shift(); if(old && old.remove) old.remove(); }
  const t = document.createElement('div'); t.className='toast '+(type||'info'); t.textContent = msg; c.appendChild(t); toastQueue.push(t);
  setTimeout(()=>{ t.style.transition='opacity .25s'; t.style.opacity='0'; setTimeout(()=>{ const i = toastQueue.indexOf(t); if(i>=0) toastQueue.splice(i,1); t.remove(); },250); }, timeout);
}

// Settings status helper
function updateSettingsStatus(state){
  const panel = document.getElementById('espSettingsPanel'); if(!panel) return;
  let badge = panel.querySelector('.status-badge');
  if(!badge){ badge = document.createElement('span'); badge.className='status-badge'; panel.querySelector('div').appendChild(badge); }
  if(state === 'ok'){ badge.textContent = 'OK'; badge.classList.remove('unreachable','unknown'); badge.classList.add('ok'); }
  else if(state === 'unreachable'){ badge.textContent = 'Unreachable'; badge.classList.remove('ok','unknown'); badge.classList.add('unreachable'); }
  else { badge.textContent = ''; badge.classList.remove('ok','unreachable'); badge.classList.add('unknown'); }
}

// Endpoint diagnostics
const endpointDiag = { pos: null, dist: null };
function updateEndpointDiag(name, ms){
  endpointDiag[name] = ms;
  const panel = document.getElementById('espSettingsPanel'); if(!panel) return;
  let container = panel.querySelector('.endpoints');
  if(!container){ container = document.createElement('div'); container.className='endpoints'; container.style.marginTop='8px'; panel.appendChild(container); }
  container.innerHTML = `
    <div class="ep"><strong>/pos</strong>: ${ms===null? '--' : (ms==='err' ? 'err' : ms+' ms')}</div>
    <div class="ep"><strong>/dist</strong>: ${endpointDiag.dist===null? '--' : (endpointDiag.dist==='err' ? 'err' : endpointDiag.dist+' ms')}</div>
  `;
}

// Diagnostic log (keeps last N entries)
const DIAG_LIMIT = 30;
const diagEntries = [];
function appendLog(msg){
  const ts = new Date().toLocaleTimeString();
  diagEntries.push(`${ts} - ${msg}`);
  if(diagEntries.length>DIAG_LIMIT) diagEntries.shift();
  const panel = document.getElementById('espSettingsPanel'); if(!panel) return;
  const d = panel.querySelector('.diag-log'); if(!d) return;
  d.innerHTML = diagEntries.slice().reverse().map(l=>`<div class="diag">${l}</div>`).join('');
}

// Periodically check ESP /pos to update status in panel
setInterval(async ()=>{
  try{
    const base = getEspBase();
    const t0 = performance.now();
    const res = await fetchWithTimeout(base + '/pos', {}, 1500);
    const t1 = Math.round(performance.now()-t0);
    if(res && res.ok){ updateSettingsStatus('ok'); updateEndpointDiag('pos', t1); } else { updateSettingsStatus('unreachable'); updateEndpointDiag('pos','err'); appendLog('/pos returned non-OK'); }
  }catch(e){ updateSettingsStatus('unreachable'); }
}, 3000);

// Auto-refresh pan/tilt
setInterval(async ()=>{
  const ESP32 = await detectEsp();
  // /pos
  try{
    const t0 = performance.now();
    const rpos = await fetchWithTimeout(`${ESP32}/pos`,{},1500);
    const t1 = Math.round(performance.now()-t0);
    if(rpos && rpos.ok){ const d = await rpos.json(); document.getElementById('pos').textContent=`Pan: ${d.pan} | Tilt: ${d.tilt}`; updateEndpointDiag('pos', t1); }
    else { updateEndpointDiag('pos','err'); appendLog('/pos fetch failed or non-OK'); }
  }catch(e){ updateEndpointDiag('pos','err'); }
  
  // ToF distance (if available)
    // Distance fetching moved to dedicated routine that first checks /dist_ready
    // (see dist polling loop below)
}, 500);

  // Periodically check if ToF sensor is ready before fetching distance
  let distSensorReady = null; // true/false/null
  async function pollDistReadyAndFetch(){
    try{
      const base = await detectEsp();
      // check ready
      const t0r = performance.now();
      const rready = await fetchWithTimeout(base + '/dist_ready', {}, 1200);
      const t1r = Math.round(performance.now()-t0r);
      if(rready && rready.ok){
        const jr = await rready.json();
        distSensorReady = !!jr.ready;
        updateEndpointDiag('dist', t1r);
        updateSettingsStatus(distSensorReady ? 'ok' : 'unknown');
        if(distSensorReady){
          // fetch actual distance
          try{
            const t0d = performance.now();
            const rdist = await fetchWithTimeout(base + '/dist', {}, 1200);
            const t1d = Math.round(performance.now()-t0d);
            if(rdist && rdist.ok){ const dd = await rdist.json(); document.getElementById('dist').textContent = dd.distance>=0 ? dd.distance+" mm" : '--'; updateEndpointDiag('dist', t1d); }
            else { document.getElementById('dist').textContent='--'; updateEndpointDiag('dist','err'); appendLog('/dist fetch failed or non-OK'); distSensorReady = false; }
          }catch(e){ document.getElementById('dist').textContent='--'; updateEndpointDiag('dist','err'); appendLog('/dist exception: '+(e.message||e)); distSensorReady = false; }
        } else {
          document.getElementById('dist').textContent='--';
        }
      } else {
        distSensorReady = false; updateEndpointDiag('dist','err'); document.getElementById('dist').textContent='--';
      }
    }catch(e){ distSensorReady = false; updateEndpointDiag('dist','err'); document.getElementById('dist').textContent='--'; appendLog('/dist_ready exception: '+(e.message||e)); }
  }

  // Start interval for dist readiness & fetching (every 2 seconds)
  setInterval(pollDistReadyAndFetch, 2000);

// Keyboard control
document.addEventListener('keydown', e=>{
  if(e.repeat) return;
  switch(e.key){
    case 'ArrowUp': move('tilt_up'); break;
    case 'ArrowDown': move('tilt_down'); break;
    case 'ArrowLeft': move('pan_left'); break;
    case 'ArrowRight': move('pan_right'); break;
    case 'w': case 'W': car('forward'); break;
    case 's': case 'S': car('backward'); break;
    case 'a': case 'A': car('left'); break;
    case 'd': case 'D': car('right'); break;
    case '1': case '2': case '3': case '4': case '5': setGear(e.key); break;
  }
});
document.addEventListener('keyup', e=>{ if(['w','a','s','d','W','A','S','D'].includes(e.key)) car('stop'); });
