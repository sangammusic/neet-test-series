/* =====================================================================
   SANGAM QUIZ CORE v5.0
   ---------------------------------------------------------------------
   1. X-RAY  : ?debug=1 live diagnostic HUD.
   2. CLOCK  : Zombie-proof single timer registry with automatic cleanup.
   3. RENDER : Offscreen MathJax prerender & cache with automatic
               citation-artifact stripping ([span_0](start_span) etc.),
               bare-LaTeX normalization, and post-DOM-injection fallback
               typeset so equations, chemical formulas, and SVGs always
               render cleanly without raw backslashes.
   ===================================================================== */

window.SangamQuiz = (function () {
  'use strict';

  var DEBUG = false;
  try {
    DEBUG = new URLSearchParams(window.location.search).has('debug');
  } catch (e) { DEBUG = false; }

  /* ===================================================================
     1. X-RAY  —  Diagnostics
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
      } catch (e) {}

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
    this.stop();
    if (this._dead) return this;
    this._handle = setInterval(function () {
      if (self._dead) { self.stop(); return; }
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
      if (++cleanChecks >= 2) clearInterval(handle);
    }, 2000);
    window.addEventListener('pagehide', function () { clearInterval(handle); });
    return handle;
  }

  window.addEventListener('pagehide', stopAllClocks);
  window.addEventListener('beforeunload', stopAllClocks);

  /* ===================================================================
     3. RENDERER  —  Text Sanitizer + Offscreen MathJax Prerender + Live Fallback
     =================================================================== */

  var SPAN_ARTIFACT_RE = /\[span_\d+\]\((?:start_span|end_span)\)|\[cite_start\]|\[cite_end\]|\[cite:\s*[\d,\s\-]+\]/gi;
  var MATH_DELIM_RE = /(\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]|\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g;
  var BARE_LATEX_RE = /\\(frac\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|sqrt(?:\[[^\]]*\])?\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|overrightarrow\s*\{[^{}]+\}|vec\s*\{[^{}]+\}|hat\s*\{[^{}]+\}|bar\s*\{[^{}]+\}|text\s*\{[^{}]+\}|mathrm\s*\{[^{}]+\}|alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega|times|div|pm|mp|cdot|leq|geq|neq|approx|equiv|propto|infty|partial|nabla|int|sum|prod|rightarrow|leftarrow|leftrightarrow|Rightarrow|Leftarrow|Leftrightarrow|rightleftharpoons|uparrow|downarrow|circ|degree|angle|triangle|perp|parallel)(?:\s*[\^_]\s*(?:\{[^{}]+\}|[A-Za-z0-9+\-]+))*/g;

  function sanitizeMathString(raw) {
    if (raw == null) return '';
    var s = String(raw);
    if (!s) return '';

    // 1. Strip AI citation / span markers
    s = s.replace(SPAN_ARTIFACT_RE, '');

    // 2. Fix double-escaped backslashes before delimiters and common LaTeX commands
    s = s.replace(/\\\\([()\[\]])/g, '\\$1');
    s = s.replace(/\\\\(frac|sqrt|times|div|pm|cdot|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|omega|Delta|Omega|rightarrow|rightleftharpoons|leq|geq|neq|approx|infty|int|sum|vec|hat|mathrm|text)\b/g, '\\$1');

    // 3. Wrap bare LaTeX commands that sit outside math delimiters into \( ... \)
    if (s.indexOf('\\') !== -1) {
      var parts = s.split(MATH_DELIM_RE);
      for (var i = 0; i < parts.length; i++) {
        // Odd indices are matched math delimiters; even indices are outside math mode
        if (i % 2 === 0 && parts[i].indexOf('\\') !== -1) {
          parts[i] = parts[i].replace(BARE_LATEX_RE, function (m) {
            return '\\(' + m + '\\)';
          });
        }
      }
      s = parts.join('');
    }
    return s;
  }

  var NEEDS_MATH_RE = /(\\\(|\\\[|\$\$|\$[^$]+\$|\\frac|\\sqrt|\\times|\\alpha|\\beta|\\gamma|\\delta|\\theta|\\lambda|\\mu|\\pi|\\sigma|\\omega|\\rightarrow|\\rightleftharpoons)/;

  function needsMath(str) {
    return typeof str === 'string' && NEEDS_MATH_RE.test(str);
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
    return new Promise(function (resolve) {
      var tries = 0;
      (function poll() {
        if (window.MathJax && window.MathJax.typesetPromise) return resolve();
        if (++tries > 80) return resolve();
        setTimeout(poll, 50);
      })();
    });
  }

  function typesetElement(el) {
    if (!el) return Promise.resolve();
    return mathjaxReady().then(function () {
      if (window.MathJax && window.MathJax.typesetPromise) {
        return window.MathJax.typesetPromise([el]).catch(function () {});
      }
    });
  }

  function createRenderer() {
    var host = null;
    var cache = Object.create(null);
    var inflight = Object.create(null);
    var queue = Promise.resolve();

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

    function prepare(key, rawMap) {
      if (cache[key]) return Promise.resolve(cache[key]);
      if (inflight[key]) return inflight[key];

      var t0 = performance.now();

      var job = queue.then(function () {
        if (cache[key]) return cache[key];

        var wrap = document.createElement('div');
        var fields = [];
        var cleanedMap = {};

        for (var f in rawMap) {
          if (!Object.prototype.hasOwnProperty.call(rawMap, f)) continue;
          var cleaned = sanitizeMathString(rawMap[f]);
          cleanedMap[f] = cleaned;
          var slot = document.createElement('div');
          slot.setAttribute('data-field', f);
          slot.innerHTML = cleaned;
          wrap.appendChild(slot);
          fields.push(f);
        }

        getHost().appendChild(wrap);

        var hasMathContent = mapNeedsMath(cleanedMap);
        var typesetStep = hasMathContent
          ? mathjaxReady().then(function () {
              if (!(window.MathJax && window.MathJax.typesetPromise)) return null;
              return window.MathJax.typesetPromise([wrap]);
            }).catch(function (e) { XRay.warn('typeset failed for ' + key, e); })
          : Promise.resolve();

        return typesetStep.then(function () {
          var out = {};
          var mathReadyNow = !!(window.MathJax && window.MathJax.typesetPromise);
          for (var i = 0; i < fields.length; i++) {
            var node = wrap.querySelector('[data-field="' + fields[i] + '"]');
            out[fields[i]] = node ? node.innerHTML : '';
          }
          // Only cache permanently if there was no math OR MathJax had finished loading
          if (!hasMathContent || mathReadyNow) {
            cache[key] = out;
          }

          try {
            if (window.MathJax && window.MathJax.typesetClear) {
              window.MathJax.typesetClear([wrap]);
            }
          } catch (e) {}
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
      needsMath: needsMath,
      sanitize: sanitizeMathString,
      typesetElement: typesetElement
    };
  }

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
    createRenderer: createRenderer,
    sanitizeMathString: sanitizeMathString,
    typesetElement: typesetElement
  };
})();
