// ── API Helper (In-Memory Cache + Route Abort + Concurrency Cap + Deduplication) ──
const _getCache = new Map();
const _getInflight = new Map();
let _getCacheGen = 0;
const GET_CACHE_MS = 10000;       // Fresh hit window across rapid screen switches
const GET_STALE_MAX_MS = 600000;  // Up to 10m for instant optimistic paint via API.peek()
const FETCH_TIMEOUT_MS = 12000;
const MAX_CONCURRENT_API = 4;     // Leave 2 of 6 HTTP/1.1 sockets free for assets/snapshots

let _activeApiCount = 0;
const _apiWaitQueue = [];
let _routeAbort = new AbortController();

function _acquireApiSlot(signal) {
  if (_activeApiCount < MAX_CONCURRENT_API) {
    _activeApiCount += 1;
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const entry = { resolve, reject };
    if (signal) {
      if (signal.aborted) {
        reject(new DOMException('Navigation aborted', 'AbortError'));
        return;
      }
      entry.onAbort = () => {
        const idx = _apiWaitQueue.indexOf(entry);
        if (idx >= 0) _apiWaitQueue.splice(idx, 1);
        reject(new DOMException('Navigation aborted', 'AbortError'));
      };
      signal.addEventListener('abort', entry.onAbort, { once: true });
    }
    _apiWaitQueue.push(entry);
  });
}

function _releaseApiSlot() {
  _activeApiCount = Math.max(0, _activeApiCount - 1);
  while (_apiWaitQueue.length && _activeApiCount < MAX_CONCURRENT_API) {
    const next = _apiWaitQueue.shift();
    if (next.onAbort && next.signal) {
      next.signal.removeEventListener('abort', next.onAbort);
    }
    _activeApiCount += 1;
    next.resolve();
  }
}

function abortRouteRequests() {
  try { _routeAbort.abort(); } catch (_) {}
  _routeAbort = new AbortController();
}

function isAbortError(err) {
  return Boolean(err && (err.name === 'AbortError' || err.code === 20));
}

function _bustGetCache() {
  _getCacheGen += 1;
  _getCache.clear();
}

function _fetchWithTimeout(path, options = {}, timeoutMs = FETCH_TIMEOUT_MS, parentSignal = null) {
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, timeoutMs);

  const onParentAbort = () => ctrl.abort();
  if (parentSignal) {
    if (parentSignal.aborted) {
      clearTimeout(timer);
      return Promise.reject(new DOMException('Navigation aborted', 'AbortError'));
    }
    parentSignal.addEventListener('abort', onParentAbort, { once: true });
  }

  return fetch(path, { ...options, signal: ctrl.signal }).finally(() => {
    clearTimeout(timer);
    if (parentSignal) parentSignal.removeEventListener('abort', onParentAbort);
    void timedOut;
  });
}

