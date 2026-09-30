#!/usr/bin/env python3
"""End-to-end real-time clinical dictation -> structured JSON note.

Single command, single file, no manual copy/paste: streams audio (live
mic or a WAV file) to Together's realtime ASR (openai/whisper-large-v3),
and structures the transcript into a JSON clinical note + draft billing
codes (Llama-3.3-70B-Instruct-Turbo, response_format: json_schema).

Structuring runs INCREMENTALLY after every finalized utterance (debounced
so calls never stack up), not just once at the end -- this is what makes
the structured JSON output itself near-real-time, not just the ASR.

Usage:
    pip install "together[realtime]>=2.0.0" sounddevice numpy pydantic
    export TOGETHER_API_KEY=...
    python3 examples/realtime_clinical_note.py --list-devices
    python3 examples/realtime_clinical_note.py --device 1        # e.g. AirPods mic
    python3 examples/realtime_clinical_note.py --file audio.wav  # 16kHz mono WAV instead of mic
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
import wave
from pathlib import Path

import sounddevice as sd
from pydantic import BaseModel, Field

from together import AsyncTogether, Together
from together.realtime import (
    SessionStarted,
    TranscriptDelta,
    TranscriptCompleted,
    RealtimeSessionEvent,
)

ASR_MODEL = "openai/whisper-large-v3"
STRUCTURING_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"

SAMPLE_RATE = 16_000
CHUNK_MS = 100
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000  # frames per 100ms mic callback


class ClinicalNote(BaseModel):
    chief_complaint: str = Field(description="One-line reason for the visit")
    history_of_present_illness: str = Field(description="Narrative HPI summary")
    exam_findings: list[str] = Field(description="Bullet list of exam findings")
    assessment: str = Field(description="Clinical assessment / likely diagnosis")
    plan: str = Field(description="Treatment plan / next steps")
    draft_billing_codes: list[str] = Field(
        description="Candidate ICD-10/CPT codes. DRAFT ONLY — must be "
        "verified by a certified coder before submission."
    )
    requires_human_review: bool = Field(
        default=True,
        description="Always true; billing codes are model-suggested, not final.",
    )


# --- structured JSON note, given a transcript (blocking sync call) -----------


def structure_transcript(transcript: str) -> ClinicalNote:
    """Turn a raw ASR transcript into a schema-constrained clinical note."""
    client = Together()
    completion = client.chat.completions.create(
        model=STRUCTURING_MODEL,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a clinical documentation assistant. The following is a "
                    "raw speech-to-text transcript of a physician dictating a patient "
                    "visit — possibly incomplete, since the visit may still be in "
                    "progress. It may contain ASR artifacts (e.g. spoken punctuation "
                    "like 'full stop' or 'new para' transcribed literally) — normalize "
                    "those into real punctuation/paragraphs. Extract a structured "
                    "clinical note matching the given JSON schema from whatever has "
                    "been said so far. Billing codes are drafts only; never fabricate "
                    "a code you are not reasonably confident about."
                ),
            },
            {"role": "user", "content": transcript},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "clinical_note",
                "schema": ClinicalNote.model_json_schema(),
            },
        },
    )
    return ClinicalNote.model_validate_json(completion.choices[0].message.content)


# --- audio sources: live mic, or a pre-recorded WAV file ---------------------


async def dictate(device: int | str | None, session) -> str:
    """Stream live mic audio into an already-open realtime ASR session."""
    audio_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def audio_callback(indata, frames, time_info, status) -> None:
        # Runs on PortAudio's own thread; hand raw PCM bytes to asyncio safely.
        if status:
            print(status, file=sys.stderr)
        loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

    info = sd.query_devices(device, "input")
    print(f"input device: {info['name']} (index {info.get('index', device)})")

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_FRAMES,
        channels=1,
        dtype="int16",
        device=device,
        callback=audio_callback,
    )
    with stream:
        print("Listening... speak now (Ctrl+C to stop)")
        try:
            while True:
                # A plain asyncio.Queue.get() (no background thread) so
                # Ctrl+C interrupts cleanly instead of hanging process exit.
                chunk = await audio_queue.get()
                await session.append(chunk)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    return await session.flush()  # finalize whatever was said last


def load_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
            raise SystemExit(f"expected mono 16-bit {SAMPLE_RATE} Hz WAV")
        return w.readframes(w.getnframes())


async def transcribe_file(path: Path, session) -> str:
    """Stream a pre-recorded WAV file into an already-open realtime ASR session."""
    audio = load_pcm(path)
    chunk_bytes = SAMPLE_RATE * 2 // 10  # 100ms of 16-bit mono per append
    position = 0
    while position < len(audio):
        await session.append(audio[position : position + chunk_bytes])
        position += chunk_bytes
        await asyncio.sleep(0.1)  # simulate a live capture cadence

    return await session.flush()


# --- driver: ASR session + incremental structuring on every final utterance -


async def run_session(audio_source) -> tuple[str, ClinicalNote | None]:
    """Open one realtime ASR session, feed it from `audio_source(session)`,
    and re-structure the transcript into JSON after every finalized
    utterance (debounced) so the structured note updates near-real-time
    instead of only once at the very end.
    """
    client = AsyncTogether()
    loop = asyncio.get_running_loop()
    finalized: list[str] = []
    latest_note: ClinicalNote | None = None
    structuring_lock = asyncio.Lock()
    pending_rerun = False

    async def maybe_structure() -> None:
        # Debounced: if a structuring call is already in flight when a new
        # utterance finishes, mark pending_rerun instead of stacking
        # concurrent calls, then loop once more after it finishes so we
        # always end up structuring the latest available text.
        nonlocal pending_rerun, latest_note
        if structuring_lock.locked():
            pending_rerun = True
            return
        async with structuring_lock:
            while True:
                pending_rerun = False
                text = " ".join(finalized)
                latest_note = await loop.run_in_executor(
                    None, structure_transcript, text
                )
                print("\n=== structured note (live update) ===")
                print(json.dumps(latest_note.model_dump(), indent=2))
                print()
                if not pending_rerun:
                    break

    def on_event(event: RealtimeSessionEvent) -> None:
        if isinstance(event, SessionStarted):
            print(f"session {event.session_id} started on {event.model}")
        elif isinstance(event, TranscriptDelta):
            print(f"interim: {event.text}", end="\r")
        elif isinstance(event, TranscriptCompleted):
            print(f"final: {event.text}")
            finalized.append(event.text)
            # Fire-and-forget: structuring runs concurrently with continued
            # listening/transcribing, so audio capture never blocks on it.
            loop.create_task(maybe_structure())

    async with client.beta.realtime.transcription(
        model=ASR_MODEL,
        sample_rate=SAMPLE_RATE,
        event_callback=on_event,
    ) as session:
        transcript = await audio_source(session)

    # Ensure the last utterance's structuring pass (in flight or pending)
    # completes before we return, so the final note reflects everything said.
    async with structuring_lock:
        pass
    # One guaranteed final pass over the complete transcript, in case the
    # debounced live-update stream was still catching up when audio ended.
    latest_note = await loop.run_in_executor(
        None, structure_transcript, " ".join(finalized)
    )

    return transcript, latest_note


async def main() -> None:
    if "--list-devices" in sys.argv:
        print(sd.query_devices())
        return

    if "--file" in sys.argv:
        idx = sys.argv.index("--file")
        audio_source = functools.partial(transcribe_file, Path(sys.argv[idx + 1]))
    else:
        device: int | str | None = None
        if "--device" in sys.argv:
            idx = sys.argv.index("--device")
            device = sys.argv[idx + 1]
            if device.isdigit():
                device = int(device)
        audio_source = functools.partial(dictate, device)

    transcript, note = await run_session(audio_source)

    print(f"\nfull transcript: {transcript}\n")
    print("=== final structured note ===")
    print(json.dumps(note.model_dump(), indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
