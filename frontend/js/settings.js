const COLORS = ['#4F6EF7','#FF6B6B','#FFB347','#34C759','#AF52DE','#FF2D55','#5AC8FA','#FF9500','#30D158','#64D2FF'];
const EMOJI_GROUPS = [
  { id: 'people', label: 'People', emojis: ['👤','👨','👩','👧','👦','👶','🧑','👴','👵','🧔','👱','🧕','👸','🤴','🦸','🦹','🧙','🧝','🧛','🧚','🧜','👮','👷','🧑‍🎓','👨‍🍳','👩‍⚕️'] },
  { id: 'animals', label: 'Animals', emojis: ['🐱','🐶','🦊','🐼','🦁','🐯','🐻','🐨','🐸','🐵','🦄','🐷','🐮','🐔','🐧','🐦','🦉','🐺','🦝','🐹','🐰','🐢','🐬','🦋','🐝','🐴'] },
  { id: 'sports', label: 'Sports', emojis: ['⚽','🏀','🏈','⚾','🎾','🏐','🏉','🥏','🎳','🏓','🏸','🏒','⛳','🏹','🥊','🥋','🛹','🎿','🏊','🚴','🏇','🤸','🏄','🧗','🥅','🏆'] },
  { id: 'activities', label: 'Activities', emojis: ['🎨','🎭','🎬','🎤','🎧','🎸','🎹','🥁','📚','✏️','💻','🎮','🧩','♟️','🎯','🎲','📷','✈️','🚀','🚗','🚲','⛺','🔬','🧪','🔭','🧵'] },
  { id: 'food', label: 'Food', emojis: ['🍕','🍔','🌮','🍣','🍜','🍩','🍪','🍰','🧁','🍦','🍎','🍓','🥑','🌽','🥨','🥐','☕','🥤','🧃','🍿','🥗','🍝'] },
  { id: 'nature', label: 'Nature', emojis: ['🌸','🌻','🌹','🌷','🍀','🌿','🍁','🌙','⭐','☀️','🌈','🔥','💧','❄️','🌊','🏔️','🌵','🌴','🐝','🐚'] },
];
let members = [];
let editingMemberId = null;
let pendingAvatarFile = null;
let removeAvatar = false;
let activeEmojiGroup = 'people';
let calendarAccounts = [];
let calendarSources = [];

document.addEventListener('DOMContentLoaded', async () => {
  // Tab switching
  document.querySelectorAll('.settings-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.settings-tab-panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    });
  });

  await loadMembers();
  loadFamilyCalendar();
  loadSyncInterval();
  loadSlideshowSettings();
  loadWeatherZip();
  loadHASettings();
  loadTimezone();
  loadNightMode();
  loadSchoolcafeSettings();
  loadStockSettings();
  loadCameraSettings();
  loadReminders();

  document.getElementById('addMemberBtn').onclick = () => openMemberModal();
  document.getElementById('closeMemberModal').onclick = closeMemberModal;
  document.getElementById('cancelMemberModal').onclick = closeMemberModal;
  document.getElementById('saveMember').onclick = saveMember;
  document.getElementById('memberModal').onclick = e => { if (e.target.id === 'memberModal') closeMemberModal(); };

  document.getElementById('colorPresets').innerHTML = COLORS.map(c =>
    `<div class="color-preset" style="background:${c}" data-color="${c}" onclick="selectColor('${c}')"></div>`
  ).join('');

  renderEmojiGroups();
  const emojiCustom = document.getElementById('memberEmojiCustom');
  if (emojiCustom) {
    emojiCustom.addEventListener('input', () => {
      const grapheme = firstGrapheme(emojiCustom.value);
      if (grapheme) selectEmoji(grapheme, true);
    });
  }
  const photoInput = document.getElementById('memberPhoto');
  if (photoInput) {
    photoInput.addEventListener('change', () => {
      pendingAvatarFile = photoInput.files && photoInput.files[0] ? photoInput.files[0] : null;
      removeAvatar = false;
      renderPhotoPreview();
    });
  }
  const clearPhoto = document.getElementById('clearMemberPhoto');
  if (clearPhoto) {
    clearPhoto.addEventListener('click', () => {
      pendingAvatarFile = null;
      removeAvatar = true;
      if (photoInput) photoInput.value = '';
      renderPhotoPreview();
    });
  }
  const schoolSel = document.getElementById('memberSchool');
  if (schoolSel) schoolSel.addEventListener('change', () => loadMemberLines());

  document.getElementById('memberColor').oninput = e => selectColor(e.target.value);

  const params = new URLSearchParams(window.location.search);
  const connected = params.get('connected');
  if (connected === 'account' || connected === 'member') {
    showToast('✅ Google Calendar connected!', 'success');
    history.replaceState({}, '', '/settings.html');
  }
});

async function loadMembers() {
  members = await API.get('/api/members/');
  loadMembersFromCache();
}

async function moveMember(id, dir) {
  const index = members.findIndex(m => m.id === id);
  const next = index + dir;
  if (index < 0 || next < 0 || next >= members.length) return;
  const reordered = members.slice();
  const [moved] = reordered.splice(index, 1);
  reordered.splice(next, 0, moved);
  members = reordered;
  loadMembersFromCache();
  try {
    await API.put('/api/members/order', { ids: members.map(m => m.id) });
  } catch (e) {
    showToast('Could not save member order', 'error');
    await loadMembers();
  }
}

function loadMembersFromCache() {
  const el = document.getElementById('membersList');
  if (!el) return;
  if (!members.length) {
    el.innerHTML = '<div class="empty-state">No family members yet — add your first one!</div>';
    return;
  }
  el.innerHTML = members.map((m, i) => `
    <div class="member-item">
      <div class="member-order">
        <button class="member-order-btn" title="Move up" aria-label="Move ${_escapeHtml(m.name)} up" onclick="moveMember(${m.id}, -1)" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="member-order-btn" title="Move down" aria-label="Move ${_escapeHtml(m.name)} down" onclick="moveMember(${m.id}, 1)" ${i === members.length - 1 ? 'disabled' : ''}>↓</button>
      </div>
      <div class="member-item-avatar">${memberAvatarHtml(m, 'avatar-lg')}</div>
      <div class="member-item-info">
        <div class="member-item-name">
          <span class="member-color-swatch" style="background:${m.color}"></span>
          ${_escapeHtml(m.name)}
        </div>
        <div class="member-item-sub">${m.school_name ? _escapeHtml(m.school_name) : 'No school assigned'}</div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="chore-action-btn" onclick="openMemberModal(${m.id})">✏️ Edit</button>
        <button class="chore-action-btn delete" onclick="deleteMember(${m.id})">🗑</button>
      </div>
    </div>`).join('');
}