async function _send(path, options) {
  _bustGetCache();
  const r = await _fetchWithTimeout(path, options, 20000, null);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

const API = {
  peek(path) {
    const hit = _getCache.get(path);
    if (hit && Date.now() - hit.at < GET_STALE_MAX_MS) return hit.data;
    return undefined;
  },
  seed(path, data) {
    if (data !== undefined) {
      _getCache.set(path, { at: Date.now(), data });
    }
  },
  async get(path, opts) {
    const force = opts && opts.force;
    const isShell = Boolean(opts && opts.shell);
    const timeoutMs = (opts && opts.timeout) || FETCH_TIMEOUT_MS;
    const navSignal = isShell ? null : _routeAbort.signal;

    if (navSignal && navSignal.aborted) {
      throw new DOMException('Navigation aborted', 'AbortError');
    }

    if (!force) {
      const hit = _getCache.get(path);
      if (hit && Date.now() - hit.at < GET_CACHE_MS) return hit.data;
    }

    let entry = _getInflight.get(path);
    if (!entry) {
      const gen = _getCacheGen;
      const netCtrl = new AbortController();
      const promise = (async () => {
        await _acquireApiSlot(netCtrl.signal);
        try {
          const r = await _fetchWithTimeout(path, {}, timeoutMs, netCtrl.signal);
          if (!r.ok) throw new Error(await r.text());
          const data = await r.json();
          if (gen === _getCacheGen) _getCache.set(path, { at: Date.now(), data });
          return data;
        } finally {
          _releaseApiSlot();
        }
      })();
      entry = { promise, netCtrl, refCount: 0, shellPinned: false };
      _getInflight.set(path, entry);
      promise.finally(() => {
        if (_getInflight.get(path) === entry) _getInflight.delete(path);
      }).catch(() => {});
    }

    if (isShell) entry.shellPinned = true;
    entry.refCount += 1;

    if (!navSignal) {
      try {
        return await entry.promise;
      } finally {
        entry.refCount = Math.max(0, entry.refCount - 1);
      }
    }

    return new Promise((resolve, reject) => {
      let settled = false;
      const onAbort = () => {
        if (settled) return;
        settled = true;
        entry.refCount = Math.max(0, entry.refCount - 1);
        if (entry.refCount === 0 && !entry.shellPinned) {
          try { entry.netCtrl.abort(); } catch (_) {}
        }
        reject(new DOMException('Navigation aborted', 'AbortError'));
      };
      navSignal.addEventListener('abort', onAbort, { once: true });
      entry.promise.then(
        val => {
          if (settled) return;
          settled = true;
          navSignal.removeEventListener('abort', onAbort);
          entry.refCount = Math.max(0, entry.refCount - 1);
          resolve(val);
        },
        err => {
          if (settled) return;
          settled = true;
          navSignal.removeEventListener('abort', onAbort);
          entry.refCount = Math.max(0, entry.refCount - 1);
          reject(err);
        }
      );
    });
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

function eventDisplayColor(event) {
  if (event && event.member_color && !Number(event.is_family)) return event.member_color;
  return (event && event.color) || '#F6BF26';
}

function memberAvatarHtml(member, className) {
  const color = (member && member.color) || '#9AA0B8';
  const cls = 'member-avatar' + (className ? ' ' + className : '');
  if (member && member.avatar_photo) {
    const src = '/api/members/avatar/' + encodeURIComponent(member.avatar_photo);
    return `<span class="${cls} has-photo" style="background:${color}22"><img src="${src}" alt="" decoding="async"></span>`;
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

/** Match server calendar filter: selected person + family-wide events. */
function filterEventsForPerson(events, person) {
  if (!events) return [];
  if (!person || person === 'all') return events;
  return events.filter(e => e.is_family || String(e.member_id) === String(person));
}

function filterRowsByMember(rows, person, key) {
  if (!rows) return [];
  if (!person || person === 'all') return rows;
  const field = key || 'member_id';
  return rows.filter(r => String(r[field]) === String(person));
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
  mountPersonFilter();
  if (FHRouter.currentRoute() === '/') {
    if (_personFilterChange) _personFilterChange('all');
  } else {
    FHRouter.navigate('/', { replace: true });
  }
}

let _lastActivityTs = 0;
function _onPersonActivity() {
  const now = Date.now();
  if (now - _lastActivityTs < 1000) return;
  _lastActivityTs = now;
  armPersonIdle();
}

let _personFilterChange = null;
function onPersonFilterChange(fn) {
  _personFilterChange = fn;
}

function _renderPersonFilterChips(el, members) {
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
      const next = btn.dataset.person;
      if (String(getPersonFilter()) === String(next)) return;
      setPersonFilter(next);
      el.querySelectorAll('.person-chip').forEach(b => b.classList.toggle('active', b === btn));
      // Person views filter cached Everyone data client-side — no abort/refetch.
      if (_personFilterChange) _personFilterChange(getPersonFilter());
      const cachedMsgs = API.peek('/api/messages/');
      if (cachedMsgs) _paintMessageBadges(cachedMsgs);
      else loadMessageBadge();
    };
  });
}

async function mountPersonFilter(onChange) {
  if (onChange !== undefined) _personFilterChange = onChange;
  const el = document.getElementById('personFilter');
  if (!el) return [];
  const cached = API.peek('/api/members/');
  if (cached) _renderPersonFilterChips(el, cached);
  let members = [];
  try {
    members = await API.get('/api/members/', { shell: true });
  } catch (e) {
    if (!cached) el.innerHTML = '';
    return cached || [];
  }
  _renderPersonFilterChips(el, members);
  loadMessageBadge();
  return members;
}

// ── Sidebar Members ───────────────────────────────────────────
function _paintSidebarMembers(members) {
  const el = document.getElementById('sidebarMembers');
  if (!el || getComputedStyle(el).display === 'none') return;
  el.innerHTML = (members || []).map(m => `
    <div class="sidebar-member-chip">
      ${memberAvatarHtml(m)}
      <span>${escapeHtml(m.name)}</span>
    </div>
  `).join('');
}

async function loadSidebarMembers() {
  const el = document.getElementById('sidebarMembers');
  if (!el || getComputedStyle(el).display === 'none') return;
  try {
    const members = await API.get('/api/members/', { shell: true });
    _paintSidebarMembers(members);
  } catch(e) {}
}

// ── Date & Timezone Helpers (Cached in localStorage for 0ms startup) ──
let TZ = localStorage.getItem('fh_tz') || 'America/New_York';
let _tzLoaded = !!localStorage.getItem('fh_tz');

function loadAppSettings(force) {
  return API.get('/api/settings/', force ? { force: true, shell: true } : { shell: true });
}

function _syncCachedSettings(s) {
  if (!s) return;
  if (s.timezone) {
    TZ = s.timezone;
    try { localStorage.setItem('fh_tz', s.timezone); } catch (_) {}
  }
  if (s.dark_mode_start != null) {
    _darkStart = parseInt(s.dark_mode_start || '21', 10);
    try { localStorage.setItem('fh_dark_start', String(_darkStart)); } catch (_) {}
  }
  if (s.dark_mode_end != null) {
    _darkEnd = parseInt(s.dark_mode_end || '7', 10);
    try { localStorage.setItem('fh_dark_end', String(_darkEnd)); } catch (_) {}
  }
  if (s.dim_minutes != null) {
    _dimDelay = parseInt(s.dim_minutes || '5', 10) * 60000;
    try { localStorage.setItem('fh_dim_min', String(s.dim_minutes)); } catch (_) {}
  }
}

async function initAppTimezone(force) {
  if (_tzLoaded && !force) {
    if (!API.peek('/api/settings/')) {
      loadAppSettings().then(_syncCachedSettings).catch(() => {});
    }
    return;
  }
  try {
    const s = await loadAppSettings(force);
    _syncCachedSettings(s);
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
  const d = new Date(date);
  const parts = d.toLocaleDateString('en-US', { timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit' }).split('/');
  return `${parts[2]}-${parts[0]}-${parts[1]}`;
}

function toLocalISO(date) {
  const d = new Date(date);
  const etStr = d.toLocaleString('en-US', { timeZone: TZ, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12: false });
  const [datePart, timePart] = etStr.split(', ');
  const [m, day, yr] = datePart.split('/');
  return `${yr}-${m}-${day}T${timePart.replace('24:', '00:')}`;
}

// ── Night Mode + Auto-dim ──────────────────────────────────────
let _darkStart = parseInt(localStorage.getItem('fh_dark_start') || '21', 10);
let _darkEnd   = parseInt(localStorage.getItem('fh_dark_end') || '7', 10);
let _dimDelay  = parseInt(localStorage.getItem('fh_dim_min') || '5', 10) * 60000;
let _dimTimer  = null;
let _themeBound = false;
let _lastDimReset = 0;

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
  const now = Date.now();
  if (now - _lastDimReset < 500 && !document.documentElement.classList.contains('dimmed')) return;
  _lastDimReset = now;
  document.documentElement.classList.remove('dimmed');
  clearTimeout(_dimTimer);
  if (_dimDelay > 0) {
    _dimTimer = setTimeout(() => document.documentElement.classList.add('dimmed'), _dimDelay);
  }
}

async function initTheme(force) {
  _applyTheme();
  if (!_themeBound) {
    _themeBound = true;
    setInterval(_applyTheme, 60000);
    ['touchstart', 'mousemove', 'keydown', 'click'].forEach(ev =>
      document.addEventListener(ev, _resetDim, { passive: true })
    );
  }
  _resetDim();
  if (force || API.peek('/api/settings/')) {
    try {
      const s = await loadAppSettings(force);
      _syncCachedSettings(s);
      _applyTheme();
      _lastDimReset = 0;
      _resetDim();
    } catch(e) {}
  }
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

function _paintMessageBadges(msgs) {
  const chips = document.querySelectorAll('#personFilter .person-chip');
  const nav = document.getElementById('msgNavBadge');
  if ((!chips.length && !nav) || !Array.isArray(msgs)) return;
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
}

// ── Message badges on person names and the Messages icon ─────
async function loadMessageBadge() {
  const chips = document.querySelectorAll('#personFilter .person-chip');
  const nav = document.getElementById('msgNavBadge');
  if (!chips.length && !nav) return;
  try {
    const msgs = await API.get('/api/messages/', { shell: true });
    _paintMessageBadges(msgs);
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
  if (header) {
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
  if (!document.getElementById('pageModals')) {
    const modalHost = document.createElement('div');
    modalHost.id = 'pageModals';
    document.querySelectorAll('body > .modal-overlay, body > .camera-modal-overlay').forEach(el => {
      modalHost.appendChild(el);
    });
    document.body.appendChild(modalHost);
  }
  if (!document.querySelector('.slideshow-trigger')) {
    const btn = document.createElement('button');
    btn.className = 'slideshow-trigger';
    btn.title = 'Start slideshow';
    btn.textContent = '📷';
    btn.onclick = () => { if (typeof Slideshow !== 'undefined') Slideshow.start(); };
    document.body.appendChild(btn);
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

function _applyHAStatesPayload(data) {
  const badge = document.getElementById('haStatusBadge');
  if (!badge) return;
  if (!data || data.error || !data.entities || !data.entities.length) {
    badge.innerHTML = '';
    _haEntities = [];
    return;
  }
  _haEntities = data.entities;
  renderHAStatus();
}

async function loadHAStatus(force) {
  if (document.hidden && !force) return;
  const badge = document.getElementById('haStatusBadge');
  if (!badge) return;
  try {
    const data = await API.get('/api/homeassistant/states', force ? { force: true, shell: true } : { shell: true });
    _applyHAStatesPayload(data);
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
    setTimeout(() => loadHAStatus(true), 4000);
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
    setTimeout(() => loadHAStatus(true), 5000);
  } catch(e) {
    showToast('Failed to send alarm command', 'error');
    btns.forEach(b => b.disabled = false);
  }
}

// ── Active WebRTC & Camera Stream Cleanup ─────────────────────
const _activePeers = new Set();

function registerPeerConnection(pc) {
  if (pc) _activePeers.add(pc);
  return pc;
}

function unregisterPeerConnection(pc) {
  if (!pc) return;
  _activePeers.delete(pc);
  try { pc.close(); } catch (_) {}
}

async function connectCameraWebRTC(entityId, videoEl) {
  /** Negotiate go2rtc WebRTC via /api/security/camera/{id}/webrtc and attach to <video>. */
  if (!entityId || !videoEl) throw new Error('Missing camera target');
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
  });
  registerPeerConnection(pc);
  videoEl._fhPeer = pc;

  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });
  pc.ontrack = (ev) => {
    if (ev.streams && ev.streams[0]) {
      videoEl.srcObject = ev.streams[0];
    } else {
      const stream = videoEl.srcObject || new MediaStream();
      stream.addTrack(ev.track);
      videoEl.srcObject = stream;
    }
    videoEl.classList.add('loaded');
    videoEl.play().catch(() => {});
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  // Wait briefly for ICE candidates so go2rtc gets a usable offer
  await new Promise((resolve) => {
    if (pc.iceGatheringState === 'complete') {
      resolve();
      return;
    }
    const done = () => {
      pc.removeEventListener('icegatheringstatechange', onChange);
      resolve();
    };
    const onChange = () => {
      if (pc.iceGatheringState === 'complete') done();
    };
    pc.addEventListener('icegatheringstatechange', onChange);
    setTimeout(done, 1500);
  });

  const local = pc.localDescription;
  if (!local || !local.sdp) {
    unregisterPeerConnection(pc);
    throw new Error('No local SDP');
  }

  const resp = await _fetchWithTimeout(
    `/api/security/camera/${encodeURIComponent(entityId)}/webrtc`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sdp: local.sdp, type: local.type || 'offer' }),
    },
    12000,
    null
  );
  if (!resp.ok) {
    unregisterPeerConnection(pc);
    throw new Error(await resp.text());
  }
  const answer = await resp.json();
  if (!answer || !answer.sdp) {
    unregisterPeerConnection(pc);
    throw new Error('Empty WebRTC answer');
  }
  await pc.setRemoteDescription({ type: answer.type || 'answer', sdp: answer.sdp });
  return pc;
}

function killActiveStreams() {
  for (const pc of _activePeers) {
    try { pc.close(); } catch (_) {}
  }
  _activePeers.clear();
  document.querySelectorAll('video.camera-feed-video, #cameraModalVideo').forEach(v => {
    try {
      v.pause();
      v.srcObject = null;
      v.removeAttribute('src');
    } catch (_) {}
  });
  document.querySelectorAll('img.camera-feed-img, #cameraModalImg').forEach(img => {
    try {
      img.onload = null;
      img.onerror = null;
      img.removeAttribute('src');
      img.src = '';
    } catch (_) {}
  });
}
window.addEventListener('pagehide', killActiveStreams);
window.addEventListener('beforeunload', killActiveStreams);

let _dashCamEpoch = 0;

function _snapBucket() {
  return Math.floor(Date.now() / 10000);
}

function _applyDashCamerasPayload(data) {
  const strip = document.getElementById('dashCameras');
  if (!strip) return;
  const epoch = ++_dashCamEpoch;
  const cameras = ((data && data.cameras) || []).slice(0, 2);
  if (!cameras.length) {
    strip.style.display = 'none';
    strip.innerHTML = '';
    return;
  }

  const bucket = _snapBucket();
  strip.innerHTML = cameras.map(cam => {
    const id = encodeURIComponent(cam.ha_entity_id);
    const name = cam.name || cam.ha_entity_id;
    return `
    <a href="/security.html" class="dash-cam-card" title="${escapeHtml(name)}">
      <img class="dash-cam-img" id="dashCam_${cam.ha_entity_id}"
           src="/api/security/camera/${id}/snapshot?t=${bucket}"
           alt="${escapeHtml(name)}" decoding="async">
    </a>`;
  }).join('');
  strip.style.display = 'flex';

  // Chain next thumbnail load AFTER current load/error finishes so we never abort in-flight images
  cameras.forEach(cam => {
    const img = document.getElementById(`dashCam_${cam.ha_entity_id}`);
    if (!img) return;
    const scheduleNext = () => {
      if (epoch !== _dashCamEpoch) return;
      setTimeout(() => {
        if (epoch !== _dashCamEpoch) return;
        const el = document.getElementById(`dashCam_${cam.ha_entity_id}`);
        if (!el) return;
        if (document.hidden || FHRouter.currentRoute() === '/security.html') {
          scheduleNext();
          return;
        }
        el.style.display = '';
        el.src = `/api/security/camera/${encodeURIComponent(cam.ha_entity_id)}/snapshot?t=${_snapBucket()}`;
      }, 10000);
    };
    img.onload = scheduleNext;
    img.onerror = function () {
      this.style.display = 'none';
      scheduleNext();
    };
  });
}

async function loadDashCameras(force) {
  const strip = document.getElementById('dashCameras');
  if (!strip) return;
  try {
    const data = await API.get('/api/security/cameras', force ? { force: true, shell: true } : { shell: true });
    _applyDashCamerasPayload(data);
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

let _weatherForecast = null;

function _wxRound(n) {
  if (n == null || n === '') return null;
  const v = Number(n);
  return Number.isFinite(v) ? Math.round(v) : null;
}

const _TEMP_EDGES = [10, 25, 32, 45, 58, 68, 78, 88, 98];

function _tempBand(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '';
  let i = 0;
  while (i < _TEMP_EDGES.length && v > _TEMP_EDGES[i]) i++;
  return 'wx-band-' + i;
}

function _setTempBand(el, n) {
  if (!el) return;
  for (let i = 0; i <= _TEMP_EDGES.length; i++) el.classList.remove('wx-band-' + i);
  const band = _tempBand(n);
  if (band) el.classList.add(band);
}

function _wxTempHtml(n, prefix, extraClass) {
  const v = _wxRound(n);
  if (v == null) return '';
  const cls = ['wx-temp', _tempBand(v), extraClass].filter(Boolean).join(' ');
  return `<span class="${cls}">${prefix || ''}${v}°</span>`;
}

function _weatherDayLabel(dateStr, index) {
  if (index === 0) return 'Today';
  if (index === 1) return 'Tomorrow';
  const parts = String(dateStr || '').slice(0, 10).split('-').map(Number);
  if (parts.length < 3 || parts.some(n => !n)) return dateStr || '';
  const dt = new Date(parts[0], parts[1] - 1, parts[2]);
  if (Number.isNaN(dt.getTime())) return dateStr || '';
  return dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function _onWeatherKey(e) {
  if (e.key === 'Escape') closeWeatherModal();
}

function ensureWeatherModal() {
  let overlay = document.getElementById('weatherModal');
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.id = 'weatherModal';
  overlay.className = 'modal-overlay';
  overlay.style.display = 'none';
  overlay.innerHTML = `
    <div class="modal weather-modal">
      <div class="modal-header">
        <h3 id="weatherModalCity"></h3>
        <button class="btn-close" type="button" id="weatherModalClose" aria-label="Close">✕</button>
      </div>
      <div class="modal-body">
        <div class="weather-modal-list" id="weatherModalList"></div>
      </div>
    </div>`;
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeWeatherModal();
  });
  overlay.querySelector('.weather-modal').addEventListener('click', (e) => e.stopPropagation());
  overlay.querySelector('#weatherModalClose').addEventListener('click', closeWeatherModal);
  document.body.appendChild(overlay);
  return overlay;
}

function openWeatherModal() {
  const data = _weatherForecast;
  if (!data || !Array.isArray(data.days) || !data.days.length) return;
  const overlay = ensureWeatherModal();
  const cityEl = document.getElementById('weatherModalCity');
  const listEl = document.getElementById('weatherModalList');
  if (cityEl) cityEl.textContent = data.city || 'Weather';
  if (listEl) {
    listEl.innerHTML = data.days.map((day, i) => {
      const high = _wxRound(day.high);
      const low = _wxRound(day.low);
      const precipN = _wxRound(day.precip);
      const windN = _wxRound(day.wind);
      const precip = precipN == null ? '' : `<span class="weather-modal-precip">💧${precipN}%</span>`;
      const wind = windN == null ? '' : `<span class="weather-modal-wind">${windN} mph</span>`;
      const hi = _wxTempHtml(high, '↑', 'weather-modal-high');
      const lo = _wxTempHtml(low, '↓', 'weather-modal-low');
      const accent = _tempBand(high != null ? high : low);
      return `
        <div class="weather-modal-day${i === 0 ? ' is-today' : ''}${accent ? ' ' + accent : ''}">
          <div class="weather-modal-icon">${day.icon || ''}</div>
          <div class="weather-modal-when">
            <div class="weather-modal-label">${escapeHtml(_weatherDayLabel(day.date, i))}</div>
            <div class="weather-modal-cond">${escapeHtml(day.condition || '')}</div>
          </div>
          <div class="weather-modal-temps">${hi}${lo}</div>
          <div class="weather-modal-extra">${precip}${wind}</div>
        </div>`;
    }).join('');
  }
  const wasOpen = overlay.style.display === 'flex';
  overlay.style.display = 'flex';
  if (!wasOpen) document.addEventListener('keydown', _onWeatherKey);
}

function closeWeatherModal() {
  const el = document.getElementById('weatherModal');
  if (el) el.style.display = 'none';
  document.removeEventListener('keydown', _onWeatherKey);
}

function _bindWeatherClick(el, data) {
  const days = data && Array.isArray(data.days) ? data.days : null;
  if (days && days.length) {
    _weatherForecast = data;
    el.style.cursor = 'pointer';
    el.title = 'Weather forecast';
    el.onclick = openWeatherModal;
  } else {
    _weatherForecast = null;
    el.style.cursor = '';
    el.removeAttribute('title');
    el.onclick = null;
  }
}

function _applyWeatherPayload(data) {
  const el = document.getElementById('weatherWidget');
  if (!el) return;
  if (!data || data.error || data.warming) {
    if (data && data.warming) {
      setTimeout(() => loadWeather(true), 3500);
      return;
    }
    el.innerHTML = '';
    _setTempBand(el, null);
    _bindWeatherClick(el, null);
    return;
  }
  const t = data.tomorrow;
  const precipStr = t && t.precip != null ? `<span class="weather-tmr-precip">💧${t.precip}%</span>` : '';
  const todayHi = _wxTempHtml(data.high, '↑');
  const todayLo = _wxTempHtml(data.low, '↓');
  el.innerHTML = `
    <div class="weather-now">
      <div class="weather-icon">${data.icon}</div>
      ${_wxTempHtml(data.temp, '', 'weather-temp')}
      <span class="weather-condition">${escapeHtml(data.condition)}</span>
      <span class="weather-meta">${todayHi}${todayLo}</span>
    </div>
    ${t && t.high != null ? `
      <div class="weather-tomorrow">
        <span class="weather-tmr-label">Tomorrow</span>
        <span class="weather-tmr-icon">${t.icon}</span>
        <span class="weather-tmr-hilo">${_wxTempHtml(t.high, '↑')}${_wxTempHtml(t.low, '↓')}</span>
        ${precipStr}
      </div>` : ''}
  `;
  _setTempBand(el, data.temp);
  _bindWeatherClick(el, data);
}

async function loadWeather(force) {
  const el = document.getElementById('weatherWidget');
  if (!el) return;
  try {
    const data = await API.get('/api/weather/', force ? { force: true, shell: true } : { shell: true });
    _applyWeatherPayload(data);
  } catch (e) {
    el.innerHTML = '';
    _setTempBand(el, null);
    _bindWeatherClick(el, null);
  }
}

async function loadShellBootstrap() {
  try {
    const b = await API.get('/api/bootstrap', { shell: true });
    if (!b) return;
    if (b.settings) {
      API.seed('/api/settings/', b.settings);
      _syncCachedSettings(b.settings);
      _tzLoaded = true;
      _applyTheme();
      _lastDimReset = 0;
      _resetDim();
    }
    if (Array.isArray(b.members)) {
      API.seed('/api/members/', b.members);
      const pf = document.getElementById('personFilter');
      if (pf) _renderPersonFilterChips(pf, b.members);
      _paintSidebarMembers(b.members);
    }
    if (Array.isArray(b.messages)) {
      API.seed('/api/messages/', b.messages);
      _paintMessageBadges(b.messages);
    }
    if (b.weather) {
      API.seed('/api/weather/', b.weather);
      _applyWeatherPayload(b.weather);
    }
    if (b.ha_states) {
      API.seed('/api/homeassistant/states', b.ha_states);
      _applyHAStatesPayload(b.ha_states);
      if (b.ha_states.warming) {
        setTimeout(() => loadHAStatus(true), 2500);
      }
    }
    if (b.cameras) {
      API.seed('/api/security/cameras', b.cameras);
      _applyDashCamerasPayload(b.cameras);
    }
  } catch (e) {
    // Individual fallbacks if bootstrap failed
    mountPersonFilter();
    loadSidebarMembers();
    loadWeather();
    loadHAStatus();
    loadDashCameras();
  }
}

// ── Instant App Shell Router (Zero-Reload Screen Switching) ───
const FHRouter = (() => {
  const ROUTES = {
    '/':              { html: '/',              script: '/js/dashboard.js', extras: ['/js/agenda.js'] },
    '/calendar.html': { html: '/calendar.html', script: '/js/calendar.js', extras: ['/js/agenda.js'] },
    '/chores.html':   { html: '/chores.html',   script: '/js/chores.js' },
    '/meals.html':    { html: '/meals.html',    script: '/js/meals.js' },
    '/shopping.html': { html: '/shopping.html', script: '/js/shopping.js' },
    '/security.html': { html: '/security.html', script: '/js/security.js' },
    '/home.html':     { html: '/home.html',     script: '/js/home.js' },
    '/messages.html': { html: '/messages.html', script: '/js/messages.js' },
    '/settings.html': { html: '/settings.html', script: '/js/settings.js' },
  };

  const _controllers = new Map();
  const _tplCache = new Map();
  const _tplInflight = new Map();
  const _scriptInflight = new Map();
  let _activeRoute = null;
  let _activeCleanup = null;
  let _navSeq = 0;
  let _booted = false;
  let _prefetchBusy = false;

  function _assetSuffix() {
    const s = document.querySelector('script[src*="/js/api.js"]');
    if (!s) return '';
    const idx = s.src.indexOf('?');
    return idx >= 0 ? s.src.slice(idx) : '';
  }

  function normRoute(pathname) {
    if (!pathname || pathname === '/' || pathname === '/index.html') return '/';
    return pathname;
  }

  function currentRoute() {
    return _activeRoute || normRoute(location.pathname);
  }

  function _updateSidebarActive(route) {
    document.querySelectorAll('.nav-links .nav-link').forEach(a => {
      const aRoute = normRoute(new URL(a.href, location.origin).pathname);
      a.classList.toggle('active', aRoute === route);
    });
  }

  function _parseTemplate(htmlText) {
    const doc = new DOMParser().parseFromString(htmlText, 'text/html');
    const main = doc.querySelector('main.main-content');
    const titleEl = main ? main.querySelector('header.page-header .page-title') : null;
    const titleId = (titleEl && titleEl.id) || '';
    const titleText = (titleEl && titleEl.textContent) || '';
    const parsedHeader = main ? main.querySelector('header.page-header') : null;
    if (parsedHeader) parsedHeader.remove();
    const modalsHtml = [...doc.querySelectorAll('body > .modal-overlay, body > .camera-modal-overlay')]
      .map(el => el.outerHTML).join('');
    return {
      docTitle: doc.title || 'Family Hub',
      mainClass: main ? main.className : 'main-content',
      titleId,
      titleText,
      bodyHtml: main ? main.innerHTML : '',
      modalsHtml,
    };
  }

  function _loadTemplate(route) {
    if (_tplCache.has(route)) return Promise.resolve(_tplCache.get(route));
    if (_tplInflight.has(route)) return _tplInflight.get(route);
    const cfg = ROUTES[route];
    if (!cfg) return Promise.reject(new Error('Unknown route ' + route));
    const p = _fetchWithTimeout(cfg.html, { credentials: 'same-origin' }, 8000, null)
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(text => {
        const tpl = _parseTemplate(text);
        _tplCache.set(route, tpl);
        return tpl;
      })
      .finally(() => _tplInflight.delete(route));
    _tplInflight.set(route, p);
    return p;
  }

  function _injectScript(path) {
    if (path.indexOf('agenda.js') !== -1 && typeof renderAgenda === 'function') {
      return Promise.resolve();
    }
    const already = [...document.scripts].some(s => {
      const src = s.getAttribute('src') || s.src || '';
      return src.indexOf(path) !== -1;
    });
    if (already) return Promise.resolve();
    const inflightKey = 'extra:' + path;
    if (_scriptInflight.has(inflightKey)) return _scriptInflight.get(inflightKey);
    const p = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = path + _assetSuffix();
      s.async = true;
      s.onload = resolve;
      s.onerror = () => reject(new Error('Failed to load ' + path));
      document.body.appendChild(s);
    }).finally(() => _scriptInflight.delete(inflightKey));
    _scriptInflight.set(inflightKey, p);
    return p;
  }

  function _loadScript(route) {
    if (_scriptInflight.has(route)) return _scriptInflight.get(route);
    const cfg = ROUTES[route];
    if (!cfg) return Promise.reject(new Error('Unknown route ' + route));
    const extras = cfg.extras || [];
    const extrasReady = extras.every(path => {
      if (path.indexOf('agenda.js') !== -1) return typeof renderAgenda === 'function';
      return [...document.scripts].some(s => (s.getAttribute('src') || s.src || '').indexOf(path) !== -1);
    });
    if (_controllers.has(route) && extrasReady) return Promise.resolve(_controllers.get(route));

    const p = Promise.all(extras.map(_injectScript)).then(() => {
      if (_controllers.has(route)) return _controllers.get(route);
      return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = cfg.script + _assetSuffix();
        s.async = true;
        s.onload = () => resolve(_controllers.get(route));
        s.onerror = () => reject(new Error('Failed to load ' + cfg.script));
        document.body.appendChild(s);
      });
    }).finally(() => _scriptInflight.delete(route));
    _scriptInflight.set(route, p);
    return p;
  }

  function prefetch(route) {
    const r = normRoute(route);
    if (!ROUTES[r] || _prefetchBusy) return;
    if (_tplCache.has(r) && _controllers.has(r)) return;
    _prefetchBusy = true;
    Promise.all([_loadTemplate(r), _loadScript(r)])
      .catch(() => {})
      .finally(() => { _prefetchBusy = false; });
  }

  function _unmountCurrent() {
    abortRouteRequests();
    closeAlarmPopover();
    document.getElementById('haCoverPopover')?.remove();
    killActiveStreams();
    onPersonFilterChange(null);
    if (_activeCleanup) {
      try { _activeCleanup(); } catch (e) { console.error(e); }
      _activeCleanup = null;
    }
  }

  function _applySwap(route, tpl, targetUrl, opts) {
    const main = document.querySelector('main.main-content');
    const header = main ? main.querySelector('header.page-header') : null;
    if (!main || !header) throw new Error('Shell missing');

    main.className = tpl.mainClass;
    const h1 = header.querySelector('.page-title');
    if (h1) {
      if (tpl.titleId) h1.id = tpl.titleId;
      else h1.removeAttribute('id');
      h1.textContent = tpl.titleText;
    }

    while (header.nextSibling) {
      header.nextSibling.remove();
    }
    header.insertAdjacentHTML('afterend', tpl.bodyHtml);
    main.scrollTop = 0;
    window.scrollTo(0, 0);

    const modalHost = document.getElementById('pageModals');
    if (modalHost) modalHost.innerHTML = tpl.modalsHtml;

    document.title = tpl.docTitle;
    _updateSidebarActive(route);

    if (!opts || !opts.fromPopstate) {
      const currentFull = location.pathname + location.search;
      if (opts && opts.replace) history.replaceState({ route }, '', targetUrl);
      else if (currentFull !== targetUrl) history.pushState({ route }, '', targetUrl);
    }

    _activeRoute = route;
    const ctrl = _controllers.get(route);
    if (ctrl && typeof ctrl.mount === 'function') {
      const cleanup = ctrl.mount();
      _activeCleanup = typeof cleanup === 'function' ? cleanup : (ctrl.unmount || null);
    }
  }

  function register(route, hooks) {
    const r = normRoute(route);
    _controllers.set(r, hooks);
    if (_booted && _activeRoute === r && !_activeCleanup && hooks && typeof hooks.mount === 'function') {
      const cleanup = hooks.mount();
      _activeCleanup = typeof cleanup === 'function' ? cleanup : (hooks.unmount || null);
    }
  }

  async function navigate(href, opts = {}) {
    const url = new URL(href, location.origin);
    const route = normRoute(url.pathname);
    if (!ROUTES[route]) {
      location.href = href;
      return;
    }
    const targetUrl = (route === '/' ? '/' : route) + url.search + url.hash;
    if (route === _activeRoute && !url.search && !opts.fromPopstate && !opts.force) {
      return;
    }

    const seq = ++_navSeq;
    _updateSidebarActive(route);
    _unmountCurrent();

    if (_tplCache.has(route) && _controllers.has(route)) {
      try {
        _applySwap(route, _tplCache.get(route), targetUrl, opts);
        return;
      } catch (e) {
        console.error('Router sync swap fallback:', e);
        location.href = targetUrl;
        return;
      }
    }

    try {
      const [tpl] = await Promise.all([_loadTemplate(route), _loadScript(route)]);
      if (seq !== _navSeq) return;
      _applySwap(route, tpl, targetUrl, opts);
    } catch (e) {
      console.error('Router async swap fallback:', e);
      if (seq === _navSeq) location.href = targetUrl;
    }
  }

  function boot() {
    _booted = true;
    _activeRoute = normRoute(location.pathname);
    _updateSidebarActive(_activeRoute);

    // Cache current page's own HTML template in memory immediately (0 network cost)
    try {
      const main = document.querySelector('main.main-content');
      if (main) {
        const clone = document.documentElement.outerHTML;
        _tplCache.set(_activeRoute, _parseTemplate(clone));
      }
    } catch (_) {}

    const ctrl = _controllers.get(_activeRoute);
    if (ctrl && typeof ctrl.mount === 'function' && !_activeCleanup) {
      const cleanup = ctrl.mount();
      _activeCleanup = typeof cleanup === 'function' ? cleanup : (ctrl.unmount || null);
    }

    document.addEventListener('click', e => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      if (a.target && a.target !== '_self') return;
      if (a.hasAttribute('download')) return;
      const rawHref = a.getAttribute('href');
      if (!rawHref || rawHref.startsWith('#') || rawHref.startsWith('javascript:')) return;
      let url;
      try { url = new URL(a.href, location.origin); } catch (_) { return; }
      if (url.origin !== location.origin) return;
      if (url.pathname.startsWith('/api/')) return;
      const route = normRoute(url.pathname);
      if (!ROUTES[route]) return;
      e.preventDefault();
      navigate(url.pathname + url.search + url.hash);
    });

    // Lazy 1-at-a-time prefetch on pointerover / touchstart (replaces 18-request boot storm)
    const warmLink = e => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      try {
        const url = new URL(a.href, location.origin);
        if (url.origin === location.origin && ROUTES[normRoute(url.pathname)]) {
          prefetch(url.pathname);
        }
      } catch (_) {}
    };
    document.addEventListener('pointerover', warmLink, { passive: true });
    document.addEventListener('touchstart', warmLink, { passive: true });

    window.addEventListener('popstate', () => {
      navigate(location.pathname + location.search + location.hash, { fromPopstate: true, force: true });
    });
  }

  return { register, navigate, prefetch, currentRoute, boot };
})();

// Init persistent shell + theme + router + single /api/bootstrap call
document.addEventListener('DOMContentLoaded', () => {
  ensureShell();
  initTheme();
  _updateFullscreenBtn();
  loadShellBootstrap();
  FHRouter.boot();
  setInterval(() => loadHAStatus(), 30000);
  setInterval(() => { if (!document.hidden) loadMessageBadge(); }, 60000);
  ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'].forEach(ev =>
    document.addEventListener(ev, _onPersonActivity, { passive: true })
  );
  armPersonIdle();
});
