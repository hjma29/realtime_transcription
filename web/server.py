#!/usr/bin/env python3
"""Local web demo: browser-mic dictation -> live transcript + TTFS metrics.

A FastAPI app that lets you click "Start Dictation" in a browser tab, speak
into your Mac's mic, and see (in real time):

  - interim (in-progress) + finalized transcript segments
  - per-segment TTFS (Time To Final Segment): mic-to-finalized-transcript
    latency, computed the same way as examples/measure_ttfs.py, but here
    it's genuinely end-to-end (real mic -> browser -> WebSocket -> Together
    realtime ASR), not a paced WAV-file simulation
  - the incrementally-structured clinical note JSON (same pipeline as
    examples/realtime_clinical_note.py)
  - running TTFS + LLM-structuring-latency statistics (mean/median/p95)

Every session's metrics are also written to web/logs/ as JSON, so the
companion marimo notebook (notebook/demo_notebook.py) can render polished
charts from a real run afterwards -- useful as a backup if live mic/network
conditions are bad during an actual panel demo.

Usage:
    source .venv/bin/activate   # from repo root
    pip install fastapi "uvicorn[standard]"   # one-time, already done if you ran setup
    export TOGETHER_API_KEY=...
    python3 web/server.py
    # open http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Reuse the exact ASR + structuring pipeline already proven out in
# examples/realtime_clinical_note.py instead of re-implementing it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))
import realtime_clinical_note as rcn  # noqa: E402
from together import AsyncTogether  # noqa: E402
from together.realtime import (  # noqa: E402
    RealtimeSessionEvent,
    SessionStarted,
    TranscriptCompleted,
    TranscriptDelta,
)

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
LOGS_DIR = WEB_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _summarize(values: list[float]) -> dict | None:
    if not values:
        return None
    s = sorted(values)
    p95 = s[max(0, int(len(s) * 0.95) - 1)]
    return {
        "count": len(values),
        "mean_ms": round(statistics.mean(values) * 1000, 1),
        "median_ms": round(statistics.median(values) * 1000, 1),
        "p95_ms": round(p95 * 1000, 1),
        "min_ms": round(min(values) * 1000, 1),
        "max_ms": round(max(values) * 1000, 1),
    }


@app.websocket("/ws/dictate")
async def ws_dictate(ws: WebSocket) -> None:
    await ws.accept()

    client = AsyncTogether()
    loop = asyncio.get_running_loop()

    finalized: list[str] = []
    ttfs_values: list[float] = []
    structuring_latencies: list[float] = []
    segments: list[dict] = []  # per-utterance log entries for the notebook
    latest_note: rcn.ClinicalNote | None = None
    structuring_lock = asyncio.Lock()
    pending_rerun = False
    stream_start: float | None = None
    session_started_at = datetime.now(timezone.utc).isoformat()

    async def send_json(payload: dict) -> None:
        try:
            await ws.send_text(json.dumps(payload))
        except RuntimeError:
            pass  # socket already closing

    async def maybe_structure() -> None:
        nonlocal pending_rerun, latest_note
        if structuring_lock.locked():
            pending_rerun = True
            return
        async with structuring_lock:
            while True:
                pending_rerun = False
                text = " ".join(finalized)
                t0 = time.monotonic()
                note = await loop.run_in_executor(
                    None, rcn.structure_transcript, text, latest_note
                )
                structuring_ms = (time.monotonic() - t0) * 1000
                structuring_latencies.append(structuring_ms / 1000)
                latest_note = note
                await send_json(
                    {
                        "type": "note",
                        "note": note.model_dump(),
                        "structuring_ms": round(structuring_ms, 1),
                    }
                )
                if not pending_rerun:
                    break

    def on_event(event: RealtimeSessionEvent) -> None:
        if isinstance(event, SessionStarted):
            loop.create_task(
                send_json(
                    {
                        "type": "session_started",
                        "session_id": event.session_id,
                        "model": event.model,
                    }
                )
            )
        elif isinstance(event, TranscriptDelta):
            loop.create_task(send_json({"type": "interim", "text": event.text}))
        elif isinstance(event, TranscriptCompleted):
            ttfs = None
            if stream_start is not None and event.audio_end is not None:
                ttfs = time.monotonic() - stream_start - event.audio_end
                ttfs_values.append(ttfs)
            finalized.append(event.text)
            segments.append(
                {
                    "index": len(finalized) - 1,
                    "text": event.text,
                    "audio_end_s": event.audio_end,
                    "ttfs_ms": round(ttfs * 1000, 1) if ttfs is not None else None,
                }
            )
            loop.create_task(
                send_json(
                    {
                        "type": "final",
                        "text": event.text,
                        "segment_index": len(finalized) - 1,
                        "ttfs_ms": round(ttfs * 1000, 1) if ttfs is not None else None,
                        "ttfs_summary": _summarize(ttfs_values),
                    }
                )
            )
            loop.create_task(maybe_structure())

    session_cm = client.beta.realtime.transcription(
        model=rcn.ASR_MODEL,
        sample_rate=rcn.SAMPLE_RATE,
        event_callback=on_event,
    )
    try:
        session = await session_cm.__aenter__()
    except Exception as e:  # noqa: BLE001 - e.g. transient "no healthy workers"
        await send_json(
            {"type": "error", "message": f"Could not start ASR session: {e!r}"}
        )
        try:
            await ws.close()
        except RuntimeError:
            pass
        return

    # Tell the client the ASR session is actually live *now* -- the client
    # waits for this "ready" signal before starting mic capture/streaming.
    # Without it, the browser (or a test client) could start sending audio
    # the instant the WebSocket opens, while this handshake with Together's
    # realtime API (session_cm.__aenter__ above) is still in flight. Any
    # audio sent during that window sits buffered in the socket and gets
    # drained in a burst the moment we start reading -- which would make
    # `audio_end` (cumulative *appended* audio duration) race ahead of
    # wall-clock elapsed time and produce bogus negative TTFS values.
    await send_json({"type": "ready"})

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"] is not None:
                if stream_start is None:
                    stream_start = time.monotonic()  # t=0 for audio_end offsets
                await session.append(message["bytes"])
            elif "text" in message and message["text"] is not None:
                control = json.loads(message["text"])
                if control.get("type") == "stop":
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001 - surface any ASR/network failure to the UI
        # Together's realtime endpoint occasionally fails over with
        # transient infra errors (e.g. "no healthy workers"). Without this,
        # the exception propagates out of the ASGI handler, the socket
        # closes with no close frame, and the browser is left on a dead
        # connection with no explanation -- bad for a live demo. Report it
        # and fall through to "finally" to still flush/save whatever was
        # captured before the failure.
        await send_json({"type": "error", "message": f"ASR session failed: {e!r}"})
    finally:
        try:
            transcript = await session.flush()
        except Exception:  # noqa: BLE001 - session is already broken; best effort only
            transcript = " ".join(finalized)
        try:
            await session_cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

        # Ensure the last debounced structuring pass (if any) completes, then
        # run one guaranteed final pass over the complete transcript.
        async with structuring_lock:
            pass
        if finalized:
            t0 = time.monotonic()
            try:
                latest_note = await loop.run_in_executor(
                    None, rcn.structure_transcript, " ".join(finalized), latest_note
                )
                structuring_latencies.append(time.monotonic() - t0)
            except Exception:  # noqa: BLE001 - keep whatever note we already had
                pass

        summary = {
            "type": "summary",
            "transcript": transcript,
            "note": latest_note.model_dump() if latest_note else None,
            "ttfs_summary": _summarize(ttfs_values),
            "structuring_summary": _summarize(structuring_latencies),
        }
        await send_json(summary)

        # Persist this session's raw numbers for the marimo notebook.
        log_path = LOGS_DIR / f"session_{int(time.time())}.json"
        log_path.write_text(
            json.dumps(
                {
                    "started_at": session_started_at,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "transcript": transcript,
                    "note": latest_note.model_dump() if latest_note else None,
                    "segments": segments,
                    "ttfs_values_s": ttfs_values,
                    "structuring_latencies_s": structuring_latencies,
                },
                indent=2,
            )
        )
        (LOGS_DIR / "last_session.json").write_text(log_path.read_text())

        try:
            await ws.close()
        except RuntimeError:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
