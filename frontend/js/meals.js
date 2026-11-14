(function () {
  let weekDays = [];
  let members = [];
  let weekOffset = 0;
  let _weekGen = 0;

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

  function renderSchools(data, loading) {
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
      el.innerHTML = loading && data.configured
        ? '<div class="meals-note">Loading school menus…</div>'
        : '';
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

  function _paintWeekPlan(el, data, start) {
    const title = document.getElementById('weekTitle');
    const range = document.getElementById('weekRange');
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
  }

  async function loadWeek() {
    const el = document.getElementById('weekPlan');
    if (!el) return;
    const gen = ++_weekGen;
    const start = weekAnchor();
    const person = getPersonFilter();
    let url = '/api/meals/week?start=' + start;
    if (person !== 'all') url += '&member_id=' + encodeURIComponent(person);
    const nextBtn = document.getElementById('weekNext');
    if (nextBtn) nextBtn.textContent = weekOffset === 0 ? 'Next week' : '›';

    const cachedFast = API.peek(url + '&include_school=false');
    const cachedFull = API.peek(url);
    if (cachedFast) {
      _paintWeekPlan(el, cachedFast, start);
      const wants = cachedFast.configured && !cachedFast.school_menus_hidden;
      renderSchools(cachedFull || cachedFast, wants && !cachedFull);
    }

    try {
      const data = await API.get(url + '&include_school=false');
      if (gen !== _weekGen || document.getElementById('weekPlan') !== el) return;
      _paintWeekPlan(el, data, start);
      const wantsSchool = data.configured && !data.school_menus_hidden;
      const fullHit = API.peek(url);
      renderSchools(fullHit || data, wantsSchool && !fullHit);
      if (!wantsSchool) return;
      try {
        const menus = await API.get(url);
        if (gen !== _weekGen || document.getElementById('weekPlan') !== el) return;
        renderSchools(menus, false);
      } catch (e) {
        if (gen !== _weekGen) return;
        const school = document.getElementById('schoolWeek');
        if (school) school.innerHTML = '<div class="meals-note">Menu unavailable</div>';
      }
    } catch (e) {
      if (!cachedFast && gen === _weekGen && document.getElementById('weekPlan') === el) {
        weekDays = [];
        el.innerHTML = '<div class="empty-state">Could not load the week</div>';
        const school = document.getElementById('schoolWeek');
        if (school) school.innerHTML = '';
      }
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

  function _paintHistory(el, rows) {
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
  }

  async function loadHistory() {
    const el = document.getElementById('mealHistory');
    if (!el) return;
    const url = '/api/meals/history?before=' + toETDateStr(new Date());
    const cached = API.peek(url);
    if (cached) _paintHistory(el, cached);
    try {
      const rows = await API.get(url);
      if (document.getElementById('mealHistory') === el) _paintHistory(el, rows);
    } catch (e) {
      if (!cached && document.getElementById('mealHistory') === el) {
        el.innerHTML = '<div class="empty-state">Could not load past meals</div>';
      }
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

  function _paintRequests(el, rows, person) {
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
  }

  async function loadRequests() {
    const el = document.getElementById('requestList');
    if (!el) return;
    const person = getPersonFilter();
    const url = person === 'all'
      ? '/api/meals/requests'
      : `/api/meals/requests?member_id=${encodeURIComponent(person)}`;
    const cached = API.peek(url);
    if (cached) _paintRequests(el, cached, person);
    try {
      const rows = await API.get(url);
      if (document.getElementById('requestList') === el) _paintRequests(el, rows, person);
    } catch (e) {
      if (!cached && document.getElementById('requestList') === el) {
        el.innerHTML = '<div class="empty-state">Could not load requests</div>';
      }
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
    try {
      const rows = await API.get('/api/meals/requests');
      const req = rows.find(r => r.id === id);
      if (!req) return;
      await API.put('/api/meals/dinner', { meal_date: date, title: req.title });
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

  window.reuseHistory = reuseHistory;
  window.planRequest = planRequest;
  window.dismissRequest = dismissRequest;

  function mount() {
    onPersonFilterChange(() => {
      fillPeople();
      loadWeek();
      loadRequests();
    });
    document.getElementById('requestForm').addEventListener('submit', submitRequest);
    document.getElementById('weekPrev').addEventListener('click', () => shiftWeek(-1));
    document.getElementById('weekNext').addEventListener('click', () => shiftWeek(1));

    const cachedMembers = API.peek('/api/members/');
    if (cachedMembers) {
      members = cachedMembers;
      fillPeople();
    }
    initAppTimezone().then(() => {
      if (FHRouter.currentRoute() !== '/meals.html') return;
      loadWeek();
      loadRequests();
      loadHistory();
    });
    API.get('/api/members/').then(rows => {
      members = rows || [];
      if (FHRouter.currentRoute() === '/meals.html') fillPeople();
    }).catch(() => { members = []; });

    return () => { _weekGen += 1; };
  }

  FHRouter.register('/meals.html', { mount });
})();
