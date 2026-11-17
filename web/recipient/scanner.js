"use strict";

(() => {
  const dialog = byId("qrDialog");
  const video = byId("qrCamera");
  const status = byId("scannerStatus");
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", {willReadFrequently: true});
  let active = null;
  let decoder = null;

  function loadDecoder() {
    if (!decoder) {
      decoder = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = root + "vendor/jsqr-1.4.0.js";
        script.onload = () => typeof window.jsQR === "function"
          ? resolve(window.jsQR) : reject(new Error("The QR reader could not load."));
        script.onerror = () => reject(new Error("The QR reader could not load."));
        document.head.append(script);
      }).catch(error => {
        decoder = null;
        throw error;
      });
    }
    return decoder;
  }

  function stopCamera() {
    if (!active) return;
    const run = active;
    active = null;
    clearTimeout(run.timer);
    run.stream?.getTracks().forEach(track => track.stop());
    video.srcObject = null;
  }

  async function readFrame(run, decode) {
    if (run !== active) return;
    if (video.readyState >= 2 && video.videoWidth && video.videoHeight) {
      const scale = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight));
      const width = Math.max(1, Math.round(video.videoWidth * scale));
      const height = Math.max(1, Math.round(video.videoHeight * scale));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      context.drawImage(video, 0, 0, width, height);
      const frame = context.getImageData(0, 0, width, height);
      const qr = decode(frame.data, width, height, {inversionAttempts: "dontInvert"});
      if (qr?.data && qr.data !== run.lastRead) {
        run.lastRead = qr.data;
        const invite = inviteCode(qr.data);
        if (!invite) {
          let scannedUrl;
          try { scannedUrl = new URL(qr.data.trim()); } catch (_) {}
          status.textContent = scannedUrl?.origin !== location.origin && scannedUrl
            ? "This QR is for a different workspace. Generate an invite from the dashboard for " + location.host + "."
            : scannedUrl?.pathname === "/join"
              ? "This is the public install QR. Generate a device invite in the demo dashboard to reconnect here."
              : "Scan a RakshaNetra device invite or paste its invite code.";
        } else {
          byId("invite").value = invite;
          status.textContent = "Checking the invite…";
          const valid = await checkScannedInvite();
          if (run !== active) return;
          if (valid) {
            dialog.close("scanned");
            return;
          }
          status.textContent = byId("inviteStatus").textContent;
        }
      }
    }
    run.timer = setTimeout(() => {
      readFrame(run, decode).catch(() => {
        if (run !== active) return;
        stopCamera();
        status.textContent = "The scan stopped. Paste the invite code or try again.";
      });
    }, 125);
  }

  byId("scanInvite").addEventListener("click", async () => {
    dialog.returnValue = "";
    status.textContent = "Opening your camera…";
    dialog.showModal();
    if (!context || !navigator.mediaDevices?.getUserMedia) {
      status.textContent = "Camera scanning needs a supported browser and an HTTPS connection. Paste the invite code to continue.";
      return;
    }
    const run = {stream: null, timer: null, lastRead: null};
    active = run;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: {facingMode: {ideal: "environment"}, width: {ideal: 1280}}
      });
      if (run !== active) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      run.stream = stream;
      video.srcObject = stream;
      const [decode] = await Promise.all([loadDecoder(), video.play()]);
      if (run !== active) return;
      status.textContent = "Place the QR code inside the frame.";
      await readFrame(run, decode);
    } catch (error) {
      if (run !== active) return;
      stopCamera();
      status.textContent = error.name === "NotAllowedError"
        ? "Camera access was denied. Paste the invite code to continue."
        : "Camera scanning is unavailable. Paste the invite code or try again.";
    }
  });

  dialog.addEventListener("close", () => {
    stopCamera();
    if (dialog.returnValue === "manual") byId("invite").focus();
    if (dialog.returnValue === "scanned") byId("enrollForm").requestSubmit();
  });
  window.addEventListener("pagehide", stopCamera);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && dialog.open) dialog.close();
  });
})();
