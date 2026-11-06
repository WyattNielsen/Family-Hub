const COLORS = ['#4F6EF7','#FF6B6B','#FFB347','#34C759','#AF52DE','#FF2D55','#5AC8FA','#FF9500','#30D158','#64D2FF'];
const EMOJIS = ['👤','👨','👩','👧','👦','👶','🧑','👴','👵','🧔','👸','🤴','🦸','🧙','🧝','🐱','🐶','🦊','🐼','🦁'];
let members = [];
let editingMemberId = null;

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

  loadMembers();
  loadGoogleStatus();
  loadSyncInterval();
  loadSlideshowSettings();
  loadWeatherZip();
  loadHASettings();
  loadTimezone();
  loadNightMode();
  loadLunchSettings();
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

  document.getElementById('emojiPicker').innerHTML = EMOJIS.map(e =>
    `<div class="emoji-opt" data-emoji="${e}" onclick="selectEmoji('${e}')">${e}</div>`
  ).join('');

  document.getElementById('memberColor').oninput = e => selectColor(e.target.value);

  const params = new URLSearchParams(window.location.search);
  if (params.get('connected') === 'member') {
    showToast('✅ Google Calendar connected!', 'success');
    history.replaceState({}, '', '/settings.html');
  }
});

async function loadMembers() {
  members = await API.get('/api/members/');
  const el = document.getElementById('membersList');
  if (!members.length) {
    el.innerHTML = '<div class="empty-state">No family members yet — add your first one!</div>';
    return;
  }
  el.innerHTML = members.map(m => `
    <div class="member-item">
      <div class="member-item-avatar">${m.avatar || '👤'}</div>
      <div class="member-item-info">
        <div class="member-item-name">
          <span class="member-color-swatch" style="background:${m.color}"></span>
          ${m.name}
        </div>
        <div class="member-item-sub">${m.google_email ? '📅 ' + m.google_email : 'No Google Calendar connected'}</div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="chore-action-btn" onclick="openMemberModal(${m.id})">✏️ Edit</button>
        <button class="chore-action-btn delete" onclick="deleteMember(${m.id})">🗑</button>
      </div>
    </div>`).join('');
}

async function loadGoogleStatus() {
  try {
    const status = await API.get('/api/auth/google/status');
    const el = document.getElementById('memberCalendars');
    if (!status.members.length) {
      el.innerHTML = '<div class="empty-state">Add family members above, then connect each person\'s Google Calendar here.</div>';
      return;
    }
    el.innerHTML = status.members.map(m => `
      <div class="settings-card" style="flex-direction:column;align-items:stretch;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;">
          <div class="settings-card-info">
            <h3>${members.find(mb => mb.id == m.id)?.avatar || '👤'} ${m.name}</h3>
            <p class="settings-desc">Google sync-in · new events write to Google primary · Apple sync-in below</p>
            <div class="google-status">
              <span class="status-dot ${m.connected ? 'connected' : 'disconnected'}"></span>
              <span class="status-text ${m.connected ? 'connected' : ''}">${m.connected ? 'Google: ' + m.email : 'Google not connected'}</span>
            </div>
          </div>
          <div class="settings-card-actions">
            ${!m.connected ? `<a href="/api/auth/google/connect/member/${m.id}" class="btn btn-google">
              <svg width="16" height="16" viewBox="0 0 18 18"><path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z"/><path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"/><path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"/><path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 6.29C4.672 4.163 6.656 3.58 9 3.58z"/></svg>
              Connect Google
            </a>` : `<button class="btn btn-danger-ghost" onclick="disconnectMember(${m.id})">Disconnect Google</button>`}
          </div>
        </div>
        <div id="memberCalPicker_${m.id}"></div>
        <div id="memberApple_${m.id}" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)"></div>
      </div>`).join('');
    setTimeout(() => {
      for (const m of (status?.members || [])) {
        if (m.connected) loadCalendarPicker('member', m.id);
        loadAppleCalendarSection(m.id);
      }
    }, 100);
  } catch(e) {}
}

