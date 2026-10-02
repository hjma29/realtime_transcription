# Agent Handover — realtime_transcription (Luminary Health demo)

Purpose: let a GitHub Copilot CLI agent on a **different computer** get this
repo running from zero with minimal back-and-forth. Read this file first on
a new machine.

## What this repo is

A demo/POC of Together.AI's realtime speech-to-text + LLM-based structured
JSON extraction, built around a fictional "Luminary Health" AI clinical
documentation use case, for a panel presentation on serverless vs. dedicated
endpoint deployment. See `README.md` for usage, `ARCHITECTURE.md` for the
pipeline diagram, `DEMO.md` for the exact commands to run live.

**Primary script**: `examples/realtime_clinical_note.py` — mic or WAV file
in, streaming ASR (`openai/whisper-large-v3`), incremental JSON clinical
note out (currently `meta-llama/Llama-3.3-70B-Instruct-Turbo`).

**Primary demo surface** (added 2026-10-01): `web/server.py` — a local
FastAPI + WebSocket app wrapping that same pipeline with a browser UI
(mic button, live timestamped transcript, latency metrics, EHR-style note
preview). This is what gets shown live in the panel; see `web/README.md`.
`notebook/demo_notebook.py` is a marimo notebook that reads logged sessions
as an offline backup if mic/wifi fails on stage.

## One-time setup on a new computer

### 1. Python — must be >= 3.10 (the `together[realtime]` extra requires it)

macOS system Python is often 3.9 (too old). Install a newer one via Homebrew
and use it explicitly to create the venv — do not rely on a bare `python3`:

```bash
brew install python@3.11
cd /path/to/realtime_transcription
python3.11 -m venv .venv
source .venv/bin/activate
python3 --version   # confirm >= 3.10 inside the venv
```

### 2. Install dependencies

The `realtime` extra isn't on PyPI yet — install `together` straight from
GitHub main:

```bash
pip install "together[realtime] @ git+https://github.com/togethercomputer/together-py.git" \
    sounddevice numpy pydantic
```

For the web demo (`web/server.py`) and the backup notebook, also install:

```bash
pip install fastapi "uvicorn[standard]" websockets marimo matplotlib
```

If `sounddevice` fails to build/import on a non-Mac / non-arm64 machine, it
usually means the PortAudio native lib isn't bundled for that platform —
`brew install portaudio` (macOS) or the OS equivalent, then retry.

There's no `requirements.txt` in the repo yet — the exact versions verified
working on this machine (Python 3.11.16, last reverified 2026-10-01) were:

```
together @ git+https://github.com/togethercomputer/together-py.git@9c9c34e47686344b996eaf19a7c470f72dcdecd6
sounddevice==0.5.6
numpy==2.4.6
pydantic==2.13.5
httpx==0.28.1
websockets==15.0.1
fastapi==0.142.2
starlette==1.7.0
uvicorn==0.54.0
marimo==0.25.0
matplotlib==3.11.2
```

If a new machine's `pip install` pulls a materially newer `together` commit
and something breaks, that pinned commit hash is the known-good fallback:

```bash
pip install "together[realtime] @ git+https://github.com/togethercomputer/together-py.git@9c9c34e47686344b996eaf19a7c470f72dcdecd6" \
    sounddevice numpy pydantic
```

### 3. Get the Together API key

The key lives in the user's **private `work-notes` GitHub repo**, file
`ai-coding.md`. On a new machine:

```bash
# fetch the key from the work-notes repo (adjust path/clone location as needed)
grep -A2 -i together ~/path/to/work-notes/ai-coding.md
```

