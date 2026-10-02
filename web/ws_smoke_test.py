#!/usr/bin/env python3
"""Simulate the browser against a running demo, headlessly.

An agent has no microphone or browser, so this streams a WAV over the same
WebSocket protocol the page uses and reports what came back. Works against a
local server or a deployed one.

    python3 web/ws_smoke_test.py audio.wav                          # ws://127.0.0.1:8000
    python3 web/ws_smoke_test.py audio.wav https://my-demo.vercel.app --key SECRET

Protocol (see web/server.py):
  1. connect to /ws/dictate (append ?key=... if the deployment requires it)
  2. wait for {"type":"ready"} BEFORE sending audio -- sending earlier corrupts TTFS
  3. send raw 16-bit mono 16 kHz PCM as binary frames, paced ~100 ms per chunk
  4. send {"type":"stop"}, read until {"type":"summary"}

Needs `pip install websockets`. A test WAV on macOS:
  say -o /tmp/s.aiff "..." && ffmpeg -i /tmp/s.aiff -ar 16000 -ac 1 /tmp/s.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from urllib.parse import quote, urlparse

import websockets

SAMPLE_RATE = 16_000
CHUNK_BYTES = SAMPLE_RATE * 2 // 10  # 100 ms of 16-bit mono


def ws_url(base: str, key: str | None) -> str:
    u = urlparse(base if "://" in base else f"http://{base}")
    scheme = "wss" if u.scheme == "https" else "ws"
    qs = f"?key={quote(key)}" if key else ""
    return f"{scheme}://{u.netloc}/ws/dictate{qs}"


async def run(wav: str, base: str, key: str | None, timeout: float) -> int:
    with wave.open(wav, "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
            sys.exit("expected mono 16-bit 16 kHz WAV")
        pcm = w.readframes(w.getnframes())

    url = ws_url(base, key)
    t0 = time.monotonic()
    el = lambda: f"{time.monotonic() - t0:6.1f}s"
    finals: list[str] = []
    note: dict | None = None
    summary: dict | None = None
    errors: list[str] = []

    try:
        async with websockets.connect(url, max_size=None, open_timeout=30) as ws:
            print(f"[{el()}] connected to {url.split('?')[0]}")

            async def reader():
                nonlocal note, summary
                async for raw in ws:
                    m = json.loads(raw)
                    kind = m.get("type")
                    if kind == "final":
                        finals.append(m.get("text", ""))
                    elif kind == "note":
                        note = m.get("note") or m
                    elif kind == "summary":
                        summary = m
                        return
                    elif kind == "error":
                        errors.append(m.get("message", ""))
                        return
                    if kind in ("ready", "session_started", "final", "summary", "error"):
                        print(f"[{el()}] <- {kind}")
                    if kind == "ready":
                        ready.set()

            ready = asyncio.Event()
            rd = asyncio.create_task(reader())
            await asyncio.wait_for(ready.wait(), 30)

            start = time.monotonic()
            for i in range(0, len(pcm), CHUNK_BYTES):
                await ws.send(pcm[i : i + CHUNK_BYTES])
                # Drift-corrected pacing, same reason as examples/measure_ttfs.py.
                await asyncio.sleep(max(0.0, start + (i + CHUNK_BYTES) / (SAMPLE_RATE * 2) - time.monotonic()))
            print(f"[{el()}] audio sent ({len(pcm) / (SAMPLE_RATE * 2):.1f}s); stopping")
            await ws.send(json.dumps({"type": "stop"}))
            await asyncio.wait_for(rd, timeout)
    except websockets.exceptions.InvalidStatus as e:
        print(f"[{el()}] REFUSED: HTTP {e.response.status_code} (wrong or missing access key?)")
        return 2
    except (asyncio.TimeoutError, OSError, websockets.exceptions.WebSocketException) as e:
        print(f"[{el()}] FAILED: {type(e).__name__}: {e}")
        return 1

    print(f"\nfinal segments: {len(finals)}")
    print("transcript:", " ".join(finals)[:300])
    if note:
        n = note.get("note", note) if isinstance(note, dict) else {}
        print("note fields:", [k for k, v in n.items() if v] if isinstance(n, dict) else n)
        print("billing codes:", n.get("draft_billing_codes") if isinstance(n, dict) else None)
        print("requires_human_review:", n.get("requires_human_review") if isinstance(n, dict) else None)
    if summary:
        print("summary:", json.dumps({k: v for k, v in summary.items() if k != "type"})[:400])
    for e in errors:
        print("server error:", e)
    ok = bool(finals) and note is not None and summary is not None and not errors
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    ap.add_argument("--key", help="DEMO_ACCESS_KEY of the deployment")
    ap.add_argument("--timeout", type=float, default=90, help="seconds to wait for the summary")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.wav, a.base, a.key, a.timeout)))
