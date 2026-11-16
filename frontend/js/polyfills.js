/* Minimal polyfills for Chromium 55–79 (Android 7 WebView / old Chrome). */
(function () {
  'use strict';

  if (!String.prototype.padStart) {
    String.prototype.padStart = function (targetLength, padString) {
      var str = String(this);
      var len = targetLength >> 0;
      var pad = String(padString !== undefined ? padString : ' ');
      if (str.length >= len) return str;
      len = len - str.length;
      if (len > pad.length) pad += Array(Math.ceil(len / pad.length) + 1).join(pad);
      return pad.slice(0, len) + str;
    };
  }

  if (typeof Promise !== 'undefined' && !Promise.prototype.finally) {
    Promise.prototype.finally = function (callback) {
      var P = this.constructor || Promise;
      return this.then(
        function (value) { return P.resolve(callback()).then(function () { return value; }); },
        function (reason) { return P.resolve(callback()).then(function () { throw reason; }); }
      );
    };
  }

  if (typeof Promise !== 'undefined' && !Promise.allSettled) {
    Promise.allSettled = function (promises) {
      return Promise.all(Array.prototype.map.call(promises, function (p) {
        return Promise.resolve(p).then(
          function (value) { return { status: 'fulfilled', value: value }; },
          function (reason) { return { status: 'rejected', reason: reason }; }
        );
      }));
    };
  }

  if (typeof AbortController === 'undefined') {
    window.__FH_ABORT_STUB = true;
    window.AbortController = function () {
      var self = this;
      this.signal = {
        aborted: false,
        addEventListener: function () {},
        removeEventListener: function () {}
      };
      this.abort = function () {
        self.signal.aborted = true;
      };
    };
  }

  // Soft marker used by api.js for low-end / old-Chromium tweaks.
  // Treat every Android device as low-end — connection limits + weak CPUs.
  window.__FH_LOW_END = (function () {
    try {
      var mem = navigator.deviceMemory || 0;
      var ua = navigator.userAgent || '';
      var m = ua.match(/Chrome\/(\d+)/);
      var chromeVer = m ? parseInt(m[1], 10) : 0;
      var oldChrome = chromeVer > 0 && chromeVer < 80;
      var android = /Android/i.test(ua);
      return android || oldChrome || (mem > 0 && mem <= 2);
    } catch (_) {
      return true;
    }
  })();
})();
