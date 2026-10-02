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
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
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

# Bump this whenever the demo's behaviour changes. It's rendered in the page
# header next to a short build hash of the static assets, so during a live
# demo (or mid-iteration) you can tell at a glance whether the browser is
# actually running the current code or a stale cached copy.
APP_VERSION = "1.3"


class NoCacheStaticFiles(StaticFiles):
    """Plain StaticFiles lets browsers cache app.js/style.css indefinitely,
    so edits made during active demo-prep (like this one) silently keep
    serving a stale cached copy even after a hard page reload -- very
    confusing mid-iteration. This is a local single-user demo, not a
    production asset pipeline, so just disable caching outright instead of
    fiddling with cache-busting query params on every edit.
    """

    def file_response(self, *args, **kwargs):  # noqa: ANN002, ANN003
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response


def asset_version() -> str:
    """Short hash of the newest static-file mtime, injected into asset URLs.

    The no-cache headers above only help for responses the browser fetches
    *after* the header was added -- an entry Chrome already cached under the
    bare "/static/app.js" URL (from before, when StaticFiles sent no
    Cache-Control at all and Chrome applied heuristic freshness) keeps being
    served from disk cache without revalidating. Versioning the URL itself
    sidesteps that entirely: "/static/app.js?v=<hash>" is a URL the browser
    has never seen before, so it must fetch it. Recomputed per request so
    editing a file is picked up by the next plain reload, no restart needed.
    """
    newest = max(
        (p.stat().st_mtime for p in STATIC_DIR.iterdir() if p.is_file()), default=0.0
    )
    return hashlib.sha1(f"{newest}".encode()).hexdigest()[:10]


app = FastAPI()
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> HTMLResponse:
    html = (
        (STATIC_DIR / "index.html")
        .read_text()
        .replace("__ASSET_V__", asset_version())
        .replace("__APP_VERSION__", APP_VERSION)
    )
    return HTMLResponse(
        html, headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
    )


async def structure_transcript_streaming(
    client: AsyncTogether, transcript: str, previous_note: rcn.ClinicalNote | None
) -> tuple[rcn.ClinicalNote, dict]:
    """Same prompt/schema as rcn.structure_transcript, but streamed so we can
    report TTFT (time to first token) and TPS (output tokens/sec) -- the two
    standard LLM-serving latency/throughput numbers, distinct from the
    end-to-end "structuring_ms" wall-clock figure already tracked.
    """
    user_content = transcript
    if previous_note is not None:
        user_content = (
            "Previously extracted note (treat as a sticky baseline — keep "
            "every field unless the new transcript below clearly "
            "contradicts or refines it; never drop a confirmed field just "
            "because the newest sentence is incomplete):\n"
            f"{json.dumps(previous_note.model_dump())}\n\n"
            f"Full transcript so far:\n{transcript}"
        )

    t0 = time.monotonic()
    ttft_s: float | None = None
    text_parts: list[str] = []
    usage = None

    stream = await client.chat.completions.create(
        model=rcn.STRUCTURING_MODEL,
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
                    "been said so far. For draft_billing_codes: as soon as you can "
                    "identify a probable diagnosis or procedure/service from the "
                    "dictation (even a single symptom or plan item is enough), ALWAYS "
                    "include your single best-guess code — do not leave this empty "
                    "just because you are not 100% certain. Format each entry as "
                    "'ICD-10-CM <code> - <short label>' for diagnoses and 'CPT <code> "
                    "- <short label>' for procedures/E&M services, e.g. 'ICD-10-CM "
                    "R51.9 - Headache, unspecified'. These are draft suggestions, not "
                    "a verified lookup — requires_human_review must always be true "
                    "regardless of your confidence. If a previously extracted note is "
                    "provided, update it incrementally rather than re-deriving "
                    "everything from scratch — keep confirmed fields stable across "
                    "updates."
                ),
            },
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "clinical_note",
                "schema": rcn.ClinicalNote.model_json_schema(),
            },
        },
        stream=True,
    )
    async for chunk in stream:
        if ttft_s is None:
            ttft_s = time.monotonic() - t0
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                text_parts.append(delta)
        if getattr(chunk, "usage", None):
            usage = chunk.usage

    total_s = time.monotonic() - t0
    note = rcn.ClinicalNote.model_validate_json("".join(text_parts))
    # Same compliance guardrail as rcn.structure_transcript.
    note.requires_human_review = True

    completion_tokens = getattr(usage, "completion_tokens", None)
    gen_s = max(total_s - (ttft_s or 0), 1e-6)  # time spent generating, excl. TTFT
    metrics = {
        "ttft_ms": round(ttft_s * 1000, 1) if ttft_s is not None else None,
        "total_ms": round(total_s * 1000, 1),
        "completion_tokens": completion_tokens,
        "tps": round(completion_tokens / gen_s, 1) if completion_tokens else None,
    }
    return note, metrics


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
    ttft_values_ms: list[float] = []
    tps_values: list[float] = []
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
                note, llm_metrics = await structure_transcript_streaming(
                    client, text, latest_note
                )
                structuring_ms = (time.monotonic() - t0) * 1000
                structuring_latencies.append(structuring_ms / 1000)
                if llm_metrics.get("ttft_ms") is not None:
                    ttft_values_ms.append(llm_metrics["ttft_ms"])
                if llm_metrics.get("tps") is not None:
                    tps_values.append(llm_metrics["tps"])
                latest_note = note
                await send_json(
                    {
                        "type": "note",
                        "note": note.model_dump(),
                        "structuring_ms": round(structuring_ms, 1),
                        "llm_metrics": llm_metrics,
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
            # Whisper occasionally finalizes an empty/whitespace-only segment
            # (an artifact around silence). Dropping it keeps the transcript
            # free of blank timestamped rows, keeps the structuring input
            # clean, and avoids polluting TTFS stats with a meaningless
            # zero-length utterance.
            if not event.text or not event.text.strip():
                return
            ttfs = None
            if stream_start is not None and event.audio_end is not None:
                ttfs = time.monotonic() - stream_start - event.audio_end
                ttfs_values.append(ttfs)
            finalized.append(event.text)
            segments.append(
                {
                    "index": len(finalized) - 1,
                    "text": event.text,
                    "audio_start_s": event.audio_start,
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
                        "audio_start_s": event.audio_start,
                        "audio_end_s": event.audio_end,
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
                latest_note, llm_metrics = await structure_transcript_streaming(
                    client, " ".join(finalized), latest_note
                )
                structuring_latencies.append(time.monotonic() - t0)
                if llm_metrics.get("ttft_ms") is not None:
                    ttft_values_ms.append(llm_metrics["ttft_ms"])
                if llm_metrics.get("tps") is not None:
                    tps_values.append(llm_metrics["tps"])
            except Exception:  # noqa: BLE001 - keep whatever note we already had
                pass

        llm_summary = {
            "ttft_ms_mean": round(statistics.mean(ttft_values_ms), 1) if ttft_values_ms else None,
            "ttft_ms_last": round(ttft_values_ms[-1], 1) if ttft_values_ms else None,
            "tps_mean": round(statistics.mean(tps_values), 1) if tps_values else None,
            "tps_last": round(tps_values[-1], 1) if tps_values else None,
        }
        summary = {
            "type": "summary",
            "transcript": transcript,
            "note": latest_note.model_dump() if latest_note else None,
            "ttfs_summary": _summarize(ttfs_values),
            "structuring_summary": _summarize(structuring_latencies),
            "llm_summary": llm_summary,
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
                    "ttft_values_ms": ttft_values_ms,
                    "tps_values": tps_values,
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
