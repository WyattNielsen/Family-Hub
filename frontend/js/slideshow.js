// ── Family Hub Slideshow ──────────────────────────────────────
// Full-screen photo slideshow that triggers after inactivity

const Slideshow = (() => {
  let photos = [];
  let currentIndex = 0;
  let inactivityTimer = null;
  let slideTimer = null;
  let slideTimer2 = null;
  let isActive = false;
  let initialized = false;
  let timeoutMinutes = 2;
  let intervalSeconds = 5;

  async function loadConfig() {
    try {
      const s = await API.get('/api/settings/');
      timeoutMinutes = parseFloat(s.slideshow_timeout || '120') / 60;
      intervalSeconds = parseFloat(s.slideshow_interval || '5');
    } catch(e) {}
  }

  async function loadPhotos() {
    try {
      const data = await API.get('/api/photos/');
      photos = (data.photos || []).slice();
      for (let i = photos.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [photos[i], photos[j]] = [photos[j], photos[i]];
      }
    } catch(e) {
      photos = [];
    }
  }

  function createOverlay() {
    if (document.getElementById('slideshowOverlay')) return;
    const el = document.createElement('div');
    el.id = 'slideshowOverlay';
    el.innerHTML = `
      <div id="slideshowBg1" class="slideshow-bg"></div>
      <div id="slideshowBg2" class="slideshow-bg"></div>
      <div class="slideshow-info">
        <div class="slideshow-clock" id="slideshowClock"></div>
        <div class="slideshow-date" id="slideshowDate"></div>
      </div>
      <div class="slideshow-controls">
        <button class="slideshow-btn" onclick="Slideshow.prev()">‹</button>
        <button class="slideshow-btn" onclick="Slideshow.stop()">✕</button>
        <button class="slideshow-btn" onclick="Slideshow.next()">›</button>
      </div>
      <div class="slideshow-tap-hint">Tap anywhere to close</div>
    `;
    el.addEventListener('click', (e) => {
      if (!e.target.closest('.slideshow-controls')) stop();
    });
    document.body.appendChild(el);
  }

  let bg1Active = true;
  function showPhoto(index) {
    if (!photos.length) return;
    const url = photos[index % photos.length];
    const bg1 = document.getElementById('slideshowBg1');
    const bg2 = document.getElementById('slideshowBg2');
    if (!bg1 || !bg2) return;

    if (bg1Active) {
      bg2.style.backgroundImage = `url(${url})`;
      bg2.style.opacity = '1';
      bg1.style.opacity = '0';
    } else {
      bg1.style.backgroundImage = `url(${url})`;
      bg1.style.opacity = '1';
      bg2.style.opacity = '0';
    }
    bg1Active = !bg1Active;
  }

  function updateClock() {
    const clock = document.getElementById('slideshowClock');
    const date = document.getElementById('slideshowDate');
    if (!clock) return;
    const now = new Date();
    clock.textContent = now.toLocaleTimeString('en-US', {
      hour: 'numeric', minute: '2-digit', timeZone: TZ
    });
    date.textContent = now.toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric', timeZone: TZ
    });
  }

  async function start() {
    if (isActive) return;
    await loadPhotos();
    if (!photos.length) return;

    isActive = true;
    currentIndex = 0;
    createOverlay();

    const el = document.getElementById('slideshowOverlay');
    el.classList.add('active');

    showPhoto(currentIndex);
    updateClock();

    slideTimer = setInterval(() => {
      currentIndex = (currentIndex + 1) % photos.length;
      showPhoto(currentIndex);
    }, intervalSeconds * 1000);

    slideTimer2 = setInterval(updateClock, 1000);
  }

  function stop() {
    if (!isActive) return;
    isActive = false;
    clearInterval(slideTimer);
    clearInterval(slideTimer2);

    const el = document.getElementById('slideshowOverlay');
    if (el) el.classList.remove('active');
    resetInactivityTimer();
  }

  function next() {
    if (!photos.length) return;
    currentIndex = (currentIndex + 1) % photos.length;
    showPhoto(currentIndex);
    resetSlideTimer();
  }

  function prev() {
    if (!photos.length) return;
    currentIndex = (currentIndex - 1 + photos.length) % photos.length;
    showPhoto(currentIndex);
    resetSlideTimer();
  }

  function resetSlideTimer() {
    clearInterval(slideTimer);
    slideTimer = setInterval(() => {
      currentIndex = (currentIndex + 1) % photos.length;
      showPhoto(currentIndex);
    }, intervalSeconds * 1000);
  }

  let _lastResetTs = 0;
  function resetInactivityTimer() {
    clearTimeout(inactivityTimer);
    if (isActive) return;
    inactivityTimer = setTimeout(() => {
      start();
    }, timeoutMinutes * 60 * 1000);
  }

  function init() {
    loadConfig().then(() => {
      resetInactivityTimer();
    });
    if (initialized) return;
    initialized = true;

    ['mousemove', 'mousedown', 'keypress', 'touchstart', 'scroll', 'click'].forEach(evt => {
      document.addEventListener(evt, () => {
        if (isActive) return;
        const now = Date.now();
        if (now - _lastResetTs < 1000) return;
        _lastResetTs = now;
        resetInactivityTimer();
      }, { passive: true });
    });
  }

  return { init, start, stop, next, prev, reloadConfig: loadConfig };
})();

document.addEventListener('DOMContentLoaded', () => Slideshow.init());
