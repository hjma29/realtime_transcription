#!/usr/bin/env python3
"""Measure Time To Final Segment (TTFS) for the ASR stage.

Industry-standard streaming-ASR latency metric (same one Deepgram/
AssemblyAI/Gladia publish): the delay between when a spoken segment
actually ends in the audio, and when the ASR API delivers the finalized
(non-revisable) transcript for it.

    TTFS = wall_clock(TranscriptCompleted received) - wall_clock(segment's audio_end)

`audio_end` comes straight from the SDK (`TranscriptCompleted.audio_end`,
an offset in seconds into the appended audio stream) -- no guessing at
timestamps ourselves. This is a separate number from the LLM structuring
latency measured in benchmark.py: TTFS is purely the ASR stage, before any
LLM call happens.

Usage:
    export TOGETHER_API_KEY=...
    python3 examples/measure_ttfs.py ~/Downloads/JonesMedical.wav
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

from together import AsyncTogether
from together.realtime import (
    RealtimeSessionEvent,
    SessionStarted,
    TranscriptCompleted,
)

import realtime_clinical_note as rcn  # reuse ASR_MODEL, SAMPLE_RATE, load_pcm


async def measure(path: Path) -> list[float]:
    client = AsyncTogether()
    stream_start: float | None = None
    ttfs_values: list[float] = []

    def on_event(event: RealtimeSessionEvent) -> None:
        if isinstance(event, SessionStarted):
            print(f"session {event.session_id} started on {event.model}")
        elif isinstance(event, TranscriptCompleted):
            if stream_start is not None and event.audio_end is not None:
                ttfs = time.monotonic() - stream_start - event.audio_end
                ttfs_values.append(ttfs)
                print(
                    f"  final @ audio_end={event.audio_end:6.2f}s  "
                    f"TTFS={ttfs * 1000:6.0f}ms  text={event.text!r}"
                )

    async with client.beta.realtime.transcription(
        model=rcn.ASR_MODEL,
        sample_rate=rcn.SAMPLE_RATE,
        event_callback=on_event,
    ) as session:
        audio = rcn.load_pcm(path)
        chunk_bytes = rcn.SAMPLE_RATE * 2 // 10  # 100ms of 16-bit mono per append
        position = 0
        stream_start = time.monotonic()  # t=0 for audio_end offsets, set just before first append
        while position < len(audio):
            await session.append(audio[position : position + chunk_bytes])
            position += chunk_bytes
            # Drift-corrected pacing: sleep until the wall-clock deadline for
            # *this much audio sent so far*, not a fixed 0.1s each iteration.
            # A fixed sleep(0.1) accumulates ~10-15ms of scheduling overhead
            # per call, which drifts the "simulated live" sender further and
            # further behind real-time as the file goes on -- inflating TTFS
            # with sender-side drift instead of real ASR finalization delay.
            target = stream_start + (position / (rcn.SAMPLE_RATE * 2))
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        await session.flush()

    return ttfs_values


def summarize(values: list[float]) -> str:
    if not values:
        return "n/a"
    s = sorted(values)
    p95 = s[max(0, int(len(s) * 0.95) - 1)]
    return (
        f"mean={statistics.mean(values) * 1000:.0f}ms "
        f"median={statistics.median(values) * 1000:.0f}ms "
        f"p95={p95 * 1000:.0f}ms "
        f"min={min(values) * 1000:.0f}ms max={max(values) * 1000:.0f}ms"
    )


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: measure_ttfs.py <wav-file>")
    ttfs_values = await measure(Path(sys.argv[1]))
    print(f"\nTTFS across {len(ttfs_values)} finalized segments: {summarize(ttfs_values)}")


if __name__ == "__main__":
    asyncio.run(main())
