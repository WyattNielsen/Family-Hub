(function () {
  let _secEntities = [];
  let _secTimer = null;
  let _feedTimers = [];

  function _paintSecurityDevices(data) {
    const notCfg = document.getElementById('haNotConfigured');
    if (!notCfg) return;
    if (data.error || !data.entities || !data.entities.length) {
      notCfg.style.display = 'block';
      return;
    }
    notCfg.style.display = 'none';
    _secEntities = data.entities;

    const covers = data.entities.filter(e => e.entity_id.startsWith('cover.'));
    const alarms = data.entities.filter(e => e.entity_type === 'alarm');
    const others = data.entities.filter(e => !e.entity_id.startsWith('cover.') && e.entity_type !== 'alarm');

    renderGarageCards(covers);
    renderAlarmCards(alarms);
    renderOtherCards(others);
  }

  async function loadSecurityDevices(force) {
    const cached = !force ? API.peek('/api/homeassistant/states') : null;
    if (cached) _paintSecurityDevices(cached);
    try {
      const data = await API.get('/api/homeassistant/states', force ? { force: true } : undefined);
      if (FHRouter.currentRoute() === '/security.html') _paintSecurityDevices(data);
    } catch (e) {
      const notCfg = document.getElementById('haNotConfigured');
      if (!cached && notCfg) notCfg.style.display = 'block';
    }
  }

  function renderGarageCards(covers) {
    const section = document.getElementById('garageSection');
    const el = document.getElementById('garageCards');
    if (!section || !el) return;
    if (!covers.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';
    el.innerHTML = covers.map(e => {
      const isOpen = e.state === 'open' || e.state === 'opening';
      const busy   = e.state === 'opening' || e.state === 'closing';
      const badge  = isOpen ? 'open' : 'closed';
      return `
        <div class="sec-row ${isOpen ? 'sec-open' : 'sec-closed'}">
          <div class="sec-row-icon">${e.icon || (isOpen ? '🚗' : '🏠')}</div>
          <div class="sec-row-info">
            <div class="sec-row-name">${e.name}</div>
            <span class="sec-row-badge ${badge}">${e.state_label || e.state}</span>
          </div>
          <div class="sec-row-btns">
            <button class="btn btn-secondary sec-btn" ${e.state === 'open' || busy ? 'disabled' : ''}
              onclick="secCoverAction('open_cover','${e.entity_id}')">🔼 Open</button>
            <button class="btn btn-primary sec-btn" ${e.state === 'closed' || busy ? 'disabled' : ''}
              onclick="secCoverAction('close_cover','${e.entity_id}')">🔽 Close</button>
          </div>
        </div>`;
    }).join('');
  }

  function renderAlarmCards(alarms) {
    const section = document.getElementById('alarmSection');
    const el = document.getElementById('alarmCards');
    if (!section || !el) return;
    if (!alarms.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';
    el.innerHTML = alarms.map(e => {
      const s = e.state;
      const busy    = s === 'arming' || s === 'pending';
      const isArmed = s.startsWith('armed');
      const badge   = isArmed ? 'armed' : 'disarmed';
      const label   = e.state_label || e.state;
      return `
        <div class="alarm-card ${isArmed ? 'sec-armed' : ''}">
          <div class="alarm-card-header">
            <span class="alarm-card-icon">${isArmed ? '🔒' : '🔓'}</span>
            <div>
              <div class="alarm-card-name">${e.name}</div>
              <span class="sec-row-badge ${badge}">${label}</span>
            </div>
          </div>
          <div class="alarm-card-btns">
            <button class="btn btn-secondary" ${s === 'disarmed' || busy ? 'disabled' : ''}
              onclick="alarmAction('alarm_disarm','${e.entity_id}')">🔓 Disarm</button>
            <button class="btn ${s === 'armed_home' ? 'btn-primary' : 'btn-ghost'}" ${s === 'armed_home' || busy ? 'disabled' : ''}
              onclick="alarmAction('alarm_arm_home','${e.entity_id}')">🏠 Arm Home</button>
            <button class="btn ${s === 'armed_away' ? 'btn-primary' : 'btn-ghost'}" ${s === 'armed_away' || busy ? 'disabled' : ''}
              onclick="alarmAction('alarm_arm_away','${e.entity_id}')">🏃 Arm Away</button>
          </div>
        </div>`;
    }).join('');
  }

  function renderOtherCards(others) {
    const section = document.getElementById('otherSection');
    const el = document.getElementById('otherCards');
    if (!section || !el) return;
    if (!others.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';
    el.innerHTML = others.map(e => `
      <div class="sec-row">
        <div class="sec-row-icon">${e.icon || '📡'}</div>
        <div class="sec-row-info">
          <div class="sec-row-name">${e.name}</div>
          <span class="sec-row-badge">${e.state_label || e.state}</span>
        </div>
      </div>`).join('');
  }

  async function secCoverAction(action, entityId) {
    try {
      await API.post(`/api/homeassistant/cover/${action}`, { entity_id: entityId });
      showToast(action === 'open_cover' ? '🔼 Opening…' : '🔽 Closing…', 'success');
      optimisticHAUpdate(entityId, action === 'open_cover' ? 'opening' : 'closing');
      loadSecurityDevices(true);
      setTimeout(() => { loadSecurityDevices(true); loadHAStatus(true); }, 3000);
    } catch (e) {
      showToast('Failed to send command', 'error');
    }
  }

  async function alarmAction(action, entityId) {
    try {
      await API.post(`/api/homeassistant/alarm/${action}`, { entity_id: entityId });
      showToast(action === 'alarm_disarm' ? '🔓 Disarmed' : '🔒 Armed', 'success');
      const expectedState = action === 'alarm_disarm' ? 'disarmed'
        : action === 'alarm_arm_away' ? 'armed_away'
        : action === 'alarm_arm_home' ? 'armed_home' : 'arming';
      optimisticHAUpdate(entityId, expectedState);
      loadSecurityDevices(true);
      setTimeout(() => { loadSecurityDevices(true); loadHAStatus(true); }, 3000);
    } catch (e) {
      showToast('Failed to send alarm command', 'error');
    }
  }

  async function loadCameraFeeds() {
    const section = document.getElementById('cameraSection');
    const notConfigured = document.getElementById('cameraNotConfigured');
    const grid = document.getElementById('cameraGrid');
    if (!section || !notConfigured || !grid) return;
    try {
      const data = await API.get('/api/security/cameras');
      if (FHRouter.currentRoute() !== '/security.html') return;
      const cameras = data.cameras || [];
      if (!cameras.length) {
        section.style.display = 'none';
        notConfigured.style.display = 'block';
        return;
      }
      notConfigured.style.display = 'none';
      section.style.display = 'block';
      _clearFeedTimers();
      grid.innerHTML = cameras.map(cam => {
        const entity = escapeHtml(cam.ha_entity_id);
        return `
          <div class="camera-feed-card" data-entity="${entity}">
            <div class="camera-feed-label">${escapeHtml(cam.name)}</div>
            <div class="camera-frame">
              <img class="camera-feed-img" alt="${escapeHtml(cam.name)}" data-entity="${entity}">
            </div>
          </div>`;
      }).join('');
      grid.querySelectorAll('.camera-feed-card').forEach(card => {
        card.onclick = () => openCameraViewer(card.dataset.entity);
      });
      grid.querySelectorAll('.camera-feed-img').forEach(bindFeedImg);
    } catch (e) {
      section.style.display = 'none';
      notConfigured.style.display = 'block';
    }
  }

  function _clearFeedTimers() {
    _feedTimers.forEach(t => clearInterval(t));
    _feedTimers = [];
  }

  function bindFeedImg(img) {
    const entity = img.dataset.entity;
    img.dataset.mode = 'stream';
    img.onerror = () => {
      if (img.dataset.mode === 'snap') return;
      img.dataset.mode = 'snap';
      const tick = () => {
        if (!img.isConnected) return;
        img.src = cameraSnapUrl(entity);
      };
      const timer = setInterval(tick, 1500);
      img._snapTimer = timer;
      _feedTimers.push(timer);
      tick();
    };
    img.src = cameraStreamUrl(entity);
  }

  window.loadSecurityDevices = loadSecurityDevices;
  window.secCoverAction = secCoverAction;
  window.alarmAction = alarmAction;

  function mount() {
    initAppTimezone();
    loadSecurityDevices();
    afterPagePaint(() => {
      if (FHRouter.currentRoute() === '/security.html') loadCameraFeeds();
    });
    _secTimer = setInterval(() => {
      if (!document.hidden) loadSecurityDevices(true);
    }, 30000);
    return unmount;
  }

  function unmount() {
    clearInterval(_secTimer);
    _secTimer = null;
    _clearFeedTimers();
    closeCameraModal();
    killActiveStreams();
  }

  FHRouter.register('/security.html', { mount, unmount });
})();
