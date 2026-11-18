/* Operator QR scanner. Uses the browser's BarcodeDetector when it has one (Chrome on Android, macOS, ChromeOS)
   and falls back to the vendored jsQR everywhere else (desktop Windows, iPhone Safari, Firefox).
   The scanned text is only ever sent to the server's lookup page, which validates it. */
(function () {
  'use strict';

  var root = document.getElementById('scan-root');
  if (!root) return;

  var lookupUrl = root.dataset.lookup;
  var stage = document.getElementById('scan-stage');
  var video = document.getElementById('scan-video');
  var statusEl = document.getElementById('scan-status');
  var startBtn = document.getElementById('scan-start');
  var stopBtn = document.getElementById('scan-stop');
  var torchBtn = document.getElementById('scan-torch');
  var fileInput = document.getElementById('scan-file');

  var canvas = document.createElement('canvas');
  var ctx = canvas.getContext('2d', { willReadFrequently: true });
  var stream = null;
  var track = null;
  var timer = 0;
  var torchOn = false;
  var found = false;
  var detector = null;

  if ('BarcodeDetector' in window) {
    try { detector = new window.BarcodeDetector({ formats: ['qr_code'] }); } catch (e) { detector = null; }
  }

  function say(text, isError) {
    statusEl.textContent = text;
    statusEl.classList.toggle('text-danger', !!isError);
  }

  function goTo(text) {
    if (found) return;
    found = true;
    stopCamera();
    if (navigator.vibrate) { try { navigator.vibrate(60); } catch (e) { /* not supported */ } }
    say('Code found. Opening the order…');
    window.location.href = lookupUrl + '?code=' + encodeURIComponent(text);
  }

  // Try to read a QR code from a video frame, an image bitmap or a canvas. Resolves to the text or null.
  async function decode(source, width, height, maxSide) {
    if (detector) {
      try {
        var hits = await detector.detect(source);
        if (hits && hits.length) return hits[0].rawValue;
      } catch (e) { /* fall through to jsQR */ }
    }
    if (typeof window.jsQR === 'function') {
      var scale = Math.min(1, maxSide / Math.max(width, height));
      var w = Math.max(1, Math.round(width * scale));
      var h = Math.max(1, Math.round(height * scale));
      canvas.width = w;
      canvas.height = h;
      ctx.drawImage(source, 0, 0, w, h);
      var result = window.jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: 'dontInvert' });
      if (result && result.data) return result.data;
    }
    return null;
  }

  function loop() {
    if (!stream || found) return;
    if (video.readyState >= 2 && video.videoWidth) {
      decode(video, video.videoWidth, video.videoHeight, 720).then(function (text) {
        if (text) { goTo(text); } else { timer = setTimeout(loop, 120); }
      }, function () { timer = setTimeout(loop, 250); });
    } else {
      timer = setTimeout(loop, 120);
    }
  }

  async function startCamera() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      say('The camera needs a secure (HTTPS) connection. Type the code below or scan from a photo.', true);
      return;
    }
    startBtn.disabled = true;
    say('Starting the camera…');
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false
      });
    } catch (err) {
      startBtn.disabled = false;
      var name = err && err.name;
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        say('Camera permission was blocked. Allow it for this site in your browser settings, or type the code below.', true);
      } else if (name === 'NotFoundError' || name === 'OverconstrainedError') {
        say('No camera was found on this device. Type the code below or scan from a photo.', true);
      } else {
        say('The camera could not start. Type the code below or scan from a photo.', true);
      }
      return;
    }
    video.srcObject = stream;
    try { await video.play(); } catch (e) { /* autoplay is already set; ignore */ }
    stage.hidden = false;
    startBtn.hidden = true;
    startBtn.disabled = false;
    stopBtn.hidden = false;
    track = stream.getVideoTracks()[0];
    var caps = track && track.getCapabilities ? track.getCapabilities() : {};
    torchBtn.hidden = !caps.torch;
    say('Point the camera at the QR code.');
    loop();
  }

  function stopCamera() {
    clearTimeout(timer);
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); }
    stream = null;
    track = null;
    video.srcObject = null;
    stage.hidden = true;
    stopBtn.hidden = true;
    torchBtn.hidden = true;
    startBtn.hidden = false;
    torchOn = false;
    torchBtn.setAttribute('aria-pressed', 'false');
    if (!found) say('Camera is off.');
  }

  startBtn.addEventListener('click', startCamera);
  stopBtn.addEventListener('click', stopCamera);

  torchBtn.addEventListener('click', async function () {
    if (!track) return;
    torchOn = !torchOn;
    try {
      await track.applyConstraints({ advanced: [{ torch: torchOn }] });
      torchBtn.setAttribute('aria-pressed', String(torchOn));
    } catch (e) {
      torchOn = !torchOn;
      say('This device would not turn the torch on.', true);
    }
  });

  // A photo of the QR code: the way in when the live camera is blocked or unavailable.
  fileInput.addEventListener('change', async function () {
    var file = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    say('Reading the photo…');
    try {
      var bitmap = await createImageBitmap(file);
      var text = await decode(bitmap, bitmap.width, bitmap.height, 1280);
      if (bitmap.close) bitmap.close();
      if (text) {
        goTo(text);
      } else {
        say('No QR code was found in that photo. Move closer, hold steady and try again, or type the code below.', true);
      }
    } catch (e) {
      say('That photo could not be read. Try again or type the code below.', true);
    }
  });

  // Never leave the camera running in the background.
  document.addEventListener('visibilitychange', function () { if (document.hidden && stream) stopCamera(); });
  window.addEventListener('pagehide', stopCamera);
})();