Then export it (don't hardcode it in any file in *this* repo — `.gitignore`
doesn't need a rule for it since it should never be written to disk here):

```bash
export TOGETHER_API_KEY="tgp_v1_..."
```

For persistence across terminal sessions, add the `export` line to
`~/.zshrc` (matches how it's set up on this machine) or `~/.bashrc`.

### 4. Verify the setup

```bash
cd examples
python3 realtime_transcription.py --help   # should print without import errors
python3 realtime_mic_transcription.py --list-devices   # should list audio input devices
```

If you have a WAV sample handy (16kHz mono ideally; the pipeline also
resamples), run the full pipeline end-to-end:

```bash
python3 realtime_clinical_note.py --file /path/to/some.wav
```

Expect streaming `interim:`/`final:` transcript lines and periodic
`=== structured note (live update) ===` JSON blocks.

### 5. Verify the web demo

```bash
cd /path/to/realtime_transcription
source .venv/bin/activate
export TOGETHER_API_KEY="tgp_v1_..."
python3 web/server.py          # serves http://localhost:8000
```

Open `http://localhost:8000` and confirm the header shows a version badge
(e.g. `v1.3 · build 018fea50e8`). Click "Start Dictation", allow mic
access, and speak.

There is no browser in a headless agent environment, so an agent cannot
verify the UI itself. Validate the backend instead with a simulated
browser client that streams a WAV over the WebSocket — a working copy of
that harness is **not committed** (it lived at `/tmp/ws_test_client.py`);
rewrite it as needed. The protocol it must follow:

1. connect to `ws://127.0.0.1:8000/ws/dictate`
2. **wait for `{"type":"ready"}` before sending any audio** (see the
   ready-handshake note under "Conventions" — sending early corrupts TTFS)
3. send raw 16-bit mono 16 kHz PCM as binary frames, paced ~100 ms/chunk
4. send `{"type":"stop"}` when done, then read until `{"type":"summary"}`

A synthetic test WAV can be generated on macOS with
`say -o /tmp/s.aiff "..."` piped through
`ffmpeg -i /tmp/s.aiff -ar 16000 -ac 1 /tmp/test_speech.wav`.

## Known-good sample audio (not in git — see `.gitignore`: `*.wav`/`*.aiff` excluded)

`~/Downloads/JonesMedical.wav` (and `.mp3`) — a real transcribed physician
referral-letter dictation sample (NCH Express Scribe), used throughout this
session for testing. **Not in the repo** (audio files are gitignored) — if
missing on the new machine, either copy it over manually or generate a
synthetic test WAV (see `realtime_transcription.py`'s docstring / earlier
session history for how a synthetic WAV was first created).

## Where things stand (as of this handover)

- Core pipeline (`realtime_clinical_note.py`) is fully working, with a
  "sticky baseline" fix so structured JSON fields never flicker
  empty/disappear across live updates.
- `benchmark.py` and `measure_ttfs.py` are POC latency/reliability tools,
  both verified working.
- **Web demo is built and is now the primary panel artifact** (session of
  2026-10-01, currently `APP_VERSION = "1.3"` in `web/server.py`):
  - `web/server.py` — FastAPI app. `GET /` serves the UI; `/ws/dictate` is
    the WebSocket endpoint that takes browser mic PCM, streams it to
    Together realtime ASR, computes TTFS per finalized segment, runs
    debounced incremental structuring, and writes a per-session JSON log
    to `web/logs/` (gitignored) for the notebook.
  - `web/static/` — UI. Transcript renders as Corti-Assistant-style
    timestamped utterance blocks (`You  00:05`); the note panel renders as
    EHR chart sections with a DRAFT badge and a *simulated* "Attest & Send
    to EHR" button (no real EHR behind it), modeled on how Nuance DAX /
    Dragon Copilot, Abridge, Suki, Nabla and Ambience present drafts for
    sign-off.
  - Live metrics: TTFS (last/mean/p95), segment count, structuring latency,
    and LLM **TTFT** + **TPS** (the latter two come from a streamed
    structuring call — `structure_transcript_streaming` in `web/server.py`,
    which duplicates `rcn.structure_transcript`'s prompt/schema but with
    `stream=True` so first-token time and decode throughput are measurable).
  - `notebook/demo_notebook.py` — marimo backup, reads `web/logs/*.json`.
    Run with `marimo run notebook/demo_notebook.py --headless --port 2718`.
    It renders nothing if `web/logs/` is empty, so run a dictation session
    (or the WS test harness) first.
- **Known-unverified**: no part of the browser-side code (getUserMedia,
  AudioWorklet resampling in `web/static/worklet.js`, actual visual
  rendering) has ever been exercised by an agent — no browser exists in the
  agent environment. The user has been told to validate this themselves
  before the panel. Backend is verified end-to-end via the WS harness.
- Earlier research (serverless vs. Dedicated Endpoint migration, verified
  live against Together's Dedicated Model Inference API) still stands and
  **has not been acted on in code**:
  - **ASR tier** (`openai/whisper-large-v3`): confirmed **no dedicated
    deployment path exists yet** on Together's DMI catalog — serverless
    only, for any STT model (Whisper, Deepgram, Parakeet, Nemotron-ASR all
    absent from the dedicated catalog as of this session).
  - **LLM/structuring tier**: `meta-llama/Llama-3.3-70B-Instruct-Turbo`
    (current, serverless FP8) and `meta-llama/Llama-3.3-70B-Instruct`
    (dedicated-only BF16) are **two different model IDs** — moving to
    dedicated means a real model swap, not a deployment flag flip.
  - `openai/gpt-oss-120b`: 100% reliable but **13.2s mean / 30.6s p95
    structuring latency** — too slow for near-real-time, disqualified
    despite being Together's documented "Top Model" for structured outputs.
  - `MiniMaxAI/MiniMax-M3`: fast structuring (1.46s mean) but only 1/3 runs
    succeeded; the 2 failures were `RealtimeConnectionError: endpoint
    signaled no healthy workers`, an ASR-side transient independent of the
    structuring model, but still worth a clean re-test.
  - **Current recommendation**: stay on `Llama-3.3-70B-Instruct-Turbo`;
    revisit `MiniMax-M3` after a clean re-test; treat `gpt-oss-120b` as
    disqualified on latency.
  - Next step if the user wants it: a "Dedicated Endpoint readiness"
    section in `ARCHITECTURE.md` / `DEMO.md`. Not started.

## Gotchas that cost real time — read before debugging

- **Negative TTFS means your t=0 is wrong, not that the ASR is psychic.**
  TTFS is `now - stream_start - event.audio_end`, where `audio_end` is
  *cumulative appended audio duration* (sample-count derived). If the
  client starts sending audio before the server has finished opening the
  Together session (~400–550 ms handshake), that audio buffers in the
  socket and drains in a burst, so `audio_end` races ahead of wall clock
  and TTFS goes systematically negative (observed −400 to −550 ms). Fixed
  by the `{"type":"ready"}` handshake — the server only signals ready once
  the ASR session is live, and the client must not send audio before it.
  Small negative values (tens of ms) are normal measurement jitter.
- **"My fix didn't work" is usually browser cache.** This burned several
  rounds. Chrome had cached `app.js`/`style.css` from before any
  `Cache-Control` header existed, applied heuristic freshness, and kept
  serving them *without revalidating* — so `index.html` updated while the
  JS/CSS silently did not. Mitigated two ways: `NoCacheStaticFiles` sends
  `no-store`, and asset URLs are versioned (`app.js?v=<hash>` derived from
  newest static-file mtime). **Before debugging any UI report, confirm the
  build hash in the page header matches what the server computes** —
  `curl -s localhost:8000/ | grep -oE 'build [0-9a-f]+'`. If they differ,
  it's cache, not code.
- **Together's realtime ASR endpoint fails transiently** with
  `RealtimeConnectionError: endpoint signaled no healthy workers; failing
  over`. This is infra-side, not a bug in this repo. `web/server.py`
  catches it at session start and mid-session and reports
  `{"type":"error"}` to the client rather than letting it escape the ASGI
  handler (which closed the socket with no close frame and left the browser
  hanging with no explanation).
- **Whisper emits empty finalized segments** around silence. They are
  filtered in `web/server.py`'s `TranscriptCompleted` branch — otherwise
  they render as blank timestamped rows, feed junk into the structuring
  prompt, and pollute TTFS stats with zero-length utterances.
- **`kill $(cat file.pid)` is rejected** by the Copilot CLI bash tool's
  safety filter. `cat` the PID file first, read the literal number, then
  `kill <literal-pid>` as a separate call.

## Conventions this session established (for consistency if you continue)

- Don't modify `realtime_clinical_note.py`'s constants (`ASR_MODEL`,
  `STRUCTURING_MODEL`) directly to test alternate models — monkey-patch
  `rcn.STRUCTURING_MODEL` in a throwaway script instead (see
  `benchmark.py`'s pattern), keeping the "current recommended" model as the
  actual committed default until a real decision is made to switch.
- `draft_billing_codes` must always come with `requires_human_review: true`
  — this schema default is a deliberate compliance guardrail; a model that
  overrides it to `false` (observed with `gpt-oss-120b` once) is a red flag,
  not just noise.
- Keep `DEMO.md` to short, copy-pasteable command blocks only (no long
  prose) — the user explicitly asked for this format.
- **Bump `APP_VERSION` in `web/server.py`** whenever demo behaviour
  changes. It renders in the page header next to the asset build hash and
  is the user's only way to confirm the browser is on current code.
- The `{"type":"ready"}` handshake is load-bearing for metric correctness,
  not just politeness — never let a client start streaming audio before it.
- `draft_billing_codes` should carry a **best-guess** ICD-10-CM/CPT code
  rather than being left empty (the user explicitly asked for this: an
  empty codes panel demos badly). The prompt instructs the model to always
  suggest its single best guess, formatted
  `ICD-10-CM <code> - <label>` / `CPT <code> - <label>`. This is safe only
  because `requires_human_review` is force-set `True` in code — keep both
  halves of that trade-off together if you touch either.
- US diagnosis codes are **ICD-10-CM** specifically (not bare "ICD-10");
  CPT covers procedures/E&M. The user corrected this, so keep the
  terminology precise in UI labels, prompts, and docs.
- When the user asks why something "still looks broken", **verify what the
  server is actually serving before changing code** (`curl` the asset and
  grep for a marker from the new version). Twice in this session the code
  was already correct and the real fault was browser caching.
