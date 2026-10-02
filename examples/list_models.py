#!/usr/bin/env python3
"""List Together AI ASR or LLM models with pricing and serverless/dedicated status.

No single Together endpoint answers "what can I run, what does it cost, and
where?", so this joins four sources:

    pricing, type      GET /v1/models                      (full catalog)
    dedicated (v2)     GET /v2/supported-models            (CURRENT dedicated platform)
    dedicated (legacy) GET /v1/models?dedicated=true       (pre-v2 catalog; stale)
    serverless         a real 1-token / 1-second call      (no catalog flag exists)

Serverless needs a live probe because a model can be listed, priced and even
dedicated-capable yet refuse serverless calls ("Unable to access non-serverless
model"). The probe is cheap: max_tokens=1 for chat, one second of silence for
ASR. Skip it with --no-probe.

Usage:
    export TOGETHER_API_KEY=...
    python3 examples/list_models.py --kind asr
    python3 examples/list_models.py --kind llm --search llama
    python3 examples/list_models.py --kind asr --json | jq '.[] | {id, serverless}'
    python3 examples/list_models.py --kind llm --no-probe --json > llm.json

Serverless values in the output:
    yes          call succeeded, or was rate-limited / stream-only (so it exists)
    no           rejected as non-serverless -> dedicated-only
    unknown      probe failed for another reason; see serverless_note
    (skipped)    --no-probe
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import wave

import httpx

BASE = "https://api.together.ai"
KINDS = {"asr": "transcribe", "llm": "chat"}


def silent_wav(seconds: float = 1.0, rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def classify(status: int, body: dict | str) -> tuple[str, str]:
    """Map a probe response to (serverless, note)."""
    if status == 200:
        return "yes", ""
    err = body.get("error", {}) if isinstance(body, dict) else {}
    code, msg = str(err.get("code", "")), str(err.get("message", body))[:110]
    if code == "model_not_available" or "non-serverless" in msg:
        return "no", "dedicated-only (non-serverless)"
    if code == "dedicated_endpoint_not_running" or "deployments are stopped" in msg:
        return "no", "dedicated-only (endpoint exists but is stopped)"
    if code == "streaming_required":
        return "yes", "streaming-only"
    if "websocket streaming" in msg.lower():
        return "yes", "WebSocket-streaming-only (rejects batch)"
    if status == 429:
        return "yes", "rate-limited"
    return "unknown", f"HTTP {status}: {msg}"


async def probe(client: httpx.AsyncClient, sem: asyncio.Semaphore, kind: str, model: str):
    """One request per attempt; retries 5xx / network errors, which are transient
    on a healthy serverless model (gpt-oss-120b returned 200, 200, 503 in a row)."""
    async with sem:
        result = ("unknown", "no response")
        for attempt in range(3):
            try:
                if kind == "asr":
                    r = await client.post(
                        f"{BASE}/v1/audio/transcriptions",
                        files={"file": ("silence.wav", silent_wav(), "audio/wav")},
                        data={"model": model},
                    )
                else:
                    r = await client.post(
                        f"{BASE}/v1/chat/completions",
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                    )
                try:
                    body = r.json()
                except ValueError:
                    body = r.text
                result = classify(r.status_code, body)
                if r.status_code < 500:
                    return result
            except httpx.HTTPError as exc:
                result = ("unknown", type(exc).__name__)
            await asyncio.sleep(1.5 * (attempt + 1))
        return result


def money(x: float | None, places: int = 2) -> str:
    return "—" if not x else f"${x:.{places}f}"


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=KINDS, required=True, help="asr = transcribe, llm = chat")
    ap.add_argument("--search", help="case-insensitive substring filter on model id")
    ap.add_argument("--no-probe", action="store_true", help="skip the live serverless probe")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    key = os.environ.get("TOGETHER_API_KEY")
    if not key:
        sys.exit("set TOGETHER_API_KEY")
    headers = {"Authorization": f"Bearer {key}"}

    async with httpx.AsyncClient(headers=headers, timeout=90) as client:
        catalog = (await client.get(f"{BASE}/v1/models")).json()
        legacy = (await client.get(f"{BASE}/v1/models", params={"dedicated": "true"})).json()
        v2 = (await client.get(f"{BASE}/v2/supported-models", params={"limit": 500})).json()

        want = KINDS[args.kind]
        models = [m for m in catalog if m.get("type") == want]
        if args.search:
            models = [m for m in models if args.search.lower() in m["id"].lower()]
        models.sort(key=lambda m: m["id"])

        legacy_ids = {m["id"].lower() for m in legacy}
        v2_profiles = {
            m["name"].lower(): [
                f'{p.get("gpuCount")}x {p.get("gpuType", "").replace("NVIDIA-", "")}'
                + (f' {p["quantization"]}' if p.get("quantization") else "")
                for p in m.get("deploymentProfiles", [])
            ]
            for m in v2.get("data", [])
        }

        sem = asyncio.Semaphore(args.concurrency)
        if args.no_probe:
            probes = [("(skipped)", "")] * len(models)
        else:
            print(f"probing {len(models)} model(s) for serverless access...", file=sys.stderr)
            probes = await asyncio.gather(*(probe(client, sem, args.kind, m["id"]) for m in models))

    rows = []
    for m, (srv, note) in zip(models, probes):
        pr = m.get("pricing") or {}
        mid = m["id"].lower()
        row = {
            "id": m["id"],
            "type": m["type"],
            "organization": m.get("organization"),
            "serverless": srv,
            "serverless_note": note,
            "dedicated_v2": mid in v2_profiles,
            "dedicated_v2_profiles": v2_profiles.get(mid, []),
            "dedicated_legacy_v1": mid in legacy_ids,
        }
        if args.kind == "asr":
            row["usd_per_audio_minute"] = (pr.get("transcribe") or {}).get("price_per_minute")
        else:
            row["context_length"] = m.get("context_length")
            row["usd_per_1m_input_tokens"] = pr.get("input")
            row["usd_per_1m_output_tokens"] = pr.get("output")
        rows.append(row)

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return

    if args.kind == "asr":
        head = f'{"model":<42}{"$/audio-min":>12}  {"serverless":<11}{"dedicated v2":<14}{"legacy v1"}'
        print(head + "\n" + "-" * len(head))
        for r in rows:
            print(f'{r["id"]:<42}{money(r["usd_per_audio_minute"], 4):>12}  {r["serverless"]:<11}'
                  f'{("yes" if r["dedicated_v2"] else "no"):<14}{"yes" if r["dedicated_legacy_v1"] else "no"}')
    else:
        head = (f'{"model":<50}{"in $/1M":>9}{"out $/1M":>10}  {"serverless":<11}'
                f'{"dedicated v2 (GPUs)":<30}{"legacy v1"}')
        print(head + "\n" + "-" * len(head))
        for r in rows:
            gpus = ", ".join(r["dedicated_v2_profiles"]) or ("yes" if r["dedicated_v2"] else "no")
            print(f'{r["id"]:<50}{money(r["usd_per_1m_input_tokens"]):>9}{money(r["usd_per_1m_output_tokens"]):>10}'
                  f'  {r["serverless"]:<11}{gpus[:28]:<30}{"yes" if r["dedicated_legacy_v1"] else "no"}')
    print(f"\n{len(rows)} model(s). 'dedicated v2' is the current platform; 'legacy v1' is stale — see model-selection-*.md.")


if __name__ == "__main__":
    asyncio.run(main())
