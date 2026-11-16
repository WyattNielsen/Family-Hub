// ── Family Hub Compatibility Polyfills for Older Browsers / WebViews (Android 7 / Chrome 50+) ──
(function () {
  'use strict';

  // Promise.prototype.finally (Chrome < 63)
  if (!Promise.prototype.finally) {
    Promise.prototype.finally = function (callback) {
      var constructor = this.constructor;
      return this.then(
        function (value) {
          return constructor.resolve(callback()).then(function () {
            return value;
          });
        },
        function (reason) {
          return constructor.resolve(callback()).then(function () {
            throw reason;
          });
        }
      );
    };
  }

  // Promise.allSettled (Chrome < 76)
  if (!Promise.allSettled) {
    Promise.allSettled = function (promises) {
      return Promise.all(
        Array.prototype.map.call(promises, function (p) {
          return Promise.resolve(p).then(
            function (value) { return { status: 'fulfilled', value: value }; },
            function (reason) { return { status: 'rejected', reason: reason }; }
          );
        })
      );
    };
  }

  // String.prototype.matchAll (Chrome < 73)
  if (!String.prototype.matchAll) {
    String.prototype.matchAll = function (regexp) {
      var flags = regexp.flags;
      if (typeof flags === 'string' && flags.indexOf('g') === -1) flags += 'g';
      var rx = new RegExp(regexp.source, flags || 'g');
      var str = String(this);
      var matches = [];
      var match;
      while ((match = rx.exec(str)) !== null) {
        matches.push(match);
        if (rx.lastIndex === match.index) rx.lastIndex++;
      }
      return matches;
    };
  }

  // String.prototype.padStart & padEnd (Chrome < 57)
  if (!String.prototype.padStart) {
    String.prototype.padStart = function (targetLength, padString) {
      targetLength = targetLength >> 0;
      padString = String(padString !== undefined ? padString : ' ');
      if (this.length > targetLength) return String(this);
      targetLength = targetLength - this.length;
      if (targetLength > padString.length) {
        padString += padString.repeat(targetLength / padString.length);
      }
      return padString.slice(0, targetLength) + String(this);
    };
  }
  if (!String.prototype.padEnd) {
    String.prototype.padEnd = function (targetLength, padString) {
      targetLength = targetLength >> 0;
      padString = String(padString !== undefined ? padString : ' ');
      if (this.length > targetLength) return String(this);
      targetLength = targetLength - this.length;
      if (targetLength > padString.length) {
        padString += padString.repeat(targetLength / padString.length);
      }
      return String(this) + padString.slice(0, targetLength);
    };
  }

  // Object.values & Object.entries (Chrome < 54)
  if (!Object.values) {
    Object.values = function (obj) {
      if (obj === null || obj === undefined) return [];
      var res = [];
      for (var key in obj) {
        if (Object.prototype.hasOwnProperty.call(obj, key)) res.push(obj[key]);
      }
      return res;
    };
  }
  if (!Object.entries) {
    Object.entries = function (obj) {
      if (obj === null || obj === undefined) return [];
      var res = [];
      for (var key in obj) {
        if (Object.prototype.hasOwnProperty.call(obj, key)) res.push([key, obj[key]]);
      }
      return res;
    };
  }
  if (!Object.fromEntries) {
    Object.fromEntries = function (entries) {
      if (!entries) return {};
      var obj = {};
      var iter = Array.isArray(entries) ? entries : Array.from(entries);
      for (var i = 0; i < iter.length; i++) {
        var pair = iter[i];
        if (pair) obj[pair[0]] = pair[1];
      }
      return obj;
    };
  }

  // Array.prototype.flat & flatMap (Chrome < 69)
  if (!Array.prototype.flat) {
    Array.prototype.flat = function (depth) {
      var d = depth === undefined ? 1 : Math.floor(depth);
      if (d < 1) return Array.prototype.slice.call(this);
      function flatten(arr, currentDepth) {
        return arr.reduce(function (acc, val) {
          return acc.concat(Array.isArray(val) && currentDepth > 0 ? flatten(val, currentDepth - 1) : val);
        }, []);
      }
      return flatten(this, d);
    };
  }
  if (!Array.prototype.flatMap) {
    Array.prototype.flatMap = function (callback, thisArg) {
      return this.map(callback, thisArg).flat();
    };
  }

  // AbortController fallback (Chrome < 66)
  if (typeof AbortController === 'undefined') {
    window.AbortController = function () {
      this.signal = {
        aborted: false,
        addEventListener: function () {},
        removeEventListener: function () {},
        dispatchEvent: function () { return true; }
      };
      this.abort = function () {
        this.signal.aborted = true;
      };
    };
  }
})();
