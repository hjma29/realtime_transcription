const dictateBtn = document.getElementById("dictateBtn");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const noteChartEl = document.getElementById("noteChart");
const draftBadgeEl = document.getElementById("draftBadge");
const attestBtn = document.getElementById("attestBtn");
const attestNoteEl = document.getElementById("attestNote");
const footerNoteEl = document.getElementById("footerNote");
const timerEl = document.getElementById("timer");
const sessionStartEl = document.getElementById("sessionStart");
const micPillEl = document.getElementById("micPill");
const waveEl = document.getElementById("wave");

const lastTtfsEl = document.getElementById("lastTtfs");
const meanTtfsEl = document.getElementById("meanTtfs");
const p95TtfsEl = document.getElementById("p95Ttfs");
const segCountEl = document.getElementById("segCount");
const lastStructuringEl = document.getElementById("lastStructuring");
const lastTtftEl = document.getElementById("lastTtft");
const lastTpsEl = document.getElementById("lastTps");
const sourceJsonEl = document.getElementById("sourceJson");

let audioCtx = null;
let micStream = null;
let workletNode = null;
let ws = null;
let recording = false;
let interimBlock = null;
let interimStartedAtS = 0;
let captureStartMs = null; // wall-clock t=0 for transcript timestamps

// ---- session clock + mic level wave ---------------------------------------
const WAVE_BARS = 32;
let analyser = null;
let waveRaf = null;
let timerId = null;
let recordStartMs = null;
const levels = new Array(WAVE_BARS).fill(0);

function fmtClock(totalSeconds) {
  const m = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const s = String(Math.floor(totalSeconds % 60)).padStart(2, "0");
  return `${m}:${s}`;
}

