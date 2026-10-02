# realtime_transcription

Test of the [Together.AI realtime transcription example](https://github.com/togethercomputer/together-py/blob/main/examples/realtime_transcription.py).

Streams PCM audio and receives interim + finalized transcripts using
`together.beta.realtime.transcription` with the `openai/whisper-large-v3` model.

## Setup

The `realtime` extra is not yet on PyPI, so install from GitHub main
(requires Python >= 3.10):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "together[realtime] @ git+https://github.com/togethercomputer/together-py.git"
```

## Usage

### Live web demo (for a panel/interview): `web/server.py`

A local browser UI — click "Start Dictation", speak into your mic, and see
live interim/finalized transcript, TTFS latency metrics, and an
EHR-chart-style structured note (modeled on how Nuance DAX/Dragon Copilot,
Abridge, Suki, Nabla, and Ambience Healthcare present drafts for clinician
sign-off). See [`web/README.md`](web/README.md) for setup, architecture,
and a companion `notebook/demo_notebook.py` (marimo) that replays a saved
session's metrics as a polished backup if live mic/network isn't
cooperating.

```bash
pip install fastapi "uvicorn[standard]"
export TOGETHER_API_KEY=...
python3 web/server.py       # open http://localhost:8000
```

### One-command demo: live dictation -> structured JSON note (`realtime_clinical_note.py`)

This is the main script — a single file, single command, no manual
copy/paste between steps. It streams audio (mic or a WAV file) to
Together's realtime ASR and structures the transcript into a JSON clinical
note + draft billing codes *incrementally*, updating live after every
finalized utterance rather than only once at the end.

```bash
pip install "together[realtime] @ git+https://github.com/togethercomputer/together-py.git" sounddevice numpy pydantic
export TOGETHER_API_KEY=...

python3 examples/realtime_clinical_note.py --list-devices     # find your AirPods' index
python3 examples/realtime_clinical_note.py --device 1          # live mic dictation
python3 examples/realtime_clinical_note.py --file audio.wav    # 16 kHz mono WAV instead of mic
```

Each live update reuses the previous JSON note as a "sticky baseline" so
already-confirmed fields (e.g. `exam_findings`, `plan`) only get added to or
refined, never disappear just because the newest sentence is still
mid-thought or being ASR-corrected. Draft billing codes are always flagged
`requires_human_review: true` and must be verified by a certified coder
before submission.

See [`DEMO.md`](DEMO.md) for the exact commands to run in front of an
audience, [`ARCHITECTURE.md`](ARCHITECTURE.md) for the pipeline/design
diagrams, and
[`model-selection-together.md`](model-selection-together.md) for why these
two models were chosen, what was rejected, and how to verify a new
candidate.

### Individual pieces (for reference / experimentation)

The combined script above is composed from these standalone pieces, kept
in the repo for reference:

- **`examples/realtime_transcription.py`** — file-based ASR only (ported
  from together-py's example): `python3 examples/realtime_transcription.py audio.wav`
- **`examples/realtime_mic_transcription.py`** — live mic ASR only, with
  `--list-devices`/`--device` flags: `python3 examples/realtime_mic_transcription.py --device 1`
- **`examples/structured_clinical_note.py`** — JSON structuring only, given
  a raw transcript text file (ported from Together AI's official
  [structured-outputs sample](https://github.com/togethercomputer/skills/blob/main/skills/together-chat-completions/scripts/structured_outputs.py)
  in `togethercomputer/skills`): `python3 examples/structured_clinical_note.py transcript.txt`

### Measurement

- **`examples/benchmark.py`** — structuring-tier reliability and latency
  (JSON validity rate, mean/median/p95) over repeated runs.
- **`examples/measure_ttfs.py`** — ASR-tier time-to-final-segment for a
  single model.
- **`examples/benchmark_asr.py`** — ASR accuracy *and* latency in one pass:
  WER, CER, clinical-critical error rate (CCER), TTFS p50/p95 and RTF across
  any set of STT models, with `--concurrency` to find where serverless
  latency degrades. Run `--make-sample` to build a synthetic ground-truth
  corpus, or `--help-corpus` for how to get real clinical audio.
  Needs `pip install jiwer` (plus `matplotlib` for `--plot`).
  Methodology and measured results:
  [`model-selection-together.md`](model-selection-together.md).

## Verified

Ran successfully against a real transcribed physician referral letter (NCH
Express Scribe medical dictation sample) via both live AirPods dictation and
a converted WAV file, producing a correct final structured JSON note
(chief complaint, HPI, exam findings, assessment, plan, and a plausible
draft ICD-10-CM code) with live incremental updates that no longer flicker
fields on/off between updates.
