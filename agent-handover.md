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

If `sounddevice` fails to build/import on a non-Mac / non-arm64 machine, it
usually means the PortAudio native lib isn't bundled for that platform —
`brew install portaudio` (macOS) or the OS equivalent, then retry.

There's no `requirements.txt` in the repo yet — the exact versions verified
working on this machine (2026-09-29) were:

```
together @ git+https://github.com/togethercomputer/together-py.git@9c9c34e47686344b996eaf19a7c470f72dcdecd6
sounddevice==0.5.6
numpy==2.4.6
pydantic==2.13.5
httpx==0.28.1
websockets==15.0.1
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
- **Just completed**: a best-practice review against Together's official
  `togethercomputer/skills` → `together-audio` skill, plus a serverless vs.
  Dedicated Endpoint migration investigation (verified live against
  Together's actual Dedicated Model Inference API, not just docs):
  - **ASR tier** (`openai/whisper-large-v3`): confirmed **no dedicated
    deployment path exists yet** on Together's DMI catalog — serverless
    only, for any STT model (Whisper, Deepgram, Parakeet, Nemotron-ASR all
    absent from the dedicated catalog as of this session).
  - **LLM/structuring tier**: `meta-llama/Llama-3.3-70B-Instruct-Turbo`
    (current, serverless FP8) and `meta-llama/Llama-3.3-70B-Instruct`
    (dedicated-only BF16) are **two different model IDs** — moving to
    dedicated means a real model swap, not a deployment flag flip.
  - Investigated `openai/gpt-oss-120b` and `MiniMaxAI/MiniMax-M3` as
    candidates that are the *same* model ID on both serverless and
    dedicated tiers (no split-SKU problem). Benchmarked both against the
    real `JonesMedical.wav` pipeline (3 runs each):
    - `gpt-oss-120b`: 100% reliable but **13.2s mean / 30.6s p95 structuring
      latency** — too slow for near-real-time, disqualified despite being
      Together's officially documented "Top Model" for structured outputs.
    - `MiniMax-M3`: fast structuring (1.46s mean, competitive with current
      Llama-3.3-Turbo's 1.93s mean) but only 1/3 runs succeeded in this
      session — 2 failures were `RealtimeConnectionError: endpoint signaled
      no healthy workers`, an ASR-side (whisper-large-v3) transient
      infra issue independent of the structuring model, but still an
      observed reliability flag worth re-testing before recommending.
  - **Current recommendation**: stay on `Llama-3.3-70B-Instruct-Turbo` for
    now (100% reliable, ~2s structuring latency, proven); revisit
    `MiniMax-M3` after a clean re-test; treat `gpt-oss-120b` as
    disqualified on latency grounds regardless of its docs pedigree.
  - **No code has been changed as a result of this research** — it was
    explicitly requested as planning/reporting only. The next step (not
    yet started) would be updating `ARCHITECTURE.md` / `DEMO.md` with a
    "Dedicated Endpoint readiness" section reflecting these findings, if
    the user decides to proceed.

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
