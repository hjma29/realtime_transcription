#!/usr/bin/env python3
"""Real-time dictation from your microphone.

Captures live audio from the default input device and streams it to
Together.AI's realtime transcription API as you speak, printing interim
text while a phrase is in progress and a finalized transcript per
utterance. Press Ctrl+C to stop and flush the final transcript.

Usage:
    pip install "together[realtime]" sounddevice numpy
    export TOGETHER_API_KEY=...
    python3 examples/realtime_mic_transcription.py               # default input device
    python3 examples/realtime_mic_transcription.py --list-devices
    python3 examples/realtime_mic_transcription.py --device 2     # e.g. AirPods mic
"""

from __future__ import annotations

import asyncio
import sys

import sounddevice as sd

from together import AsyncTogether
from together.realtime import (
    SessionStarted,
    TranscriptDelta,
    TranscriptCompleted,
    RealtimeSessionEvent,
)

MODEL = "openai/whisper-large-v3"

SAMPLE_RATE = 16_000
CHUNK_MS = 100
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000  # frames per 100ms callback


def on_event(event: RealtimeSessionEvent) -> None:
    """Handle session events as they arrive."""
    if isinstance(event, SessionStarted):
        print(f"session {event.session_id} started on {event.model}")
    elif isinstance(event, TranscriptDelta):
        print(f"interim: {event.text}", end="\r")
    elif isinstance(event, TranscriptCompleted):
        print(f"final: {event.text}")


async def dictate(device: int | str | None) -> str:
    client = AsyncTogether()
    audio_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def audio_callback(indata, frames, time_info, status) -> None:
        # Runs on PortAudio's own thread; hand raw PCM bytes to asyncio safely.
        if status:
            print(status, file=sys.stderr)
        loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

    info = sd.query_devices(device, "input")
    print(f"input device: {info['name']} (index {info.get('index', device)})")

    async with client.beta.realtime.transcription(
        model=MODEL,
        sample_rate=SAMPLE_RATE,
        event_callback=on_event,
    ) as session:
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
                    # Ctrl+C interrupts cleanly instead of leaving a blocked
                    # executor thread that hangs process exit.
                    chunk = await audio_queue.get()
                    await session.append(chunk)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass

        return await session.flush()  # finalize whatever was said last


async def main() -> None:
    if "--list-devices" in sys.argv:
        print(sd.query_devices())
        return

    device: int | str | None = None
    if "--device" in sys.argv:
        idx = sys.argv.index("--device")
        device = sys.argv[idx + 1]
        if device.isdigit():
            device = int(device)

    transcript = await dictate(device)
    print(f"\nfull transcript: {transcript}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
