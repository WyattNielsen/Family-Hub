(function () {
  let _secEntities = [];
  let _secTimer = null;
  let _modalCameras = [];
  let _modalIndex = 0;
  let _swipeStartX = null;
  let _gridEpoch = 0;

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
      if (isAbortError(e)) return;
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

  function _showSnapshotFallback(entityId, name) {
    const ph = document.getElementById('ph-' + entityId);
    const video = document.getElementById('vid-' + entityId);
    const snap = document.getElementById('snap-' + entityId);
    if (video) {
      try {
        if (video._fhPeer) unregisterPeerConnection(video._fhPeer);
        video._fhPeer = null;
        video.srcObject = null;
      } catch (_) {}
      video.style.display = 'none';
    }
    if (snap) {
      snap.style.display = 'block';
      snap.src = `/api/security/camera/${encodeURIComponent(entityId)}/snapshot?t=${Date.now()}`;
      snap.onload = () => {
        snap.classList.add('loaded');
        if (ph) ph.remove();
      };
      snap.onerror = () => {
        if (ph) ph.innerHTML = '<div class="camera-placeholder-icon">⚠️</div><div>Feed unavailable</div>';
      };
    } else if (ph) {
      ph.innerHTML = '<div class="camera-placeholder-icon">⚠️</div><div>Feed unavailable</div>';
    }
  }

  async function _startGridWebRTC(cam, epoch) {
    const entityId = cam.ha_entity_id;
    const video = document.getElementById('vid-' + entityId);
    const ph = document.getElementById('ph-' + entityId);
    if (!video || epoch !== _gridEpoch || FHRouter.currentRoute() !== '/security.html') return;
    try {
      await connectCameraWebRTC(entityId, video);
      if (epoch !== _gridEpoch) {
        if (video._fhPeer) unregisterPeerConnection(video._fhPeer);
        return;
      }
      video.style.display = 'block';
      video.classList.add('loaded');
      if (ph) ph.remove();
    } catch (e) {
      if (epoch !== _gridEpoch) return;
      console.warn('WebRTC failed for', entityId, e);
      _showSnapshotFallback(entityId, cam.name);
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
      const epoch = ++_gridEpoch;
      grid.innerHTML = cameras.map(cam => {
        const id = cam.ha_entity_id;
        const safeId = encodeURIComponent(id);
        const safeName = String(cam.name || id).replace(/'/g, "\\'");
        return `
          <div class="camera-feed-card" data-entity="${escapeHtml(id)}" data-name="${escapeHtml(cam.name || id)}"
               onclick="openCameraModal('${safeName}','${safeId}')">
            <div class="camera-feed-label">${escapeHtml(cam.name || id)}</div>
            <div class="camera-placeholder" id="ph-${id}">
              <div class="camera-placeholder-icon">📷</div>
              <div>Connecting to ${escapeHtml(cam.name || id)}…</div>
            </div>
            <video id="vid-${id}" class="camera-feed-video" autoplay muted playsinline></video>
            <img id="snap-${id}" class="camera-feed-img" alt="${escapeHtml(cam.name || id)}" style="display:none"
                 src="/api/security/camera/${safeId}/snapshot?t=${Date.now()}">
          </div>`;
      }).join('');

      // Stagger WebRTC connects so ICE/signaling does not spike all at once
      cameras.forEach((cam, idx) => {
        setTimeout(() => {
          if (epoch === _gridEpoch) _startGridWebRTC(cam, epoch);
        }, idx * 200);
      });
    } catch (e) {
      if (isAbortError(e)) return;
      section.style.display = 'none';
      notConfigured.style.display = 'block';
    }
  }

  function openCameraModal(name, entityId) {
    const cards = document.querySelectorAll('.camera-feed-card');
    _modalCameras = Array.from(cards).map(card => ({
      name: card.getAttribute('data-name') || card.querySelector('.camera-feed-label')?.textContent || '',
      entityId: card.getAttribute('data-entity') || '',
    })).filter(c => c.entityId);
    _modalIndex = _modalCameras.findIndex(c => c.entityId === entityId || c.name === name);
    if (_modalIndex < 0) _modalIndex = 0;
    _showModalCamera(_modalIndex);
    document.getElementById('cameraModal').style.display = 'flex';
    document.addEventListener('keydown', _modalKeyHandler);
    const video = document.getElementById('cameraModalVideo');
    if (video) {
      video.addEventListener('touchstart', _swipeStart, { passive: true });
      video.addEventListener('touchend', _swipeEnd, { passive: true });
    }
  }

  async function _showModalCamera(idx) {
    const cam = _modalCameras[idx];
    if (!cam) return;
    document.getElementById('cameraModalTitle').textContent = cam.name;
    const dots = document.getElementById('cameraModalDots');
    dots.innerHTML = _modalCameras.map((_, i) =>
      `<span class="cam-dot ${i === idx ? 'active' : ''}"></span>`
    ).join('');
    document.getElementById('camPrev').style.visibility = _modalCameras.length > 1 ? 'visible' : 'hidden';
    document.getElementById('camNext').style.visibility = _modalCameras.length > 1 ? 'visible' : 'hidden';

    const video = document.getElementById('cameraModalVideo');
    if (!video) return;
    if (video._fhPeer) {
      unregisterPeerConnection(video._fhPeer);
      video._fhPeer = null;
    }
    video.srcObject = null;
    video.classList.remove('loaded');
    try {
      await connectCameraWebRTC(cam.entityId, video);
    } catch (e) {
      console.warn('Modal WebRTC failed', e);
      // Fall back to snapshot as a still frame
      video.poster = `/api/security/camera/${encodeURIComponent(cam.entityId)}/snapshot?t=${Date.now()}`;
    }
  }

  function cycleCameraModal(dir) {
    if (!_modalCameras.length) return;
    _modalIndex = (_modalIndex + dir + _modalCameras.length) % _modalCameras.length;
    _showModalCamera(_modalIndex);
  }

  function closeCameraModal() {
    const modal = document.getElementById('cameraModal');
    if (modal) modal.style.display = 'none';
    const video = document.getElementById('cameraModalVideo');
    if (video) {
      if (video._fhPeer) {
        unregisterPeerConnection(video._fhPeer);
        video._fhPeer = null;
      }
      try {
        video.pause();
        video.srcObject = null;
        video.removeAttribute('poster');
      } catch (_) {}
      video.removeEventListener('touchstart', _swipeStart);
      video.removeEventListener('touchend', _swipeEnd);
    }
    document.removeEventListener('keydown', _modalKeyHandler);
  }

  function _modalKeyHandler(e) {
    if (e.key === 'Escape')      closeCameraModal();
    if (e.key === 'ArrowRight')  cycleCameraModal(1);
    if (e.key === 'ArrowLeft')   cycleCameraModal(-1);
  }

  function _swipeStart(e) { _swipeStartX = e.touches[0].clientX; }
  function _swipeEnd(e) {
    if (_swipeStartX === null) return;
    const dx = e.changedTouches[0].clientX - _swipeStartX;
    if (Math.abs(dx) > 50) cycleCameraModal(dx < 0 ? 1 : -1);
    _swipeStartX = null;
  }

  window.loadSecurityDevices = loadSecurityDevices;
  window.secCoverAction = secCoverAction;
  window.alarmAction = alarmAction;
  window.openCameraModal = openCameraModal;
  window.cycleCameraModal = cycleCameraModal;
  window.closeCameraModal = closeCameraModal;

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
    _gridEpoch += 1;
    clearInterval(_secTimer);
    _secTimer = null;
    closeCameraModal();
    killActiveStreams();
  }

  FHRouter.register('/security.html', { mount, unmount });
})();
