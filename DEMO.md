# Demo

## List microphones (find your AirPods index)

```bash
cd ~/work/dictation/realtime_transcription
source .venv/bin/activate
python3 examples/realtime_clinical_note.py --list-devices
```

## Run — live mic

```bash
python3 examples/realtime_clinical_note.py --device 1
```

Speak, then press **Ctrl+C** to stop. It prints the full transcript and the
structured JSON note automatically — no other commands needed.

## Run — from a WAV file (fallback / no mic needed)

```bash
python3 examples/realtime_clinical_note.py --file ~/Downloads/JonesMedical.wav
```

## Proof-of-concept: latency + reliability numbers

```bash
python3 examples/benchmark.py ~/Downloads/JonesMedical.wav --runs 10
```

Runs the pipeline 10 times against the same file and prints: reliability
(% of runs that completed without error), end-to-end pipeline latency, and
per-utterance LLM structuring latency (mean/median/p95) — the numbers to
cite for "near-real-time" and "production-reliable" claims.
