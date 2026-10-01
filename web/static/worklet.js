// AudioWorkletProcessor: downsamples the mic's native sample rate (usually
// 44.1/48kHz) to 16kHz mono, converts Float32 -> Int16 PCM, and posts
// ~100ms chunks back to the main thread for streaming over WebSocket.
//
// Simple linear-interpolation resampler -- good enough for 16kHz speech ASR
// (no external deps, runs off the main thread so UI stays smooth).

class PcmDownsampler extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetRate = (options.processorOptions && options.processorOptions.targetRate) || 16000;
    this.inputRate = sampleRate; // AudioWorkletGlobalScope's native context rate
    this.ratio = this.inputRate / this.targetRate;
    this.chunkSamples = Math.round(this.targetRate * 0.1); // ~100ms per message
    this.outBuffer = [];
    this.carry = 0; // fractional read position carried across render quanta
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel || channel.length === 0) return true;

    // Downsample this 128-sample render quantum by picking samples at the
    // fractional stride `ratio`, carrying leftover fractional position
    // across calls so the resampling stays phase-continuous.
    let pos = this.carry;
    while (pos < channel.length) {
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const s0 = channel[idx] || 0;
      const s1 = channel[idx + 1] !== undefined ? channel[idx + 1] : s0;
      const sample = s0 + (s1 - s0) * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      this.outBuffer.push(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
      pos += this.ratio;

      if (this.outBuffer.length >= this.chunkSamples) {
        const int16 = new Int16Array(this.outBuffer.splice(0, this.chunkSamples));
        this.port.postMessage(int16.buffer, [int16.buffer]);
      }
    }
    this.carry = pos - channel.length;
    return true;
  }
}

registerProcessor("pcm-downsampler", PcmDownsampler);
