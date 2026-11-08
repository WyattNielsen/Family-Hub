let weekDays = [];
let members = [];
let weekOffset = 0;

document.addEventListener('DOMContentLoaded', async () => {
  onPersonFilterChange(() => {
    fillPeople();
    loadWeek();
    loadRequests();
  });
  await initAppTimezone();
  try {
    members = await API.get('/api/members/');
  } catch (e) {
    members = [];
  }
  fillPeople();
  document.getElementById('requestForm').addEventListener('submit', submitRequest);
  document.getElementById('weekPrev').addEventListener('click', () => shiftWeek(-1));
  document.getElementById('weekNext').addEventListener('click', () => shiftWeek(1));
  await loadWeek();
  await loadRequests();
  await loadHistory();
});

function shiftWeek(delta) {
  weekOffset += delta;
  loadWeek();
}

function ymd(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function parseIso(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function addDays(iso, n) {
  const date = parseIso(iso);
  date.setDate(date.getDate() + n);
  return ymd(date);
}

function mondayOf(iso) {
  const date = parseIso(iso);
  const sinceMonday = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - sinceMonday);
  return ymd(date);
}

function weekAnchor() {
  return addDays(mondayOf(toETDateStr(new Date())), weekOffset * 7);
}

function fillPeople() {
  const sel = document.getElementById('requestPerson');
  if (!sel) return;
  const person = getPersonFilter();
  sel.innerHTML = '<option value="">Who wants this?</option>' + members.map(m =>
    `<option value="${m.id}">${escapeHtml(m.name)}</option>`
  ).join('');
  if (person !== 'all') sel.value = String(person);
}

function dayLabel(iso) {
  const label = parseIso(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  return iso === toETDateStr(new Date()) ? 'Today · ' + label : label;
}

function rangeLabel(start, end) {
  const fmt = iso => parseIso(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return fmt(start) + ' – ' + fmt(end);
}

function weekHeading(start, end) {
  if (weekOffset === 0) return 'This week';
  if (weekOffset === 1) return 'Next week';
  if (weekOffset === -1) return 'Last week';
  return rangeLabel(start, end);
}

function menuLine(items) {
  if (!items || !items.length) return '';
  const entrees = items.filter(i => i.is_entree).slice(0, 4);
  const sides = items.filter(i => !i.is_entree).slice(0, entrees.length ? 2 : 0);
  const shown = entrees.length ? entrees.concat(sides) : items.slice(0, 5);
  const extra = items.length - shown.length;
  const text = shown.map(i =>
    i.is_entree ? `<span class="menu-entree">${escapeHtml(i.name)}</span>` : escapeHtml(i.name)
  ).join(' · ');
  return text + (extra > 0 ? ` · +${extra}` : '');
}

function renderSchools(data) {
  const el = document.getElementById('schoolWeek');
  if (!el) return;
  if (data.school_menus_hidden) {
    el.innerHTML = '';
    return;
  }
  const schools = data.schools || [];
  if (!data.configured) {
    el.innerHTML = '<div class="meals-note">Connect SchoolCafe in Settings to see school menus.</div>';
    return;
  }
  if (!schools.length) {
    el.innerHTML = '';
    return;
  }
  const today = toETDateStr(new Date());
  el.innerHTML = `<h2 class="section-title school-week-title">School</h2>` + schools.map(s => {
    const days = (s.days || []).map(day => {
      const breakfast = menuLine(day.breakfast);
      const lunch = menuLine(day.lunch);
      if (!breakfast && !lunch) return '';
      const label = parseIso(day.date).toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' });
      return `<div class="school-day${day.date === today ? ' is-today' : ''}">
        <div class="school-day-label">${label}</div>
        <div>
          ${breakfast ? `<div class="menu-row"><span class="menu-row-label">Breakfast</span><span class="menu-row-items">${breakfast}</span></div>` : ''}
          ${lunch ? `<div class="menu-row"><span class="menu-row-label">Lunch</span><span class="menu-row-items">${lunch}</span></div>` : ''}
        </div>
      </div>`;
    }).join('');
    const body = s.error
      ? `<div class="meals-note">${escapeHtml(s.error)}</div>`
      : (days || '<div class="meals-note">No menu posted this week</div>');
    return `<div class="school-week" style="--event:${s.member_color || '#4A90D9'}">
      <div class="school-block-head">
        ${memberAvatarHtml(s, 'avatar-sm')}
        <div>
          <div class="school-block-name" style="color:${s.member_color || 'inherit'}">${escapeHtml(s.member_name)}</div>
          <div class="school-block-school">${escapeHtml(s.school_name || '')}</div>
        </div>
      </div>
      ${body}
    </div>`;
  }).join('');
}

async function loadWeek() {
  const el = document.getElementById('weekPlan');
  const start = weekAnchor();
  const person = getPersonFilter();
  let url = '/api/meals/week?start=' + start;
  if (person !== 'all') url += '&member_id=' + encodeURIComponent(person);
  const title = document.getElementById('weekTitle');
  const range = document.getElementById('weekRange');
  const nextBtn = document.getElementById('weekNext');
  if (nextBtn) nextBtn.textContent = weekOffset === 0 ? 'Next week' : '›';
  try {
    const data = await API.get(url);
    weekDays = data.days || [];
    if (title) title.textContent = weekHeading(data.start, data.end);
    if (range) range.textContent = rangeLabel(data.start || start, data.end || addDays(start, 6));
    const today = toETDateStr(new Date());
    el.innerHTML = weekDays.map(day => `
      <label class="week-day${day.date === today ? ' is-today' : ''}">
        <span>${dayLabel(day.date)}</span>
        <input class="form-input" data-date="${day.date}" value="${escapeHtml(day.title)}" placeholder="Dinner" autocomplete="off">
      </label>`).join('');
    el.querySelectorAll('input').forEach(input => {
      input.addEventListener('keydown', ev => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
      });
      input.addEventListener('blur', () => saveDay(input));
    });
    renderSchools(data);
  } catch (e) {
    weekDays = [];
    el.innerHTML = '<div class="empty-state">Could not load the week</div>';
    const school = document.getElementById('schoolWeek');
    if (school) school.innerHTML = '';
  }
}

async function saveDay(input) {
  const original = (weekDays.find(d => d.date === input.dataset.date) || {}).title || '';
  const next = input.value.trim();
  if (next === original) return;
  try {
    await API.put('/api/meals/dinner', { meal_date: input.dataset.date, title: next });
    const row = weekDays.find(d => d.date === input.dataset.date);
    if (row) row.title = next;
    if (input.dataset.date < toETDateStr(new Date())) loadHistory();
  } catch (e) {
    showToast('Could not save dinner', 'error');
  }
}

async function loadHistory() {
  const el = document.getElementById('mealHistory');
  if (!el) return;
  try {
    const rows = await API.get('/api/meals/history?before=' + toETDateStr(new Date()));
    if (!rows.length) {
      el.innerHTML = '<div class="empty-state">Past dinners show up here</div>';
      return;
    }
    el.innerHTML = rows.map(row => `
      <div class="history-item">
        <span class="history-date">${escapeHtml(dayLabel(row.meal_date).replace('Today · ', ''))}</span>
        <span class="history-title">${escapeHtml(row.title)}</span>
        <button type="button" class="btn btn-secondary" data-title="${escapeHtml(row.title)}" onclick="reuseHistory(this)">Use</button>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Could not load past meals</div>';
  }
}

async function reuseHistory(btn) {
  const title = btn.dataset.title || '';
  const date = firstOpenDate();
  if (!title) return;
  if (!date) {
    showToast('The week is full. Clear a day first.', 'info');
    return;
  }
  try {
    await API.put('/api/meals/dinner', { meal_date: date, title });
    await loadWeek();
    showToast('Added to ' + dayLabel(date), 'success');
  } catch (e) {
    showToast('Could not plan that meal', 'error');
  }
}

async function loadRequests() {
  const el = document.getElementById('requestList');
  const person = getPersonFilter();
  const url = person === 'all'
    ? '/api/meals/requests'
    : `/api/meals/requests?member_id=${encodeURIComponent(person)}`;
  try {
    const rows = await API.get(url);
    if (!rows.length) {
      el.innerHTML = `<div class="empty-state">${person === 'all' ? 'No meal requests' : 'No requests from this person'}</div>`;
      return;
    }
    el.innerHTML = rows.map(r => `
      <div class="meal-request">
        ${memberAvatarHtml(r, 'avatar-lg')}
        <div class="meal-request-body">
          <div class="meal-request-name" style="color:${r.color || 'inherit'}">${escapeHtml(r.name)}</div>
          <div class="meal-request-title">${escapeHtml(r.title)}</div>
        </div>
        <button type="button" class="btn btn-secondary" onclick="planRequest(${r.id})">Plan</button>
        <button type="button" class="shop-delete" onclick="dismissRequest(${r.id})" aria-label="Dismiss">✕</button>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Could not load requests</div>';
  }
}

async function submitRequest(ev) {
  ev.preventDefault();
  const memberId = document.getElementById('requestPerson').value;
  const titleInput = document.getElementById('requestTitle');
  const title = titleInput.value.trim();
  if (!memberId) {
    showToast('Pick who wants it', 'error');
    return;
  }
  if (!title) return;
  try {
    await API.post('/api/meals/requests', { member_id: Number(memberId), title });
    titleInput.value = '';
    await loadRequests();
  } catch (e) {
    showToast('Could not save the request', 'error');
  }
}

function firstOpenDate() {
  const input = [...document.querySelectorAll('#weekPlan input')].find(i => !i.value.trim());
  return input ? input.dataset.date : null;
}

async function planRequest(id) {
  const date = firstOpenDate();
  if (!date) {
    showToast('The week is full. Clear a day first.', 'info');
    return;
  }
  let title = '';
  try {
    const rows = await API.get('/api/meals/requests');
    const req = rows.find(r => r.id === id);
    if (!req) return;
    title = req.title;
    await API.put('/api/meals/dinner', { meal_date: date, title });
    await API.delete('/api/meals/requests/' + id);
    await loadWeek();
    await loadRequests();
    showToast('Added to ' + dayLabel(date), 'success');
  } catch (e) {
    showToast('Could not plan that meal', 'error');
  }
}

async function dismissRequest(id) {
  try {
    await API.delete('/api/meals/requests/' + id);
    await loadRequests();
  } catch (e) {
    showToast('Could not remove the request', 'error');
  }
}
