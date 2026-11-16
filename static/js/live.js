/*
 * PrintQ live updates (ES5, no build step).
 * Polls the live endpoint with the version the page shows. A 204 means nothing changed.
 * A 200 carries a change: students get the page region swapped in place; operators get
 * a "Show" bar and apply the update only when they click it (or when the tab was hidden).
 */
(function () {
  'use strict';

  var configEl = document.getElementById('live-config');
  if (!configEl) { return; }

  var config;
  try {
    config = JSON.parse(configEl.textContent);
  } catch (e) {
    return;
  }

  var MIN_INTERVAL = 15000;
  var MAX_INTERVAL = 60000;
  var BACKOFF = 1.5;

  var interval = MIN_INTERVAL;
  var timer = null;
  var busy = false;          // check() and refresh() never overlap
  var applyQueued = false;   // Show clicked while a request was in flight
  var generation = 0;        // a stale refresh never overwrites a newer one
  var stopped = false;       // session ended or access denied: stop polling for good

  var appliedVersion = config.version;  // what the page currently shows; sent as v
  var notifiedVersion = null;           // notify mode: last version announced in the bar
  var pending = null;                   // notify mode: data waiting for the Show click

  var bar = document.getElementById('live-bar');
  var barText = document.getElementById('live-bar-text');
  var applyBtn = document.getElementById('live-apply');
  var announcer = document.getElementById('live-announcer');

  function backOff() {
    interval = Math.min(MAX_INTERVAL, interval * BACKOFF);
  }

  function schedule() {
    clearTimeout(timer);
    if (stopped || document.hidden || navigator.onLine === false) { return; }
    timer = setTimeout(check, interval);
  }

  function finish() {
    busy = false;
    if (applyQueued && pending) {
      applyQueued = false;
      busy = true;
      refresh(pending).then(finish);
      return;
    }
    applyQueued = false;
    schedule();
  }

  function announce(message) {
    if (!announcer || !message) { return; }
    announcer.textContent = '';
    setTimeout(function () { announcer.textContent = message; }, 50);
  }

  function showBar(data) {
    if (!bar) { return; }
    if (barText) { barText.textContent = data.message; }
    bar.hidden = false;
    announce(data.message);
  }

  function hideBar() {
    if (bar) { bar.hidden = true; }
  }

  // Re-fetch the current page and swap only #live-region.
  // Note: this page fetch also consumes any queued flash messages (rare, accepted).
  // Inline onsubmit confirms are attributes, so they survive the swap.
  // Caller must hold `busy`; returns a promise that never rejects.
  function refresh(data) {
    var gen = ++generation;
    return fetch(location.href, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (response) {
        if (response.redirected) {
          location.reload();
          return null;
        }
        if (!response.ok) { return null; }  // keep the bar/version; next poll retries
        return response.text();
      })
      .then(function (html) {
        if (html === null || html === undefined) { return; }
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var fresh = doc.getElementById('live-region');
        var current = document.getElementById('live-region');
        if (!fresh || !current) {
          location.reload();
          return;
        }
        if (gen !== generation) { return; }

        current.innerHTML = fresh.innerHTML;

        var newVersion = data.version;
        var freshConfig = doc.getElementById('live-config');
        if (freshConfig) {
          try {
            var parsed = JSON.parse(freshConfig.textContent);
            if (parsed && parsed.version) { newVersion = parsed.version; }
          } catch (e) { /* fall back to data.version */ }
        }
        appliedVersion = newVersion;
        notifiedVersion = null;
        pending = null;
        hideBar();

        if (window.PrintQ && window.PrintQ.bindCopyButtons) {
          window.PrintQ.bindCopyButtons(current);
        }
        announce(data.message);
      })
      .catch(function () { /* network error: next poll retries */ });
  }

  function handleChange(data) {
    if (config.mode === 'auto') {
      interval = MIN_INTERVAL;
      return refresh(data);
    }
    if (data.version === notifiedVersion) {
      // Same change already announced: keep the bar, back off quietly.
      backOff();
      return null;
    }
    interval = MIN_INTERVAL;
    notifiedVersion = data.version;
    pending = data;
    if (document.hidden) {
      return refresh(data);
    }
    showBar(data);
    return null;
  }

  function check() {
    if (busy || stopped) { return; }
    busy = true;
    clearTimeout(timer);

    var url = config.url + '?v=' + encodeURIComponent(appliedVersion);
    fetch(url, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (response) {
        if (response.redirected || response.status === 401 || response.status === 403) {
          stopped = true;   // session ended or no access: stop for good
          return null;
        }
        if (response.status === 204) {
          backOff();
          return null;
        }
        if (response.status === 200) {
          return response.json().then(handleChange);
        }
        backOff();          // 5xx and anything else: transient
        return null;
      })
      .catch(function () {
        backOff();          // network or JSON parse error: transient
      })
      .then(finish);
  }

  function onApply() {
    if (!pending) { return; }
    if (busy) {
      applyQueued = true;   // runs as soon as the in-flight request finishes
      return;
    }
    busy = true;
    clearTimeout(timer);
    refresh(pending).then(finish);
  }

  if (applyBtn) { applyBtn.addEventListener('click', onApply); }

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) {
      interval = MIN_INTERVAL;
      check();
    } else {
      clearTimeout(timer);
    }
  });

  window.addEventListener('online', function () { check(); });

  schedule();
})();
