/* =====================================================================
   SANGAM QUIZ CORE
   ---------------------------------------------------------------------
   Ek hi jagah par teen cheezein:

   1. X-RAY  : ?debug=1 lagao to screen pe live HUD aayega — kaunsa
               transition kitna laga, cache hit hua ya miss, clock zinda
               hai ya nahi, main thread kab block hua.
   2. CLOCK  : Pure page ka SINGLE timer registry. Chahe component do
               baar init ho jaye, zombie interval possible hi nahi —
               stopAll() har ek ko maar deta hai.
   3. RENDER : Questions ko offscreen pehle se MathJax-typeset karke
               cache karta hai. Next dabate hi ready HTML inject hota
               hai — koi black flash nahi, koi re-typeset nahi.
   ===================================================================== */

window.SangamQuiz = (function () {
  'use strict';

  var DEBUG = false;
  try {
    DEBUG = new URLSearchParams(window.location.search).has('debug');
  } catch (e) { DEBUG = false; }

  /* ===================================================================
     1. X-RAY  —  CCTV + diagnostics
     =================================================================== */

  var XRay = {
    enabled: DEBUG,
    stats: {
      transitions: 0,
      cacheHits: 0,
      cacheMisses: 0,
      lastSwapMs: 0,
      lastPrepareMs: 0,
      lastPaintMs: 0,
      longTasks: 0,
      worstLongTask: 0,
      clockAlarms: 0
    },
    _hudEl: null,
    _rows: {},

    log: function (label, data) {
      if (!this.enabled) return;
      if (data !== undefined) console.log('[xray] ' + label, data);
      else console.log('[xray] ' + label);
    },

    warn: function (label, data) {
      if (!this.enabled) return;
      console.warn('[xray] ' + label, data === undefined ? '' : data);
    },

    /* Kisi bhi async/sync block ka duration naapta hai */
    span: function (label, fn) {
      if (!this.enabled) return fn();
      var t0 = performance.now();
      var out = fn();
      if (out && typeof out.then === 'function') {
        return out.then(function (v) {
          XRay.log(label + ' (async)', Math.round(performance.now() - t0) + 'ms');
          return v;
        });
      }
      this.log(label, Math.round(performance.now() - t0) + 'ms');
      return out;
    },

    /* Actual paint-complete time — black screen ka asli end point */
    afterPaint: function (cb) {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () { cb(performance.now()); });
      });
    },

    set: function (key, value) {
      if (!this.enabled) return;
      this._rows[key] = value;
      this._renderHud();
    },

    _renderHud: function () {
      if (!this.enabled) return;
      if (!this._hudEl) {
        var el = document.createElement('div');
        el.id = 'sangam-xray-hud';
        el.style.cssText = [
          'position:fixed', 'right:8px', 'bottom:8px', 'z-index:2147483647',
          'font:11px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace',
          'background:rgba(8,8,8,.92)', 'color:#e5e7eb',
          'border:1px solid rgba(212,160,23,.45)', 'border-radius:8px',
          'padding:8px 10px', 'max-width:280px', 'pointer-events:none',
          'white-space:pre', 'box-shadow:0 6px 24px rgba(0,0,0,.6)'
        ].join(';');
        document.body.appendChild(el);
        this._hudEl = el;
      }
      var lines = ['\u2716 X-RAY'];
      for (var k in this._rows) {
        if (Object.prototype.hasOwnProperty.call(this._rows, k)) {
          lines.push(k + ': ' + this._rows[k]);
        }
      }
      this._hudEl.textContent = lines.join('\n');
    },

    init: function () {
      if (!this.enabled) return;
      // Long-task sensor: main thread 50ms+ block hua to pakdo
      try {
        var po = new PerformanceObserver(function (list) {
          list.getEntries().forEach(function (entry) {
            XRay.stats.longTasks++;
            if (entry.duration > XRay.stats.worstLongTask) {
              XRay.stats.worstLongTask = Math.round(entry.duration);
            }
            XRay.set('longtask', XRay.stats.longTasks + ' (worst ' + XRay.stats.worstLongTask + 'ms)');
          });
        });
        po.observe({ entryTypes: ['longtask'] });
      } catch (e) { /* browser support nahi hai, koi baat nahi */ }

      this.set('status', 'armed');
      this.log('x-ray online. ?debug=1 active.');
    }
  };

  /* ===================================================================
     2. CLOCK  —  zombie-proof timer registry
     =================================================================== */

  var _clocks = [];

  function Clock(onTick, intervalMs) {
    this._handle = null;
    this._onTick = onTick;
    this._interval = intervalMs || 1000;
    this._dead = false;
    _clocks.push(this);
  }

  Clock.prototype.start = function () {
    var self = this;
    this.stop();               // restart hone par purana hamesha marta hai
    if (this._dead) return this;
    this._handle = setInterval(function () {
      if (self._dead) { self.stop(); return; }   // self-destruct guard
      try { self._onTick(); } catch (e) { XRay.warn('clock tick error', e); }
    }, this._interval);
    XRay.set('clock', 'running (' + _clocks.length + ' registered)');
    return this;
  };

  Clock.prototype.stop = function () {
    if (this._handle) { clearInterval(this._handle); this._handle = null; }
    return this;
  };

  Clock.prototype.kill = function () {
    this._dead = true;
    this.stop();
    return this;
  };

  Clock.prototype.isRunning = function () { return this._handle !== null; };

  function createClock(onTick, intervalMs) {
    return new Clock(onTick, intervalMs);
  }

  /* NUKE: page par jo bhi clock kabhi bana tha, sab marega.
     Double-init se bacha hua zombie bhi yahin dafan hoga. */
  function stopAllClocks() {
    for (var i = 0; i < _clocks.length; i++) _clocks[i].kill();
    XRay.set('clock', 'STOPPED (' + _clocks.length + ' killed)');
    XRay.log('stopAllClocks -> ' + _clocks.length + ' clock(s) killed');
  }

  function liveClockCount() {
    var n = 0;
    for (var i = 0; i < _clocks.length; i++) if (_clocks[i].isRunning()) n++;
    return n;
  }

  /* Sentinel: agar quiz finish ho chuka hai par koi clock abhi bhi
     zinda hai, to debug mode me chillayega aur use maar dega. */
  function armClockSentinel(isFinishedFn) {
    var cleanChecks = 0;
    var handle = setInterval(function () {
      if (!isFinishedFn()) { cleanChecks = 0; return; }
      var live = liveClockCount();
      if (live > 0) {
        XRay.stats.clockAlarms++;
        XRay.warn('ZOMBIE CLOCK DETECTED — killing', live);
        XRay.set('ALARM', 'zombie clock x' + live + ' killed');
        stopAllClocks();
        cleanChecks = 0;
        return;
      }
      // Do baar lagataar saaf mila — sentinel ka kaam khatam,
      // khud bhi band ho jao (warna yeh khud ek leak ban jata).
      if (++cleanChecks >= 2) clearInterval(handle);
    }, 2000);
    window.addEventListener('pagehide', function () { clearInterval(handle); });
    return handle;
  }

  /* Tab band / navigate — har haal me sab band */
  window.addEventListener('pagehide', stopAllClocks);
  window.addEventListener('beforeunload', stopAllClocks);

  /* ===================================================================
     3. RENDERER  —  offscreen prerender + cache (black flash killer)
     =================================================================== */

  var MATH_RE = /(\\\(|\\\[|\$\$|\$[^$]+\$)/;

  function needsMath(str) {
    return typeof str === 'string' && MATH_RE.test(str);
  }

  function mapNeedsMath(map) {
    for (var k in map) {
      if (Object.prototype.hasOwnProperty.call(map, k) && needsMath(map[k])) return true;
    }
    return false;
  }

  function mathjaxReady() {
    if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise) {
      return window.MathJax.startup.promise;
    }
    // MathJax abhi load ho raha hai — thoda wait, phir bhi na aaye to aage badho
    return new Promise(function (resolve) {
      var tries = 0;
      (function poll() {
        if (window.MathJax && window.MathJax.typesetPromise) return resolve();
        if (++tries > 60) return resolve();          // ~3s baad haar maan lo
        setTimeout(poll, 50);
      })();
    });
  }

  function createRenderer() {
    var host = null;
    var cache = Object.create(null);
    var inflight = Object.create(null);
    var queue = Promise.resolve();      // MathJax ko serialize karna zaroori hai

    function getHost() {
      if (host) return host;
      host = document.createElement('div');
      host.id = 'sangam-prerender-host';
      host.setAttribute('aria-hidden', 'true');
      host.style.cssText = [
        'position:fixed', 'left:-100000px', 'top:0',
        'width:860px', 'max-width:860px',
        'visibility:hidden', 'pointer-events:none',
        'contain:layout style size', 'overflow:hidden', 'z-index:-1'
      ].join(';');
      document.body.appendChild(host);
      return host;
    }

    function get(key) { return cache[key] || null; }
    function has(key) { return !!cache[key]; }

    /* Ek question ka poora field-set offscreen typeset karke cache karo */
    function prepare(key, rawMap) {
      if (cache[key]) return Promise.resolve(cache[key]);
      if (inflight[key]) return inflight[key];

      var t0 = performance.now();

      var job = queue.then(function () {
        if (cache[key]) return cache[key];

        var wrap = document.createElement('div');
        var fields = [];

        for (var f in rawMap) {
          if (!Object.prototype.hasOwnProperty.call(rawMap, f)) continue;
          var slot = document.createElement('div');
          slot.setAttribute('data-field', f);
          slot.innerHTML = rawMap[f] == null ? '' : String(rawMap[f]);
          wrap.appendChild(slot);
          fields.push(f);
        }

        getHost().appendChild(wrap);

        /* Agar math hai hi nahi to MathJax ko bilkul mat chhedo.
           Lekin wrap phir bhi DOM me gaya — iska fayda: browser ne
           SVG parse kar liya aur <img> fetch shuru kar diya. Warm. */
        var typesetStep = mapNeedsMath(rawMap)
          ? mathjaxReady().then(function () {
              if (!(window.MathJax && window.MathJax.typesetPromise)) return null;
              return window.MathJax.typesetPromise([wrap]);
            }).catch(function (e) { XRay.warn('typeset failed for ' + key, e); })
          : Promise.resolve();

        return typesetStep.then(function () {
          var out = {};
          for (var i = 0; i < fields.length; i++) {
            var node = wrap.querySelector('[data-field="' + fields[i] + '"]');
            out[fields[i]] = node ? node.innerHTML : '';
          }
          cache[key] = out;

          /* MathJax ka internal reference chhod do, phir node hatao */
          try {
            if (window.MathJax && window.MathJax.typesetClear) {
              window.MathJax.typesetClear([wrap]);
            }
          } catch (e) { /* ignore */ }
          if (wrap.parentNode) wrap.parentNode.removeChild(wrap);

          XRay.stats.lastPrepareMs = Math.round(performance.now() - t0);
          XRay.set('prepare', key + ' ' + XRay.stats.lastPrepareMs + 'ms');
          return out;
        });
      });

      inflight[key] = job;
      job.then(function () { delete inflight[key]; },
               function () { delete inflight[key]; });
      queue = job.catch(function () {});
      return job;
    }

    /* Idle time me aage ke questions warm kar do */
    function warm(items) {
      if (!items || !items.length) return;
      var idle = window.requestIdleCallback || function (cb) { return setTimeout(cb, 120); };
      idle(function () {
        for (var i = 0; i < items.length; i++) {
          (function (it) {
            if (!it || cache[it.key]) return;
            prepare(it.key, it.raw);
            if (it.image) { var im = new Image(); im.src = it.image; }
          })(items[i]);
        }
      });
    }

    function stats() {
      var n = 0;
      for (var k in cache) if (Object.prototype.hasOwnProperty.call(cache, k)) n++;
      return { cached: n };
    }

    return {
      get: get,
      has: has,
      prepare: prepare,
      warm: warm,
      stats: stats,
      needsMath: needsMath
    };
  }

  /* ===================================================================
     Public API
     =================================================================== */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { XRay.init(); });
  } else {
    XRay.init();
  }

  return {
    debug: DEBUG,
    xray: XRay,
    createClock: createClock,
    stopAllClocks: stopAllClocks,
    liveClockCount: liveClockCount,
    armClockSentinel: armClockSentinel,
    createRenderer: createRenderer
  };
})();
