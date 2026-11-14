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

// ── Message badges on person names ────────────────────────────
async function loadMessageBadge() {
  const chips = document.querySelectorAll('#personFilter .person-chip');
  if (!chips.length) return;
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
      const badge = chip.querySelector('.person-badge');
      if (!badge) return;
      const n = counts[chip.dataset.person] || 0;
      if (n > 0) {
        badge.textContent = n > 9 ? '9+' : String(n);
        badge.style.display = 'inline-block';
      } else {
        badge.style.display = 'none';
      }
    });
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
  loadSidebarMembers();
  initTheme();
  _updateFullscreenBtn();
  mountPersonFilter();
  afterPagePaint(loadWeather);
  ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'].forEach(ev =>
    document.addEventListener(ev, _onPersonActivity, { passive: true })
  );
  armPersonIdle();
});
