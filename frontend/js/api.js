// ── API Helper ────────────────────────────────────────────────
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async put(path, body) {
    const r = await fetch(path, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async delete(path) {
    const r = await fetch(path, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
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

// ── Sidebar Members ───────────────────────────────────────────
async function loadSidebarMembers() {
  const el = document.getElementById('sidebarMembers');
  if (!el) return;
  try {
    const members = await API.get('/members/');
    el.innerHTML = members.map(m => `
      <div class="sidebar-member-chip">
        <div class="member-avatar" style="background:${m.color}22">${m.avatar || '👤'}</div>
        <span>${m.name}</span>
      </div>
    `).join('');
  } catch(e) {}
}

// ── Date Helpers ──────────────────────────────────────────────
let TZ = 'America/New_York';
let _tzLoaded = false;
async function initAppTimezone() {
  if (_tzLoaded) return;
  try {
    const s = await API.get('/api/settings/timezone');
    if (s.value) TZ = s.value;
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

// Init sidebar on all pages
document.addEventListener('DOMContentLoaded', loadSidebarMembers);