function openMemberModal(memberId) {
  editingMemberId = memberId || null;
  document.getElementById('memberModalTitle').textContent = memberId ? 'Edit Member' : 'Add Family Member';
  if (memberId) {
    const m = members.find(m => m.id === memberId);
    if (m) {
      document.getElementById('memberName').value = m.name;
      selectColor(m.color);
      selectEmoji(m.avatar || '👤');
    }
  } else {
    document.getElementById('memberName').value = '';
    selectColor(COLORS[0]);
    selectEmoji('👤');
  }
  document.getElementById('memberModal').style.display = 'flex';
  document.getElementById('memberName').focus();
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

function selectEmoji(emoji) {
  document.getElementById('memberAvatar').value = emoji;
  document.querySelectorAll('.emoji-opt').forEach(el => {
    el.classList.toggle('selected', el.dataset.emoji === emoji);
  });
}

async function saveMember() {
  const name = document.getElementById('memberName').value.trim();
  if (!name) { showToast('Please enter a name', 'error'); return; }
  const payload = {
    name,
    color: document.getElementById('memberColor').value,
    avatar: document.getElementById('memberAvatar').value,
  };
  try {
    if (editingMemberId) {
      await API.put(`/api/members/${editingMemberId}`, payload);
      showToast('✅ Member updated!', 'success');
    } else {
      await API.post('/api/members/', payload);
      showToast('✅ Member added!', 'success');
    }
    closeMemberModal();
    loadMembers();
    loadSidebarMembers();
    loadGoogleStatus();
  } catch(e) {
    showToast('Failed to save member', 'error');
  }
}

async function deleteMember(id) {
  if (!confirm('Remove this family member? Their chores and events will remain.')) return;
  try {
    await API.delete(`/api/members/${id}`);
    showToast('Member removed', '');
    loadMembers();
    loadSidebarMembers();
  } catch(e) {
    showToast('Failed to delete', 'error');
  }
}

async function disconnectMember(id) {
  if (!confirm('Disconnect their Google Calendar?')) return;
  await API.delete(`/api/auth/google/disconnect/member/${id}`);
  showToast('Disconnected', '');
  loadGoogleStatus();
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

async function loadCalendarPicker(type, memberId) {
  const containerId = `memberCalPicker_${memberId}`;
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '<div style="font-size:13px;color:var(--text2);padding:8px">Loading calendars...</div>';
  try {
    const data = await API.get(`/api/calendar/google-calendars/member/${memberId}`);
    if (!data || !data.calendars) throw new Error('Bad response');
    const { calendars, selected } = data;
    let selectedIds = new Set(selected);
    container.innerHTML = `
      <div class="cal-picker">
        <div class="cal-picker-title">Calendars to sync into Family Hub (⭐ = primary, used for new events):</div>
        ${calendars.map(c => `
          <div class="cal-option ${selectedIds.has(c.id) ? 'selected' : ''}"
               data-id="${c.id}"
               onclick="toggleCalOption(this, '${containerId}')">
            <div class="cal-option-dot" style="background:${c.backgroundColor}"></div>
            <div class="cal-option-name">${c.summary}${c.primary ? ' ⭐' : ''}</div>
            ${selectedIds.has(c.id) ? '<div class="cal-option-check">✓</div>' : '<div class="cal-option-check" style="opacity:0">✓</div>'}
          </div>`).join('')}
        <button class="btn btn-primary cal-picker-save" onclick="saveCalendarSelection('member', ${memberId}, '${containerId}')">
          Save Selection
        </button>
      </div>`;
  } catch(e) {
    container.innerHTML = `<div style="font-size:13px;color:var(--red);padding:8px">Error: ${e.message}</div>`;
  }
}

function toggleCalOption(el, _containerId) {
  el.classList.toggle('selected');
  const check = el.querySelector('.cal-option-check');
  check.style.opacity = el.classList.contains('selected') ? '1' : '0';
}

async function saveCalendarSelection(type, memberId, containerId) {
  const container = document.getElementById(containerId);
  const selected = [...container.querySelectorAll('.cal-option.selected')].map(el => el.dataset.id);
  if (!selected.length) { showToast('Select at least one calendar', 'error'); return; }
  try {
    await API.post(`/api/calendar/google-calendars/member/${memberId}/select`, { calendar_ids: selected });
    showToast(`✅ Saved! ${selected.length} calendar${selected.length !== 1 ? 's' : ''} will sync`, 'success');
  } catch(e) {
    showToast('Failed to save selection', 'error');
  }
}

async function loadAppleCalendarSection(memberId) {
  const el = document.getElementById(`memberApple_${memberId}`);
  if (!el) return;
  el.innerHTML = '<div style="font-size:13px;color:var(--text2)">Loading Apple calendars…</div>';
  try {
    const data = await API.get(`/api/calendar/apple/member/${memberId}`);
    const sources = data.sources || [];
    const sourceList = sources.length
      ? sources.map(s => `
          <div class="cal-option selected" style="cursor:default">
            <div class="cal-option-dot" style="background:${s.color || '#AF52DE'}"></div>
            <div class="cal-option-name">${s.kind === 'caldav' ? '☁️ ' : '🔗 '}${s.name}</div>
            <button class="btn btn-danger-ghost" style="padding:4px 8px;font-size:12px"
              onclick="removeAppleSource(${s.id}, ${memberId})">Remove</button>
          </div>`).join('')
      : '<div style="font-size:13px;color:var(--text2);padding:4px 0">No Apple / ICS calendars yet.</div>';

    el.innerHTML = `
      <h3 style="margin:0 0 6px">🍎 Apple Calendar</h3>
      <p class="settings-desc" style="margin-bottom:10px">
        Sync from iCloud (CalDAV) or a shared webcal/ICS link. Events are tagged to this person (read-only sync-in).
      </p>
      <div class="cal-picker" style="margin-bottom:12px">${sourceList}</div>

      <div style="display:grid;gap:10px;margin-bottom:14px">
        <div style="font-weight:800;font-size:13px">Add shared calendar (ICS / webcal)</div>
        <input id="appleIcsName_${memberId}" class="form-input" placeholder="Name (e.g. School sports)">
        <input id="appleIcsUrl_${memberId}" class="form-input" placeholder="webcal://… or https://…/*.ics">
        <button class="btn btn-primary" onclick="addAppleIcs(${memberId})">Add ICS calendar</button>
      </div>

      <div style="display:grid;gap:10px;padding-top:12px;border-top:1px solid var(--border)">
        <div style="font-weight:800;font-size:13px">iCloud account</div>
        ${data.icloud_connected ? `
          <div class="google-status">
            <span class="status-dot connected"></span>
            <span class="status-text connected">Connected: ${data.apple_id}</span>
          </div>
          <div id="appleIcloudPicker_${memberId}"></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn btn-primary" onclick="loadIcloudCalendarPicker(${memberId})">Choose iCloud calendars</button>
            <button class="btn btn-danger-ghost" onclick="disconnectIcloud(${memberId})">Disconnect iCloud</button>
          </div>
        ` : `
          <p class="settings-desc">Use an <a href="https://appleid.apple.com" target="_blank" rel="noopener">app-specific password</a> (Apple ID → Sign-In and Security).</p>
          <input id="appleId_${memberId}" class="form-input" placeholder="Apple ID email" autocomplete="username">
          <input id="applePass_${memberId}" class="form-input" type="password" placeholder="App-specific password" autocomplete="current-password">
          <button class="btn btn-primary" onclick="connectIcloud(${memberId})">Connect iCloud</button>
        `}
      </div>
    `;
    if (data.icloud_connected) {
      // Show currently selected CalDAV sources hint only; picker on demand
    }
  } catch (e) {
    el.innerHTML = `<div style="font-size:13px;color:var(--red)">Apple calendar error: ${e.message}</div>`;
  }
}

async function addAppleIcs(memberId) {
  const name = document.getElementById(`appleIcsName_${memberId}`)?.value.trim();
  const url = document.getElementById(`appleIcsUrl_${memberId}`)?.value.trim();
  if (!name || !url) { showToast('Enter a name and calendar URL', 'error'); return; }
  try {
    await API.post(`/api/calendar/apple/member/${memberId}/ics`, { name, url });
    showToast('✅ Apple/ICS calendar added', 'success');
    loadAppleCalendarSection(memberId);
  } catch (e) {
    showToast(e.message || 'Failed to add calendar', 'error');
  }
}

async function removeAppleSource(sourceId, memberId) {
  if (!confirm('Remove this Apple/ICS calendar and its synced events?')) return;
  try {
    await API.delete(`/api/calendar/apple/sources/${sourceId}`);
    showToast('Removed', '');
    loadAppleCalendarSection(memberId);
  } catch (e) {
    showToast('Failed to remove', 'error');
  }
}

async function connectIcloud(memberId) {
  const apple_id = document.getElementById(`appleId_${memberId}`)?.value.trim();
  const app_password = document.getElementById(`applePass_${memberId}`)?.value.trim();
  if (!apple_id || !app_password) { showToast('Enter Apple ID and app-specific password', 'error'); return; }
  try {
    const res = await API.post(`/api/calendar/apple/member/${memberId}/icloud`, { apple_id, app_password });
    showToast('✅ iCloud connected', 'success');
    await loadAppleCalendarSection(memberId);
    if (res.calendars?.length) {
      await renderIcloudPicker(memberId, res.calendars);
    }
  } catch (e) {
    showToast(e.message || 'iCloud connect failed', 'error');
  }
}

async function disconnectIcloud(memberId) {
  if (!confirm('Disconnect iCloud and remove synced CalDAV calendars for this person?')) return;
  try {
    await API.delete(`/api/calendar/apple/member/${memberId}/icloud`);
    showToast('iCloud disconnected', '');
    loadAppleCalendarSection(memberId);
  } catch (e) {
    showToast('Failed to disconnect', 'error');
  }
}

async function loadIcloudCalendarPicker(memberId) {
  try {
    const data = await API.get(`/api/calendar/apple/member/${memberId}/icloud/calendars`);
    await renderIcloudPicker(memberId, data.calendars || []);
  } catch (e) {
    showToast(e.message || 'Failed to list iCloud calendars', 'error');
  }
}

async function renderIcloudPicker(memberId, calendars) {
  const box = document.getElementById(`appleIcloudPicker_${memberId}`);
  if (!box) return;
  const status = await API.get(`/api/calendar/apple/member/${memberId}`);
  const selectedUrls = new Set(
    (status.sources || []).filter(s => s.kind === 'caldav').map(s => s.url)
  );
  box.innerHTML = `
    <div class="cal-picker" style="margin-top:10px">
      <div class="cal-picker-title">Choose iCloud calendars to sync:</div>
      ${calendars.map(c => `
        <div class="cal-option ${selectedUrls.has(c.url) ? 'selected' : ''}"
             data-url="${encodeURIComponent(c.url)}" data-name="${encodeURIComponent(c.name || 'Calendar')}"
             onclick="toggleCalOption(this)">
          <div class="cal-option-dot" style="background:#AF52DE"></div>
          <div class="cal-option-name">${c.name}</div>
          <div class="cal-option-check" style="opacity:${selectedUrls.has(c.url) ? '1' : '0'}">✓</div>
        </div>`).join('') || '<div style="padding:8px;color:var(--text2)">No calendars found</div>'}
      <button class="btn btn-primary cal-picker-save" onclick="saveIcloudSelection(${memberId})">Save iCloud selection</button>
    </div>`;
}

async function saveIcloudSelection(memberId) {
  const box = document.getElementById(`appleIcloudPicker_${memberId}`);
  const selected = [...box.querySelectorAll('.cal-option.selected')].map(el => ({
    url: decodeURIComponent(el.dataset.url || ''),
    name: decodeURIComponent(el.dataset.name || 'Calendar'),
  }));
  if (!selected.length) { showToast('Select at least one iCloud calendar', 'error'); return; }
  try {
    await API.post(`/api/calendar/apple/member/${memberId}/icloud/select`, { calendars: selected });
    showToast(`✅ Saved ${selected.length} iCloud calendar${selected.length !== 1 ? 's' : ''}`, 'success');
    loadAppleCalendarSection(memberId);
  } catch (e) {
    showToast(e.message || 'Failed to save', 'error');
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

async function loadHASettings() {
  try {
    const s = await API.get('/api/settings/');
    if (s.ha_url) document.getElementById('haUrl').value = s.ha_url;
    if (s.ha_token) document.getElementById('haToken').value = s.ha_token;
    if (s.ha_alarm_code) document.getElementById('haAlarmCode').value = s.ha_alarm_code;
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
    await API.post('/api/settings/', {
      ha_url: url,
      ha_token: token,
      ha_entities: JSON.stringify(entities),
      ha_alarm_code: alarmCode,
    });
    showToast('✅ Home Assistant settings saved!', 'success');
  } catch(e) {
    showToast('Failed to save', 'error');
  }
}

async function loadLunchSettings() {
  try {
    const s = await API.get('/api/settings/');
    const district = document.getElementById('lunchDistrict');
    const slug = document.getElementById('lunchSchoolSlug');
    const menuType = document.getElementById('lunchMenuType');
    const hidden = document.getElementById('lunchHidden');
    if (district) district.value = s.lunch_district || 'bcps';
    if (slug) slug.value = s.lunch_school_slug || 'bcps-weekly-menus';
    if (menuType) menuType.value = s.lunch_menu_type || 'weekly-menus';
    if (hidden) hidden.checked = s.lunch_hidden === 'true';
  } catch(e) {}
}

async function saveLunchSettings() {
  const district = (document.getElementById('lunchDistrict')?.value || '').trim();
  const slug = (document.getElementById('lunchSchoolSlug')?.value || '').trim();
  const menuType = (document.getElementById('lunchMenuType')?.value || '').trim();
  const hidden = document.getElementById('lunchHidden')?.checked ?? false;
  try {
    await API.post('/api/settings/', {
      lunch_district: district,
      lunch_school_slug: slug,
      lunch_menu_type: menuType,
      lunch_hidden: String(hidden),
    });
    showToast('✅ Lunch menu settings saved!', 'success');
  } catch(e) {
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
