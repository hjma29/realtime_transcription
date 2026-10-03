# Web demo: live dictation for the panel discussion

A local web interface to demo the realtime clinical-dictation pipeline
live: click "Start Dictation", speak into your Mac's mic, and watch
interim/finalized transcript, TTFS (Time To Final Segment) latency, and an
incrementally-structured EHR-style note appear in real time.

**Design pattern**: modeled after how real ambient clinical-scribe products
(Nuance DAX / Microsoft Dragon Copilot, Abridge, Suki AI, Nabla, Ambience
Healthcare) present drafts for sign-off — streaming ASR &rarr; LLM-structured
SOAP-style note &rarr; draft ICD-10-CM/CPT billing codes &rarr; mandatory
clinician review/attest &rarr; (simulated) FHIR write-back into the EHR
chart. The note panel renders as labeled chart sections with a DRAFT badge
and an "Attest & Send to EHR" button, not a raw JSON blob — this demo has no
real EHR behind that button, it just shows what production write-back would
do.

The transcript panel follows Corti Assistant's layout: each finalized
utterance is its own block with a speaker label and an `MM:SS` offset into
the recording, so the transcript reads as a scannable timeline rather than
one run-on paragraph. Timestamps come from the ASR's `audio_start` (position
within the audio stream, not wall-clock), and the in-progress utterance is
shown italicized with a `live` marker until it finalizes. There's no
diarization here — it's a single dictation mic, so every turn is labeled
"You"; a production ambient scribe would split clinician vs. patient turns.

## Run it

```bash
cd realtime_transcription
source .venv/bin/activate
export TOGETHER_API_KEY=...
python3 web/server.py
# open http://localhost:8000 in Chrome or Safari, click "Start Dictation",
# allow mic access, and speak.
```

Click the button again (or just stop talking and click "Stop Dictation")
to flush the final transcript, run one guaranteed final structuring pass,
and see the session summary (full transcript + latency stats) in the page
footer.

Each session is also logged to `web/logs/session_<unix-ts>.json` (and
mirrored to `web/logs/last_session.json`) for the companion marimo
notebook.

## Metrics shown live

- **Last / Mean / p95 TTFS** — Time To Final Segment: wall-clock delay
  between a spoken segment ending and the ASR delivering its finalized
  transcript. Same metric Deepgram/AssemblyAI/Gladia publish, but measured
  genuinely end-to-end here (real mic &rarr; browser &rarr; WebSocket &rarr;
  Together's realtime API), not a paced synthetic file replay like
  `examples/measure_ttfs.py`.
- **Segments** — count of finalized utterances so far.
- **Last structuring** — latency of the most recent LLM call that turned
  the growing transcript into the structured JSON note (end-to-end wall
  clock, including network).
- **LLM TTFT** — Time To First Token of that structuring call: how long
  the model takes to start emitting output. The standard
  inference-serving latency number.
- **LLM TPS** — output tokens/sec sustained during generation (measured
  excluding TTFT), i.e. decode throughput.

## Version badge

The page header shows e.g. `v1.2 · build 9c1f4a9d05`. The first part is
`APP_VERSION` in `server.py`; the second is a hash of the static files'
newest mtime, which is also appended to the `app.js` / `style.css` /
`worklet.js` URLs as a `?v=` query param.

This exists because browsers aggressively cache local static assets —
during iteration it's easy to stare at a stale UI and think a fix didn't
work. If you edit a file and the build hash in the header doesn't change
after a plain reload, you're looking at a cached page. (Editing a static
file changes the hash on the next reload; no server restart needed.
Bump `APP_VERSION` for behaviour changes worth labelling.)

## Backup: marimo notebook

If live mic/network conditions are shaky during the actual panel, open
`notebook/demo_notebook.py` instead — it replays the saved JSON log from
any previous session (dropdown to pick which one) and renders the same
transcript, structured note, and TTFS/structuring latency charts as
polished, static output:

```bash
source .venv/bin/activate
marimo edit notebook/demo_notebook.py          # interactive, editable
# or:
marimo run notebook/demo_notebook.py --headless --port 2719   # read-only app
```

## Architecture

```
Browser mic (getUserMedia)
  -> AudioWorklet (worklet.js): downsample to 16kHz mono, Float32->Int16 PCM
  -> WebSocket binary frames (~100ms chunks)
  -> FastAPI /ws/dictate (web/server.py)
       -> Together realtime ASR session (openai/whisper-large-v3)
            - interim deltas -> pushed to browser live
            - finalized segments -> TTFS computed, pushed to browser
       -> incremental LLM structuring (Qwen/Qwen3.5-9B, thinking off),
          debounced so calls never stack up, reusing
          examples/realtime_clinical_note.py's structure_transcript()
            -> structured note JSON -> pushed to browser, rendered as an
               EHR-chart-style preview
  -> session JSON logged to web/logs/ for the marimo notebook
```

The server intentionally sends a `{"type": "ready"}` message to the browser
only once Together's realtime ASR session handshake has actually
completed, and the browser waits for it before starting mic capture.
Without this, audio sent while the handshake is still in flight would sit
buffered and get drained in a burst once the server starts reading --
making `audio_end` (cumulative appended-audio duration) race ahead of
wall-clock time and produce bogus negative TTFS values.

## Deploying to Vercel (free Hobby tier)

Deployed at <https://luminary-dictation-demo.vercel.app>. The app is a standard
FastAPI ASGI app, so Vercel runs it as a single Python Function; WebSockets are
served natively (public beta on all plans; Python support since 2026-07-23).

Files that make it work: `pyproject.toml` (dependencies + `[tool.vercel]
entrypoint = "web.server:app"`), `vercel.json` (300 s function duration),
`.vercelignore` (keeps the venv, logs and docs out of the upload).

```bash
npm i -g vercel            # or prefix commands with `npx`
vercel link --project luminary-dictation-demo
printf '%s' "$TOGETHER_API_KEY" | vercel env add TOGETHER_API_KEY production --sensitive
printf '%s' "<random>"          | vercel env add DEMO_ACCESS_KEY production --sensitive
vercel deploy --prod
```

Open the page as `https://luminary-dictation-demo.vercel.app/?key=<DEMO_ACCESS_KEY>`.
`/ws/dictate` refuses (HTTP 403) any connection without the key, because every
session spends Together API credits and the production alias is public. The
page itself is public; it holds no secrets.

Verify a deployment without a browser (streams a WAV over the real protocol):

```bash
python3 web/ws_smoke_test.py audio.wav https://luminary-dictation-demo.vercel.app --key <KEY>
```

Behaviour that differs from running locally:

- **Each WebSocket is cut after 300 s** (the Hobby maximum; Pro allows 800 s).
  The browser sees a close with no `summary`. Keep demo dictations short.
- **WebSocket support is a public beta** and may change.
- **No persistent logs.** The deployment bundle is read-only, so session JSON
  goes to `/tmp` and is lost when the instance recycles; the marimo notebook
  fallback needs logs from a local run.
- **Mic modes are unavailable** (no PortAudio). `examples/realtime_clinical_note.py`
  imports `sounddevice` optionally for this reason; use `--file` or the browser.
- Use `DEMO_ACCESS_KEY` rotation (`vercel env rm` / `add`, then redeploy) if the
  key leaks. `TOGETHER_API_KEY` is stored as a sensitive variable and can't be
  read back from Vercel.
- Not yet verified: the browser-side mic path (`getUserMedia`, AudioWorklet) on
  the deployed HTTPS origin. The backend is verified end to end.
