(function () {
  let _mealsGen = 0;
  let _schoolPaint = null;
  let schoolMealTypes = 'both';
  let _stocksTimer = null;
  let _refreshTimer = null;
  let _cachedStocks = null;

  function setDashboardDate() {
    const el = document.getElementById('todayDate');
    if (!el) return;
    el.textContent = new Date().toLocaleDateString('en-US', {
      timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric'
    });
  }

  function visibleMessages(msgs) {
    const person = getPersonFilter();
    if (person === 'all') return msgs;
    return msgs.filter(m => !m.recipient_id || String(m.recipient_id) === String(person));
  }

  function _renderMessagePreviewList(msgsRaw) {
    const content = document.getElementById('msgPreviewContent');
    const ack = document.getElementById('msgPreviewAck');
    if (!content) return;
    const msgs = visibleMessages(msgsRaw);
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
  }

  async function loadMessagePreview() {
    if (!document.getElementById('msgPreviewContent')) return;
    const cached = API.peek('/api/messages/');
    if (cached) _renderMessagePreviewList(cached);
    try {
      const msgs = await API.get('/api/messages/');
      _renderMessagePreviewList(msgs);
    } catch (e) {}
  }

  async function ackPreviewMessage(e) {
    e.preventDefault();
    const ackEl = document.getElementById('msgPreviewAck');
    const id = ackEl && ackEl.dataset ? ackEl.dataset.id : null;
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
    const author = document.getElementById('composeAuthor');
    const recipient = document.getElementById('composeRecipient');
    if (!author || !recipient) return;
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
    } catch (_) {}
    document.getElementById('composeBody').value = '';
    document.getElementById('composeModal').style.display = 'flex';
    setTimeout(() => {
      const b = document.getElementById('composeBody');
      if (b) b.focus();
    }, 100);
  }

  function closeComposeModal() {
    const el = document.getElementById('composeModal');
    if (el) el.style.display = 'none';
  }

  async function postQuickMessage() {
    const bodyEl = document.getElementById('composeBody');
    if (!bodyEl) return;
    const body = bodyEl.value.trim();
    if (!body) return;
    const author_id = document.getElementById('composeAuthor').value || null;
    const recipient_id = document.getElementById('composeRecipient').value || null;
    try {
      await API.post('/api/messages/', {
        body,
        author_id: author_id ? parseInt(author_id, 10) : null,
        recipient_id: recipient_id ? parseInt(recipient_id, 10) : null,
      });
      closeComposeModal();
      showToast('Message posted!', 'success');
      loadMessagePreview();
      loadMessageBadge();
    } catch (e) {
      showToast('Failed to post message', 'error');
    }
  }

  function eventStillGoing(e, now) {
    const endRaw = e.end_datetime || e.start_datetime;
    if (!endRaw) return false;
    if (e.all_day || endRaw.indexOf('T') === -1) {
      const [y, m, d] = endRaw.slice(0, 10).split('-').map(Number);
      return new Date(y, m - 1, d).getTime() > now.getTime();
    }
    const end = new Date(endRaw);
    return !Number.isNaN(end.getTime()) && end > now;
  }

  function eventWhoHtml(e) {
    if (e.member_name) {
      return `${memberAvatarHtml({avatar:e.member_avatar, avatar_photo:e.member_avatar_photo, color: e.member_color}, 'avatar-sm')}<span>${escapeHtml(e.member_name)}</span>`;
    }
    if (e.is_family) {
      return `${memberAvatarHtml({avatar:'🏠', color: eventDisplayColor(e)}, 'avatar-sm')}<span>Family</span>`;
    }
    return '';
  }

  function _renderEventsList(el, allFetched, now) {
    const events = allFetched.filter(e => eventStillGoing(e, now));
    if (!events.length) {
      el.innerHTML = '<div class="empty-state">🎉 Nothing coming up!</div>';
      return;
    }
    const todayStr = toETDateStr(now);
    const todayDate = new Date(todayStr + 'T12:00:00');
    el.innerHTML = events.map(e => {
      const color = eventDisplayColor(e);
      const eventDate = e.start_datetime ? new Date(e.start_datetime) : null;
      const eventDateStr = e.start_datetime ? e.start_datetime.slice(0, 10) : null;
      const isTodayEv = eventDateStr === todayStr;
      let dateLabel = '';
      if (eventDateStr) {
        if (isTodayEv) {
          dateLabel = 'Today';
        } else if (e.all_day) {
          const [yr, mo, dy] = eventDateStr.split('-').map(Number);
          dateLabel = new Date(yr, mo - 1, dy).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
        } else {
          dateLabel = eventDate.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: TZ });
        }
      }
      if (eventDateStr && !isTodayEv) {
        const evDate = new Date(eventDateStr + 'T12:00:00');
        const days = Math.round((evDate - todayDate) / 86400000);
        if (days === 1) dateLabel = 'Tomorrow';
        else if (days > 1 && days <= 14) dateLabel += ` · in ${days} days`;
      }
      const who = eventWhoHtml(e);

      return `
        <div class="event-card" style="--event:${color}">
          <div class="event-card-head">
            <div class="event-card-title">${escapeHtml(e.title)}</div>
            ${who ? `<div class="event-card-who">${who}</div>` : ''}
          </div>
          <div class="event-card-time">${e.all_day ? `${dateLabel} · All day` : `${dateLabel}${dateLabel ? ' · ' : ''}${formatTime(e.start_datetime)}`}</div>
        </div>`;
    }).join('');
  }

  let _lastEventsKey = '';
  let _lastEventsData = null;
  let dashCalView = localStorage.getItem('fh_dash_cal_view') === 'agenda' ? 'agenda' : 'upnext';
  let dashAgendaDate = toETDateStr(new Date());

  function dashboardWeekEventsUrl() {
    const now = new Date();
    const minuteNow = new Date(Math.floor(now.getTime() / 60000) * 60000);
    const start = minuteNow.toISOString();
    const endWeek = new Date(minuteNow.getTime() + 7 * 24 * 60 * 60 * 1000);
    const person = getPersonFilter();
    // Always fetch Everyone; person chips filter client-side (instant, no hang).
    const eventsUrl = `/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(endWeek.toISOString())}`;
    return { eventsUrl, now, endWeek, person };
  }

  function dashboardDayEventsUrl(dateStr) {
    return `/api/calendar/events?start=${encodeURIComponent(dateStr + 'T00:00:00')}&end=${encodeURIComponent(dateStr + 'T23:59:59')}`;
  }

  function dateInWeekWindow(dateStr, now, endWeek) {
    return dateStr >= toETDateStr(now) && dateStr <= toETDateStr(endWeek);
  }

  function applyDashCalView() {
    const title = document.getElementById('dashEventsTitle');
    const nav = document.getElementById('dashAgendaNav');
    const dateEl = document.getElementById('dashAgendaDate');
    if (title) title.textContent = dashCalView === 'agenda' ? '📅 Agenda' : '📅 Up Next';
    if (nav) nav.hidden = dashCalView !== 'agenda';
    if (dateEl) dateEl.textContent = agendaDateLabel(dashAgendaDate, { weekday: 'short', month: 'short', day: 'numeric' });
    document.querySelectorAll('#dashCalViewTabs [data-view]').forEach(btn => {
      btn.classList.toggle('active', btn.getAttribute('data-view') === dashCalView);
    });
  }

  function setDashCalView(view) {
    dashCalView = view === 'agenda' ? 'agenda' : 'upnext';
    try { localStorage.setItem('fh_dash_cal_view', dashCalView); } catch (_) {}
    applyDashCalView();
    loadTodayEvents();
  }

  function _renderDashAgenda(el, events) {
    applyDashCalView();
    renderAgenda(el, events, dashAgendaDate, {});
  }

  async function loadTodayEvents(opts) {
    const el = document.getElementById('todayEvents');
    if (!el) return;
    applyDashCalView();
    const { eventsUrl, now, endWeek } = dashboardWeekEventsUrl();
    const useWeek = dashCalView === 'upnext' || dateInWeekWindow(dashAgendaDate, now, endWeek);
    const network = !(opts && opts.cacheOnly);

    const paint = (rows, when) => {
      const visible = filterEventsForPerson(rows, getPersonFilter());
      if (dashCalView === 'agenda') _renderDashAgenda(el, visible);
      else _renderEventsList(el, visible, when);
    };

    if (useWeek) {
      const cached = API.peek(eventsUrl) || _lastEventsData;
      if (cached) paint(cached, now);
      if (!network) return;
      try {
        const allFetched = await API.get(eventsUrl);
        _lastEventsKey = 'all';
        _lastEventsData = allFetched;
        if (document.getElementById('todayEvents') === el) paint(allFetched, new Date());
      } catch (e) {
        if (!cached && document.getElementById('todayEvents') === el) {
          el.innerHTML = '<div class="empty-state">Failed to load events</div>';
        }
      }
      return;
    }

    const dayUrl = dashboardDayEventsUrl(dashAgendaDate);
    const cachedDay = API.peek(dayUrl);
    if (cachedDay) paint(cachedDay, now);
    if (!network) return;
    try {
      const dayFetched = await API.get(dayUrl);
      if (document.getElementById('todayEvents') === el && dashCalView === 'agenda') {
        paint(dayFetched, now);
      }
    } catch (e) {
      if (!cachedDay && document.getElementById('todayEvents') === el) {
        el.innerHTML = '<div class="empty-state">Failed to load events</div>';
      }
    }
  }

  const TOD_ORDER = ['morning', 'afternoon', 'evening'];
  const TOD_LABEL = { morning: '🌅 Morning', afternoon: '☀️ Afternoon', evening: '🌙 Evening' };

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

  async function _paintUpcomingChores(el, chores, members) {
    const person = getPersonFilter();
    const visibleMembers = person === 'all' ? members : members.filter(m => String(m.id) === String(person));
    const visibleChores = person === 'all' ? chores : chores.filter(c => String(c.assigned_to) === String(person));

    if (!visibleChores.length) {
      if (person !== 'all') {
        el.innerHTML = '<div class="empty-state">🙌 All done today!</div>';
        return;
      }
      const paintLb = (lb) => {
        if (document.getElementById('upcomingChores') !== el) return;
        if (lb && lb.length) {
          const medals = ['🥇', '🥈', '🥉'];
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
      };
      const cachedLb = API.peek('/api/chores/leaderboard');
      if (cachedLb) {
        paintLb(cachedLb);
        return;
      }
      try {
        paintLb(await API.get('/api/chores/leaderboard'));
      } catch {
        if (document.getElementById('upcomingChores') === el) {
          el.innerHTML = '<div class="empty-state">🙌 All chores done!</div>';
        }
      }
      return;
    }

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
  }

  async function loadUpcomingChores(opts) {
    const el = document.getElementById('upcomingChores');
    if (!el) return;
    const choresUrl = '/api/chores/?hide_future=true&completed=false&since_days=0';
    const cachedChores = API.peek(choresUrl);
    const cachedMembers = API.peek('/api/members/');
    if (cachedChores && cachedMembers) {
      _paintUpcomingChores(el, cachedChores, cachedMembers);
    }
    if (opts && opts.cacheOnly) return;
    try {
      const [chores, members] = await Promise.all([
        API.get(choresUrl),
        API.get('/api/members/')
      ]);
      if (document.getElementById('upcomingChores') === el) {
        await _paintUpcomingChores(el, chores, members);
      }
    } catch (e) {
      if (!cachedChores && document.getElementById('upcomingChores') === el) {
        el.innerHTML = '<div class="empty-state">Failed to load chores</div>';
      }
    }
  }

  async function toggleDashboardChore(id, currentlyCompleted) {
    try {
      await API.put(`/api/chores/${id}`, { completed: !currentlyCompleted });
      if (!currentlyCompleted) showToast('✅ Nice work! Points earned!', 'success');
      loadUpcomingChores();
    } catch (e) {
      showToast('Failed to update chore', 'error');
    }
  }

  function _paintStocks(el, stocks) {
    if (!stocks || !stocks.length) { el.style.display = 'none'; return; }
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
  }

  async function loadStocks() {
    const el = document.getElementById('stockTicker');
    if (!el) return;
    const cached = API.peek('/api/stocks/') || _cachedStocks;
    if (cached) _paintStocks(el, cached);
    try {
      const stocks = await API.get('/api/stocks/');
      _cachedStocks = stocks;
      if (document.getElementById('stockTicker') === el) _paintStocks(el, stocks);
    } catch (e) {
      if (!cached && document.getElementById('stockTicker') === el) el.style.display = 'none';
    }
  }

  function appHour() {
    const raw = new Date().toLocaleString('en-US', { timeZone: TZ, hour: '2-digit', hourCycle: 'h23' });
    const hour = parseInt(raw, 10);
    return hour === 24 ? 0 : hour;
  }

  function addDays(dateStr, n) {
    const [y, m, d] = dateStr.split('-').map(Number);
    const dt = new Date(y, m - 1, d);
    dt.setDate(dt.getDate() + n);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
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

  function normalizeMealType(value) {
    const v = String(value || '').trim().toLowerCase();
    return v === 'breakfast' || v === 'lunch' ? v : 'both';
  }

  function mealsSchoolHtml(data, person, dinnerDate, loading) {
    _schoolPaint = { gen: _mealsGen, data, person, dinnerDate, loading };
    if (data.school_menus_hidden) {
      return '<div class="meals-note">School menus are hidden. <a href="/settings.html">Change this in Settings</a></div>';
    }
    if (!data.configured) {
      return '<div class="meals-note">Connect SchoolCafe in Settings to see school menus.</div>';
    }
    if (loading) return '<div class="meals-note">Loading school menus…</div>';
    const schools = filterRowsByMember(data.schools || [], person);
    if (!schools.length) {
      return person === 'all'
        ? '<div class="meals-note">Assign a school to each student in Settings.</div>'
        : '<div class="meals-note">No school menu for this person.</div>';
    }
    const servedDate = data.school_date || schoolMenuDate(dinnerDate);
    const schoolWhen = schoolWhenLabel(dinnerDate, servedDate);
    const mealType = schoolMealTypes;
    return `<div class="meals-school-label">School menu · ${schoolWhen}</div>` + schools.map(s => {
      const breakfast = mealType === 'lunch' ? '' : menuItemsText(s.breakfast);
      const lunch = mealType === 'breakfast' ? '' : menuItemsText(s.lunch);
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

  function paintDinner(el, data, requests, dinnerDate, schoolHtml) {
    const dinnerTitle = (data.dinner && data.dinner.title) || '';
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
      <div id="mealsSchool">${schoolHtml}</div>`;
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
  }

  async function loadMeals(opts) {
    const el = document.getElementById('mealsBody');
    if (!el) return;
    const gen = ++_mealsGen;
    const dinnerDate = toETDateStr(new Date());
    if (typeof window.__fhSchoolMealType === 'string') {
      schoolMealTypes = normalizeMealType(window.__fhSchoolMealType);
    }
    const cachedSettings = API.peek('/api/settings/');
    if (cachedSettings) schoolMealTypes = normalizeMealType(cachedSettings.school_meal_types);
    window.__fhSchoolMealType = schoolMealTypes;
    const network = !(opts && opts.cacheOnly);
    if (network) {
      API.get('/api/settings/').then(s => {
        if (gen !== _mealsGen) return;
        const next = normalizeMealType(s && s.school_meal_types);
        if (next === schoolMealTypes) return;
        schoolMealTypes = next;
        window.__fhSchoolMealType = next;
        const slot = document.getElementById('mealsSchool');
        if (!slot || !_schoolPaint || _schoolPaint.gen !== gen) return;
        if (document.getElementById('mealsBody') !== el) return;
        const p = _schoolPaint;
        slot.innerHTML = mealsSchoolHtml(p.data, p.person, p.dinnerDate, p.loading);
      }).catch(() => {});
    }
    const schoolDate = schoolMenuDate(dinnerDate);
    // Always Everyone; person chips filter school rows / requests client-side.
    const url = `/api/meals/day?date=${dinnerDate}&school_date=${schoolDate}`;
    const reqUrl = '/api/meals/requests';
    const paintReqs = (reqs) => filterRowsByMember(reqs || [], getPersonFilter());
    const paintSchool = (data, loading) => mealsSchoolHtml(data, getPersonFilter(), dinnerDate, loading);

    const cachedFast = API.peek(url + '&include_school=false');
    const cachedFull = API.peek(url);
    const cachedReqs = API.peek(reqUrl);
    if (cachedFull && cachedReqs) {
      paintDinner(el, cachedFull, paintReqs(cachedReqs), dinnerDate, paintSchool(cachedFull, false));
    } else if (cachedFast && cachedReqs) {
      const wants = cachedFast.configured && !cachedFast.school_menus_hidden;
      paintDinner(el, cachedFast, paintReqs(cachedReqs), dinnerDate, paintSchool(cachedFast, wants));
    }
    if (!network) return;

    try {
      const [data, requests] = await Promise.all([
        API.get(url + '&include_school=false', { timeout: 45000 }),
        API.get(reqUrl).catch(() => []),
      ]);
      if (gen !== _mealsGen || document.getElementById('mealsBody') !== el) return;
      const wantsSchool = data.configured && !data.school_menus_hidden;
      const fullCached = API.peek(url);
      if (fullCached && wantsSchool) {
        paintDinner(el, data, paintReqs(requests), dinnerDate, paintSchool(fullCached, false));
      } else {
        paintDinner(el, data, paintReqs(requests), dinnerDate, paintSchool(data, wantsSchool));
      }
      if (!wantsSchool) return;
      try {
        const menus = await API.get(url, { timeout: 45000 });
        if (gen !== _mealsGen || document.getElementById('mealsBody') !== el) return;
        const slot = document.getElementById('mealsSchool');
        if (slot) slot.innerHTML = paintSchool(menus, false);
      } catch (e) {
        const slot = document.getElementById('mealsSchool');
        if (slot && gen === _mealsGen) {
          _schoolPaint = null;
          slot.innerHTML = '<div class="meals-note">Menu unavailable</div>';
        }
      }
    } catch (e) {
      if (!cachedFast && gen === _mealsGen && document.getElementById('mealsBody') === el) {
        el.innerHTML = '<div class="empty-state">Meals unavailable</div>';
      }
    }
  }

  function _paintShoppingStrip(el, items) {
    const open = items.filter(i => !i.checked);
    const shown = open.slice(0, 6);
    el.style.display = 'flex';
    el.innerHTML = `
      <a href="/shopping.html" class="shopping-strip-link">🛒 Shopping</a>
      ${shown.map(i => `<button type="button" class="shopping-chip" onclick="checkShoppingItem(${i.id})">${escapeHtml(i.name)}${i.quantity ? ' · ' + escapeHtml(i.quantity) : ''}</button>`).join('')}
      ${open.length > 6 ? `<a href="/shopping.html" class="shopping-strip-more">+${open.length - 6}</a>` : ''}
      ${!open.length ? '<span class="shopping-strip-empty">Nothing to buy</span>' : ''}`;
  }

  async function loadShoppingStrip() {
    const el = document.getElementById('shoppingStrip');
    if (!el) return;
    const cached = API.peek('/api/shopping/');
    if (cached) _paintShoppingStrip(el, cached);
    try {
      const items = await API.get('/api/shopping/');
      if (document.getElementById('shoppingStrip') === el) _paintShoppingStrip(el, items);
    } catch (e) {
      if (!cached && document.getElementById('shoppingStrip') === el) el.style.display = 'none';
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

  window.ackPreviewMessage = ackPreviewMessage;
  window.openComposeModal = openComposeModal;
  window.closeComposeModal = closeComposeModal;
  window.postQuickMessage = postQuickMessage;
  window.toggleDashboardChore = toggleDashboardChore;
  window.checkShoppingItem = checkShoppingItem;

  function mount() {
    onPersonFilterChange(() => {
      // Instant client-side filter from cached Everyone data — no network.
      loadTodayEvents({ cacheOnly: true });
      loadUpcomingChores({ cacheOnly: true });
      loadMeals({ cacheOnly: true });
      const cachedMsgs = API.peek('/api/messages/');
      if (cachedMsgs) _renderMessagePreviewList(cachedMsgs);
      else loadMessagePreview();
    });
    setDashboardDate();

    const params = new URLSearchParams(window.location.search);
    if (params.get('connected') === 'family') {
      showToast('✅ Family Google Calendar connected!', 'success');
      history.replaceState({}, '', '/');
    }

    document.querySelectorAll('#dashCalViewTabs [data-view]').forEach(btn => {
      btn.onclick = () => setDashCalView(btn.getAttribute('data-view'));
    });
    const prev = document.getElementById('dashAgendaPrev');
    const next = document.getElementById('dashAgendaNext');
    const todayBtn = document.getElementById('dashAgendaToday');
    if (prev) prev.onclick = () => { dashAgendaDate = shiftDateStr(dashAgendaDate, -1); loadTodayEvents(); };
    if (next) next.onclick = () => { dashAgendaDate = shiftDateStr(dashAgendaDate, 1); loadTodayEvents(); };
    if (todayBtn) todayBtn.onclick = () => { dashAgendaDate = toETDateStr(new Date()); loadTodayEvents(); };
    applyDashCalView();

    initAppTimezone().then(() => {
      if (FHRouter.currentRoute() !== '/') return;
      setDashboardDate();
      loadTodayEvents();
      loadUpcomingChores();
      loadMeals();
      loadShoppingStrip();
      loadMessagePreview();
    });
    afterPagePaint(loadStocks);

    _stocksTimer = setInterval(loadStocks, 5 * 60 * 1000);
    _refreshTimer = setInterval(() => {
      if (document.hidden) return;
      setDashboardDate();
      loadTodayEvents();
      loadUpcomingChores();
      loadMeals();
      loadShoppingStrip();
      loadWeather();
      loadMessagePreview();
      loadMessageBadge();
    }, 5 * 60 * 1000);

    return unmount;
  }

  function unmount() {
    clearInterval(_stocksTimer);
    clearInterval(_refreshTimer);
    _stocksTimer = null;
    _refreshTimer = null;
    _mealsGen += 1;
  }

  FHRouter.register('/', { mount, unmount });
})();
