const AGENDA_PX_PER_HOUR = 48;
const AGENDA_HOUR_DEFAULT_START = 6;
const AGENDA_HOUR_DEFAULT_END = 22;

function eventOccursOnDate(e, dateStr) {
  if (!e || !e.start_datetime) return false;
  const start = e.start_datetime.split('T')[0];
  let end = e.end_datetime ? e.end_datetime.split('T')[0] : start;
  if (e.all_day && end > start) {
    const endDate = new Date(end + 'T12:00:00');
    endDate.setDate(endDate.getDate() - 1);
    end = endDate.toISOString().split('T')[0];
  }
  return dateStr >= start && dateStr <= end;
}

function shiftDateStr(dateStr, deltaDays) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + deltaDays);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function agendaDateLabel(dateStr, opts) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', opts || {
    weekday: 'long', month: 'long', day: 'numeric'
  });
}

function _agendaHourLabel(hour) {
  const h = ((hour % 24) + 24) % 24;
  if (h === 0) return '12 AM';
  if (h === 12) return '12 PM';
  if (h > 12) return `${h - 12} PM`;
  return `${h} AM`;
}

function _minutesInTZ(dateStr) {
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return 0;
  try {
    if (typeof Intl !== 'undefined' && Intl.DateTimeFormat && Intl.DateTimeFormat.prototype.formatToParts) {
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: TZ, hour: 'numeric', minute: 'numeric', hour12: false
      }).formatToParts(d);
      let hour = 0;
      let minute = 0;
      for (let i = 0; i < parts.length; i++) {
        if (parts[i].type === 'hour') hour = Number(parts[i].value || 0);
        if (parts[i].type === 'minute') minute = Number(parts[i].value || 0);
      }
      if (hour === 24) hour = 0;
      return hour * 60 + minute;
    }
  } catch (_) {}
  try {
    const raw = d.toLocaleTimeString('en-US', { timeZone: TZ, hour12: false });
    const bits = String(raw).split(':');
    let hour = parseInt(bits[0], 10) || 0;
    const minute = parseInt(bits[1], 10) || 0;
    if (hour === 24) hour = 0;
    return hour * 60 + minute;
  } catch (_) {
    return d.getHours() * 60 + d.getMinutes();
  }
}

function _isAllDayEvent(e) {
  return !!(e.all_day || (e.start_datetime && e.start_datetime.indexOf('T') === -1));
}

function _eventMinutesOnDay(e, dateStr) {
  const startDate = e.start_datetime.split('T')[0];
  const endRaw = e.end_datetime || e.start_datetime;
  const endDate = endRaw.split('T')[0];
  let startMin = e.start_datetime.indexOf('T') === -1 ? 0 : _minutesInTZ(e.start_datetime);
  let endMin = !e.end_datetime || endRaw.indexOf('T') === -1 ? startMin + 60 : _minutesInTZ(e.end_datetime);
  if (startDate < dateStr) startMin = 0;
  if (endDate > dateStr) endMin = 24 * 60;
  if (endMin <= startMin) endMin = Math.min(24 * 60, startMin + 30);
  return { startMin, endMin };
}

function _assignColumns(items) {
  items.sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin);
  const colEnds = [];
  items.forEach(item => {
    let col = 0;
    while (col < colEnds.length && colEnds[col] > item.startMin) col += 1;
    item.col = col;
    colEnds[col] = item.endMin;
  });
  items.forEach(item => {
    let maxCol = item.col;
    items.forEach(other => {
      if (other.startMin < item.endMin && other.endMin > item.startMin) {
        maxCol = Math.max(maxCol, other.col);
      }
    });
    item.colCount = maxCol + 1;
  });
}