function fmtStart(d) {
  const day = d.toLocaleDateString("en-US", { month: "long", day: "numeric" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${day} \u00B7 ${time}`;
}

function startClock() {
  recordStartMs = Date.now();
  sessionStartEl.textContent = fmtStart(new Date(recordStartMs));
  timerEl.textContent = "00:00";
  clearInterval(timerId);
  timerId = setInterval(() => {
    timerEl.textContent = fmtClock((Date.now() - recordStartMs) / 1000);
  }, 250);
}

function stopClock() {
  clearInterval(timerId); // leave the final length on screen
  timerId = null;
}

function drawWave() {
  const dpr = window.devicePixelRatio || 1;
  const w = waveEl.clientWidth, h = waveEl.clientHeight;
  if (waveEl.width !== w * dpr) { waveEl.width = w * dpr; waveEl.height = h * dpr; }
  const ctx = waveEl.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const step = w / WAVE_BARS, barW = Math.max(2, step * 0.55);
  ctx.fillStyle = analyser ? "#22c55e" : "#d1d5db";
  for (let i = 0; i < WAVE_BARS; i++) {
    const bh = Math.max(3, levels[i] * h);
    ctx.beginPath();
    ctx.roundRect(i * step + (step - barW) / 2, (h - bh) / 2, barW, bh, barW / 2);
    ctx.fill();
  }
}

function startWave(source) {
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const buf = new Uint8Array(analyser.fftSize);
  let last = 0;
  micPillEl.classList.add("live");
  const tick = (now) => {
    if (!analyser) return;
    if (now - last > 60) { // scroll one bar roughly every 60 ms
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
      levels.push(Math.min(1, Math.sqrt(sum / buf.length) * 4));
      levels.shift();
      last = now;
    }
    drawWave();
    waveRaf = requestAnimationFrame(tick);
  };
  waveRaf = requestAnimationFrame(tick);
}

function stopWave() {
  analyser = null;
  if (waveRaf) cancelAnimationFrame(waveRaf);
  waveRaf = null;
  levels.fill(0);
  micPillEl.classList.remove("live");
  drawWave();
}
drawWave();

function fmtMs(ms) {
  if (ms === null || ms === undefined) return "—";
  return `${Math.round(ms)}ms`;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function resetUi() {
  transcriptEl.innerHTML = '<span class="placeholder">Press "Start recording" and speak…</span>';
  noteChartEl.innerHTML = '<p class="placeholder">Structured note will populate here as you dictate…</p>';
  draftBadgeEl.textContent = "DRAFT — Pending Review";
  attestBtn.disabled = true;
  attestBtn.classList.remove("enabled");
  attestNoteEl.textContent = "";
  footerNoteEl.textContent = "";
  lastTtfsEl.textContent = "—";
  meanTtfsEl.textContent = "—";
  p95TtfsEl.textContent = "—";
  segCountEl.textContent = "0";
  lastStructuringEl.textContent = "—";
  lastTtftEl.textContent = "—";
  lastTpsEl.textContent = "—";
  sourceJsonEl.textContent = "{}";
  interimBlock = null;
  interimStartedAtS = 0;
  captureStartMs = null;
}

// Renders the structured note the way real ambient-scribe products (DAX
// Copilot, Abridge, Suki, Nabla, Ambience) present a draft for sign-off:
// labeled chart sections instead of raw JSON, billing codes as distinct
// chips, and an explicit "requires human review" flag -- never implying
// the note is final or already in the chart.
function renderNoteChart(note) {
  if (!note) return;
  const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const section = (label, bodyHtml) =>
    `<div class="ehr-section"><div class="ehr-section-label">${label}</div><div class="ehr-section-body">${bodyHtml}</div></div>`;

  const examList = (note.exam_findings || []).length
    ? `<ul>${note.exam_findings.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`
    : '<span class="placeholder">—</span>';
  const billingChips = (note.draft_billing_codes || []).length
    ? `<div class="billing-codes">${note.draft_billing_codes.map((c) => `<span class="billing-chip">${esc(c)}</span>`).join("")}</div>`
    : '<span class="placeholder">—</span>';

  noteChartEl.innerHTML =
    section("Chief Complaint", esc(note.chief_complaint) || '<span class="placeholder">—</span>') +
    section("History of Present Illness", esc(note.history_of_present_illness) || '<span class="placeholder">—</span>') +
    section("Exam Findings", examList) +
    section("Assessment", esc(note.assessment) || '<span class="placeholder">—</span>') +
    section("Plan", esc(note.plan) || '<span class="placeholder">—</span>') +
    section(
      "Draft Billing Codes (ICD-10-CM / CPT)",
      billingChips + (note.requires_human_review ? '<div class="review-flag">⚠ Requires certified-coder review before submission</div>' : "")
    );

  const hasContent = Object.values(note).some((v) => (Array.isArray(v) ? v.length : v));
  attestBtn.disabled = !hasContent;
  attestBtn.classList.toggle("enabled", hasContent);
}

function renderSourceJson(note) {
  sourceJsonEl.textContent = JSON.stringify(note ?? {}, null, 2);
}

attestBtn.addEventListener("click", () => {
  // This demo has no real EHR behind it -- in production this is where a
  // FHIR DocumentReference (note) + Condition/Procedure (billing codes)
  // write-back call would fire against Epic/Cerner/athenahealth, same as
  // DAX Copilot, Abridge, Suki, Nabla, and Ambience Healthcare do today.
  draftBadgeEl.textContent = "ATTESTED (demo)";
  attestNoteEl.textContent =
    "Simulated: would write back via FHIR (DocumentReference + Condition/Procedure) into the EHR chart for clinician sign-off.";
  attestBtn.disabled = true;
  attestBtn.classList.remove("enabled");
});

function fmtClock(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return "--:--";
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

// Renders one utterance as a speaker-labelled, timestamped block (the
// layout Corti Assistant and other ambient-scribe UIs use) rather than one
// run-on paragraph -- far easier to follow back to a point in the audio.
// There's no diarization here (single dictation mic), so every utterance is
// attributed to "You"; a real ambient product would separate clinician vs.
// patient turns.
function makeUtterance(text, offsetSeconds, isInterim) {
  const block = document.createElement("div");
  block.className = "utterance" + (isInterim ? " utterance-interim" : "");

  const meta = document.createElement("div");
  meta.className = "utt-meta";
  const who = document.createElement("span");
  who.className = "utt-speaker";
  who.textContent = "You";
  const when = document.createElement("span");
  when.className = "utt-time";
  when.textContent = fmtClock(offsetSeconds);
  meta.appendChild(who);
  meta.appendChild(when);

  const body = document.createElement("div");
  body.className = "utt-text";
  body.textContent = text;

  block.appendChild(meta);
  block.appendChild(body);
  return block;
}

function clearPlaceholder() {
  const ph = transcriptEl.querySelector(".placeholder");
  if (ph) transcriptEl.innerHTML = "";
}

function appendFinalSegment(text, index, audioStartS) {
  clearPlaceholder();
  if (interimBlock) {
    interimBlock.remove(); // the finalized text below replaces the live
    interimBlock = null;   // in-progress block -- don't leave both visible.
  }
  // Prefer the server's audio_start (authoritative position within the
  // stream); fall back to the interim block's locally-measured offset.
  const offset = audioStartS != null ? audioStartS : interimStartedAtS;
  transcriptEl.appendChild(makeUtterance(text, offset, false));
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function showInterim(text) {
  clearPlaceholder();
  if (!interimBlock) {
    // Timestamp the moment this utterance started, not "now", so the label
    // doesn't creep forward while the speaker is still mid-sentence.
    interimStartedAtS = captureStartMs ? (Date.now() - captureStartMs) / 1000 : 0;
    interimBlock = makeUtterance(text, interimStartedAtS, true);
    transcriptEl.appendChild(interimBlock);
  } else {
    interimBlock.querySelector(".utt-text").textContent = text;
  }
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

async function startDictation() {
  resetUi();
  setStatus("connecting…");

  const proto = location.protocol === "https:" ? "wss" : "ws";
  // A deployment with DEMO_ACCESS_KEY set expects the same key in the page URL.
  const key = new URLSearchParams(location.search).get("key");
  const qs = key ? `?key=${encodeURIComponent(key)}` : "";
  ws = new WebSocket(`${proto}://${location.host}/ws/dictate${qs}`);
  ws.binaryType = "arraybuffer";
  let opened = false;

  ws.onopen = () => {
    opened = true;
    setStatus("warming up ASR session…");
    dictateBtn.textContent = "End recording";
    dictateBtn.classList.add("recording");
    recording = true;
    // Mic capture starts only once the server confirms (via a "ready"
    // message) that the Together realtime ASR session is actually live --
    // see server.py for why starting earlier would corrupt TTFS timing.
  };

  async function beginCapture() {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    await audioCtx.audioWorklet.addModule(
      `/static/worklet.js?v=${window.__ASSET_V || Date.now()}`
    );

    const source = audioCtx.createMediaStreamSource(micStream);
    workletNode = new AudioWorkletNode(audioCtx, "pcm-downsampler", {
      processorOptions: { targetRate: 16000 },
    });
    workletNode.port.onmessage = (event) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        // t=0 for transcript timestamps is the first PCM chunk actually
        // sent, matching the server's `stream_start` reference so locally
        // measured interim offsets line up with server audio_start values.
        if (captureStartMs === null) captureStartMs = Date.now();
        ws.send(event.data); // raw Int16 PCM ArrayBuffer
      }
    };
    source.connect(workletNode);
    startWave(source);
    startClock();
    // Not connecting workletNode to destination -- we don't want to hear
    // our own mic echoed back through the speakers.
    setStatus("listening…");
  }

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
      case "ready":
        beginCapture().catch((err) => {
          console.error(err);
          setStatus(`mic error: ${err.message}`);
        });
        break;
      case "session_started":
        setStatus(`session live (${msg.model})`);
        break;
      case "interim":
        showInterim(msg.text);
        break;
      case "final":
        appendFinalSegment(msg.text, msg.segment_index, msg.audio_start_s);
        lastTtfsEl.textContent = fmtMs(msg.ttfs_ms);
        segCountEl.textContent = String(msg.segment_index + 1);
        if (msg.ttfs_summary) {
          meanTtfsEl.textContent = fmtMs(msg.ttfs_summary.mean_ms);
          p95TtfsEl.textContent = fmtMs(msg.ttfs_summary.p95_ms);
        }
        break;
      case "note":
        renderNoteChart(msg.note);
        renderSourceJson(msg.note);
        lastStructuringEl.textContent = fmtMs(msg.structuring_ms);
        if (msg.llm_metrics) {
          lastTtftEl.textContent = fmtMs(msg.llm_metrics.ttft_ms);
          lastTpsEl.textContent =
            msg.llm_metrics.tps != null ? `${msg.llm_metrics.tps} tok/s` : "—";
        }
        break;
      case "summary":
        if (msg.note) {
          renderNoteChart(msg.note);
          renderSourceJson(msg.note);
        }
        const t = msg.ttfs_summary;
        const s = msg.structuring_summary;
        const l = msg.llm_summary;
        footerNoteEl.textContent =
          `Session complete — TTFS: ${t ? `mean ${t.mean_ms}ms / p95 ${t.p95_ms}ms / median ${t.median_ms}ms over ${t.count} segments` : "n/a"}` +
          `  |  Structuring latency: ${s ? `mean ${s.mean_ms}ms / p95 ${s.p95_ms}ms` : "n/a"}` +
          `  |  LLM TTFT: ${l && l.ttft_ms_mean != null ? `mean ${l.ttft_ms_mean}ms` : "n/a"}` +
          `  |  LLM TPS: ${l && l.tps_mean != null ? `mean ${l.tps_mean} tok/s` : "n/a"}`;
        break;
      case "error":
        setStatus(`error: ${msg.message}`);
        if (recording) stopDictation(true, `error: ${msg.message}`);
        break;
    }
  };

  ws.onclose = () => {
    if (!opened) {
      setStatus("connection refused — is the access key (?key=…) in the URL?");
      return;
    }
    if (recording) stopDictation(/* alreadyClosed */ true);
  };
}

function stopDictation(alreadyClosed, finalStatus) {
  recording = false;
  dictateBtn.textContent = "Start recording";
  dictateBtn.classList.remove("recording");
  setStatus(finalStatus || "finalizing…");

  stopClock();
  stopWave();

  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  if (workletNode) {
    workletNode.disconnect();
    workletNode = null;
  }
  if (audioCtx) {
    audioCtx.close();
    audioCtx = null;
  }
  if (!alreadyClosed && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop" }));
  }
  if (!finalStatus) setStatus("idle");
}

dictateBtn.addEventListener("click", () => {
  if (!recording) {
    startDictation().catch((err) => {
      console.error(err);
      setStatus(`mic error: ${err.message}`);
    });
  } else {
    stopDictation(false);
  }
});
