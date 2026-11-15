(function () {
  let weekDays = [];
  let members = [];
  let weekOffset = 0;
  let _weekGen = 0;
  let schoolMenus = [];
  let schoolMealTypes = 'both';
  let lastSchoolData = null;
  let lastSchoolLoading = false;
  let openPlan = null;
  let mealTypeEpoch = 0;

  function shiftWeek(delta) {
    weekOffset += delta;
    closeMealPlan();
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

  function fullDateLabel(iso) {
    return parseIso(iso).toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
    });
  }

  function schoolDaysMap(school) {
    const days = school && school.days;
    const map = new Map();
    if (Array.isArray(days)) {
      days.forEach(day => {
        if (day && day.date) map.set(day.date, day);
      });
    } else if (days && typeof days === 'object') {
      // API returns { "YYYY-MM-DD": { breakfast, lunch } }
      Object.entries(days).forEach(([date, meals]) => {
        map.set(date, {
          date,
          breakfast: (meals && meals.breakfast) || [],
          lunch: (meals && meals.lunch) || [],
        });
      });
    }
    return map;
  }

  function weekDates(data, school) {
    let start = data && data.start;
    if (!start && school) {
      const first = schoolDaysMap(school).keys().next().value;
      if (first) start = mondayOf(first);
    }
    if (!start) start = weekAnchor();
    return Array.from({ length: 7 }, (_, i) => addDays(start, i));
  }

  function normalizeMealType(value) {
    const v = String(value || '').trim().toLowerCase();
    return v === 'breakfast' || v === 'lunch' ? v : 'both';
  }

  function visibleMeals(day) {
    const breakfast = schoolMealTypes === 'lunch' ? [] : ((day && day.breakfast) || []);
    const lunch = schoolMealTypes === 'breakfast' ? [] : ((day && day.lunch) || []);
    return { breakfast, lunch };
  }

  function hasMenu(day) {
    const meals = visibleMeals(day);
    return !!(meals.breakfast.length || meals.lunch.length);
  }

  function previewPick(items, limit) {
    const list = items || [];
    if (!list.length || limit <= 0) return [];
    const entrees = list.filter(i => i.is_entree);
    const rest = list.filter(i => !i.is_entree);
    return (entrees.length ? entrees.concat(rest) : list).slice(0, limit);
  }

  function cellPreview(day) {
    const meals = visibleMeals(day);
    const breakfast = meals.breakfast;
    const lunch = meals.lunch;
    const lines = [];
    const push = (items, meal, limit) => {
      previewPick(items, limit).forEach(item => {
        lines.push({ meal, name: item.name, entree: !!item.is_entree });
      });
    };
    if (breakfast.length && lunch.length) {
      push(breakfast, 'B', 1);
      push(lunch, 'L', 2);
    } else if (breakfast.length) {
      push(breakfast, 'B', 3);
    } else {
      push(lunch, 'L', 3);
    }
    return { lines, extra: breakfast.length + lunch.length - lines.length };
  }

  function groupByCategory(items) {
    const order = [];
    const map = new Map();
    (items || []).forEach(item => {
      const category = String(item.category || '').trim() || 'Other';
      if (!map.has(category)) {
        map.set(category, []);
        order.push(category);
      }
      map.get(category).push(item);
    });
    return order.map(category => ({ category, items: map.get(category) }));
  }

  function mealGroupHtml(items) {
    const groups = groupByCategory(items);
    if (!groups.length) return '<p class="meal-plan-empty">Nothing posted</p>';
    return groups.map(group => `
      <div class="meal-plan-cat">
        <div class="meal-plan-cat-name">${escapeHtml(group.category)}</div>
        <ul class="meal-plan-items">
          ${group.items.map(item => `<li${item.is_entree ? ' class="is-entree"' : ''}>${escapeHtml(item.name)}</li>`).join('')}
        </ul>
      </div>`).join('');
  }

  function dayMeta(data, school) {
    const today = toETDateStr(new Date());
    const byDate = schoolDaysMap(school);
    return weekDates(data, school).map(iso => {
      const day = byDate.get(iso) || { date: iso, breakfast: [], lunch: [] };
      const date = parseIso(iso);
      const weekend = date.getDay() === 0 || date.getDay() === 6;
      return { iso, day, date, weekend, posted: hasMenu(day), today: iso === today };
    });
  }

  function dayCellHtml(schoolIndex, studentName, meta) {
    const preview = cellPreview(meta.day);
    const previewHtml = preview.lines.length
      ? preview.lines.map(line => `
          <span class="school-day-name${line.entree ? ' is-entree' : ''}">
            <span class="school-day-tag">${line.meal}</span>${escapeHtml(line.name)}
          </span>`).join('') + (preview.extra > 0 ? `<span class="school-day-more">+${preview.extra}</span>` : '')
      : '<span class="school-day-empty">No menu</span>';
    const classes = ['school-day-cell'];
    if (meta.today) classes.push('is-today');
    if (!meta.posted) classes.push('is-empty');
    if (meta.weekend && !meta.posted) classes.push('is-dim');
    const label = escapeHtml(`View full menu for ${studentName}, ${fullDateLabel(meta.iso)}`);
    return `<button type="button" class="${classes.join(' ')}" data-school="${schoolIndex}" data-date="${meta.iso}" aria-haspopup="dialog" aria-label="${label}">
      <span class="school-day-num">${meta.date.getDate()}</span>
      <span class="school-day-preview">${previewHtml}</span>
    </button>`;
  }

  function weekGridHtml(data, school, schoolIndex) {
    const metas = dayMeta(data, school);
    const heads = metas.map(meta => {
      const classes = ['school-week-dow'];
      if (meta.today) classes.push('is-today');
      if (meta.weekend && !meta.posted) classes.push('is-dim');
      const label = meta.date.toLocaleDateString('en-US', { weekday: 'short' });
      return `<div class="${classes.join(' ')}">${label}</div>`;
    }).join('');
    const cells = metas.map(meta => dayCellHtml(schoolIndex, school.member_name, meta)).join('');
    return `<div class="school-week-grid" role="group" aria-label="${escapeHtml(school.member_name)} school menu">${heads}${cells}</div>`;
  }

  function renderSchools(data, loading) {
    const el = document.getElementById('schoolWeek');
    if (!el) return;
    lastSchoolData = data;
    lastSchoolLoading = !!loading;
    bindSchoolClicks();
    if (data.school_menus_hidden) {
      schoolMenus = [];
      closeMealPlan();
      el.innerHTML = '<div class="meals-note">School menus are hidden. <a href="/settings.html">Change this in Settings</a></div>';
      return;
    }
    const person = getPersonFilter();
    const schools = filterRowsByMember(data.schools || [], person);
    if (!data.configured) {
      schoolMenus = [];
      closeMealPlan();
      el.innerHTML = '<div class="meals-note">Connect SchoolCafe in Settings to see school menus.</div>';
      return;
    }
    if (!schools.length) {
      schoolMenus = [];
      closeMealPlan();
      el.innerHTML = loading
        ? '<div class="meals-note">Loading school menus…</div>'
        : (person === 'all'
          ? '<div class="meals-note">Assign a school in <a href="/settings.html">Settings</a>.</div>'
          : '<div class="meals-note">No school menu for this person.</div>');
      return;
    }
    schoolMenus = schools;
    el.innerHTML = schools.map((s, index) => {
      const body = s.error
        ? `<div class="meals-note">${escapeHtml(s.error)}</div>`
        : weekGridHtml(data, s, index);
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

  function onMealPlanKey(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeMealPlan();
    }
  }

  function closeMealPlan() {
    openPlan = null;
    const overlay = document.getElementById('mealPlanModal');
    if (overlay) overlay.style.display = 'none';
    document.removeEventListener('keydown', onMealPlanKey);
  }

  function openMealPlan(index, iso) {
    const overlay = document.getElementById('mealPlanModal');
    const school = schoolMenus[index];
    if (!overlay || !school || !iso) return;
    openPlan = { index, iso };
    const day = schoolDaysMap(school).get(iso) || { date: iso, breakfast: [], lunch: [] };
    const title = document.getElementById('mealPlanTitle');
    const schoolEl = document.getElementById('mealPlanSchool');
    const body = document.getElementById('mealPlanBody');
    const bar = document.getElementById('mealPlanBar');
    if (title) title.textContent = `${school.member_name} · ${fullDateLabel(iso)}`;
    if (schoolEl) {
      schoolEl.textContent = school.school_name || '';
      schoolEl.hidden = !school.school_name;
    }
    if (bar) bar.style.background = school.member_color || '#4A90D9';
    if (body) {
      const sections = [];
      if (schoolMealTypes !== 'lunch') {
        sections.push(`<section class="meal-plan-section"><h4>Breakfast</h4>${mealGroupHtml(day.breakfast)}</section>`);
      }
      if (schoolMealTypes !== 'breakfast') {
        sections.push(`<section class="meal-plan-section"><h4>Lunch</h4>${mealGroupHtml(day.lunch)}</section>`);
      }
      body.innerHTML = sections.join('');
    }
    const wasOpen = overlay.style.display === 'flex';
    overlay.style.display = 'flex';
    if (!wasOpen) document.addEventListener('keydown', onMealPlanKey);
    const closeBtn = document.getElementById('mealPlanClose');
    if (closeBtn && !wasOpen) closeBtn.focus();
  }

  function bindSchoolClicks() {
    const el = document.getElementById('schoolWeek');
    if (!el || el.dataset.bound === '1') return;
    el.dataset.bound = '1';
    el.addEventListener('click', (e) => {
      const cell = e.target.closest('.school-day-cell');
      if (!cell || !el.contains(cell)) return;
      const index = Number(cell.dataset.school);
      if (!Number.isInteger(index)) return;
      openMealPlan(index, cell.dataset.date || '');
    });
  }

  function bindMealPlanModal() {
    const overlay = document.getElementById('mealPlanModal');
    if (!overlay || overlay.dataset.bound === '1') return;
    overlay.dataset.bound = '1';
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeMealPlan();
    });
    const panel = overlay.querySelector('.meal-plan-modal');
    if (panel) panel.addEventListener('click', (e) => e.stopPropagation());
    const closeBtn = overlay.querySelector('#mealPlanClose');
    if (closeBtn) closeBtn.addEventListener('click', closeMealPlan);
  }

  function paintMealTypeToggle() {
    if (FHRouter.currentRoute() !== '/meals.html') return;
    const root = document.getElementById('schoolMealType');
    if (!root) return;
    root.querySelectorAll('[data-meal]').forEach(btn => {
      btn.setAttribute('aria-pressed', btn.dataset.meal === schoolMealTypes ? 'true' : 'false');
    });
  }

  function applyMealType(value) {
    const next = normalizeMealType(value);
    const changed = next !== schoolMealTypes;
    schoolMealTypes = next;
    window.__fhSchoolMealType = next;
    paintMealTypeToggle();
    return changed;
  }

  function rerenderSchoolView() {
    const plan = openPlan;
    if (lastSchoolData) renderSchools(lastSchoolData, lastSchoolLoading);
    if (!plan) return;
    const overlay = document.getElementById('mealPlanModal');
    if (!overlay || overlay.style.display === 'none') return;
    openMealPlan(plan.index, plan.iso);
  }

  async function setMealType(value) {
    const next = normalizeMealType(value);
    if (next === schoolMealTypes) return;
    const prev = schoolMealTypes;
    mealTypeEpoch += 1;
    const epoch = mealTypeEpoch;
    applyMealType(next);
    rerenderSchoolView();
    try {
      await API.post('/api/settings/', { school_meal_types: next });
    } catch (e) {
      if (epoch !== mealTypeEpoch || FHRouter.currentRoute() !== '/meals.html') return;
      applyMealType(prev);
      rerenderSchoolView();
      showToast('Could not save meal type', 'error');
    }
  }

  function loadMealType() {
    const epoch = mealTypeEpoch;
    if (typeof window.__fhSchoolMealType === 'string' && epoch === mealTypeEpoch) {
      applyMealType(window.__fhSchoolMealType);
    }
    const cached = API.peek('/api/settings/');
    if (cached && epoch === mealTypeEpoch) {
      if (applyMealType(cached.school_meal_types)) rerenderSchoolView();
    }
    API.get('/api/settings/').then(s => {
      if (epoch !== mealTypeEpoch || FHRouter.currentRoute() !== '/meals.html') return;
      if (applyMealType(s && s.school_meal_types)) rerenderSchoolView();
    }).catch(() => {});
  }

  function bindMealTypeToggle() {
    const root = document.getElementById('schoolMealType');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    root.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-meal]');
      if (!btn || !root.contains(btn)) return;
      setMealType(btn.dataset.meal);
    });
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
    // One request for dinner + school. Backend school reads are local snapshots only.
    const url = '/api/meals/week?start=' + start;
    const nextBtn = document.getElementById('weekNext');
    if (nextBtn) nextBtn.textContent = weekOffset === 0 ? 'Next week' : '›';

    const cached = API.peek(url);
    if (cached) {
      _paintWeekPlan(el, cached, start);
      renderSchools(cached, Boolean(cached.configured && !cached.school_menus_hidden && cached.warming));
    }

    try {
      const menus = await API.get(url);
      if (gen !== _weekGen || document.getElementById('weekPlan') !== el) return;
      _paintWeekPlan(el, menus, start);
      const wantsSchool = menus.configured && !menus.school_menus_hidden;
      renderSchools(menus, Boolean(wantsSchool && menus.warming));
      if (wantsSchool && menus.warming && gen <= 3) {
        setTimeout(() => {
          if (gen === _weekGen && FHRouter.currentRoute() === '/meals.html') loadWeek();
        }, 4000 + gen * 2000);
      }
    } catch (e) {
      if (typeof isAbortError === 'function' && isAbortError(e)) return;
      if (!cached && gen === _weekGen && document.getElementById('weekPlan') === el) {
        weekDays = [];
        schoolMenus = [];
        closeMealPlan();
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
    const url = '/api/meals/requests';
    const paint = (rows) => _paintRequests(el, filterRowsByMember(rows || [], person), person);
    const cached = API.peek(url);
    if (cached) paint(cached);
    try {
      const rows = await API.get(url);
      if (document.getElementById('requestList') === el) paint(rows);
    } catch (e) {
      if (typeof isAbortError === 'function' && isAbortError(e)) return;
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
    lastSchoolData = null;
    lastSchoolLoading = false;
    openPlan = null;
    bindSchoolClicks();
    bindMealPlanModal();
    bindMealTypeToggle();
    loadMealType();
    onPersonFilterChange(() => {
      closeMealPlan();
      fillPeople();
      // Instant client-side filter from cached Everyone data — no network.
      if (lastSchoolData) renderSchools(lastSchoolData, lastSchoolLoading);
      else loadWeek();
      const reqCached = API.peek('/api/meals/requests');
      const reqEl = document.getElementById('requestList');
      if (reqEl && reqCached) {
        const person = getPersonFilter();
        _paintRequests(reqEl, filterRowsByMember(reqCached, person), person);
      } else {
        loadRequests();
      }
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

    return () => {
      _weekGen += 1;
      closeMealPlan();
    };
  }

  FHRouter.register('/meals.html', { mount });
})();
