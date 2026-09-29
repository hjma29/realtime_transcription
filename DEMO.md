# Live demo script (for panel presentation)

Total time: ~4-5 minutes. Rehearse the dictated line once beforehand so it
comes out clean on the recording.

## 0. Before you start

- Have AirPods connected and confirmed as the input device:
  ```bash
  cd ~/work/dictation/realtime_transcription
  source .venv/bin/activate
  python3 examples/realtime_mic_transcription.py --list-devices
  ```
  Confirm your AirPods' index (e.g. `1`).
- Have a **fallback transcript file** ready (`/tmp/jones_transcript.txt` or
  similar) in case live mic/network hiccups during the panel — see step 4.
- Open two terminal panes/tabs: one for the live mic demo, one ready for the
  structuring step.

## 1. Set the stage (30s, no screen share needed yet)

Talking points:
- "This demo shows the two-model pipeline: real-time speech-to-text, then
  an LLM structuring pass that outputs schema-constrained JSON with draft
  billing codes."
- Show the architecture diagram (`ARCHITECTURE.md`) briefly before diving
  into the terminal.

## 2. Live dictation (60-90s)

```bash
python3 examples/realtime_mic_transcription.py --device 1
```

- Wait for `Listening... speak now (Ctrl+C to stop)`.
- Dictate a short, rehearsed clinical line, e.g.:
  > "Patient is a 45-year-old male presenting with lower back pain for two
  > weeks, worse with prolonged sitting. No red flag symptoms. Plan:
  > NSAIDs and physical therapy referral."
- Let the panel see the **interim** text updating live, then the **final**
  line printing — this demonstrates true streaming, not batch processing.
- Press **Ctrl+C**. It prints the full transcript and exits cleanly.

Talking point while it streams: "Notice the interim text updates as I
speak, then locks in as 'final' once the model detects the end of that
utterance — this is what makes it feel real-time to the physician."

## 3. Structure it into JSON (30-45s)

Copy the printed `full transcript:` line into a file, then run:

```bash
echo "Patient is a 45-year-old male presenting with lower back pain for two weeks, worse with prolonged sitting. No red flag symptoms. Plan: NSAIDs and physical therapy referral." > /tmp/demo_transcript.txt

python3 examples/structured_clinical_note.py /tmp/demo_transcript.txt
```

Talking point: "This second call uses Together's `json_schema` response
format — the model's output is constrained to exactly match our Pydantic
schema, so it's guaranteed parseable. Note `draft_billing_codes` and
`requires_human_review: true` — billing codes are never auto-submitted."

## 4. Fallback if live mic fails

If Bluetooth/audio has any hiccup during the panel, skip straight to a
pre-transcribed real physician dictation sample (already validated working):

```bash
python3 examples/structured_clinical_note.py /tmp/jones_transcript.txt
```

This still demonstrates the full JSON structuring capability end-to-end
without depending on live audio in the room.

## 5. Close (30s)

Talking points:
- Phased infrastructure: "We'd start on serverless for speed, and migrate
  to a Dedicated Endpoint once volume justifies it — or immediately if
  real PHI requires a BAA."
- Safety boundary: "Billing codes are always drafts, always reviewed by a
  certified coder before submission — the model never has final say."
