/* BigCat Learning Hub — i18n + TTS
 *
 * THREE MODES detected at load:
 *
 * 1. SPLIT mode (new, default for clean pages):
 *    - Page has <html lang="zh-CN"> or <html lang="en"> AND no data-zh attrs
 *    - Each language lives in its own file: foo.html (zh) / foo.en.html (en)
 *    - Lang toggle = navigate to the other file
 *    - TTS reads current page lang only
 *
 * 2. FULL mode (legacy embedded):
 *    - <html data-i18n-mode="full"> + data-zh / data-en attributes everywhere
 *    - Lang toggle = swap innerHTML in place
 *
 * 3. LEGACY mode (oldest pages):
 *    - Bilingual sections labeled by class/text, show/hide by language
 */
(function () {
  'use strict';

  const LANG_KEY = 'mmd-lang';
  const RATE_KEY = 'mmd-tts-rate';
  const RATES = [0.75, 1, 1.25, 1.5, 2];

  const fullMode = document.documentElement.getAttribute('data-i18n-mode') === 'full';
  const hasDataZh = document.querySelector('[data-zh][data-en]') !== null;
  const splitMode = !fullMode && !hasDataZh;

  // In split mode, page's own lang attribute is authoritative; localStorage is irrelevant.
  let currentLang;
  if (splitMode) {
    currentLang = (document.documentElement.lang || 'zh').toLowerCase().startsWith('en') ? 'en' : 'zh';
  } else {
    currentLang = localStorage.getItem(LANG_KEY) || 'zh';
  }

  // ---------- Split-mode lang toggle: navigate to other file ----------
  function otherLangUrl() {
    const p = window.location.pathname;
    // index.html -> index.en.html and vice versa
    // foo-day9.html -> foo-day9.en.html
    // foo-day9.en.html -> foo-day9.html
    if (/\.en\.html$/.test(p)) return p.replace(/\.en\.html$/, '.html');
    if (/\.html$/.test(p))     return p.replace(/\.html$/, '.en.html');
    // Bare dir like /repo/ -> /repo/index.en.html etc.
    if (p.endsWith('/')) return p + (currentLang === 'zh' ? 'index.en.html' : 'index.html');
    return p;
  }

  // ---------- Language ----------
  function applyLang(lang) {
    currentLang = lang;
    localStorage.setItem(LANG_KEY, lang);
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';

    if (fullMode) {
      document.querySelectorAll('[data-zh][data-en]').forEach((el) => {
        const html = el.getAttribute('data-' + lang);
        if (html != null && el.innerHTML !== html) el.innerHTML = html;
      });
      // Hide sections not wanted in this language
      document.querySelectorAll('[data-hide-in]').forEach((el) => {
        el.style.display = el.getAttribute('data-hide-in') === lang ? 'none' : '';
      });
    } else {
      // Legacy fallback: toggle bilingual sections by their label text
      const isEn = lang === 'en';
      document.querySelectorAll('.section').forEach((section) => {
        const label = section.querySelector('.section-label');
        if (!label) return;
        const t = label.textContent;
        if (/中文/.test(t)) section.style.display = isEn ? 'none' : '';
        else if (/English\s+Summary/i.test(t)) section.style.display = isEn ? '' : '';
      });
      document.querySelectorAll('.prompt-item').forEach((item) => {
        const lab = item.querySelector('.lang');
        if (!lab) return;
        const t = lab.textContent;
        if (/中文/.test(t)) item.style.display = isEn ? 'none' : '';
        else if (/English/i.test(t)) item.style.display = '';
      });
    }

    updateLangButton();
    if (tts.playing) tts.stop();
    rebuildSegments();
  }

  // ---------- TTS ----------
  const tts = {
    segments: [],
    idx: -1,
    playing: false,
    paused: false,
    rate: parseFloat(localStorage.getItem(RATE_KEY)) || 1,
    utter: null,
    audio: null,

    play() {
      if (this.paused) {
        if (this.audio) this.audio.play().catch(() => {});
        else if ('speechSynthesis' in window) speechSynthesis.resume();
        this.paused = false;
        updatePlayButton();
        return;
      }
      if (!this.segments.length) rebuildSegments();
      if (!this.segments.length) return;
      if (this.idx < 0 || this.idx >= this.segments.length) this.idx = 0;
      this.playing = true;
      this.speakCurrent();
      updatePlayButton();
    },

    speakCurrent() {
      if (this.idx >= this.segments.length) {
        this.stop();
        return;
      }
      const seg = this.segments[this.idx];
      document.querySelectorAll('.tts-active').forEach((el) => el.classList.remove('tts-active'));
      seg.classList.add('tts-active');
      seg.scrollIntoView({ behavior: 'smooth', block: 'center' });
      updateProgress();

      // Tear down any in-flight audio/utterance from the previous segment
      this._cancelPlayback();

      const hash = splitMode
        ? seg.getAttribute('data-tts')
        : seg.getAttribute('data-tts-' + currentLang);
      if (hash) {
        const url = `audio/${currentLang}/${hash}.mp3`;
        const audio = new Audio(url);
        audio.playbackRate = this.rate;
        audio.preload = 'auto';
        const myToken = ++this._token;
        const isStale = () => myToken !== this._token;
        audio.onended = () => {
          if (isStale() || !this.playing) return;
          this.idx++;
          this.speakCurrent();
        };
        audio.onerror = () => {
          if (isStale()) return;
          console.warn(`[mmd-tts] ${url} unavailable, falling back to Web Speech`);
          this.speakWebSpeech(seg, isStale);
        };
        audio.onloadedmetadata = () => {
          if (isStale()) return;
          setSeekEnabled(true);
          updateSeek(0, audio.duration);
        };
        audio.ontimeupdate = () => {
          if (isStale() || isScrubbing) return;
          updateSeek(audio.currentTime, audio.duration);
        };
        this.audio = audio;
        audio.play().catch((e) => {
          if (isStale()) return;
          console.warn(`[mmd-tts] audio.play() rejected (${e?.message || e}); falling back`);
          this.speakWebSpeech(seg, isStale);
        });
        return;
      }
      // No baked audio for this segment → Web Speech (no seek support)
      setSeekEnabled(false);
      updateSeek(0, 0);
      const myToken = ++this._token;
      this.speakWebSpeech(seg, () => myToken !== this._token);
    },

    seekTo(fraction) {
      if (!this.audio || !this.audio.duration) return;
      const t = Math.max(0, Math.min(this.audio.duration, fraction * this.audio.duration));
      this.audio.currentTime = t;
      updateSeek(t, this.audio.duration);
    },

    skip(deltaSeconds) {
      if (!this.audio || !isFinite(this.audio.duration)) return;
      const t = Math.max(0, Math.min(this.audio.duration, this.audio.currentTime + deltaSeconds));
      this.audio.currentTime = t;
      updateSeek(t, this.audio.duration);
    },

    speakWebSpeech(seg, isStale) {
      if (!('speechSynthesis' in window)) return;
      const text = seg.textContent.trim();
      if (!text) {
        if (isStale && isStale()) return;
        this.idx++;
        this.speakCurrent();
        return;
      }
      const u = new SpeechSynthesisUtterance(text);
      u.lang = currentLang === 'zh' ? 'zh-CN' : 'en-US';
      u.rate = this.rate;
      const voice = pickVoice(u.lang);
      if (voice) u.voice = voice;
      u.onend = () => {
        if ((isStale && isStale()) || !this.playing) return;
        this.idx++;
        this.speakCurrent();
      };
      u.onerror = (e) => {
        if (isStale && isStale()) return;
        if (e.error && e.error !== 'interrupted' && e.error !== 'canceled') {
          this.idx++;
          if (this.playing) this.speakCurrent();
        }
      };
      this.utter = u;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    },

    _token: 0,

    _cancelPlayback() {
      // Invalidate any pending callbacks from the previous segment
      this._token++;
      if (this.audio) {
        try { this.audio.pause(); } catch (e) {}
        this.audio.removeAttribute('src');
        this.audio.load?.();
        this.audio = null;
      }
      if ('speechSynthesis' in window) speechSynthesis.cancel();
    },

    pause() {
      if (!this.playing || this.paused) return;
      if (this.audio) {
        this.audio.pause();
      } else if ('speechSynthesis' in window) {
        speechSynthesis.pause();
      }
      this.paused = true;
      updatePlayButton();
    },

    stop() {
      this.playing = false;
      this.paused = false;
      this.idx = -1;
      this._cancelPlayback();
      document.querySelectorAll('.tts-active').forEach((el) => el.classList.remove('tts-active'));
      updatePlayButton();
      updateProgress();
      setSeekEnabled(false);
      updateSeek(0, 0);
    },

    next() {
      if (!this.segments.length) return;
      this.idx = Math.min(this.idx + 1, this.segments.length - 1);
      this.playing = true;
      this.paused = false;
      this.speakCurrent();
      updatePlayButton();
    },

    prev() {
      if (!this.segments.length) return;
      this.idx = Math.max(0, this.idx - 1);
      this.playing = true;
      this.paused = false;
      this.speakCurrent();
      updatePlayButton();
    },

    setRate(r) {
      this.rate = r;
      localStorage.setItem(RATE_KEY, String(r));
      updateRateLabel();
      // Audio supports live rate changes; Web Speech needs a restart
      if (this.audio && !this.audio.paused) {
        this.audio.playbackRate = r;
      } else if (this.playing && !this.paused && !this.audio) {
        this.speakCurrent();
      }
    },
  };

  function pickVoice(lang) {
    const voices = speechSynthesis.getVoices();
    if (!voices.length) return null;
    const prefix = lang.slice(0, 2).toLowerCase();
    const preferred = {
      zh: ['Tingting', 'Sinji', 'Meijia', 'Mei-Jia', 'Microsoft Xiaoxiao', 'Google 普通话', 'Yaoyao'],
      en: ['Samantha', 'Karen', 'Daniel', 'Microsoft Aria', 'Google US English', 'Alex'],
    }[prefix] || [];
    for (const name of preferred) {
      const v = voices.find((x) => x.name && x.name.includes(name));
      if (v) return v;
    }
    return voices.find((v) => v.lang && v.lang.toLowerCase().startsWith(prefix)) || null;
  }

  function rebuildSegments() {
    const visible = (el) => {
      if (el.closest('.mmd-controls')) return false;
      if (el.closest('nav')) return false;
      if (!el.textContent.trim()) return false;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      return true;
    };
    // Prefer per-group baked audio. Split-mode pages use simple `data-tts`
    // (one lang per page); legacy/embedded pages use `data-tts-zh` / `data-tts-en`.
    const ttsAttr = splitMode ? 'data-tts' : `data-tts-${currentLang}`;
    const tagged = document.querySelectorAll(`[${ttsAttr}]`);
    if (tagged.length > 0) {
      tts.segments = Array.from(tagged).filter(visible);
    } else {
      // Fallback for un-baked pages: read all headings + paragraphs via Web Speech
      const nodes = document.querySelectorAll('h1, h2, h3, p');
      tts.segments = Array.from(nodes).filter(visible);
    }
    updateProgress();
  }

  // ---------- UI ----------
  function injectStyles() {
    const css = `
body{padding-bottom:96px!important}
body.mmd-tts-on #search-fab{bottom:78px!important}
.mmd-controls{position:fixed;bottom:18px;right:18px;background:rgba(255,255,255,0.96);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-radius:28px;box-shadow:0 6px 24px rgba(0,0,0,0.15);padding:6px;display:flex;align-items:center;gap:2px;z-index:9999;font-family:-apple-system,"Noto Sans SC","Segoe UI",Roboto,sans-serif;border:1px solid rgba(0,0,0,0.06);user-select:none}
.mmd-controls button{background:transparent;border:none;cursor:pointer;padding:8px 11px;border-radius:18px;color:#2d3436;font-size:14px;font-weight:600;line-height:1;display:flex;align-items:center;justify-content:center;transition:background 0.15s,color 0.15s;min-width:36px;min-height:36px}
.mmd-controls button:hover{background:#f0f0f4}
.mmd-controls button.active{background:#6c5ce7;color:#fff}
.mmd-controls button:disabled{opacity:0.35;cursor:default}
.mmd-controls .sep{width:1px;height:18px;background:rgba(0,0,0,0.1);margin:0 3px}
.mmd-controls .rate{background:transparent;border:none;cursor:pointer;font-size:12px;color:#636e72;padding:6px 10px;border-radius:14px;font-weight:600;font-variant-numeric:tabular-nums;min-width:34px;text-align:center}
.mmd-controls .rate:hover{background:#f0f0f4}
.mmd-controls .progress{font-size:11px;color:#8a93a0;padding:0 6px;min-width:38px;text-align:center;font-variant-numeric:tabular-nums;letter-spacing:0.5px}
.mmd-controls .skip{font-size:11px;font-weight:700;letter-spacing:0;padding:8px 8px;min-width:auto}
.mmd-lang-toggle{position:fixed;top:16px;right:16px;z-index:10000;background:rgba(255,255,255,0.96);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-radius:22px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:4px;display:flex;align-items:center;gap:0;font-family:-apple-system,"Noto Sans SC","Segoe UI",Roboto,sans-serif;border:1px solid rgba(0,0,0,0.06);user-select:none}
.mmd-lang-toggle button{background:transparent;border:none;cursor:pointer;font-size:13px;font-weight:700;letter-spacing:0.5px;padding:6px 14px;border-radius:18px;color:#636e72;transition:background 0.15s,color 0.15s;line-height:1;min-width:38px}
.mmd-lang-toggle button.active{background:#6c5ce7;color:#fff}
.mmd-lang-toggle button:not(.active):hover{background:#f0f0f4;color:#2d3436}
.mmd-controls .seek-wrap{display:flex;align-items:center;gap:6px;padding:0 4px;min-width:140px}
.mmd-controls .seek-bar{flex:1;height:18px;cursor:pointer;position:relative;display:flex;align-items:center;touch-action:none}
.mmd-controls .seek-bar.disabled{cursor:not-allowed;opacity:0.4}
.mmd-controls .seek-track{position:absolute;left:0;right:0;top:50%;height:4px;margin-top:-2px;background:rgba(0,0,0,0.12);border-radius:2px}
.mmd-controls .seek-fill{position:absolute;left:0;top:50%;height:4px;margin-top:-2px;background:#6c5ce7;border-radius:2px;width:0%;pointer-events:none}
.mmd-controls .seek-knob{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;background:#6c5ce7;transform:translate(-50%,-50%);box-shadow:0 1px 3px rgba(0,0,0,0.25);left:0;opacity:0;transition:opacity 0.15s;pointer-events:none}
.mmd-controls .seek-bar:hover .seek-knob,.mmd-controls .seek-bar.scrubbing .seek-knob{opacity:1}
.mmd-controls .seek-time{font-size:11px;color:#8a93a0;font-variant-numeric:tabular-nums;min-width:36px;text-align:right}
.tts-active{background:rgba(108,92,231,0.10)!important;box-shadow:0 0 0 2px rgba(108,92,231,0.35),0 0 0 6px rgba(108,92,231,0.08);border-radius:6px;transition:background 0.2s,box-shadow 0.2s;scroll-margin-top:80px;scroll-margin-bottom:120px}
@media(max-width:600px){
  .mmd-controls{bottom:10px;right:10px;left:10px;justify-content:center;border-radius:22px;padding:5px}
  .mmd-controls button{min-width:32px;min-height:32px;padding:6px 8px}
  .mmd-controls .progress{display:none}
  .mmd-controls .skip{padding:6px 5px;font-size:10px}
  .mmd-controls .seek-wrap{min-width:0;flex:1}
  .mmd-controls .seek-time{display:none}
  .mmd-lang-toggle{top:10px;right:10px}
  .mmd-lang-toggle button{padding:5px 10px;font-size:12px;min-width:32px}
}
`;
    const s = document.createElement('style');
    s.textContent = css;
    document.head.appendChild(s);
  }

  function injectControls() {
    // Top-right: language toggle (separated from playback controls)
    const langBar = document.createElement('div');
    langBar.className = 'mmd-lang-toggle';
    langBar.setAttribute('role', 'group');
    langBar.setAttribute('aria-label', 'Language toggle');
    langBar.innerHTML = `
      <button data-action="lang-zh" aria-label="中文">中文</button>
      <button data-action="lang-en" aria-label="English">EN</button>
    `;
    document.body.appendChild(langBar);

    // Bottom-right: playback controls
    const bar = document.createElement('div');
    bar.className = 'mmd-controls';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Audio controls');
    bar.innerHTML = `
      <button data-action="prev" title="上一段 / Previous" aria-label="Previous segment">⏮</button>
      <button class="skip" data-action="back10" title="后退 10 秒 / Back 10s" aria-label="Back 10 seconds">−10</button>
      <button class="play-btn" data-action="play" title="播放 / 暂停" aria-label="Play or pause">▶</button>
      <button class="skip" data-action="fwd10" title="快进 10 秒 / Forward 10s" aria-label="Forward 10 seconds">+10</button>
      <button data-action="next" title="下一段 / Next" aria-label="Next segment">⏭</button>
      <button data-action="stop" title="停止 / Stop" aria-label="Stop">■</button>
      <span class="progress" aria-live="polite">0/0</span>
      <div class="seek-wrap">
        <div class="seek-bar disabled" role="slider" aria-label="Seek within current segment" tabindex="0" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="seek-track"></div>
          <div class="seek-fill"></div>
          <div class="seek-knob"></div>
        </div>
        <span class="seek-time">0:00</span>
      </div>
      <span class="sep"></span>
      <button class="rate" data-action="rate" title="语速 / Speed" aria-label="Playback speed">1×</button>
    `;
    document.body.appendChild(bar);
    document.body.classList.add('mmd-tts-on');

    langBar.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      const targetLang = btn.dataset.action === 'lang-zh' ? 'zh' : 'en';
      if (splitMode) {
        // Navigate to the other-language file if it differs from current
        if (targetLang !== currentLang) window.location.assign(otherLangUrl());
      } else {
        applyLang(targetLang);
      }
    });

    bar.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      switch (btn.dataset.action) {
        case 'play': (tts.playing && !tts.paused) ? tts.pause() : tts.play(); break;
        case 'stop': tts.stop(); break;
        case 'next': tts.next(); break;
        case 'prev': tts.prev(); break;
        case 'back10': tts.skip(-10); break;
        case 'fwd10': tts.skip(10); break;
        case 'rate': {
          const i = RATES.indexOf(tts.rate);
          tts.setRate(RATES[(i + 1) % RATES.length]);
          break;
        }
      }
    });
  }

  function updatePlayButton() {
    const btn = document.querySelector('.mmd-controls .play-btn');
    if (!btn) return;
    btn.textContent = (tts.playing && !tts.paused) ? '⏸' : '▶';
    btn.classList.toggle('active', tts.playing && !tts.paused);
  }

  function updateProgress() {
    const el = document.querySelector('.mmd-controls .progress');
    if (!el) return;
    const total = tts.segments.length;
    const cur = tts.idx >= 0 ? tts.idx + 1 : 0;
    el.textContent = `${cur}/${total}`;
  }

  function updateLangButton() {
    const zhBtn = document.querySelector('.mmd-lang-toggle [data-action="lang-zh"]');
    const enBtn = document.querySelector('.mmd-lang-toggle [data-action="lang-en"]');
    if (!zhBtn || !enBtn) return;
    zhBtn.classList.toggle('active', currentLang === 'zh');
    enBtn.classList.toggle('active', currentLang === 'en');
  }

  function updateRateLabel() {
    const el = document.querySelector('.mmd-controls .rate');
    if (el) el.textContent = `${tts.rate}×`;
  }

  // ---------- Seek bar ----------
  let isScrubbing = false;

  function fmtTime(s) {
    if (!isFinite(s) || s < 0) s = 0;
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  function updateSeek(currentTime, duration) {
    const fill = document.querySelector('.mmd-controls .seek-fill');
    const knob = document.querySelector('.mmd-controls .seek-knob');
    const time = document.querySelector('.mmd-controls .seek-time');
    const bar = document.querySelector('.mmd-controls .seek-bar');
    if (!fill) return;
    const pct = duration > 0 ? (currentTime / duration) * 100 : 0;
    fill.style.width = pct + '%';
    if (knob) knob.style.left = pct + '%';
    if (time) time.textContent = duration > 0
      ? `${fmtTime(currentTime)} / ${fmtTime(duration)}`
      : '0:00';
    if (bar) bar.setAttribute('aria-valuenow', String(Math.round(pct)));
  }

  function setSeekEnabled(enabled) {
    const bar = document.querySelector('.mmd-controls .seek-bar');
    if (bar) bar.classList.toggle('disabled', !enabled);
  }

  function wireSeekBar() {
    const bar = document.querySelector('.mmd-controls .seek-bar');
    if (!bar) return;

    const fractionFromEvent = (e) => {
      const rect = bar.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
      return Math.max(0, Math.min(1, x / rect.width));
    };

    const startScrub = (e) => {
      if (bar.classList.contains('disabled')) return;
      e.preventDefault();
      isScrubbing = true;
      bar.classList.add('scrubbing');
      const f = fractionFromEvent(e);
      if (tts.audio) updateSeek(f * tts.audio.duration, tts.audio.duration);
    };
    const moveScrub = (e) => {
      if (!isScrubbing) return;
      const f = fractionFromEvent(e);
      if (tts.audio) updateSeek(f * tts.audio.duration, tts.audio.duration);
    };
    const endScrub = (e) => {
      if (!isScrubbing) return;
      const f = fractionFromEvent(e.changedTouches ? e : (e.type === 'mouseup' ? e : e));
      tts.seekTo(f);
      isScrubbing = false;
      bar.classList.remove('scrubbing');
    };

    bar.addEventListener('mousedown', startScrub);
    bar.addEventListener('touchstart', startScrub, { passive: false });
    window.addEventListener('mousemove', moveScrub);
    window.addEventListener('touchmove', moveScrub, { passive: false });
    window.addEventListener('mouseup', endScrub);
    window.addEventListener('touchend', endScrub);

    // Keyboard support (arrows = ±5s)
    bar.addEventListener('keydown', (e) => {
      if (!tts.audio || bar.classList.contains('disabled')) return;
      const step = 5;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        tts.audio.currentTime = Math.max(0, tts.audio.currentTime - step);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        tts.audio.currentTime = Math.min(tts.audio.duration, tts.audio.currentTime + step);
      }
    });
  }

  // ---------- Init ----------
  function init() {
    injectStyles();
    injectControls();
    wireSeekBar();
    if (splitMode) {
      // In split mode the page is already the right language; just mark the
      // toggle UI to reflect that and skip attribute-based content swap.
      const zhBtn = document.querySelector('.mmd-lang-toggle [data-action="lang-zh"]');
      const enBtn = document.querySelector('.mmd-lang-toggle [data-action="lang-en"]');
      if (zhBtn && enBtn) {
        zhBtn.classList.toggle('active', currentLang === 'zh');
        enBtn.classList.toggle('active', currentLang === 'en');
      }
    } else {
      applyLang(currentLang);
    }
    updateRateLabel();
    rebuildSegments();
    if ('speechSynthesis' in window && speechSynthesis.getVoices().length === 0) {
      speechSynthesis.addEventListener?.('voiceschanged', () => {}, { once: true });
    }
    window.addEventListener('beforeunload', () => {
      if ('speechSynthesis' in window) speechSynthesis.cancel();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
