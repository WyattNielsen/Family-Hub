(function () {
  let _generation = 0;

  function dashboardUrl(base, path) {
    let root = String(base || '').trim();
    if (!root) return '';
    if (!/^https?:\/\//i.test(root)) {
      if (/^[a-z][a-z0-9+.-]*:/i.test(root)) return '';
      root = 'http://' + root;
    }
    root = root.replace(/\/+$/, '');
    let suffix = String(path || '').trim() || '/lovelace';
    if (!suffix.startsWith('/')) suffix = '/' + suffix;
    suffix = '/' + suffix.replace(/^\/+/, '');
    suffix = suffix.replace(/\/{2,}/g, '/');
    return root + suffix;
  }

  function watchFrame(frame, note, generation) {
    const markBlocked = () => {
      if (generation !== _generation || !note) return;
      note.classList.add('is-blocked');
    };
    frame.addEventListener('error', markBlocked);
    frame.addEventListener('load', () => {
      if (generation !== _generation) return;
      try {
        const href = frame.contentWindow && frame.contentWindow.location.href;
        if (!href || href === 'about:blank') markBlocked();
      } catch (_) {
        // A cross-origin document loaded, so framing was allowed.
        // A blocked frame can look the same; the note under the frame stays up.
      }
    });
  }

  async function loadDashboard() {
    const generation = ++_generation;
    const empty = document.getElementById('haDashEmpty');
    const frame = document.getElementById('haDashFrame');
    const note = document.getElementById('haDashNote');
    const open = document.getElementById('haDashOpen');
    if (!empty || !frame || !note) return;

    empty.hidden = true;
    frame.hidden = true;
    note.hidden = true;
    note.classList.remove('is-blocked');
    frame.removeAttribute('src');

    let settings = {};
    try {
      settings = await API.get('/api/settings/') || {};
    } catch (_) {
      settings = {};
    }
    if (generation !== _generation || FHRouter.currentRoute() !== '/home.html') return;

    const url = dashboardUrl(settings.ha_url, settings.ha_dashboard_path);
    if (!url) {
      empty.hidden = false;
      return;
    }

    if (open) open.href = url;
    note.hidden = false;
    frame.hidden = false;
    watchFrame(frame, note, generation);
    frame.src = url;
  }

  function unmount() {
    _generation += 1;
    const frame = document.getElementById('haDashFrame');
    if (frame) frame.src = 'about:blank';
  }

  function mount() {
    initAppTimezone();
    loadDashboard();
    return unmount;
  }

  FHRouter.register('/home.html', { mount, unmount });
})();
