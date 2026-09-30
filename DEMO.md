# Live demo script (for panel presentation)

Total time: ~4-5 minutes. Rehearse the dictated line once beforehand so it
comes out clean on the recording.

## 0. Before you start

- Have AirPods connected and confirmed as the input device:
  ```bash
  cd ~/work/dictation/realtime_transcription
  source .venv/bin/activate
  python3 examples/realtime_clinical_note.py --list-devices
  ```
  Confirm your AirPods' index (e.g. `1`).
- Have a **fallback WAV file** ready in case of live mic/network hiccups
  during the panel — see step 3. `~/Downloads/JonesMedical.wav` (converted
  from the NCH Express Scribe sample) is already validated working.

## 1. Set the stage (30s, no screen share needed yet)

Talking points:
- "This is a single command, end-to-end: live speech capture, real-time
  transcription, then an LLM structuring pass that outputs schema-constrained
  JSON with draft billing codes — no manual steps in between."
- Show the architecture diagram (`ARCHITECTURE.md`) briefly before diving
  into the terminal.

## 2. Live demo — one command (90-120s)

```bash
python3 examples/realtime_clinical_note.py --device 1
```

- Wait for `Listening... speak now (Ctrl+C to stop)`.
- Dictate a short, rehearsed clinical line, e.g.:
  > "Patient is a 45-year-old male presenting with lower back pain for two
  > weeks, worse with prolonged sitting. No red flag symptoms. Plan:
  > NSAIDs and physical therapy referral."
- Let the panel see the **interim** text updating live, then **final**
  lines locking in per utterance — demonstrates true streaming.
- Press **Ctrl+C**. The script then *automatically* prints the full
  transcript and the structured JSON note — no copy/paste, no second
  command.

Talking point while it streams: "Notice interim text updates as I speak,
then locks in as 'final' per utterance. The moment I stop, it feeds
straight into the structuring model and prints the JSON note with draft
billing codes — this is one continuous pipeline, not two disconnected
demos."

## 3. Fallback if live mic fails

If Bluetooth/audio has any hiccup during the panel, run the exact same
script against a pre-recorded WAV instead of the mic — same one file, same
JSON output, no live audio dependency:

```bash
python3 examples/realtime_clinical_note.py --file ~/Downloads/JonesMedical.wav
```

## 4. Close (30s)

Talking points:
- Phased infrastructure: "We'd start on serverless for speed, and migrate
  to a Dedicated Endpoint once volume justifies it — or immediately if
  real PHI requires a BAA."
- Safety boundary: "Billing codes are always drafts, always reviewed by a
  certified coder before submission — the model never has final say."
