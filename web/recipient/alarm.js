"use strict";

class RecipientAlarm {
  constructor() {
    this.audio = null;
    this.source = null;
    this.timer = null;
    this.armed = false;
    this.vibrationAccepted = false;
  }

  async arm() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (Audio) {
      if (!this.audio) this.audio = new Audio();
      await this.audio.resume();
      if (this.audio.state !== "running") throw new Error("Tap inside the app again to allow sound.");
    }
    this.armed = true;
    return !!Audio;
  }

  start() {
    if (!this.armed || document.hidden || this.timer !== null) return;
    if (this.audio?.state === "running") {
      const rate = this.audio.sampleRate;
      const buffer = this.audio.createBuffer(1, rate * 3, rate);
      const samples = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index++) {
        const time = index / rate;
        const pulse = time < 0.4 ? time : time >= 0.6 && time < 1 ? time - 0.6 : -1;
        if (pulse < 0) continue;
        const envelope = Math.min(1, pulse / 0.015, (0.4 - pulse) / 0.015);
        samples[index] = 0.12 * envelope * (
          Math.sin(2 * Math.PI * 440 * time) + Math.sin(2 * Math.PI * 480 * time)
        );
      }
      this.source = this.audio.createBufferSource();
      this.source.buffer = buffer;
      this.source.loop = true;
      this.source.connect(this.audio.destination);
      this.source.start();
    }
    const vibrate = () => {
      try { this.vibrationAccepted = navigator.vibrate?.([600, 250, 600]) === true; }
      catch (_) { this.vibrationAccepted = false; }
    };
    vibrate();
    this.timer = setInterval(vibrate, 3000);
  }

  stop() {
    if (this.source) {
      this.source.stop();
      this.source.disconnect();
      this.source = null;
    }
    clearInterval(this.timer);
    this.timer = null;
    try { navigator.vibrate?.(0); } catch (_) {}
  }
}
