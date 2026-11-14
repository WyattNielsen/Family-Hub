// ── API Helper ────────────────────────────────────────────────
// A page asks for the same lists several times (sidebar, person filter, and
// the section itself). Share the in-flight request, and reuse a very fresh
// response so those calls don't each wait on the database.
const _getCache = new Map();
const _getInflight = new Map();
let _getCacheGen = 0;
const GET_CACHE_MS = 2000;

function _bustGetCache() {
  _getCacheGen += 1;
  _getCache.clear();
}

async function _send(path, options) {
  _bustGetCache();
  const r = await fetch(path, options);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

const API = {
  async get(path) {
    const hit = _getCache.get(path);
    if (hit && Date.now() - hit.at < GET_CACHE_MS) return hit.data;
    const pending = _getInflight.get(path);
    if (pending) return pending;
    const gen = _getCacheGen;
    const request = (async () => {
      const r = await fetch(path);
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      if (gen === _getCacheGen) _getCache.set(path, { at: Date.now(), data });
      return data;
    })();
    _getInflight.set(path, request);
    try {
      return await request;
    } finally {
      if (_getInflight.get(path) === request) _getInflight.delete(path);
    }
  },
  post(path, body) {
    return _send(path, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
  },
  put(path, body) {
    return _send(path, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
  },
  delete(path) {
    return _send(path, { method: 'DELETE' });
  },
  patch(path, body) {
    return _send(path, {
      method: 'PATCH', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
  }
};

// ── Toast ─────────────────────────────────────────────────────
function showToast(msg, type = '') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

function escapeHtml(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function memberAvatarHtml(member, className) {
  const color = (member && member.color) || '#9AA0B8';
  const cls = 'member-avatar' + (className ? ' ' + className : '');
  if (member && member.avatar_photo) {
    const src = '/api/members/avatar/' + encodeURIComponent(member.avatar_photo);
    return `<span class="${cls} has-photo" style="background:${color}22"><img src="${src}" alt=""></span>`;
  }
  const emoji = (member && member.avatar) || '👤';
  return `<span class="${cls}" style="background:${color}22">${emoji}</span>`;
}

const PERSON_KEY = 'fh_person';

function getPersonFilter() {
  const value = localStorage.getItem(PERSON_KEY);
  return value && value !== 'all' ? value : 'all';
}

function setPersonFilter(id) {
  localStorage.setItem(PERSON_KEY, !id || id === 'all' ? 'all' : String(id));
  armPersonIdle();
}

const PERSON_IDLE_MS = 5 * 60 * 1000;
let _personIdleTimer = null;

function armPersonIdle() {
  clearTimeout(_personIdleTimer);
  _personIdleTimer = null;
  if (getPersonFilter() === 'all') return;
  _personIdleTimer = setTimeout(revertPersonView, PERSON_IDLE_MS);
}

function revertPersonView() {
  if (getPersonFilter() === 'all') return;
  setPersonFilter('all');
  const path = location.pathname;
  if (path === '/' || path.endsWith('/index.html')) location.reload();
  else location.replace('/');
}

function _onPersonActivity() {
  armPersonIdle();
}

let _personFilterChange = null;
function onPersonFilterChange(fn) {
  _personFilterChange = fn;
}

async function mountPersonFilter(onChange) {
  if (onChange) _personFilterChange = onChange;
  const el = document.getElementById('personFilter');
  if (!el) return [];
  let members = [];
  try {
    members = await API.get('/api/members/');
  } catch (e) {
    el.innerHTML = '';
    return [];
  }
  let current = getPersonFilter();
  if (current !== 'all' && !members.some(m => String(m.id) === String(current))) {
    current = 'all';
    setPersonFilter('all');
  }
  const badge = '<span class="person-badge" style="display:none"></span>';
  const everyone = `<button type="button" class="person-chip${current === 'all' ? ' active' : ''}" data-person="all">Everyone${badge}</button>`;
  const chips = members.map(m => `
    <button type="button" class="person-chip${String(current) === String(m.id) ? ' active' : ''}" data-person="${m.id}" style="--chip:${m.color}">
      ${memberAvatarHtml(m, 'avatar-sm')}
      <span>${escapeHtml(m.name)}</span>
      ${badge}
    </button>`).join('');
  el.innerHTML = everyone + chips;
  el.querySelectorAll('.person-chip').forEach(btn => {
    btn.onclick = () => {
      setPersonFilter(btn.dataset.person);
      el.querySelectorAll('.person-chip').forEach(b => b.classList.toggle('active', b === btn));
      if (_personFilterChange) _personFilterChange(getPersonFilter());
      loadMessageBadge();
    };
  });
  loadMessageBadge();
  return members;
}

// ── Sidebar Members ───────────────────────────────────────────
async function loadSidebarMembers() {
  const el = document.getElementById('sidebarMembers');
  if (!el) return;
  try {
    const members = await API.get('/api/members/');
    el.innerHTML = members.map(m => `
      <div class="sidebar-member-chip">
        ${memberAvatarHtml(m)}
        <span>${escapeHtml(m.name)}</span>
      </div>
    `).join('');
  } catch(e) {}
}

// ── Date Helpers ──────────────────────────────────────────────
let TZ = 'America/New_York';
let _tzLoaded = false;
function loadAppSettings() {
  return API.get('/api/settings/');
}

async function initAppTimezone() {
  if (_tzLoaded) return;
  try {
    const s = await loadAppSettings();
    if (s.timezone) TZ = s.timezone;
  } catch(e) {}
  _tzLoaded = true;
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  return d.toLocaleDateString('en-US', { weekday:'short', month:'short', day:'numeric', timeZone: TZ });
}

function formatTime(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  return d.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit', timeZone: TZ });
}

function isToday(dateStr) {
  const d = new Date(dateStr);
  const t = new Date();
  const opts = { timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit' };
  return d.toLocaleDateString('en-US', opts) === t.toLocaleDateString('en-US', opts);
}

function isPast(dateStr) {
  return new Date(dateStr) < new Date();
}

function toETDateStr(date) {
  // Returns YYYY-MM-DD in Eastern Time
  const d = new Date(date);
  const parts = d.toLocaleDateString('en-US', { timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit' }).split('/');
  return `${parts[2]}-${parts[0]}-${parts[1]}`;
}

function toLocalISO(date) {
  const d = new Date(date);
  // Use ET time components
  const etStr = d.toLocaleString('en-US', { timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12: false });
  const [datePart, timePart] = etStr.split(', ');
  const [m, day, yr] = datePart.split('/');
  return `${yr}-${m}-${day}T${timePart.replace('24:', '00:')}`;
}

// ── Night Mode + Auto-dim ──────────────────────────────────────
let _darkStart = 21;
let _darkEnd   = 7;
let _dimDelay  = 5 * 60 * 1000;
let _dimTimer  = null;

function _applyTheme() {
  const h = new Date().getHours();
  const isNight = _darkStart > _darkEnd
    ? (h >= _darkStart || h < _darkEnd)
    : (h >= _darkStart && h < _darkEnd);
  document.documentElement.setAttribute('data-theme', isNight ? 'dark' : 'light');
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = isNight ? '#1a1a1a' : '#F6F7F9';
}

function _resetDim() {
  document.documentElement.classList.remove('dimmed');
  clearTimeout(_dimTimer);
  if (_dimDelay > 0) {
    _dimTimer = setTimeout(() => document.documentElement.classList.add('dimmed'), _dimDelay);
  }
}

async function initTheme() {
  try {
    const s = await loadAppSettings();
    _darkStart = parseInt(s.dark_mode_start || '21');
    _darkEnd   = parseInt(s.dark_mode_end || '7');
    _dimDelay  = parseInt(s.dim_minutes || '5') * 60000;
  } catch(e) {}
  _applyTheme();
  setInterval(_applyTheme, 60000);
  ['touchstart', 'mousemove', 'keydown', 'click'].forEach(ev =>
    document.addEventListener(ev, _resetDim, { passive: true })
  );
  _resetDim();
}

function paintCountBadge(el, n) {
  if (!el) return;
  if (n > 0) {
    el.textContent = n > 9 ? '9+' : String(n);
    el.style.display = 'inline-block';
  } else {
    el.style.display = 'none';
  }
}

// ── Message badges on person names and the Messages icon ─────
async function loadMessageBadge() {
  const chips = document.querySelectorAll('#personFilter .person-chip');
  const nav = document.getElementById('msgNavBadge');
  if (!chips.length && !nav) return;
  try {
    const msgs = await API.get('/api/messages/');
    const counts = { all: 0 };
    for (const m of msgs) {
      if (!m.recipient_id) counts.all += 1;
      else {
        const id = String(m.recipient_id);
        counts[id] = (counts[id] || 0) + 1;
      }
    }
    chips.forEach(chip => {
      paintCountBadge(chip.querySelector('.person-badge'), counts[chip.dataset.person] || 0);
    });
    const person = getPersonFilter();
    const navCount = person === 'all'
      ? msgs.length
      : msgs.filter(m => !m.recipient_id || String(m.recipient_id) === String(person)).length;
    paintCountBadge(nav, navCount);
  } catch(e) {}
}

// Header cameras and Home Assistant status, plus the Messages count,
// are part of the shell so every page shows the same icons.
function ensureShell() {
  const msgLink = document.querySelector('a.nav-link[href="/messages.html"]');
  if (msgLink && !document.getElementById('msgNavBadge')) {
    const badge = document.createElement('span');
    badge.className = 'nav-badge';
    badge.id = 'msgNavBadge';
    badge.style.display = 'none';
    msgLink.appendChild(badge);
  }
  const header = document.querySelector('header.page-header');
  if (!header) return;
  const weather = document.getElementById('weatherWidget');
  if (!document.getElementById('dashCameras')) {
    const cams = document.createElement('div');
    cams.id = 'dashCameras';
    cams.className = 'dash-cam-strip';
    cams.style.display = 'none';
    header.insertBefore(cams, weather);
  }
  if (!document.getElementById('haStatusBadge')) {
    const ha = document.createElement('div');
    ha.id = 'haStatusBadge';
    ha.className = 'ha-status-badge';
    const cams = document.getElementById('dashCameras');
    header.insertBefore(ha, cams ? cams.nextSibling : weather);
  }
}

let _haEntities = [];

function renderHAStatus() {
  const badge = document.getElementById('haStatusBadge');
  if (!badge) return;
  badge.innerHTML = _haEntities.map(e => {
    const isAlarm = e.entity_type === 'alarm';
    const isCover = String(e.entity_id).startsWith('cover.');
    const clickable = (isAlarm || isCover) ? 'ha-alarm-clickable' : '';
    const onclick = isAlarm
      ? `onclick="toggleAlarmPopover(event,'${e.entity_id}')"`
      : isCover
        ? `onclick="toggleCoverPopover(event,'${e.entity_id}')"`
        : '';
    return `
      <div class="ha-badge-item ${e.css_class} ${clickable}" ${onclick} data-entity-id="${e.entity_id}">
        <span class="ha-badge-icon">${e.icon}</span>
        <span class="ha-badge-label">${escapeHtml(e.name)}:</span>
        <span class="ha-badge-state">${escapeHtml(e.state_label || e.state)}</span>
      </div>`;
  }).join('');
}

async function loadHAStatus() {
  const badge = document.getElementById('haStatusBadge');
  if (!badge) return;
  try {
    const data = await API.get('/api/homeassistant/states');
    if (data.error || !data.entities || !data.entities.length) {
      badge.innerHTML = '';
      _haEntities = [];
      return;
    }
    _haEntities = data.entities;
    renderHAStatus();
  } catch(e) {
    badge.innerHTML = '';
  }
}

function toggleAlarmPopover(evt, entityId) {
  evt.stopPropagation();
  const existing = document.getElementById('haAlarmPopover');
  if (existing) { existing.remove(); return; }

  const entity = _haEntities.find(e => e.entity_id === entityId);
  if (!entity) return;

  const s = entity.state;
  const busy = s === 'arming' || s === 'pending';

  const popover = document.createElement('div');
  popover.id = 'haAlarmPopover';
  popover.className = 'ha-alarm-popover';
  popover.innerHTML = `
    <div class="ha-popover-title">🔐 ${escapeHtml(entity.name)}</div>
    <button class="ha-popover-btn ${s === 'disarmed' ? 'active' : ''}"
      ${s === 'disarmed' || busy ? 'disabled' : ''}
      onclick="popoverAlarmAction('alarm_disarm','${entityId}')">🔓 Disarm</button>
    <button class="ha-popover-btn ${s === 'armed_away' ? 'active-arm' : ''}"
      ${s === 'armed_away' || busy ? 'disabled' : ''}
      onclick="popoverAlarmAction('alarm_arm_away','${entityId}')">🏃 Arm Away</button>
    <button class="ha-popover-btn ${s === 'armed_home' ? 'active-arm' : ''}"
      ${s === 'armed_home' || busy ? 'disabled' : ''}
      onclick="popoverAlarmAction('alarm_arm_home','${entityId}')">🏠 Arm Home</button>
  `;

  const rect = evt.currentTarget.getBoundingClientRect();
  popover.style.top = (rect.bottom + 8) + 'px';
  popover.style.right = (window.innerWidth - rect.right) + 'px';
  document.body.appendChild(popover);

  setTimeout(() => document.addEventListener('click', closeAlarmPopover, { once: true }), 0);
}

function closeAlarmPopover() {
  document.getElementById('haAlarmPopover')?.remove();
}

function optimisticHAUpdate(entityId, newState) {
  const entity = _haEntities.find(e => e.entity_id === entityId);
  if (!entity) return;
  entity.state = newState;
  if (entity.entity_type === 'alarm') {
    const ALARM_LABELS = {
      disarmed: ['🔓', 'ha-alarm-disarmed', 'Disarmed'],
      armed_away: ['🔒', 'ha-alarm-armed', 'Armed Away'],
      armed_home: ['🔒', 'ha-alarm-armed', 'Armed Home'],
      arming: ['⏳', 'ha-alarm-pending', 'Arming…'],
    };
    const [icon, css, label] = ALARM_LABELS[newState] || ['❓', 'ha-state-unknown', newState];
    entity.icon = icon; entity.css_class = css; entity.state_label = label;
  } else if (String(entity.entity_id).startsWith('cover.')) {
    entity.icon = newState === 'open' ? '🚗' : '🏠';
    entity.css_class = newState === 'open' ? 'ha-state-open' : 'ha-state-closed';
    entity.state_label = newState;
  }
  renderHAStatus();
}

function toggleCoverPopover(evt, entityId) {
  evt.stopPropagation();
  const existing = document.getElementById('haCoverPopover');
  if (existing) { existing.remove(); return; }

  const entity = _haEntities.find(e => e.entity_id === entityId);
  if (!entity) return;

  const s = entity.state;
  const busy = s === 'opening' || s === 'closing';

  const popover = document.createElement('div');
  popover.id = 'haCoverPopover';
  popover.className = 'ha-alarm-popover';
  popover.innerHTML = `
    <div class="ha-popover-title">🚗 ${escapeHtml(entity.name)}</div>
    <button class="ha-popover-btn ${s === 'open' ? 'active-arm' : ''}"
      ${s === 'open' || busy ? 'disabled' : ''}
      onclick="coverAction('open_cover','${entityId}')">🔼 Open</button>
    <button class="ha-popover-btn ${s === 'closed' ? 'active' : ''}"
      ${s === 'closed' || busy ? 'disabled' : ''}
      onclick="coverAction('close_cover','${entityId}')">🔽 Close</button>
  `;

  const rect = evt.currentTarget.getBoundingClientRect();
  popover.style.top = (rect.bottom + 8) + 'px';
  popover.style.right = (window.innerWidth - rect.right) + 'px';
  document.body.appendChild(popover);

  setTimeout(() => document.addEventListener('click', () => document.getElementById('haCoverPopover')?.remove(), { once: true }), 0);
}

async function coverAction(action, entityId) {
  const btns = document.querySelectorAll('#haCoverPopover button');
  btns.forEach(b => b.disabled = true);
  try {
    await API.post(`/api/homeassistant/cover/${action}`, { entity_id: entityId });
    showToast(action === 'open_cover' ? '🔼 Opening garage…' : '🔽 Closing garage…', 'success');
    document.getElementById('haCoverPopover')?.remove();
    optimisticHAUpdate(entityId, action === 'open_cover' ? 'opening' : 'closing');
    setTimeout(loadHAStatus, 4000);
  } catch(e) {
    showToast('Failed to send command', 'error');
    btns.forEach(b => b.disabled = false);
  }
}

async function popoverAlarmAction(action, entityId) {
  const btns = document.querySelectorAll('#haAlarmPopover button');
  btns.forEach(b => b.disabled = true);
  const expectedState = action === 'alarm_disarm' ? 'disarmed'
    : action === 'alarm_arm_away' ? 'armed_away'
    : action === 'alarm_arm_home' ? 'armed_home' : 'arming';
  try {
    await API.post(`/api/homeassistant/alarm/${action}`, { entity_id: entityId });
    showToast(action === 'alarm_disarm' ? '🔓 Alarm disarmed' : '🔒 Alarm armed', 'success');
    closeAlarmPopover();
    optimisticHAUpdate(entityId, expectedState);
    setTimeout(loadHAStatus, 5000);
  } catch(e) {
    showToast('Failed to send alarm command', 'error');
    btns.forEach(b => b.disabled = false);
  }
}

let _camRefreshTimer = null;

async function loadDashCameras() {
  const strip = document.getElementById('dashCameras');
  if (!strip) return;
  try {
    const data = await API.get('/api/security/cameras');
    const cameras = (data.cameras || []).slice(0, 2);
    if (!cameras.length) {
      strip.style.display = 'none';
      strip.innerHTML = '';
      return;
    }

    strip.innerHTML = cameras.map(cam => {
      const id = encodeURIComponent(cam.ha_entity_id);
      const name = cam.name || cam.ha_entity_id;
      return `
      <a href="/security.html" class="dash-cam-card" title="${escapeHtml(name)}">
        <img class="dash-cam-img" id="dashCam_${cam.ha_entity_id}"
             src="/api/security/camera/${id}/snapshot?t=${Date.now()}"
             alt="${escapeHtml(name)}" onerror="this.style.display='none'">
      </a>`;
    }).join('');
    strip.style.display = 'flex';

    clearInterval(_camRefreshTimer);
    _camRefreshTimer = setInterval(() => {
      cameras.forEach(cam => {
        const img = document.getElementById(`dashCam_${cam.ha_entity_id}`);
        if (img) {
          img.style.display = '';
          img.src = `/api/security/camera/${encodeURIComponent(cam.ha_entity_id)}/snapshot?t=${Date.now()}`;
        }
      });
    }, 10000);
  } catch(e) {}
}

// ── PWA Install Prompt ────────────────────────────────────────
let _installPrompt = null;
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _installPrompt = e;
  const btn = document.getElementById('pwaInstallBtn');
  if (btn) btn.style.display = 'flex';
});
window.addEventListener('appinstalled', () => {
  _installPrompt = null;
  const btn = document.getElementById('pwaInstallBtn');
  if (btn) btn.style.display = 'none';
});
function pwaInstall() {
  if (!_installPrompt) return;
  _installPrompt.prompt();
  _installPrompt.userChoice.then(() => { _installPrompt = null; });
}

// ── Fullscreen ────────────────────────────────────────────────
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
}
function _updateFullscreenBtn() {
  const btn = document.getElementById('fullscreenBtn');
  if (!btn) return;
  btn.title = document.fullscreenElement ? 'Exit fullscreen' : 'Enter fullscreen';
  btn.innerHTML = document.fullscreenElement
    ? '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v3a2 2 0 01-2 2H3m18 0h-3a2 2 0 01-2-2V3m0 18v-3a2 2 0 012-2h3M3 16h3a2 2 0 012 2v3"/></svg>'
    : '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>';
}
document.addEventListener('fullscreenchange', _updateFullscreenBtn);

function afterPagePaint(fn) {
  requestAnimationFrame(() => setTimeout(fn, 0));
}

async function loadWeather() {
  const el = document.getElementById('weatherWidget');
  if (!el) return;
  try {
    const data = await API.get('/api/weather/');
    if (!data || data.error) { el.innerHTML = ''; return; }
    const t = data.tomorrow;
    const precipStr = t && t.precip != null ? `<span class="weather-tmr-precip">💧${t.precip}%</span>` : '';
    el.innerHTML = `
      <div class="weather-now">
        <div class="weather-icon">${data.icon}</div>
        <span class="weather-temp">${Math.round(data.temp)}°</span>
        <span class="weather-condition">${escapeHtml(data.condition)}</span>
        <span class="weather-meta">↑${Math.round(data.high)}° ↓${Math.round(data.low)}°</span>
      </div>
      ${t && t.high != null ? `
        <div class="weather-tomorrow">
          <span class="weather-tmr-label">Tomorrow</span>
          <span class="weather-tmr-icon">${t.icon}</span>
          <span class="weather-tmr-hilo">↑${Math.round(t.high)}° ↓${Math.round(t.low)}°</span>
          ${precipStr}
        </div>` : ''}
    `;
  } catch (e) {
    el.innerHTML = '';
  }
}

// Init sidebar + theme on all pages
document.addEventListener('DOMContentLoaded', () => {
  ensureShell();
  loadSidebarMembers();
  initTheme();
  _updateFullscreenBtn();
  mountPersonFilter();
  afterPagePaint(() => {
    loadWeather();
    loadHAStatus();
    loadDashCameras();
  });
  setInterval(loadHAStatus, 30000);
  setInterval(loadMessageBadge, 60000);
  ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'].forEach(ev =>
    document.addEventListener(ev, _onPersonActivity, { passive: true })
  );
  armPersonIdle();
});
