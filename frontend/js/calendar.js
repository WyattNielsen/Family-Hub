(function () {
  let currentDate = new Date();
  let allEvents = [];
  let members = [];
  let selectedDay = null;
  window.selectedDay = null;
  let currentView = localStorage.getItem('fh_cal_view') === 'agenda' ? 'agenda' : 'month';

  function dateStrFromCurrent() {
    return `${currentDate.getFullYear()}-${String(currentDate.getMonth() + 1).padStart(2, '0')}-${String(currentDate.getDate()).padStart(2, '0')}`;
  }

  function applyCalView() {
    const wrapper = document.getElementById('calendarWrapper');
    const agendaEl = document.getElementById('agendaView');
    const panel = document.getElementById('dayEventsPanel');
    if (wrapper) wrapper.hidden = currentView !== 'month';
    if (agendaEl) agendaEl.hidden = currentView !== 'agenda';
    if (panel && currentView === 'agenda') panel.style.display = 'none';
    document.querySelectorAll('#calViewTabs [data-view]').forEach(btn => {
      btn.classList.toggle('active', btn.getAttribute('data-view') === currentView);
    });
  }

  function setCalView(view) {
    currentView = view === 'agenda' ? 'agenda' : 'month';
    try { localStorage.setItem('fh_cal_view', currentView); } catch (_) {}
    applyCalView();
    renderActiveView();
    loadEvents();
  }

  function visibleEvents() {
    return filterEventsForPerson(allEvents, getPersonFilter());
  }

  function renderAgendaView() {
    const el = document.getElementById('agendaView');
    const titleEl = document.getElementById('calendarTitle');
    if (!el) return;
    const dateStr = dateStrFromCurrent();
    if (titleEl) titleEl.textContent = agendaDateLabel(dateStr);
    renderAgenda(el, visibleEvents(), dateStr, {
      onEventClick: (ev, domEvent) => showEventDetail(ev.id, domEvent),
      onAdd: (d) => openEventModal(d),
    });
  }

  function renderActiveView() {
    applyCalView();
    if (currentView === 'agenda') renderAgendaView();
    else renderCalendar();
  }

  function eventBodyHtml(raw) {
    let text = String(raw ?? '').replace(/\r\n|\r/g, '\n');
    text = text.replace(/<a\b[^>]*\bhref\s*=\s*(?:"(https?:\/\/[^"]+)"|'(https?:\/\/[^']+)')[^>]*>([\s\S]*?)<\/a>/gi,
      (_, href1, href2, label) => {
        const href = href1 || href2;
        const visible = label.replace(/<[^>]+>/g, '').trim();
        return visible && visible !== href ? `${visible} (${href})` : href;
      });
    text = text.replace(/<\s*br\s*\/?\s*>/gi, '\n');
    text = text.replace(/<\s*\/\s*(?:p|div|li|tr|h[1-6])\s*>/gi, '\n');
    text = text.replace(/<[^>]+>/g, '');
    text = text
      .replace(/&nbsp;/gi, ' ')
      .replace(/&amp;/gi, '&')
      .replace(/&lt;/gi, '<')
      .replace(/&gt;/gi, '>')
      .replace(/&quot;/gi, '"')
      .replace(/&#39;|&apos;/gi, "'");

    const re = /https?:\/\/[^\s]+/g;
    let out = '';
    let last = 0;
    for (const m of text.matchAll(re)) {
      out += escapeHtml(text.slice(last, m.index)).replace(/\n/g, '<br>');
      let url = m[0];
      let trail = '';
      while (url && /[),.;:!?\]"']$/.test(url)) {
        trail = url.slice(-1) + trail;
        url = url.slice(0, -1);
      }
      if (/^https?:\/\//i.test(url)) {
        const safe = escapeHtml(url);
        out += `<a href="${safe}" target="_blank" rel="noopener">${safe}</a>`;
      } else {
        out += escapeHtml(url);
      }
      out += escapeHtml(trail);
      last = m.index + m[0].length;
    }
    out += escapeHtml(text.slice(last)).replace(/\n/g, '<br>');
    return out;
  }

  function eventPersonTag(e, color) {
    let html = '';
    if (e.member_name) {
      html += `<div class="event-card-member" style="background:${color}22;color:${color}">${memberAvatarHtml({avatar:e.member_avatar, avatar_photo:e.member_avatar_photo, color}, 'avatar-sm')}<span>${escapeHtml(e.member_name)}</span></div>`;
    }
    if (e.is_family) {
      const familyColor = eventDisplayColor(e);
      html += `<div class="event-card-member" style="background:${familyColor}22;color:${familyColor}">🏠 Family</div>`;
    }
    return html;
  }

  function populateMemberSelect() {
    const sel = document.getElementById('eventMember');
    if (!sel) return;
    sel.innerHTML = '<option value="">Select who…</option>' +
      '<option value="family">🏠 Family</option>' +
      members.map(m => `<option value="${m.id}">${m.avatar || '👤'} ${escapeHtml(m.name)}</option>`).join('');
  }

  const CAL_VISIBLE_LANES = 3;

  function _eventAllDay(e) {
    return !!(e.all_day || (e.start_datetime && String(e.start_datetime).indexOf('T') === -1));
  }

  function _eventTimeKey(e) {
    if (_eventAllDay(e)) return '';
    const raw = String(e.start_datetime || '');
    const time = raw.indexOf('T') === -1 ? '' : raw.split('T')[1];
    return time.slice(0, 5);
  }

  function _seriesKey(e) {
    return [
      _eventAllDay(e) ? '1' : '0',
      _eventTimeKey(e),
      e.title || '',
      e.member_id == null ? '' : String(e.member_id),
      e.is_family ? '1' : '0',
    ].join('\u0000');
  }

  function _cmpSeries(a, b) {
    if (a.allDay !== b.allDay) return a.allDay ? -1 : 1;
    if (b.columns.length !== a.columns.length) return b.columns.length - a.columns.length;
    if (a.timeKey !== b.timeKey) return a.timeKey < b.timeKey ? -1 : 1;
    const at = (a.title || '').toLowerCase();
    const bt = (b.title || '').toLowerCase();
    if (at !== bt) return at < bt ? -1 : 1;
    return a.key < b.key ? -1 : 1;
  }

  // One lane per repeating or multi-day event so the same item stays on the
  // same row across the week. Single-day events fill the gaps that are left.
  function _layoutWeekLanes(dateStrs, events) {
    const byKey = new Map();
    dateStrs.forEach((dateStr, col) => {
      if (!dateStr) return;
      events.forEach(e => {
        if (!eventOccursOnDate(e, dateStr)) return;
        const key = _seriesKey(e);
        let series = byKey.get(key);
        if (!series) {
          series = {
            key,
            title: e.title,
            allDay: _eventAllDay(e),
            timeKey: _eventTimeKey(e),
            columns: [],
            eventsByCol: {},
            extraByCol: {},
          };
          byKey.set(key, series);
        }
        if (!series.eventsByCol[col]) {
          series.columns.push(col);
          series.eventsByCol[col] = e;
        } else {
          series.extraByCol[col] = (series.extraByCol[col] || 0) + 1;
        }
      });
    });

    const multi = [];
    const single = [];
    byKey.forEach(series => (series.columns.length >= 2 ? multi : single).push(series));
    multi.sort(_cmpSeries);
    single.sort(_cmpSeries);

    const laneCols = [];
    function claim(series) {
      let lane = 0;
      while (lane < laneCols.length && series.columns.some(c => laneCols[lane].has(c))) lane += 1;
      if (!laneCols[lane]) laneCols[lane] = new Set();
      series.columns.forEach(c => laneCols[lane].add(c));
      series.lane = lane;
    }
    multi.forEach(claim);
    single.forEach(claim);

    return dateStrs.map((dateStr, col) => {
      if (!dateStr) return { visible: [], hidden: 0 };
      const slots = [];
      let hidden = 0;
      byKey.forEach(series => {
        const ev = series.eventsByCol[col];
        if (!ev) return;
        const extra = series.extraByCol[col] || 0;
        if (series.lane < CAL_VISIBLE_LANES) {
          slots[series.lane] = ev;
          hidden += extra;
        } else {
          hidden += 1 + extra;
        }
      });
      let last = slots.length - 1;
      while (last >= 0 && !slots[last]) last -= 1;
      const visible = [];
      for (let i = 0; i <= last; i++) visible.push(slots[i] || null);
      return { visible, hidden };
    });
  }

  function _eventChipHtml(e) {
    const color = eventDisplayColor(e);
    return `<div class="cal-event-dot" style="--event:${color}" onclick="showEventDetail(${e.id},event)">${escapeHtml(e.title)}</div>`;
  }

  function renderCalendar() {
    const titleEl = document.getElementById('calendarTitle');
    const grid = document.getElementById('calendarGrid');
    if (!titleEl || !grid) return;

    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    titleEl.textContent =
      new Date(year, month).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: TZ });

    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    let html = days.map(d => `<div class="cal-day-header"><span>${d}</span></div>`).join('');

    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const prevDays = new Date(year, month, 0).getDate();
    const today = new Date();
    const cells = [];

    for (let i = firstDay - 1; i >= 0; i--) {
      cells.push({ inMonth: false, dayNum: prevDays - i });
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      cells.push({
        inMonth: true,
        dayNum: d,
        dateStr,
        isToday: d === today.getDate() && month === today.getMonth() && year === today.getFullYear(),
        isSelected: selectedDay === dateStr,
      });
    }
    const totalCells = 42;
    for (let d = 1; d <= totalCells - cells.length; d++) {
      cells.push({ inMonth: false, dayNum: d });
    }

    for (let w = 0; w < 6; w++) {
      const week = cells.slice(w * 7, w * 7 + 7);
      const laid = _layoutWeekLanes(week.map(c => (c.inMonth ? c.dateStr : null)), visibleEvents());
      week.forEach((cell, i) => { cell.lanes = laid[i]; });
    }

    html += cells.map(cell => {
      if (!cell.inMonth) {
        return `<div class="cal-day other-month"><div class="cal-date">${cell.dayNum}</div></div>`;
      }
      const lanes = cell.lanes || { visible: [], hidden: 0 };
      const chips = lanes.visible.map(e => e ? _eventChipHtml(e) : '<div class="cal-event-spacer"></div>').join('');
      const more = lanes.hidden ? `<div class="cal-more">+${lanes.hidden} more</div>` : '';
      const hasEvents = lanes.visible.some(Boolean) || lanes.hidden > 0;
      return `<div class="cal-day ${cell.isToday ? 'today' : ''} ${cell.isSelected ? 'selected' : ''} ${hasEvents ? 'has-events' : ''}"
                 data-date="${cell.dateStr}" onclick="selectDay('${cell.dateStr}')">
        <div class="cal-date">${cell.dayNum}</div>
        <div class="cal-events">${chips}${more}</div>
      </div>`;
    }).join('');

    grid.innerHTML = html;
  }

  function _calendarRange() {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    const monthNum = String(month + 1).padStart(2, '0');
    const lastDay = new Date(year, month + 1, 0).getDate();
    return {
      start: `${year}-${monthNum}-01T00:00:00`,
      end: `${year}-${monthNum}-${String(lastDay).padStart(2, '0')}T23:59:59`,
    };
  }

  function _applySyncStatus(data) {
    const el = document.getElementById('syncStatus');
    if (!el || !data || !data.last_synced) return;
    const mins = Math.round((Date.now() - new Date(data.last_synced + 'Z').getTime()) / 60000);
    el.textContent = mins < 2 ? 'Synced just now' : `Synced ${mins}m ago`;
  }

  function _applyCalendarPage(data) {
    if (!data) return;
    if (Array.isArray(data.events)) {
      allEvents = data.events;
      API.seed(`/api/calendar/events?start=${_calendarRange().start}&end=${_calendarRange().end}`, data.events);
    }
    if (Array.isArray(data.members)) {
      members = data.members;
      API.seed('/api/members/', data.members);
      populateMemberSelect();
    }
    if (data.sync) _applySyncStatus(data.sync);
    renderActiveView();
  }

  async function loadEvents() {
    // Always Everyone for the month; person chips filter client-side. One page GET.
    const { start, end } = _calendarRange();
    const url = `/api/page/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
    const cached = API.peek(url);
    if (cached) _applyCalendarPage(cached);
    try {
      const data = await API.get(url);
      if (FHRouter.currentRoute() === '/calendar.html') _applyCalendarPage(data);
    } catch (e) {
      if (typeof isAbortError === 'function' && isAbortError(e)) return;
      if (!cached) showToast('Failed to load events', 'error');
    }
  }

  function selectDay(dateStr) {
    selectedDay = dateStr;
    window.selectedDay = dateStr;
    const [y, m, d] = dateStr.split('-').map(Number);
    currentDate = new Date(y, m - 1, d);
    setCalView('agenda');
  }

  function openEventModal(dateStr) {
    document.getElementById('eventModalTitle').textContent = 'Add Event';
    document.getElementById('eventTitle').value = '';
    document.getElementById('eventDesc').value = '';
    document.getElementById('eventLocation').value = '';
    document.getElementById('eventAllDay').checked = false;
    document.getElementById('eventMember').value = '';
    if (dateStr) {
      document.getElementById('eventStart').value = dateStr + 'T09:00';
      document.getElementById('eventEnd').value = dateStr + 'T10:00';
    } else {
      const now = new Date();
      document.getElementById('eventStart').value = toLocalISO(now);
      document.getElementById('eventEnd').value = toLocalISO(new Date(now.getTime() + 3600000));
    }
    document.getElementById('eventModal').style.display = 'flex';
    document.getElementById('eventTitle').focus();
  }

  function closeEventModal() {
    const el = document.getElementById('eventModal');
    if (el) el.style.display = 'none';
  }

  async function saveEvent() {
    const title = document.getElementById('eventTitle').value.trim();
    if (!title) { showToast('Please enter a title', 'error'); return; }
    const who = document.getElementById('eventMember').value;
    if (!who) { showToast('Select a person or Family for this event', 'error'); return; }
    const isFamily = who === 'family';
    const payload = {
      title,
      description: document.getElementById('eventDesc').value.trim(),
      location: document.getElementById('eventLocation').value.trim(),
      start_datetime: document.getElementById('eventStart').value,
      end_datetime: document.getElementById('eventEnd').value,
      all_day: document.getElementById('eventAllDay').checked,
      member_id: isFamily ? null : Number(who),
      is_family: isFamily,
    };
    try {
      await API.post('/api/calendar/events', payload);
      closeEventModal();
      if (isFamily) {
        showToast('✅ Event saved to the family calendar', 'success');
      } else {
        const person = members.find(m => String(m.id) === String(who));
        const name = person ? person.name : 'their';
        showToast(`✅ Event saved to ${name}'s calendar`, 'success');
      }
      loadEvents();
    } catch (e) {
      showToast(e.message || 'Failed to save event', 'error');
    }
  }

  async function deleteEvent(id) {
    if (!confirm('Delete this event?')) return;
    try {
      await API.delete(`/api/calendar/events/${id}`);
      showToast('Event deleted', '');
      await loadEvents();
    } catch (e) {
      showToast('Failed to delete', 'error');
    }
  }

  function showEventDetail(eventId, domEvent) {
    if (domEvent && domEvent.stopPropagation) domEvent.stopPropagation();
    const e = allEvents.find(ev => ev.id === eventId);
    if (!e) return;

    const color = eventDisplayColor(e);
    document.getElementById('eventDetailBar').style.background = color;
    document.getElementById('eventDetailTitle').textContent = e.title;

    let timeStr;
    if (e.all_day) {
      const start = new Date(e.start_datetime.split('T')[0] + 'T12:00:00');
      const end   = e.end_datetime ? new Date(e.end_datetime.split('T')[0] + 'T12:00:00') : null;
      const fmt = d => d.toLocaleDateString('en-US', { weekday:'short', month:'short', day:'numeric', timeZone: TZ });
      timeStr = end && e.end_datetime.split('T')[0] !== e.start_datetime.split('T')[0]
        ? `${fmt(start)} – ${fmt(end)}`
        : fmt(start);
      timeStr += ' · All day';
    } else {
      const start = new Date(e.start_datetime);
      timeStr = start.toLocaleDateString('en-US', { weekday:'short', month:'short', day:'numeric', timeZone: TZ })
        + ' · ' + formatTime(e.start_datetime);
      if (e.end_datetime) timeStr += ' – ' + formatTime(e.end_datetime);
    }
    document.getElementById('eventDetailTime').textContent = timeStr;

    const locEl = document.getElementById('eventDetailLocation');
    if (e.location && e.location.trim()) {
      const mapsUrl = `https://maps.google.com/?q=${encodeURIComponent(e.location)}`;
      locEl.innerHTML = `<a href="${mapsUrl}" target="_blank" rel="noopener" style="color:var(--primary);text-decoration:none">📍 ${escapeHtml(e.location)}</a>`;
      locEl.style.display = 'block';
    } else {
      locEl.style.display = 'none';
    }

    const descEl = document.getElementById('eventDetailDesc');
    const descHtml = e.description ? eventBodyHtml(e.description) : '';
    if (descHtml.replace(/<br>/g, '').trim()) {
      descEl.innerHTML = descHtml;
      descEl.style.display = 'block';
    } else {
      descEl.innerHTML = '';
      descEl.style.display = 'none';
    }

    const tagsEl = document.getElementById('eventDetailTags');
    let tags = '';
    if (e.member_name) {
      tags += `<span class="event-detail-tag" style="background:${color}22;color:${color}">${memberAvatarHtml({avatar:e.member_avatar, avatar_photo:e.member_avatar_photo, color}, 'avatar-sm')}<span>${escapeHtml(e.member_name)}</span></span>`;
    } else if (e.is_family) {
      tags += `<span class="event-detail-tag" style="background:${color}22;color:${color}">🏠 Family</span>`;
    }
    tagsEl.innerHTML = tags;

    document.getElementById('eventDetailDelete').onclick = () => {
      closeEventDetail();
      deleteEvent(eventId);
    };

    document.getElementById('eventDetailModal').style.display = 'flex';
  }

  function closeEventDetail() {
    const el = document.getElementById('eventDetailModal');
    if (el) el.style.display = 'none';
  }

  async function syncGoogle() {
    const btn = document.getElementById('syncBtn');
    if (!btn) return;
    btn.textContent = '⏳ Syncing...';
    btn.disabled = true;
    try {
      const r = await API.post('/api/calendar/sync/all', {});
      showToast(`✅ Synced ${r.synced || 0} events`, 'success');
      loadEvents();
    } catch (e) {
      showToast('Sync failed — check calendar connections in Settings', 'error');
    } finally {
      btn.textContent = '🔄 Sync Calendars';
      btn.disabled = false;
    }
  }

  function _blockFileDrop(e) {
    if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) e.preventDefault();
  }

  window.openEventModal = openEventModal;
  window.closeEventDetail = closeEventDetail;
  window.selectDay = selectDay;
  window.showEventDetail = showEventDetail;
  window.deleteEvent = deleteEvent;

  function mount() {
    onPersonFilterChange(() => renderActiveView());
    applyCalView();
    renderActiveView();

    const cachedMembers = API.peek('/api/members/');
    if (cachedMembers) {
      members = cachedMembers;
      populateMemberSelect();
    }

    initAppTimezone().then(() => {
      if (FHRouter.currentRoute() !== '/calendar.html') return;
      renderActiveView();
      loadEvents();
    });

    document.getElementById('prevMonth').onclick = () => {
      if (currentView === 'agenda') currentDate.setDate(currentDate.getDate() - 1);
      else currentDate.setMonth(currentDate.getMonth() - 1);
      renderActiveView();
      loadEvents();
    };
    document.getElementById('nextMonth').onclick = () => {
      if (currentView === 'agenda') currentDate.setDate(currentDate.getDate() + 1);
      else currentDate.setMonth(currentDate.getMonth() + 1);
      renderActiveView();
      loadEvents();
    };
    document.getElementById('todayBtn').onclick = () => {
      currentDate = new Date();
      renderActiveView();
      loadEvents();
    };
    document.querySelectorAll('#calViewTabs [data-view]').forEach(btn => {
      btn.onclick = () => setCalView(btn.getAttribute('data-view'));
    });
    document.getElementById('addEventBtn').onclick = () => openEventModal();
    document.getElementById('closeEventModal').onclick = closeEventModal;
    document.getElementById('cancelEventModal').onclick = closeEventModal;
    document.getElementById('saveEvent').onclick = saveEvent;
    document.getElementById('addEventDayBtn').onclick = () => openEventModal(selectedDay);
    document.getElementById('closeDayPanel').onclick = () => {
      document.getElementById('dayEventsPanel').style.display = 'none';
    };
    document.getElementById('syncBtn').onclick = syncGoogle;
    document.getElementById('eventModal').onclick = (e) => {
      if (e.target.id === 'eventModal') closeEventModal();
    };

    document.addEventListener('dragover', _blockFileDrop);
    document.addEventListener('drop', _blockFileDrop);

    return unmount;
  }

  function unmount() {
    document.removeEventListener('dragover', _blockFileDrop);
    document.removeEventListener('drop', _blockFileDrop);
  }

  FHRouter.register('/calendar.html', { mount, unmount });
})();
