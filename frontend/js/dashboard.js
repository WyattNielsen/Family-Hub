function setDashboardDate() {
  const el = document.getElementById('todayDate');
  if (!el) return;
  el.textContent = new Date().toLocaleDateString('en-US', {
    timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric'
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  onPersonFilterChange(() => {
    loadTodayEvents();
    loadUpcomingChores();
    loadMeals();
    loadMessagePreview();
  });
  await initAppTimezone();
  setDashboardDate();

  const params = new URLSearchParams(window.location.search);
  if (params.get('connected') === 'family') {
    showToast('✅ Family Google Calendar connected!', 'success');
    history.replaceState({}, '', '/');
  }

  loadTodayEvents();
  loadUpcomingChores();
  loadMeals();
  loadShoppingStrip();
  loadHAStatus();
  loadStocks();
  loadMessagePreview();
  loadDashCameras();
  setInterval(loadHAStatus, 30000);
  setInterval(loadStocks, 5 * 60 * 1000);

  // Auto-refresh all dashboard data every 5 minutes
  setInterval(() => {
    setDashboardDate();
    loadTodayEvents();
    loadUpcomingChores();
    loadMeals();
    loadShoppingStrip();
    loadWeather();
    loadMessagePreview();
    loadMessageBadge();
  }, 5 * 60 * 1000);
});

function visibleMessages(msgs) {
  const person = getPersonFilter();
  if (person === 'all') return msgs;
  return msgs.filter(m => !m.recipient_id || String(m.recipient_id) === String(person));
}

async function loadMessagePreview() {
  const content = document.getElementById('msgPreviewContent');
  const ack = document.getElementById('msgPreviewAck');
  if (!content) return;
  try {
    const msgs = visibleMessages(await API.get('/api/messages/'));
    if (!msgs.length) {
      content.innerHTML = `<span class="msg-preview-text" style="color:var(--text3)">No messages yet — tap ✏️ to leave a note</span>`;
      if (ack) ack.style.display = 'none';
      return;
    }
    const m = msgs[0];
    const to = m.recipient_id ? {
      name: m.recipient_name || 'Someone',
      color: m.recipient_color || '#9AA0B8',
      avatar: m.recipient_avatar,
      avatar_photo: m.recipient_avatar_photo,
    } : { name: 'Everyone', color: '#9AA0B8', avatar: '👨‍👩‍👧‍👦' };
    const extra = msgs.length > 1 ? `+${msgs.length - 1}` : '';
    content.innerHTML = `
      <span class="msg-preview-from" style="color:${to.color}">${memberAvatarHtml(to, 'avatar-sm')} ${escapeHtml(to.name)}</span>
      <span class="msg-preview-text">${escapeHtml(m.body)}</span>
      <span class="msg-preview-by">— ${escapeHtml(m.name || 'Someone')}</span>
      ${extra ? `<span class="msg-preview-more">${extra} →</span>` : ''}
    `;
    if (ack) {
      ack.style.display = '';
      ack.dataset.id = m.id;
    }
  } catch(e) { /* silently fail */ }
}

async function ackPreviewMessage(e) {
  e.preventDefault();
  const id = document.getElementById('msgPreviewAck')?.dataset.id;
  if (!id) return;
  try {
    await API.delete('/api/messages/' + id);
    loadMessagePreview();
    loadMessageBadge();
  } catch (err) {
    showToast('Could not clear that message', 'error');
  }
}

async function openComposeModal(e) {
  e.preventDefault();
  // Populate author dropdown
  const author = document.getElementById('composeAuthor');
  const recipient = document.getElementById('composeRecipient');
  author.innerHTML = '<option value="">Who\'s posting?</option>';
  recipient.innerHTML = '<option value="">Everyone</option>';
  try {
    const members = await API.get('/api/members/');
    const person = getPersonFilter();
    members.forEach(m => {
      const opt = `<option value="${m.id}">${m.avatar || '👤'} ${escapeHtml(m.name)}</option>`;
      author.innerHTML += opt;
      recipient.innerHTML += opt;
    });
    if (person !== 'all') recipient.value = person;
  } catch(_) {}
  document.getElementById('composeBody').value = '';
  document.getElementById('composeModal').style.display = 'flex';
  setTimeout(() => document.getElementById('composeBody').focus(), 100);
}

function closeComposeModal() {
  document.getElementById('composeModal').style.display = 'none';
}

async function postQuickMessage() {
  const body = document.getElementById('composeBody').value.trim();
  if (!body) return;
  const author_id = document.getElementById('composeAuthor').value || null;
  const recipient_id = document.getElementById('composeRecipient').value || null;
  try {
    await API.post('/api/messages/', {
      body,
      author_id: author_id ? parseInt(author_id) : null,
      recipient_id: recipient_id ? parseInt(recipient_id) : null,
    });
    closeComposeModal();
    showToast('Message posted!', 'success');
    loadMessagePreview();
    loadMessageBadge();
  } catch(e) {
    showToast('Failed to post message', 'error');
  }
}

async function loadTodayEvents() {
  const el = document.getElementById('todayEvents');
  try {
    const now = new Date();
    const start = now.toISOString().replace('.000', '');
    const end48 = new Date(now.getTime() + 48 * 60 * 60 * 1000);
    const end = end48.toISOString().replace('.000', '');
    let eventsUrl = `/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
    const person = getPersonFilter();
    if (person !== 'all') eventsUrl += `&member_id=${encodeURIComponent(person)}`;
    const allFetched = await API.get(eventsUrl);
    const events = allFetched.filter(e => {
      const endTime = e.end_datetime ? new Date(e.end_datetime) : new Date(e.start_datetime);
      return endTime > now;
    });
    if (!events.length) {
      el.innerHTML = '<div class="empty-state">🎉 Nothing coming up!</div>';
      return;
    }
    el.innerHTML = events.map(e => {
      const color = e.color || e.member_color || '#F6BF26';
      const eventDate = e.start_datetime ? new Date(e.start_datetime) : null;
      const eventDateStr = e.start_datetime ? e.start_datetime.slice(0, 10) : null;
      const todayStr = toETDateStr(new Date());
      const isToday = eventDateStr === todayStr;
      let dateLabel = '';
      if (eventDateStr) {
        if (isToday) {
          dateLabel = 'Today';
        } else if (e.all_day) {
          // Parse date-only string as local time to avoid UTC-shift bug
          const [yr, mo, dy] = eventDateStr.split('-').map(Number);
          dateLabel = new Date(yr, mo - 1, dy).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
        } else {
          dateLabel = eventDate.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: TZ });
        }
      }
      // Countdown — integrate into date label
      if (eventDateStr && !isToday) {
        const todayDate = new Date(todayStr + 'T12:00:00');
        const evDate = new Date(eventDateStr + 'T12:00:00');
        const days = Math.round((evDate - todayDate) / 86400000);
        if (days === 1) dateLabel = 'Tomorrow';
        else if (days > 1 && days <= 14) dateLabel += ` · in ${days} days`;
      }

      return `
        <div class="event-card" style="--event:${color}">
          <div class="event-card-title">${e.title}</div>
          <div class="event-card-time">${e.all_day ? `${dateLabel} · All day` : `${dateLabel}${dateLabel ? ' · ' : ''}${formatTime(e.start_datetime)}`}</div>
          ${e.member_name ? `<div class="event-card-member" style="background:${color}22;color:${color}">${memberAvatarHtml({avatar:e.member_avatar, avatar_photo:e.member_avatar_photo, color}, 'avatar-sm')}<span>${escapeHtml(e.member_name)}</span></div>` : ''}
          ${e.is_family ? '<div class="event-card-member" style="background:#F6BF2622;color:#c49a00">🏠 Family</div>' : ''}
        </div>`;
    }).join('');
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Failed to load events</div>';
  }
}

const TOD_ORDER = ['morning', 'afternoon', 'evening'];
const TOD_LABEL = { morning: '🌅 Morning', afternoon: '☀️ Afternoon', evening: '🌙 Evening' };

function renderChoresByTOD(chores, color) {
  const grouped = { morning: [], afternoon: [], evening: [], none: [] };
  for (const c of chores) {
    (grouped[c.time_of_day] || grouped.none).push(c);
  }
  let html = '';
  for (const tod of TOD_ORDER) {
    if (!grouped[tod].length) continue;
    html += `<div class="chore-tod-group-header">${TOD_LABEL[tod]}</div>`;
    html += grouped[tod].map(c => renderDashboardChore(c, color)).join('');
  }
  html += grouped.none.map(c => renderDashboardChore(c, color)).join('');
  return html;
}

async function loadUpcomingChores() {
  const el = document.getElementById('upcomingChores');
  try {
    const [chores, members] = await Promise.all([
      API.get('/api/chores/?hide_future=true&completed=false&since_days=0'),
      API.get('/api/members/')
    ]);
    const person = getPersonFilter();
    const visibleMembers = person === 'all' ? members : members.filter(m => String(m.id) === String(person));
    const visibleChores = person === 'all' ? chores : chores.filter(c => String(c.assigned_to) === String(person));

    if (!visibleChores.length) {
      if (person !== 'all') {
        el.innerHTML = '<div class="empty-state">🙌 All done today!</div>';
        return;
      }
      // Show mini leaderboard instead of blank state
      try {
        const lb = await API.get('/api/chores/leaderboard');
        if (lb && lb.length) {
          const medals = ['🥇','🥈','🥉'];
          el.innerHTML = `
            <div class="dash-chores-done">
              <div class="dash-chores-done-title">🙌 All done today!</div>
              <div class="dash-mini-lb">
                ${lb.slice(0, 3).map((p, i) => `
                  <div class="dash-mini-lb-row">
                    <span class="dash-mini-lb-medal">${medals[i] || ''}</span>
                    <span class="dash-mini-lb-avatar">${memberAvatarHtml(p, 'avatar-sm')}</span>
                    <span class="dash-mini-lb-name" style="color:${p.color}">${p.name}</span>
                    <span class="dash-mini-lb-pts" style="color:${p.color}">${p.streak > 0 ? `🔥 ${p.streak}` : '—'}<small> day streak</small></span>
                  </div>`).join('')}
              </div>
            </div>`;
        } else {
          el.innerHTML = '<div class="empty-state">🙌 All chores done!</div>';
        }
      } catch { el.innerHTML = '<div class="empty-state">🙌 All chores done!</div>'; }
      return;
    }

    // Group by member
    const byMember = {};
    const unassigned = [];
    for (const c of visibleChores) {
      if (c.assigned_to) {
        if (!byMember[c.assigned_to]) byMember[c.assigned_to] = [];
        byMember[c.assigned_to].push(c);
      } else {
        unassigned.push(c);
      }
    }

    let html = '';
    for (const m of visibleMembers) {
      const mChores = (byMember[m.id] || []).slice(0, 10);
      if (!mChores.length) continue;
      html += `<div class="person-chores-section">
        <div class="person-chores-header">
          ${memberAvatarHtml(m, 'avatar-sm')}
          <span style="color:${m.color}">${escapeHtml(m.name)}</span>
        </div>
        ${renderChoresByTOD(mChores, m.color)}
      </div>`;
    }
    if (person === 'all' && unassigned.length) {
      html += `<div class="person-chores-section">
        <div class="person-chores-header">📋 Unassigned</div>
        ${renderChoresByTOD(unassigned.slice(0, 5), '#9AA0B8')}
      </div>`;
    }
    el.innerHTML = html || '<div class="empty-state">🙌 All chores done!</div>';
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Failed to load chores</div>';
  }
}

function renderDashboardChore(c, color) {
  return `
    <div class="dashboard-chore-item ${c.completed ? 'done' : ''}" id="dchore-${c.id}" style="--event:${color}">
      <div class="dashboard-chore-check ${c.completed ? 'checked' : ''}"
           style="${c.completed ? `background:${color}` : `border-color:${color}`}"
           onclick="toggleDashboardChore(${c.id}, ${c.completed})">
        ${c.completed ? '✓' : ''}
      </div>
      <div class="dashboard-chore-title ${c.completed ? 'done' : ''}">${c.title}</div>
      <div class="dashboard-chore-pts">🌟${c.points}</div>
    </div>`;
}

async function toggleDashboardChore(id, currentlyCompleted) {
  try {
    await API.put(`/api/chores/${id}`, { completed: !currentlyCompleted });
    if (!currentlyCompleted) showToast('✅ Nice work! Points earned!', 'success');
    loadUpcomingChores();
  } catch(e) {
    showToast('Failed to update chore', 'error');
  }
}


// Track last-fetched HA entities for the popover
let _haEntities = [];

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
    badge.innerHTML = data.entities.map(e => {
      const isAlarm = e.entity_type === 'alarm';
      const isCover = e.entity_id.startsWith('cover.');
      const clickable = (isAlarm || isCover) ? 'ha-alarm-clickable' : '';
      const onclick = isAlarm
        ? `onclick="toggleAlarmPopover(event,'${e.entity_id}')"`
        : isCover
          ? `onclick="toggleCoverPopover(event,'${e.entity_id}')"`
          : '';
      return `
        <div class="ha-badge-item ${e.css_class} ${clickable}" ${onclick} data-entity-id="${e.entity_id}">
          <span class="ha-badge-icon">${e.icon}</span>
          <span class="ha-badge-label">${e.name}:</span>
          <span class="ha-badge-state">${e.state_label || e.state}</span>
        </div>`;
    }).join('');
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
    <div class="ha-popover-title">🔐 ${entity.name}</div>
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

  // Position below the clicked badge
  const rect = evt.currentTarget.getBoundingClientRect();
  popover.style.top = (rect.bottom + 8) + 'px';
  popover.style.right = (window.innerWidth - rect.right) + 'px';
  document.body.appendChild(popover);

  // Close on outside click
  setTimeout(() => document.addEventListener('click', closeAlarmPopover, { once: true }), 0);
}

function closeAlarmPopover() {
  document.getElementById('haAlarmPopover')?.remove();
}

function optimisticHAUpdate(entityId, newState) {
  const entity = _haEntities.find(e => e.entity_id === entityId);
  if (!entity) return;
  entity.state = newState;
  // Re-derive visuals for the new state
  if (entity.entity_type === 'alarm') {
    const ALARM_LABELS = {
      disarmed: ['🔓', 'ha-alarm-disarmed', 'Disarmed'],
      armed_away: ['🔒', 'ha-alarm-armed', 'Armed Away'],
      armed_home: ['🔒', 'ha-alarm-armed', 'Armed Home'],
      arming: ['⏳', 'ha-alarm-pending', 'Arming…'],
    };
    const [icon, css, label] = ALARM_LABELS[newState] || ['❓', 'ha-state-unknown', newState];
    entity.icon = icon; entity.css_class = css; entity.state_label = label;
  } else if (entity.entity_id.startsWith('cover.')) {
    entity.icon = newState === 'open' ? '🚗' : '🏠';
    entity.css_class = newState === 'open' ? 'ha-state-open' : 'ha-state-closed';
    entity.state_label = newState;
  }
  // Re-render the badge immediately
  const badge = document.getElementById('haStatusBadge');
  if (!badge) return;
  badge.innerHTML = _haEntities.map(e => {
    const isAlarm = e.entity_type === 'alarm';
    const isCover = e.entity_id.startsWith('cover.');
    const clickable = (isAlarm || isCover) ? 'ha-alarm-clickable' : '';
    const onclick = isAlarm
      ? `onclick="toggleAlarmPopover(event,'${e.entity_id}')"`
      : isCover ? `onclick="toggleCoverPopover(event,'${e.entity_id}')"` : '';
    return `
      <div class="ha-badge-item ${e.css_class} ${clickable}" ${onclick} data-entity-id="${e.entity_id}">
        <span class="ha-badge-icon">${e.icon}</span>
        <span class="ha-badge-label">${e.name}:</span>
        <span class="ha-badge-state">${e.state_label || e.state}</span>
      </div>`;
  }).join('');
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
    <div class="ha-popover-title">🚗 ${entity.name}</div>
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

async function loadStocks() {
  const el = document.getElementById('stockTicker');
  if (!el) return;
  try {
    const settingsRes = await API.get('/api/stocks/settings');
    const symbols = settingsRes.symbols || 'BTC,TSLA,SPY';
    const stocks = await API.get(`/api/stocks/?symbols=${encodeURIComponent(symbols)}`);
    if (!stocks.length) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    const items = stocks.map(s => {
      const up = s.change_pct >= 0;
      return `<div class="ticker-item">
        <span class="ticker-symbol">${s.symbol}</span>
        <span class="ticker-price">$${s.price_fmt}</span>
        <span class="ticker-change ${up ? 'ticker-up' : 'ticker-down'}">${up ? '▲' : '▼'} ${Math.abs(s.change_pct).toFixed(2)}%</span>
      </div>`;
    }).join('<div class="ticker-sep">|</div>');
    el.innerHTML = `<div class="ticker-scroll">${items}</div>`;
  } catch(e) {
    if (el) el.style.display = 'none';
  }
}

function appHour() {
  const raw = new Date().toLocaleString('en-US', { timeZone: TZ, hour: '2-digit', hourCycle: 'h23' });
  const hour = parseInt(raw, 10);
  return hour === 24 ? 0 : hour;
}

function schoolMenuDate(dinnerDate) {
  let iso = appHour() >= 17 ? addDays(dinnerDate, 1) : dinnerDate;
  const [y, m, d] = iso.split('-').map(Number);
  const dow = new Date(y, m - 1, d).getDay();
  if (dow === 6) iso = addDays(iso, 2);
  else if (dow === 0) iso = addDays(iso, 1);
  return iso;
}

function schoolWhenLabel(dinnerDate, schoolDate) {
  if (schoolDate === dinnerDate) return 'Today';
  if (schoolDate === addDays(dinnerDate, 1)) return 'Tomorrow';
  const [y, m, d] = schoolDate.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { weekday: 'long' });
}

function addDays(dateStr, n) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + n);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function menuItemsText(items) {
  if (!items || !items.length) return '';
  const shown = items.slice(0, 8).map(i =>
    i.is_entree ? `<span class="menu-entree">${escapeHtml(i.name)}</span>` : escapeHtml(i.name)
  ).join(' · ');
  return shown + (items.length > 8 ? ` · +${items.length - 8}` : '');
}

async function saveDinner(date, title) {
  await API.put('/api/meals/dinner', { meal_date: date, title });
}

async function loadMeals() {
  const el = document.getElementById('mealsBody');
  if (!el) return;
  const dinnerDate = toETDateStr(new Date());
  const schoolDate = schoolMenuDate(dinnerDate);
  const person = getPersonFilter();
  let url = `/api/meals/day?date=${dinnerDate}&school_date=${schoolDate}`;
  if (person !== 'all') url += `&member_id=${encodeURIComponent(person)}`;
  try {
    const data = await API.get(url);
    const reqUrl = person === 'all'
      ? '/api/meals/requests'
      : `/api/meals/requests?member_id=${encodeURIComponent(person)}`;
    const requests = await API.get(reqUrl).catch(() => []);
    const dinnerTitle = (data.dinner && data.dinner.title) || '';
    const servedDate = data.school_date || schoolDate;
    const schoolWhen = schoolWhenLabel(dinnerDate, servedDate);
    let schoolsHtml = '';
    if (data.school_menus_hidden) {
      schoolsHtml = '';
    } else if (!data.configured) {
      schoolsHtml = '<div class="meals-note">Connect SchoolCafe in Settings to see school menus.</div>';
    } else if (!data.schools.length) {
      schoolsHtml = person === 'all'
        ? '<div class="meals-note">Assign a school to each student in Settings.</div>'
        : '<div class="meals-note">No school menu for this person.</div>';
    } else {
      schoolsHtml = `<div class="meals-school-label">School menu · ${schoolWhen}</div>` + data.schools.map(s => {
        const breakfast = menuItemsText(s.breakfast);
        const lunch = menuItemsText(s.lunch);
        const body = s.error
          ? `<div class="meals-note">${escapeHtml(s.error)}</div>`
          : `${breakfast ? `<div class="menu-row"><span class="menu-row-label">🌅 Breakfast</span><span class="menu-row-items">${breakfast}</span></div>` : ''}
             ${lunch ? `<div class="menu-row"><span class="menu-row-label">🍕 Lunch</span><span class="menu-row-items">${lunch}</span></div>` : ''}
             ${!breakfast && !lunch ? '<div class="meals-note">No menu posted</div>' : ''}`;
        return `<div class="school-block" style="--event:${s.member_color || '#4A90D9'}">
          <div class="school-block-head">
            ${memberAvatarHtml({ avatar: s.avatar, avatar_photo: s.avatar_photo, color: s.member_color }, 'avatar-sm')}
            <div>
              <div class="school-block-name" style="color:${s.member_color || 'inherit'}">${escapeHtml(s.member_name)}</div>
              <div class="school-block-school">${escapeHtml(s.school_name || '')}</div>
            </div>
          </div>
          ${body}
        </div>`;
      }).join('');
    }
    const editing = document.activeElement && document.activeElement.id === 'dinnerTitleInput';
    const draft = editing ? document.activeElement.value : null;
    const shownAsks = (requests || []).slice(0, 3);
    const asksHtml = shownAsks.length ? `<div class="meal-ask-list">${shownAsks.map(r => `
      <a class="meal-ask" href="/meals.html">
        ${memberAvatarHtml(r, 'avatar-sm')}
        <span><strong style="color:${r.color || 'inherit'}">${escapeHtml(r.name)}</strong> wants ${escapeHtml(r.title)}</span>
      </a>`).join('')}${requests.length > 3 ? `<a href="/meals.html" class="meals-note">+${requests.length - 3} more</a>` : ''}</div>` : '';
    el.innerHTML = `
      <div class="dinner-card">
        <div class="dinner-kicker">Tonight</div>
        <input id="dinnerTitleInput" class="dinner-input" placeholder="What's for dinner?" value="${escapeHtml(draft !== null ? draft : dinnerTitle)}" autocomplete="off">
      </div>
      ${asksHtml}
      ${schoolsHtml}`;
    const input = document.getElementById('dinnerTitleInput');
    if (input) {
      input.addEventListener('keydown', ev => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
      });
      input.addEventListener('blur', async () => {
        const next = input.value.trim();
        if (next === dinnerTitle) return;
        try {
          await saveDinner(dinnerDate, next);
        } catch (e) {
          showToast('Could not save dinner', 'error');
        }
      });
      if (editing) input.focus();
    }
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Meals unavailable</div>';
  }
}

async function loadShoppingStrip() {
  const el = document.getElementById('shoppingStrip');
  if (!el) return;
  try {
    const items = await API.get('/api/shopping/');
    const open = items.filter(i => !i.checked);
    const shown = open.slice(0, 6);
    el.style.display = 'flex';
    el.innerHTML = `
      <a href="/shopping.html" class="shopping-strip-link">🛒 Shopping</a>
      ${shown.map(i => `<button type="button" class="shopping-chip" onclick="checkShoppingItem(${i.id})">${escapeHtml(i.name)}${i.quantity ? ' · ' + escapeHtml(i.quantity) : ''}</button>`).join('')}
      ${open.length > 6 ? `<a href="/shopping.html" class="shopping-strip-more">+${open.length - 6}</a>` : ''}
      ${!open.length ? '<span class="shopping-strip-empty">Nothing to buy</span>' : ''}`;
  } catch (e) {
    el.style.display = 'none';
  }
}

async function checkShoppingItem(id) {
  try {
    await API.patch('/api/shopping/' + id, { checked: true });
    loadShoppingStrip();
  } catch (e) {
    showToast('Could not update the list', 'error');
  }
}

// ── Dashboard Cameras ─────────────────────────────────────────
let _camRefreshTimer = null;

async function loadDashCameras() {
  const strip = document.getElementById('dashCameras');
  if (!strip) return;
  try {
    const data = await API.get('/api/security/cameras');
    const cameras = (data.cameras || []).slice(0, 2);
    if (!cameras.length) return;

    strip.innerHTML = cameras.map(cam => `
      <a href="/security.html" class="dash-cam-card">
        <img class="dash-cam-img" id="dashCam_${cam.ha_entity_id}"
             src="/api/security/camera/${cam.ha_entity_id}/snapshot?t=${Date.now()}"
             alt="${cam.name || cam.ha_entity_id}" onerror="this.style.display='none'">
        <div class="dash-cam-label">${cam.name || cam.ha_entity_id}</div>
      </a>`).join('');
    strip.style.display = 'flex';

    // Refresh snapshots every 10 seconds
    clearInterval(_camRefreshTimer);
    _camRefreshTimer = setInterval(() => {
      cameras.forEach(cam => {
        const img = document.getElementById(`dashCam_${cam.ha_entity_id}`);
        if (img) { img.style.display = ''; img.src = `/api/security/camera/${cam.ha_entity_id}/snapshot?t=${Date.now()}`; }
      });
    }, 10000);
  } catch(e) {}
}
