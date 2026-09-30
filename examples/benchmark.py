#!/usr/bin/env python3
"""Proof-of-concept latency + reliability benchmark for realtime_clinical_note.py.

Runs the ASR -> incremental-structuring pipeline against a fixed WAV file
N times (deterministic input, so runs are comparable) and reports:

  - reliability: what fraction of runs completed without an exception and
    produced a non-empty final note
  - end-to-end pipeline latency: wall-clock time from first audio byte to
    the last structured note
  - per-utterance LLM structuring latency: how long each individual
    structure_transcript() call took (mean/median/p95/min/max) -- this is
    the number that matters for "near-real-time" claims, since ASR itself
    streams continuously regardless of LLM speed

Does not modify realtime_clinical_note.py: latency is measured by
temporarily monkey-patching its module-level `structure_transcript`
reference with a timing wrapper for the duration of each run.

Usage:
    export TOGETHER_API_KEY=...
    python3 examples/benchmark.py ~/Downloads/JonesMedical.wav --runs 5
"""

from __future__ import annotations

import asyncio
import functools
import statistics
import sys
import time
from pathlib import Path

import realtime_clinical_note as rcn


async def run_once(path: Path) -> dict:
    """Run the pipeline once against a WAV file, with structuring latency timed."""
    latencies: list[float] = []
    original_structure = rcn.structure_transcript

    def timed_structure(transcript, previous_note=None):
        start = time.monotonic()
        result = original_structure(transcript, previous_note)
        latencies.append(time.monotonic() - start)
        return result

    rcn.structure_transcript = timed_structure
    start = time.monotonic()
    try:
        audio_source = functools.partial(rcn.transcribe_file, path)
        transcript, note = await rcn.run_session(audio_source)
        elapsed = time.monotonic() - start
        ok = note is not None and bool(transcript.strip())
        return {
            "ok": ok,
            "total_s": elapsed,
            "structuring_latencies_s": list(latencies),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 - benchmark: any failure counts against reliability
        return {
            "ok": False,
            "total_s": time.monotonic() - start,
            "structuring_latencies_s": list(latencies),
            "error": repr(e),
        }
    finally:
        rcn.structure_transcript = original_structure


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
        raise SystemExit("usage: benchmark.py <wav-file> [--runs N]")
    path = Path(sys.argv[1])
    runs = 5
    if "--runs" in sys.argv:
        runs = int(sys.argv[sys.argv.index("--runs") + 1])

    results = []
    for i in range(runs):
        print(f"--- run {i + 1}/{runs} ---")
        result = await run_once(path)
        results.append(result)
        status = "OK" if result["ok"] else f"FAIL ({result['error']})"
        print(
            f"  {status}  total={result['total_s']:.1f}s  "
            f"structuring calls={len(result['structuring_latencies_s'])}"
        )

    successes = sum(1 for r in results if r["ok"])
    all_latencies = [lat for r in results for lat in r["structuring_latencies_s"]]
    totals = [r["total_s"] for r in results]

    print("\n=== summary ===")
    print(
        f"reliability: {successes}/{runs} runs succeeded "
        f"({successes / runs * 100:.0f}%)"
    )
    print(f"end-to-end pipeline latency: {summarize(totals)}")
    print(f"per-utterance LLM structuring latency: {summarize(all_latencies)}")


if __name__ == "__main__":
    asyncio.run(main())