function renderAgenda(container, events, dateStr, options) {
  if (!container) return;
  const opts = options || {};
  const dayEvents = (events || []).filter(e => eventOccursOnDate(e, dateStr));
  const allDay = dayEvents.filter(_isAllDayEvent);
  const timed = dayEvents.filter(e => !_isAllDayEvent(e)).map(e => {
    const mins = _eventMinutesOnDay(e, dateStr);
    return { e, ...mins };
  });
  _assignColumns(timed);

  let hourStart = AGENDA_HOUR_DEFAULT_START;
  let hourEnd = AGENDA_HOUR_DEFAULT_END;
  timed.forEach(item => {
    hourStart = Math.min(hourStart, Math.floor(item.startMin / 60));
    hourEnd = Math.max(hourEnd, Math.ceil(item.endMin / 60));
  });
  hourStart = Math.max(0, hourStart);
  hourEnd = Math.min(24, Math.max(hourStart + 1, hourEnd));

  const height = (hourEnd - hourStart) * AGENDA_PX_PER_HOUR;
  const clickable = typeof opts.onEventClick === 'function';
  const addHtml = typeof opts.onAdd === 'function'
    ? ' <a href="#" class="agenda-add-link">+ Add one</a>'
    : '';

  const allDayPills = allDay.length
    ? allDay.map(e => {
        const color = eventDisplayColor(e);
        return `<button type="button" class="agenda-pill" data-event-id="${e.id}" style="--event:${color}">${escapeHtml(e.title)}</button>`;
      }).join('')
    : '<span class="agenda-allday-empty">No all-day events</span>';

  const hoursHtml = Array.from({ length: hourEnd - hourStart }, (_, i) => {
    const h = hourStart + i;
    return `<div class="agenda-hour" style="height:${AGENDA_PX_PER_HOUR}px"><span class="agenda-hour-label">${_agendaHourLabel(h)}</span></div>`;
  }).join('');

  const blocksHtml = timed.map(item => {
    const color = eventDisplayColor(item.e);
    const top = ((item.startMin / 60) - hourStart) * AGENDA_PX_PER_HOUR;
    const h = Math.max(((item.endMin - item.startMin) / 60) * AGENDA_PX_PER_HOUR, 22);
    const widthPct = 100 / item.colCount;
    const leftPct = item.col * widthPct;
    const timeLabel = `${formatTime(item.e.start_datetime)}${item.e.end_datetime ? ' – ' + formatTime(item.e.end_datetime) : ''}`;
    return `<button type="button" class="agenda-block" data-event-id="${item.e.id}" style="--event:${color};top:${top}px;height:${h}px;left:calc(${leftPct}% + 3px);width:calc(${widthPct}% - 6px)">
      <strong>${escapeHtml(item.e.title)}</strong>
      <em>${escapeHtml(timeLabel)}</em>
    </button>`;
  }).join('');

  const todayStr = toETDateStr(new Date());
  let nowHtml = '';
  if (dateStr === todayStr) {
    const nowMin = _minutesInTZ(new Date().toISOString());
    if (nowMin >= hourStart * 60 && nowMin <= hourEnd * 60) {
      const top = (nowMin / 60 - hourStart) * AGENDA_PX_PER_HOUR;
      nowHtml = `<div class="agenda-now" style="top:${top}px"></div>`;
    }
  }

  const emptyHtml = !dayEvents.length
    ? `<div class="empty-state agenda-empty">No events.${addHtml}</div>`
    : '';

  container.innerHTML = `
    <div class="agenda-view">
      <div class="agenda-allday">
        <span class="agenda-allday-label">All day</span>
        <div class="agenda-allday-items">${allDayPills}</div>
      </div>
      ${emptyHtml}
      <div class="agenda-timeline" style="height:${height}px">
        ${hoursHtml}
        <div class="agenda-lane">${blocksHtml}</div>
        ${nowHtml}
      </div>
    </div>`;

  const byId = id => dayEvents.find(ev => String(ev.id) === String(id));
  container.querySelectorAll('[data-event-id]').forEach(el => {
    if (!clickable) {
      el.style.cursor = 'default';
      return;
    }
    el.addEventListener('click', ev => {
      ev.stopPropagation();
      const found = byId(el.getAttribute('data-event-id'));
      if (found) opts.onEventClick(found, ev);
    });
  });
  const addLink = container.querySelector('.agenda-add-link');
  if (addLink && opts.onAdd) {
    addLink.addEventListener('click', ev => {
      ev.preventDefault();
      opts.onAdd(dateStr);
    });
  }
}