async function apiPatch(path, body) {
  const r = await fetch(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function _escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _accountLabel(a) {
  return a.label || a.email || a.apple_id || a.name || `Account ${a.id}`;
}

function _accountProvider(a) {
  const p = (a.provider || a.kind || '').toLowerCase();
  if (p.includes('google')) return 'google';
  if (p.includes('icloud') || p.includes('apple') || p.includes('caldav')) return 'icloud';
  return p || 'unknown';
}

function _sourceKindLabel(s) {
  const k = (s.kind || '').toLowerCase();
  if (k === 'google') return 'Google';
  if (k === 'caldav') return 'iCloud';
  if (k === 'ics') return 'ICS';
  return k || 'Calendar';
}

let familyWriteSourceId = null;

async function loadFamilyCalendar() {
  await loadCalendarAccounts();
  await loadCalendarSources();
  // Refresh members so write_calendar_source_id stays current
  try { members = await API.get('/api/members/'); } catch (_) {}
  try {
    const fw = await API.get('/api/calendar/family/write-calendar');
    familyWriteSourceId = fw.source_id ?? null;
  } catch (_) {
    familyWriteSourceId = null;
  }
  renderWriteCalendars();
}

async function loadCalendarAccounts() {
  const el = document.getElementById('calendarAccountsList');
  const addEl = document.getElementById('accountAddCalendars');
  if (!el) return;
  try {
    const data = await API.get('/api/calendar/accounts');
    calendarAccounts = Array.isArray(data) ? data : (data.accounts || []);
  } catch (e) {
    calendarAccounts = [];
    el.innerHTML = `<div style="font-size:13px;color:var(--red)">Could not load accounts: ${_escapeHtml(e.message)}</div>`;
    if (addEl) addEl.innerHTML = '';
    return;
  }

  if (!calendarAccounts.length) {
    el.innerHTML = '<div class="empty-state" style="padding:8px 0">No accounts yet — connect Google or iCloud above.</div>';
  } else {
    el.innerHTML = calendarAccounts.map(a => {
      const provider = _accountProvider(a);
      const icon = provider === 'google' ? '🔵' : provider === 'icloud' ? '☁️' : '📅';
      const providerName = provider === 'google' ? 'Google' : provider === 'icloud' ? 'iCloud' : provider;
      return `
        <div class="settings-card" style="margin-bottom:8px">
          <div class="settings-card-info">
            <h3 style="margin:0">${icon} ${_escapeHtml(_accountLabel(a))}</h3>
            <div class="google-status" style="margin-top:4px">
              <span class="status-dot connected"></span>
              <span class="status-text connected">${providerName}</span>
            </div>
          </div>
          <div class="settings-card-actions">
            <button class="btn btn-danger-ghost" onclick="disconnectCalendarAccount(${a.id})">Disconnect</button>
          </div>
        </div>`;
    }).join('');
  }

  if (addEl) {
    if (!calendarAccounts.length) {
      addEl.innerHTML = '';
    } else {
      addEl.innerHTML = calendarAccounts.map(a => `
        <div style="margin-bottom:12px">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:6px">
            <div style="font-weight:800;font-size:13px">From ${_escapeHtml(_accountLabel(a))}</div>
            <button class="btn btn-secondary" onclick="loadAvailableCalendars(${a.id})">Browse calendars</button>
          </div>
          <div id="availableCals_${a.id}"></div>
        </div>`).join('');
    }
  }
}

async function loadCalendarSources() {
  const el = document.getElementById('calendarSourcesList');
  if (!el) return;
  try {
    const data = await API.get('/api/calendar/sources');
    calendarSources = Array.isArray(data) ? data : (data.sources || []);
  } catch (e) {
    calendarSources = [];
    el.innerHTML = `<div style="font-size:13px;color:var(--red)">Could not load calendars: ${_escapeHtml(e.message)}</div>`;
    return;
  }

  if (!calendarSources.length) {
    el.innerHTML = '<div class="empty-state" style="padding:8px 0">No calendars in the pool yet.</div>';
    return;
  }

  el.innerHTML = `
    <div class="cal-picker">
      <div class="cal-picker-title">Synced calendars</div>
      ${calendarSources.map(s => {
        const acct = calendarAccounts.find(a => a.id === s.account_id);
        const acctHint = acct ? ` · ${_escapeHtml(_accountLabel(acct))}` : '';
        const color = s.color || '#4F6EF7';
        const isFamily = !!Number(s.is_family);
        const memberOpts = members.map(m => {
          const sel = !isFamily && s.member_id != null && String(s.member_id) === String(m.id) ? ' selected' : '';
          return `<option value="${m.id}"${sel}>${_escapeHtml(m.avatar || '👤')} ${_escapeHtml(m.name)}</option>`;
        }).join('');
        const assignVal = isFamily ? 'family' : (s.member_id == null ? '' : String(s.member_id));
        return `
          <div class="cal-option selected" style="cursor:default;flex-wrap:wrap;gap:8px">
            <div class="cal-option-dot" style="background:${_escapeHtml(color)}"></div>
            <div class="cal-option-name" style="flex:1;min-width:120px">
              ${_escapeHtml(s.name || 'Calendar')}
              <div style="font-size:11px;font-weight:600;color:var(--text2)">${_sourceKindLabel(s)}${acctHint}${isFamily ? ' · Family' : ''}</div>
            </div>
            <select class="form-input" style="max-width:180px;padding:6px 8px;font-size:13px"
              onchange="assignCalendarSource(${s.id}, this.value)">
              <option value=""${assignVal === '' ? ' selected' : ''}>Unassigned</option>
              <option value="family"${assignVal === 'family' ? ' selected' : ''}>🏠 Family</option>
              ${memberOpts}
            </select>
            <button class="btn btn-danger-ghost" style="padding:4px 8px;font-size:12px"
              onclick="removeCalendarSource(${s.id})">Remove</button>
          </div>`;
      }).join('')}
    </div>`;
}

function renderWriteCalendars() {
  const el = document.getElementById('writeCalendarsList');
  if (!el) return;

  const familyWritable = calendarSources.filter(s =>
    Number(s.is_family) && (s.kind === 'google' || s.kind === 'caldav')
  );
  const familyAssignedAny = calendarSources.some(s => Number(s.is_family));
  let familyEmpty = 'Assign a Google or iCloud calendar to Family first';
  if (familyWritable.length) familyEmpty = 'No write calendar';
  else if (familyAssignedAny) familyEmpty = 'ICS feeds are sync-in only';
  const familyOpts = familyWritable.map(s => {
    const sel = String(familyWriteSourceId) === String(s.id) ? ' selected' : '';
    return `<option value="${s.id}"${sel}>${_escapeHtml(s.name || 'Calendar')} (${_sourceKindLabel(s)})</option>`;
  }).join('');

  const familyRow = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid var(--border)">
      <div style="min-width:120px;font-weight:700">🏠 Family</div>
      <select id="writeCal_family" class="form-input" style="flex:1;min-width:200px;max-width:360px"
        ${familyWritable.length ? '' : 'disabled'}>
        <option value="">${familyEmpty}</option>
        ${familyOpts}
      </select>
      <button class="btn btn-primary" style="padding:8px 12px"
        onclick="saveFamilyWriteCalendar()" ${familyWritable.length ? '' : 'disabled'}>Save</button>
    </div>`;

  if (!members.length) {
    el.innerHTML = familyRow + '<div class="empty-state" style="padding:8px 0">Add family members for personal write calendars.</div>';
    return;
  }
  el.innerHTML = familyRow + members.map(m => {
    const writable = calendarSources.filter(s =>
      String(s.member_id) === String(m.id) && (s.kind === 'google' || s.kind === 'caldav')
    );
    const assignedAny = calendarSources.some(s => String(s.member_id) === String(m.id));
    const opts = writable.length
      ? writable.map(s => {
          const sel = String(m.write_calendar_source_id) === String(s.id) ? ' selected' : '';
          return `<option value="${s.id}"${sel}>${_escapeHtml(s.name || 'Calendar')} (${_sourceKindLabel(s)})</option>`;
        }).join('')
      : '';
    let emptyLabel = 'Assign a Google or iCloud calendar first';
    if (writable.length) emptyLabel = 'No write calendar';
    else if (assignedAny) emptyLabel = 'ICS feeds are sync-in only';
    return `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid var(--border)">
        <div style="min-width:140px;font-weight:700;display:flex;align-items:center;gap:8px">${memberAvatarHtml(m, 'avatar-sm')} ${_escapeHtml(m.name)}</div>
        <select id="writeCal_${m.id}" class="form-input" style="flex:1;min-width:200px;max-width:360px"
          ${writable.length ? '' : 'disabled'}>
          <option value="">${emptyLabel}</option>
          ${opts}
        </select>
        <button class="btn btn-primary" style="padding:8px 12px"
          onclick="saveWriteCalendar(${m.id})" ${writable.length ? '' : 'disabled'}>Save</button>
      </div>`;
  }).join('');
}

async function disconnectCalendarAccount(id) {
  if (!confirm('Disconnect this account? Calendars from it may stop syncing.')) return;
  try {
    await API.delete(`/api/calendar/accounts/${id}`);
    showToast('Account disconnected', '');
    await loadFamilyCalendar();
  } catch (e) {
    showToast(e.message || 'Failed to disconnect', 'error');
  }
}

async function connectIcloudAccount() {
  const apple_id = document.getElementById('icloudAppleId')?.value.trim();
  const app_password = document.getElementById('icloudAppPassword')?.value.trim();
  const label = document.getElementById('icloudLabel')?.value.trim();
  if (!apple_id || !app_password) {
    showToast('Enter Apple ID and app-specific password', 'error');
    return;
  }
  try {
    const body = { apple_id, app_password };
    if (label) body.label = label;
    await API.post('/api/calendar/accounts/icloud', body);
    showToast('✅ iCloud connected', 'success');
    document.getElementById('icloudAppleId').value = '';
    document.getElementById('icloudAppPassword').value = '';
    document.getElementById('icloudLabel').value = '';
    await loadFamilyCalendar();
  } catch (e) {
    showToast(e.message || 'iCloud connect failed', 'error');
  }
}

function copyGoogleAccountOAuthHelp() {
  const text = [
    'Connect a shared Google account (Proxmox / LAN):',
    '1. In Google Cloud Console, add redirect URI: http://127.0.0.1:8765/callback',
    '2. On your PC run:',
    '   python tools/google-oauth-local/oauth_local.py --hub http://YOUR_HUB:3000 --target account',
    '3. Or with public HTTPS, use Connect Google Account in Settings (links to /api/auth/google/connect/account).',
  ].join('\n');
  navigator.clipboard.writeText(text).then(
    () => showToast('OAuth instructions copied', 'success'),
    () => showToast(text, 'info')
  );
}

async function loadAvailableCalendars(accountId) {
  const box = document.getElementById(`availableCals_${accountId}`);
  if (!box) return;
  box.innerHTML = '<div style="font-size:13px;color:var(--text2);padding:8px">Loading calendars…</div>';
  try {
    const data = await API.get(`/api/calendar/accounts/${accountId}/available`);
    const calendars = data.calendars || [];
    const account = calendarAccounts.find(a => a.id === accountId);
    const provider = _accountProvider(account || {});
    const kind = provider === 'google' ? 'google' : 'caldav';
    const existing = new Set(
      calendarSources
        .filter(s => s.account_id === accountId)
        .map(s => s.external_id || s.url)
    );

    if (!calendars.length) {
      box.innerHTML = '<div style="padding:8px;color:var(--text2);font-size:13px">No calendars found</div>';
      return;
    }

    box.innerHTML = `
      <div class="cal-picker">
        <div class="cal-picker-title">Select calendars to add</div>
        ${calendars.map(c => {
          const extId = c.id || c.url || '';
          const already = existing.has(extId) || (c.url && existing.has(c.url));
          const name = c.name || c.summary || 'Calendar';
          const color = c.backgroundColor || c.color || '#4F6EF7';
          return `
            <div class="cal-option ${already ? 'selected' : ''}"
                 data-ext="${encodeURIComponent(extId)}"
                 data-url="${encodeURIComponent(c.url || '')}"
                 data-name="${encodeURIComponent(name)}"
                 data-color="${encodeURIComponent(color)}"
                 data-kind="${kind}"
                 ${already ? 'style="opacity:0.55;cursor:default"' : `onclick="toggleCalOption(this)"`}>
              <div class="cal-option-dot" style="background:${_escapeHtml(color)}"></div>
              <div class="cal-option-name">${_escapeHtml(name)}${c.primary ? ' ⭐' : ''}${already ? ' (added)' : ''}</div>
              <div class="cal-option-check" style="opacity:${already ? '1' : '0'}">✓</div>
            </div>`;
        }).join('')}
        <button class="btn btn-primary cal-picker-save" onclick="addSelectedAccountCalendars(${accountId})">
          Add selected
        </button>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div style="font-size:13px;color:var(--red);padding:8px">${_escapeHtml(e.message)}</div>`;
  }
}

async function addSelectedAccountCalendars(accountId) {
  const box = document.getElementById(`availableCals_${accountId}`);
  if (!box) return;
  // Only newly toggled ones (have onclick); already-added ones have no onclick
  const toAdd = [...box.querySelectorAll('.cal-option')].filter(el =>
    el.classList.contains('selected') && el.getAttribute('onclick')
  );
  if (!toAdd.length) {
    showToast('Select at least one calendar to add', 'error');
    return;
  }
  try {
    for (const el of toAdd) {
      const kind = el.dataset.kind || 'google';
      const external_id = decodeURIComponent(el.dataset.ext || '');
      const url = decodeURIComponent(el.dataset.url || '');
      const name = decodeURIComponent(el.dataset.name || 'Calendar');
      const color = decodeURIComponent(el.dataset.color || '');
      const body = { account_id: accountId, kind, external_id: external_id || url, name };
      if (color) body.color = color;
      if (kind === 'caldav' && url) body.external_id = url;
      await API.post('/api/calendar/sources', body);
    }
    showToast(`✅ Added ${toAdd.length} calendar${toAdd.length !== 1 ? 's' : ''}`, 'success');
    await loadFamilyCalendar();
    await loadAvailableCalendars(accountId);
  } catch (e) {
    showToast(e.message || 'Failed to add calendars', 'error');
  }
}

async function addIcsSource() {
  const name = document.getElementById('icsSourceName')?.value.trim();
  const url = document.getElementById('icsSourceUrl')?.value.trim();
  if (!name || !url) {
    showToast('Enter a name and calendar URL', 'error');
    return;
  }
  try {
    await API.post('/api/calendar/sources', { kind: 'ics', url, name });
    showToast('✅ ICS calendar added', 'success');
    document.getElementById('icsSourceName').value = '';
    document.getElementById('icsSourceUrl').value = '';
    await loadFamilyCalendar();
  } catch (e) {
    showToast(e.message || 'Failed to add calendar', 'error');
  }
}

async function assignCalendarSource(sourceId, assignVal) {
  let body;
  if (assignVal === 'family') {
    body = { is_family: true, member_id: null };
  } else if (assignVal === '') {
    body = { is_family: false, member_id: null };
  } else {
    body = { is_family: false, member_id: Number(assignVal) };
  }
  try {
    await apiPatch(`/api/calendar/sources/${sourceId}`, body);
    const label = assignVal === 'family' ? 'Assigned to Family' : (assignVal ? 'Assigned' : 'Unassigned');
    showToast(label, 'success');
    await loadFamilyCalendar();
  } catch (e) {
    showToast(e.message || 'Failed to assign', 'error');
  }
}

async function saveFamilyWriteCalendar() {
  const sel = document.getElementById('writeCal_family');
  if (!sel) return;
  const source_id = sel.value === '' ? null : Number(sel.value);
  try {
    const r = await API.put('/api/calendar/family/write-calendar', { source_id });
    familyWriteSourceId = r.source_id ?? null;
    showToast('✅ Family write calendar saved', 'success');
  } catch (e) {
    showToast(e.message || 'Failed to save', 'error');
  }
}

async function removeCalendarSource(sourceId) {
  if (!confirm('Remove this calendar and its synced events?')) return;
  try {
    await API.delete(`/api/calendar/sources/${sourceId}`);
    showToast('Removed', '');
    await loadFamilyCalendar();
  } catch (e) {
    showToast(e.message || 'Failed to remove', 'error');
  }
}

async function saveWriteCalendar(memberId) {
  const sel = document.getElementById(`writeCal_${memberId}`);
  if (!sel) return;
  const val = sel.value;
  const write_calendar_source_id = val === '' ? null : Number(val);
  try {
    await API.put(`/api/members/${memberId}`, { write_calendar_source_id });
    const m = members.find(x => x.id === memberId);
    if (m) m.write_calendar_source_id = write_calendar_source_id;
    showToast('✅ Write calendar saved', 'success');
  } catch (e) {
    showToast(e.message || 'Failed to save', 'error');
  }
}

function toggleCalOption(el, _containerId) {
  el.classList.toggle('selected');
  const check = el.querySelector('.cal-option-check');
  if (check) check.style.opacity = el.classList.contains('selected') ? '1' : '0';
}

function firstGrapheme(value) {
  const text = (value || '').trim();
  if (!text) return '';
  if (typeof Intl !== 'undefined' && Intl.Segmenter) {
    const seg = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    return [...seg.segment(text)][0].segment;
  }
  return [...text][0];
}

function renderEmojiGroups() {
  const tabs = document.getElementById('emojiTabs');
  const picker = document.getElementById('emojiPicker');
  if (!tabs || !picker) return;
  tabs.innerHTML = EMOJI_GROUPS.map(g =>
    `<button type="button" class="emoji-tab${g.id === activeEmojiGroup ? ' active' : ''}" onclick="showEmojiGroup('${g.id}')">${g.label}</button>`
  ).join('');
  const group = EMOJI_GROUPS.find(g => g.id === activeEmojiGroup) || EMOJI_GROUPS[0];
  const current = document.getElementById('memberAvatar')?.value;
  picker.innerHTML = group.emojis.map(e =>
    `<div class="emoji-opt${e === current ? ' selected' : ''}" data-emoji="${e}" onclick="selectEmoji('${e}')">${e}</div>`
  ).join('');
}

function showEmojiGroup(id) {
  activeEmojiGroup = id;
  renderEmojiGroups();
}

function editingMember() {
  return editingMemberId ? members.find(m => m.id === editingMemberId) : null;
}

function renderPhotoPreview() {
  const el = document.getElementById('memberPhotoPreview');
  if (!el) return;
  if (pendingAvatarFile) {
    el.innerHTML = `<img src="${URL.createObjectURL(pendingAvatarFile)}" alt="">`;
    return;
  }
  const m = editingMember();
  if (m && m.avatar_photo && !removeAvatar) {
    el.innerHTML = memberAvatarHtml(m, 'avatar-lg');
    return;
  }
  el.innerHTML = memberAvatarHtml({ avatar: document.getElementById('memberAvatar')?.value || '👤', color: document.getElementById('memberColor')?.value }, 'avatar-lg');
}

async function fillSchoolSelect(selectedId) {
  const sel = document.getElementById('memberSchool');
  if (!sel) return;
  sel.innerHTML = '<option value="">No school</option>';
  try {
    const data = await API.get('/api/schoolcafe/schools');
    if (!data.configured) {
      sel.innerHTML = '<option value="">Set a SchoolCafe district first</option>';
      return;
    }
    sel.innerHTML = '<option value="">No school</option>' + data.schools.map(s =>
      `<option value="${s.id}"${s.id === selectedId ? ' selected' : ''}>${_escapeHtml(s.name)}</option>`
    ).join('');
    if (selectedId) sel.value = selectedId;
  } catch (e) {
    sel.innerHTML = '<option value="">Could not load schools</option>';
  }
}

async function loadMemberLines() {
  const box = document.getElementById('memberLines');
  const schoolId = document.getElementById('memberSchool')?.value || '';
  if (!box) return;
  if (!schoolId) { box.style.display = 'none'; return; }
  try {
    const data = await API.get('/api/schoolcafe/lines?school_id=' + encodeURIComponent(schoolId));
    const fill = (selectId, lines, selected) => {
      const sel = document.getElementById(selectId);
      if (!sel) return;
      sel.innerHTML = lines.map(line => `<option value="${_escapeHtml(line)}"${line === selected ? ' selected' : ''}>${_escapeHtml(line)}</option>`).join('');
      if (selected) sel.value = selected;
    };
    fill('memberBreakfastLine', data.breakfast || [], (data.selected || {}).Breakfast || '');
    fill('memberLunchLine', data.lunch || [], (data.selected || {}).Lunch || '');
    const show = (data.breakfast || []).length > 1 || (data.lunch || []).length > 1;
    box.style.display = show ? 'block' : 'none';
    document.getElementById('breakfastLineWrap').style.display = (data.breakfast || []).length > 1 ? 'block' : 'none';
    document.getElementById('lunchLineWrap').style.display = (data.lunch || []).length > 1 ? 'block' : 'none';
  } catch (e) {
    box.style.display = 'none';
  }
}

async function openMemberModal(memberId) {
  editingMemberId = memberId || null;
  pendingAvatarFile = null;
  removeAvatar = false;
  const photoInput = document.getElementById('memberPhoto');
  if (photoInput) photoInput.value = '';
  document.getElementById('memberModalTitle').textContent = memberId ? 'Edit Member' : 'Add Family Member';
  let schoolId = '';
  if (memberId) {
    const m = members.find(m => m.id === memberId);
    if (m) {
      document.getElementById('memberName').value = m.name;
      selectColor(m.color);
      selectEmoji(m.avatar || '👤');
      schoolId = m.school_id || '';
    }
  } else {
    document.getElementById('memberName').value = '';
    selectColor(COLORS[0]);
    selectEmoji('👤');
  }
  renderPhotoPreview();
  document.getElementById('memberModal').style.display = 'flex';
  document.getElementById('memberName').focus();
  await fillSchoolSelect(schoolId);
  await loadMemberLines();
}

function closeMemberModal() {
  document.getElementById('memberModal').style.display = 'none';
}

function selectColor(color) {
  document.getElementById('memberColor').value = color;
  document.querySelectorAll('.color-preset').forEach(el => {
    el.classList.toggle('selected', el.dataset.color === color);
  });
}

function selectEmoji(emoji, fromCustom) {
  const value = emoji || '👤';
  document.getElementById('memberAvatar').value = value;
  const custom = document.getElementById('memberEmojiCustom');
  if (custom && !fromCustom && document.activeElement !== custom) custom.value = value;
  const group = EMOJI_GROUPS.find(g => g.emojis.includes(value));
  if (group && group.id !== activeEmojiGroup) {
    activeEmojiGroup = group.id;
    renderEmojiGroups();
  } else {
    document.querySelectorAll('.emoji-opt').forEach(el => {
      el.classList.toggle('selected', el.dataset.emoji === value);
    });
  }
  if (!pendingAvatarFile) renderPhotoPreview();
}

async function saveMember() {
  const name = document.getElementById('memberName').value.trim();
  if (!name) { showToast('Please enter a name', 'error'); return; }
  const schoolSel = document.getElementById('memberSchool');
  const schoolId = schoolSel && !schoolSel.value.startsWith('Set ') && !schoolSel.value.startsWith('Could ')
    ? schoolSel.value : '';
  const schoolName = schoolId ? (schoolSel.selectedOptions[0]?.textContent || '').trim() : '';
  const payload = {
    name,
    color: document.getElementById('memberColor').value,
    avatar: document.getElementById('memberAvatar').value,
    school_id: schoolId,
    school_name: schoolName,
  };
  try {
    const saved = editingMemberId
      ? await API.put(`/api/members/${editingMemberId}`, payload)
      : await API.post('/api/members/', payload);
    const id = saved.id;
    const lines = document.getElementById('memberLines');
    if (schoolId && lines && lines.style.display !== 'none') {
      await API.put('/api/schoolcafe/lines', {
        school_id: schoolId,
        breakfast: document.getElementById('memberBreakfastLine')?.value || '',
        lunch: document.getElementById('memberLunchLine')?.value || '',
      });
    }
    if (removeAvatar && !pendingAvatarFile) {
      await API.delete(`/api/members/${id}/avatar`);
    }
    if (pendingAvatarFile) {
      const body = new FormData();
      body.append('file', pendingAvatarFile);
      const res = await fetch(`/api/members/${id}/avatar`, { method: 'POST', body });
      if (!res.ok) throw new Error(await res.text());
    }
    showToast(editingMemberId ? '✅ Member updated!' : '✅ Member added!', 'success');
    closeMemberModal();
    await loadMembers();
    loadSidebarMembers();
    await loadFamilyCalendar();
  } catch(e) {
    showToast(e.message || 'Failed to save member', 'error');
  }
}

async function deleteMember(id) {
  if (!confirm('Remove this family member? Their chores and events will remain.')) return;
  try {
    await API.delete(`/api/members/${id}`);
    showToast('Member removed', '');
    await loadMembers();
    loadSidebarMembers();
    await loadFamilyCalendar();
  } catch(e) {
    showToast('Failed to delete', 'error');
  }
}

async function loadSyncInterval() {
  try {
    const data = await API.get('/api/calendar/sync/status');
    const sel = document.getElementById('syncIntervalPicker');
    if (sel) sel.value = data.interval_minutes || 0;
    _updateSyncStatus(data.last_synced);
  } catch(e) {}
}

function _updateSyncStatus(lastSynced) {
  const el = document.getElementById('syncIntervalStatus');
  if (!el) return;
  if (!lastSynced) { el.textContent = ''; return; }
  const mins = Math.round((Date.now() - new Date(lastSynced + 'Z').getTime()) / 60000);
  el.textContent = mins < 2 ? 'Last synced just now' : `Last synced ${mins} min ago`;
}

async function saveSyncInterval() {
  const val = document.getElementById('syncIntervalPicker').value;
  try {
    await API.post('/api/settings/', { google_sync_interval: val });
    showToast(val === '0' ? 'Auto-sync disabled' : 'Auto-sync saved', 'success');
  } catch(e) {
    showToast('Failed to save', 'error');
  }
}

// ── Photo Upload ──────────────────────────────────────────────

async function loadSlideshowSettings() {
  try {
    const s = await API.get('/api/settings/');
    const timeout = document.getElementById('slideshowTimeout');
    const interval = document.getElementById('slideshowInterval');
    if (timeout && s.slideshow_timeout) timeout.value = s.slideshow_timeout;
    if (interval && s.slideshow_interval) interval.value = s.slideshow_interval;
  } catch(e) {}
  await loadPhotosGrid();
}

async function loadPhotosGrid() {
  try {
    const data = await API.get('/api/photos/');
    const grid = document.getElementById('photosGrid');
    const empty = document.getElementById('photosEmpty');
    const label = document.getElementById('photosCountLabel');
    const clearBtn = document.getElementById('clearAllPhotosBtn');

    label.textContent = `Uploaded Photos (${data.count})`;
    clearBtn.style.display = data.count > 0 ? 'inline-flex' : 'none';

    if (!data.photos.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = data.photos.map(url => {
      const filename = url.split('/').pop();
      return `
        <div style="position:relative;aspect-ratio:1;border-radius:8px;overflow:hidden;background:var(--bg2)">
          <img src="${url}" style="width:100%;height:100%;object-fit:cover" loading="lazy">
          <button onclick="deletePhoto('${filename}')"
            style="position:absolute;top:4px;right:4px;background:rgba(0,0,0,0.6);color:#fff;border:none;border-radius:50%;width:22px;height:22px;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center">✕</button>
        </div>`;
    }).join('');
  } catch(e) {
    console.error('loadPhotosGrid error:', e);
  }
}

function handleDragOver(e) {
  e.preventDefault();
  const zone = document.getElementById('photoDropZone');
  zone.style.borderColor = 'var(--primary)';
  zone.style.background = 'var(--bg2)';
}

function handleDragLeave(_e) {
  const zone = document.getElementById('photoDropZone');
  zone.style.borderColor = 'var(--border)';
  zone.style.background = '';
}

function handleDrop(e) {
  e.preventDefault();
  handleDragLeave(e);
  const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/'));
  if (files.length) uploadFiles(files);
}

function handleFileSelect(e) {
  const files = [...e.target.files];
  if (files.length) uploadFiles(files);
  e.target.value = '';
}

async function uploadFiles(files) {
  const statusEl = document.getElementById('uploadStatus');
  statusEl.style.display = 'block';
  statusEl.textContent = `⏳ Uploading ${files.length} photo${files.length !== 1 ? 's' : ''}...`;

  const formData = new FormData();
  for (const file of files) formData.append('files', file);

  try {
    const resp = await fetch('/api/photos/upload', { method: 'POST', body: formData });
    const result = await resp.json();
    if (result.uploaded > 0) {
      statusEl.textContent = `✅ ${result.uploaded} photo${result.uploaded !== 1 ? 's' : ''} uploaded!`;
      showToast(`✅ ${result.uploaded} photos added to slideshow`, 'success');
      await loadPhotosGrid();
    }
    if (result.errors?.length) statusEl.textContent += ` (${result.errors.length} failed)`;
    setTimeout(() => { statusEl.style.display = 'none'; }, 3000);
  } catch(e) {
    statusEl.textContent = '❌ Upload failed: ' + e.message;
  }
}

async function deletePhoto(filename) {
  try {
    await API.delete(`/api/photos/file/${filename}`);
    await loadPhotosGrid();
  } catch(e) {
    showToast('Failed to delete photo', 'error');
  }
}

async function clearAllPhotos() {
  if (!confirm('Remove all photos from the slideshow?')) return;
  try {
    const data = await API.get('/api/photos/');
    for (const url of data.photos) {
      const filename = url.split('/').pop();
      await API.delete(`/api/photos/file/${filename}`);
    }
    await loadPhotosGrid();
    showToast('All photos removed', '');
  } catch(e) {
    showToast('Failed to clear photos', 'error');
  }
}

async function loadWeatherZip() {
  try {
    const data = await API.get('/api/settings/weather_zip');
    const el = document.getElementById('weatherZip');
    if (el && data.value) el.value = data.value;
  } catch(e) { /* not set yet */ }
}

async function saveWeatherZip() {
  const zip = (document.getElementById('weatherZip')?.value || '').trim();
  try {
    await API.post('/api/settings/', { weather_zip: zip, weather_lat: '', weather_lon: '', weather_city: '' });
    showToast('✅ Weather location saved!', 'success');
  } catch(e) {
    showToast('Failed to save', 'error');
  }
}

async function loadTimezone() {
  try {
    const s = await API.get('/api/settings/timezone');
    const sel = document.getElementById('timezoneSelect');
    if (sel && s.value) sel.value = s.value;
  } catch(e) {}
}

async function saveTimezone() {
  const tz = document.getElementById('timezoneSelect')?.value || 'America/New_York';
  try {
    await API.post('/api/settings/', { timezone: tz });
    showToast('✅ Timezone saved!', 'success');
  } catch(e) {
    showToast('Failed to save', 'error');
  }
}

function setSecretStatus(statusId, clearId, isSet, savedText, emptyText) {
  const status = document.getElementById(statusId);
  const clearBtn = document.getElementById(clearId);
  if (status) status.textContent = isSet ? savedText : emptyText;
  if (clearBtn) clearBtn.style.display = isSet ? '' : 'none';
}

async function loadHASettings() {
  try {
    const s = await API.get('/api/settings/');
    if (s.ha_url) document.getElementById('haUrl').value = s.ha_url;
    const tokenEl = document.getElementById('haToken');
    const codeEl = document.getElementById('haAlarmCode');
    if (tokenEl) tokenEl.value = '';
    if (codeEl) codeEl.value = '';
    setSecretStatus(
      'haTokenStatus',
      'haTokenClear',
      !!s.ha_token_set,
      'A token is saved. Paste a new one to replace it.',
      'No token saved yet.'
    );
    setSecretStatus(
      'haAlarmStatus',
      'haAlarmClear',
      !!s.ha_alarm_code_set,
      'A code is saved. Paste a new one to replace it.',
      'No alarm code saved.'
    );
    const entities = s.ha_entities ? JSON.parse(s.ha_entities) : [];
    const list = document.getElementById('haEntityList');
    list.innerHTML = '';
    if (entities.length) {
      entities.forEach(e => addHAEntityRow(e.entity_id, e.name));
    }
  } catch(e) { /* settings not set yet */ }
}

function addHAEntityRow(entityId = '', name = '') {
  const list = document.getElementById('haEntityList');
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:8px;align-items:center';
  row.innerHTML = `
    <input type="text" class="form-input ha-entity-id" placeholder="cover.garage_door" value="${entityId}" style="flex:1.5">
    <input type="text" class="form-input ha-entity-name" placeholder="Friendly name" value="${name}" style="flex:1">
    <button class="btn btn-danger-ghost" onclick="this.parentElement.remove()" style="padding:8px 10px">✕</button>
  `;
  list.appendChild(row);
}

function toggleHATokenVisibility() {
  const el = document.getElementById('haToken');
  el.type = el.type === 'password' ? 'text' : 'password';
}

function toggleAlarmCodeVisibility() {
  const el = document.getElementById('haAlarmCode');
  el.type = el.type === 'password' ? 'text' : 'password';
}

async function clearStoredSecret(key) {
  const label = key === 'ha_token' ? 'Home Assistant token' : 'alarm code';
  if (!confirm(`Remove the saved ${label}?`)) return;
  try {
    await API.post('/api/settings/', { [key]: null });
    showToast('Removed', '');
    await loadHASettings();
  } catch (e) {
    showToast('Failed to remove', 'error');
  }
}

async function saveHASettings() {
  const url = document.getElementById('haUrl')?.value.trim() || '';
  const token = document.getElementById('haToken')?.value.trim() || '';
  const rows = document.querySelectorAll('#haEntityList > div');
  const entities = [];
  rows.forEach(row => {
    const eid = row.querySelector('.ha-entity-id')?.value.trim();
    const name = row.querySelector('.ha-entity-name')?.value.trim();
    if (eid) entities.push({ entity_id: eid, name: name || eid });
  });
  try {
    const alarmCode = document.getElementById('haAlarmCode')?.value.trim() || '';
    const body = {
      ha_url: url,
      ha_entities: JSON.stringify(entities),
    };
    if (token) body.ha_token = token;
    if (alarmCode) body.ha_alarm_code = alarmCode;
    await API.post('/api/settings/', body);
    const tokenEl = document.getElementById('haToken');
    const codeEl = document.getElementById('haAlarmCode');
    if (tokenEl) tokenEl.value = '';
    if (codeEl) codeEl.value = '';
    showToast('✅ Home Assistant settings saved!', 'success');
    await loadHASettings();
  } catch(e) {
    showToast('Failed to save', 'error');
  }
}

async function loadSchoolcafeSettings() {
  try {
    const s = await API.get('/api/settings/');
    const hidden = document.getElementById('lunchHidden');
    if (hidden) hidden.checked = s.lunch_hidden === 'true';
    const current = document.getElementById('districtCurrent');
    if (current) {
      current.textContent = s.schoolcafe_district_name
        ? 'Connected: ' + s.schoolcafe_district_name
        : 'No district selected';
    }
  } catch(e) {}
}

async function searchSchoolDistricts() {
  const q = (document.getElementById('districtSearch')?.value || '').trim();
  const el = document.getElementById('districtResults');
  if (!el) return;
  if (q.length < 2) { showToast('Type at least 2 letters', 'error'); return; }
  el.innerHTML = '<div class="meals-note">Searching…</div>';
  try {
    const rows = await API.get('/api/schoolcafe/districts?q=' + encodeURIComponent(q));
    if (!rows.length) {
      el.innerHTML = '<div class="meals-note">No districts found</div>';
      return;
    }
    el.innerHTML = rows.slice(0, 12).map(d =>
      `<button type="button" class="district-result" data-id="${d.id}" data-name="${_escapeHtml(d.name)}">${_escapeHtml(d.name)}${d.published ? '' : ' <span style="color:var(--text2)">(menu not published)</span>'}</button>`
    ).join('');
    el.querySelectorAll('.district-result').forEach(btn => {
      btn.onclick = () => selectSchoolDistrict(btn.dataset.id, btn.dataset.name);
    });
  } catch (e) {
    el.innerHTML = '';
    showToast('SchoolCafe search failed', 'error');
  }
}

async function selectSchoolDistrict(id, name) {
  try {
    await API.post('/api/settings/', {
      schoolcafe_district_id: String(id),
      schoolcafe_district_name: name,
    });
    const current = document.getElementById('districtCurrent');
    if (current) current.textContent = 'Connected: ' + name;
    const results = document.getElementById('districtResults');
    if (results) results.innerHTML = '';
    showToast('✅ District saved', 'success');
  } catch (e) {
    showToast('Failed to save district', 'error');
  }
}

async function saveSchoolMenuVisibility() {
  const hidden = document.getElementById('lunchHidden')?.checked ?? false;
  try {
    await API.post('/api/settings/', { lunch_hidden: String(hidden) });
    showToast('✅ School menu visibility saved', 'success');
  } catch (e) {
    showToast('Failed to save', 'error');
  }
}

async function loadStockSettings() {
  try {
    const data = await API.get('/api/stocks/settings');
    const el = document.getElementById('stockSymbols');
    if (el && data.symbols) el.value = data.symbols;
  } catch(e) {}
}

async function saveStockSettings() {
  const symbols = (document.getElementById('stockSymbols')?.value || '').trim();
  try {
    await API.post('/api/settings/', { stock_symbols: symbols });
    showToast('✅ Stock tickers saved!', 'success');
  } catch(e) {
    showToast('Failed to save', 'error');
  }
}

async function loadCameraSettings() {
  try {
    const data = await API.get('/api/security/cameras');
    const cameras = data.cameras || [];
    const list = document.getElementById('cameraList');
    list.innerHTML = '';
    cameras.forEach(c => addCameraRow(c.name, c.ha_entity_id || ''));
  } catch(e) {}
}

function addCameraRow(name = '', haEntityId = '') {
  const list = document.getElementById('cameraList');
  const row = document.createElement('div');
  row.draggable = true;
  row.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px;background:var(--bg2);border-radius:10px;cursor:default';
  row.innerHTML = `
    <span class="cam-drag-handle" title="Drag to reorder" style="cursor:grab;font-size:18px;color:var(--text-muted);padding:0 4px;flex-shrink:0">⠿</span>
    <input type="text" class="form-input cam-name" placeholder="Camera name (e.g. Driveway)" value="${name}" style="flex:1;min-width:160px">
    <input type="text" class="form-input cam-ha-entity" placeholder="HA Entity ID (e.g. camera.driveway)" value="${haEntityId}" style="flex:1;min-width:200px">
    <button class="btn btn-danger-ghost" onclick="this.closest('div[draggable]').remove()" style="padding:8px 10px">✕</button>
  `;
  _bindCameraDrag(row);
  list.appendChild(row);
}

function _bindCameraDrag(row) {
  row.addEventListener('dragstart', e => {
    e.dataTransfer.effectAllowed = 'move';
    row.classList.add('cam-dragging');
    row._dragSrc = true;
  });
  row.addEventListener('dragend', () => {
    row.classList.remove('cam-dragging');
    document.querySelectorAll('#cameraList > div').forEach(r => r.classList.remove('cam-drag-over'));
  });
  row.addEventListener('dragover', e => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const list = document.getElementById('cameraList');
    const dragging = list.querySelector('.cam-dragging');
    if (dragging && dragging !== row) {
      document.querySelectorAll('#cameraList > div').forEach(r => r.classList.remove('cam-drag-over'));
      row.classList.add('cam-drag-over');
      const rows = [...list.children];
      const fromIdx = rows.indexOf(dragging);
      const toIdx = rows.indexOf(row);
      if (fromIdx < toIdx) list.insertBefore(dragging, row.nextSibling);
      else list.insertBefore(dragging, row);
    }
  });
  row.addEventListener('drop', e => { e.preventDefault(); });
}

async function saveCameraSettings() {
  const rows = document.querySelectorAll('#cameraList > div');
  const cameras = [];
  rows.forEach(row => {
    const name = row.querySelector('.cam-name')?.value.trim();
    const haEntity = row.querySelector('.cam-ha-entity')?.value.trim();
    if (name && haEntity) cameras.push({ name, ha_entity_id: haEntity });
  });
  try {
    await API.post('/api/security/cameras', { cameras });
    showToast('✅ Cameras saved!', 'success');
  } catch(e) {
    showToast('Failed to save', 'error');
  }
}

// ── Chore Reminders ───────────────────────────────────────────
async function loadReminders() {
  try {
    const data = await API.get('/api/settings/chore_reminders').catch(() => ({ value: null }));
    const reminders = data.value ? JSON.parse(data.value) : [
      { enabled: true, time: '07:40', message: 'Please make sure you have done your morning chores', entity_id: 'media_player.kitchen_hub' }
    ];
    const el = document.getElementById('reminderRows');
    el.innerHTML = '';
    reminders.forEach(r => addReminderRow(r));
  } catch(e) {}
}

function addReminderRow(r = {}) {
  const el = document.getElementById('reminderRows');
  const div = document.createElement('div');
  div.style = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px;background:var(--surface);border-radius:10px;border:1px solid var(--border)';
  div.innerHTML = `
    <input type="checkbox" class="rem-enabled" ${r.enabled !== false ? 'checked' : ''} title="Enabled" style="width:18px;height:18px;flex-shrink:0">
    <input type="time" class="form-input rem-time" value="${r.time || '07:40'}" style="width:120px">
    <input type="text" class="form-input rem-message" value="${r.message || ''}" placeholder="Reminder message" style="flex:1;min-width:200px">
    <input type="text" class="form-input rem-entity" value="${r.entity_id || 'media_player.kitchen_hub'}" placeholder="media_player.kitchen_hub" style="width:230px" title="Speaker / media player entity ID">
    <input type="text" class="form-input rem-tts-engine" value="${r.tts_engine || 'tts.google_translate_en_com'}" placeholder="tts.google_translate_en_com" style="width:230px" title="TTS engine entity (for tts.speak service)">
    <button class="btn btn-ghost" onclick="this.closest('div').remove()" style="color:var(--red);flex-shrink:0">✕</button>`;
  el.appendChild(div);
}

async function saveReminders() {
  const rows = document.querySelectorAll('#reminderRows > div');
  const reminders = [];
  rows.forEach(row => {
    const message = row.querySelector('.rem-message')?.value.trim();
    const entity_id = row.querySelector('.rem-entity')?.value.trim();
    if (message && entity_id) {
      reminders.push({
        enabled: row.querySelector('.rem-enabled')?.checked ?? true,
        time: row.querySelector('.rem-time')?.value || '07:40',
        message,
        entity_id,
        tts_engine: row.querySelector('.rem-tts-engine')?.value.trim() || 'tts.google_translate_en_com',
      });
    }
  });
  try {
    await API.post('/api/settings/', { chore_reminders: JSON.stringify(reminders) });
    document.getElementById('reminderSaveStatus').textContent = '✅ Saved';
    setTimeout(() => { document.getElementById('reminderSaveStatus').textContent = ''; }, 3000);
  } catch(e) {
    showToast('Failed to save reminders', 'error');
  }
}

async function testReminder() {
  const firstRow = document.querySelector('#reminderRows > div');
  if (!firstRow) { showToast('Add a reminder first', 'error'); return; }
  const message = firstRow.querySelector('.rem-message')?.value.trim();
  const entity_id = firstRow.querySelector('.rem-entity')?.value.trim();
  const tts_engine = firstRow.querySelector('.rem-tts-engine')?.value.trim() || 'tts.google_translate_en_com';
  if (!message || !entity_id) { showToast('Fill in message and speaker', 'error'); return; }
  try {
    await API.post('/api/homeassistant/tts', { message, entity_id, tts_engine });
    showToast('🔊 Playing on speaker...', 'success');
  } catch(e) {
    showToast('Failed — check HA connection and entity ID', 'error');
  }
}

async function saveSlideShowSettings() {
  const timeout = document.getElementById('slideshowTimeout')?.value || '120';
  const interval = document.getElementById('slideshowInterval')?.value || '5';
  try {
    await API.post('/api/settings/', {
      slideshow_timeout: timeout,
      slideshow_interval: interval,
    });
    showToast('✅ Slideshow settings saved!', 'success');
  } catch(e) {
    showToast('Failed to save settings', 'error');
  }
}

async function loadNightMode() {
  try {
    const [s, e, d] = await Promise.all([
      API.get('/api/settings/dark_mode_start').catch(() => ({value: '21'})),
      API.get('/api/settings/dark_mode_end').catch(() => ({value: '7'})),
      API.get('/api/settings/dim_minutes').catch(() => ({value: '5'}))
    ]);
    document.getElementById('darkStart').value  = s?.value ?? '21';
    document.getElementById('darkEnd').value    = e?.value ?? '7';
    document.getElementById('dimMinutes').value = d?.value ?? '5';
  } catch(e) {}
}

async function saveNightMode() {
  const start = document.getElementById('darkStart').value;
  const end   = document.getElementById('darkEnd').value;
  const dim   = document.getElementById('dimMinutes').value;
  try {
    await API.post('/api/settings/', {
      dark_mode_start: start,
      dark_mode_end:   end,
      dim_minutes:     dim
    });
    showToast('🌙 Night mode saved!', 'success');
  } catch(e) {
    showToast('Failed to save night mode', 'error');
  }
}

async function resetChorePoints() {
  if (!confirm('Reset everyone\'s chore points to 0?\n\nThis deletes all completed chore history but keeps your chore list intact. This cannot be undone.')) return;
  try {
    await API.post('/api/chores/reset?mode=points');
    showToast('Points reset! Everyone starts at 0.', 'success');
  } catch(e) {
    showToast('Failed to reset points', 'error');
  }
}

async function resetAllChores() {
  if (!confirm('Delete ALL chores and history?\n\nThis is a full fresh start — all chores, completions, and points will be gone. This cannot be undone.')) return;
  if (!confirm('Are you sure? This will delete every chore. You will need to create a new chore list from scratch.')) return;
  try {
    await API.post('/api/chores/reset?mode=all');
    showToast('All chores cleared. Fresh start!', 'success');
  } catch(e) {
    showToast('Failed to clear chores', 'error');
  }
}
