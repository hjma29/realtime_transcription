#!/usr/bin/env python3
"""Evaluate structuring LLMs against the golden dictation, with cost.

Replays the *real* pipeline: the dictation is revealed one sentence at a time and
each call gets the cumulative transcript plus the previous note ("sticky
baseline"), exactly as `web/server.py` does. The final note is graded against a
fixed rubric; token usage across all calls gives the real serverless cost per
visit, which is then set against the dedicated GPU price for the model's v2
footprint.

    export TOGETHER_API_KEY=...
    python3 eval/run_llm_eval.py                      # default candidates, 3 runs
    python3 eval/run_llm_eval.py --models zai-org/GLM-5.2 --runs 5
    python3 eval/run_llm_eval.py --transcript asr_output.txt   # grade ASR -> LLM end to end

Uses the production prompt and schema from examples/realtime_clinical_note.py
(`build_messages`, `response_format`), so there is no copy to drift.

What the rubric is (and is not): 20 pass/fail content checks written from the
script in eval/golden-dictation.md, plus error flags and expected billing codes.
The checks are regexes over the note text. They are a consistent yardstick, not a
clinician's judgement; read the saved notes before trusting a close call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import realtime_clinical_note as rcn  # noqa: E402

API = "https://api.together.ai"
DEFAULT_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "deepseek-ai/DeepSeek-V4.1-Flash",
    "zai-org/GLM-5.2",
    "zai-org/GLM-5.3",
    "deepseek-ai/DeepSeek-V4-Pro-0813",
    "MiniMaxAI/MiniMax-M3",
    "openai/gpt-oss-120b",
    "Qwen/Qwen3.5-9B",
]
# Not reasoning models: never send the reasoning switch.
NO_REASONING_FLAG = {"meta-llama/Llama-3.3-70B-Instruct-Turbo"}
# Set by --thinking: leave the model in its default thinking mode (no switch sent).
THINKING = False
GPU_KEY = {"NVIDIA-H100": "h100-80gb", "NVIDIA-B200": "b200-180gb"}

# --- rubric ------------------------------------------------------------------
NEG = r"(?:no|denies|denied|without|negative for|absence of|not)"


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I | re.S) is not None


FACTS = {
    "age 52 male": lambda t, n: _has(r"\b52\b", t) and _has(r"\bmale\b|\bman\b", t),
    "hypertension follow-up": lambda t, n: _has(r"hypertension", t),
    "elevated cholesterol": lambda t, n: _has(r"cholesterol|hyperlipid", t),
    "takes BP medication regularly": lambda t, n: _has(r"regularly|adheren|complian", t),
    "NEGATED chest pain": lambda t, n: _has(rf"{NEG}\b[^.;]{{0,45}}chest pain|chest pain[^.;]{{0,20}}(?:absent|denied)", t),
    "NEGATED shortness of breath": lambda t, n: _has(rf"{NEG}\b[^.;]{{0,60}}(?:shortness of breath|dyspnea)|(?:shortness of breath|dyspnea)[^.;]{{0,20}}(?:absent|denied)", t),
    "BP 138/86": lambda t, n: _has(r"138\s*(?:/|over)\s*86", t),
    "weight stable": lambda t, n: _has(r"weight[^.;]{0,30}stable|stable weight", t),
    "well appearing, no acute distress": lambda t, n: _has(r"well[- ]appearing", t) and _has(r"no acute distress|nad\b|not in acute distress", t),
    "heart regular rate and rhythm": lambda t, n: _has(r"regular rate and rhythm|\brrr\b|regular rhythm", t),
    "NEGATED murmurs": lambda t, n: _has(r"no murmurs?|without murmurs?|absence of murmurs?|murmurs?[^.;]{0,10}(?:absent|none)", t),
    "lungs clear bilaterally": lambda t, n: _has(r"lungs?[^.;]{0,40}clear|clear[^.;]{0,25}(?:lungs?|bilateral)", t),
    "assessment lists hypertension": lambda t, n: _has(r"hypertension", n["assessment"]),
    "assessment lists hyperlipidemia": lambda t, n: _has(r"hyperlipid|cholesterol", n["assessment"]),
    "lisinopril 10 mg": lambda t, n: _has(r"lisinopril[^.;]{0,30}\b10\s*(?:mg|milligrams?)|\b10\s*(?:mg|milligrams?)[^.;]{0,30}lisinopril", t),
    "atorvastatin 20 mg": lambda t, n: _has(r"atorvastatin[^.;]{0,30}\b20\s*(?:mg|milligrams?)|\b20\s*(?:mg|milligrams?)[^.;]{0,30}atorvastatin", t),
    "medications daily": lambda t, n: _has(r"daily|once a day|every day", n["plan"]),
    "basic metabolic panel ordered": lambda t, n: _has(r"basic metabolic panel|\bbmp\b", t),
    "lipid panel ordered": lambda t, n: _has(r"lipid panel", t),
    "labs before next visit": lambda t, n: _has(r"(?:before|prior to)[^.;]{0,20}(?:next|his)[^.;]{0,15}visit|next visit", t),
}

# Things the dictation does NOT contain. Each hit is a fabrication.
HALLUCINATED_DRUGS = r"aspirin|metformin|amlodipine|losartan|metoprolol|hydrochlorothiazide|insulin|warfarin|simvastatin|rosuvastatin|clopidogrel"
HALLUCINATED_DX = r"diabetes|angina|coronary|atheroscl|heart failure|\bchf\b|copd|asthma|pneumonia|kidney disease|\bckd\b"
AUDIO_TAG_LEAK = r"cough|clears? (?:his )?throat|short pause|thoughtful"

EXPECTED_CODES = {
    "I10 hypertension": lambda c: any(x.startswith("I10") for x in c["icd"]),
    "E78.x hyperlipidemia": lambda c: any(x.startswith("E78") for x in c["icd"]),
    "CPT 80048 BMP": lambda c: "80048" in c["cpt"],
    "CPT 80061 lipid panel": lambda c: "80061" in c["cpt"],
}
ALLOWED_ICD_PREFIX = ("I10", "E78", "Z79", "Z71", "Z13", "Z00", "Z09")


def flatten(x) -> str:
    if isinstance(x, (list, tuple)):
        return " ; ".join(flatten(i) for i in x)
    if isinstance(x, dict):
        return " ; ".join(flatten(v) for v in x.values())
    return "" if x is None else _plain(str(x))


def _plain(s: str) -> str:
    """Models sometimes emit typographic characters (a non-breaking hyphen in
    'well-appearing', a narrow no-break space before units). Fold them to ASCII
    so the regexes compare content, not typography."""
    s = re.sub("[\u2010-\u2015\u2212]", "-", s)
    return re.sub("[\u00a0\u2009\u202f]", " ", s)


def parse_codes(codes: list[str]) -> dict:
    icd, cpt = [], []
    for s in codes:
        m = re.search(r"ICD-10-CM\s+([A-Z]\d[\w.]*)", s, re.I)
        if m:
            icd.append(m.group(1).upper())
        m = re.search(r"CPT\s+(\d{5})", s, re.I)
        if m:
            cpt.append(m.group(1))
    return {"icd": icd, "cpt": cpt}


def grade(note: dict) -> dict:
    n = {k: flatten(v) for k, v in note.items()}
    text = " ; ".join(n.values())
    facts = {name: bool(fn(text, n)) for name, fn in FACTS.items()}
    errors = []
    for m in re.finditer(HALLUCINATED_DRUGS, text, re.I):
        errors.append(f"unsupported drug: {m.group(0)}")
    for m in re.finditer(HALLUCINATED_DX, text, re.I):
        errors.append(f"unsupported condition: {m.group(0)}")
    for drug, ok in (("lisinopril", "10"), ("atorvastatin", "20")):
        for m in re.finditer(rf"{drug}[^.;]{{0,30}}?\b(\d+)\s*(?:mg|milligrams?)", text, re.I):
            if m.group(1) != ok:
                errors.append(f"wrong {drug} dose: {m.group(1)} mg")
    for m in re.finditer(r"\b(\d{2,3})\s*(?:/|over)\s*(\d{2,3})\b", text):
        if (m.group(1), m.group(2)) != ("138", "86"):
            errors.append(f"wrong BP: {m.group(0)}")
    if re.search(AUDIO_TAG_LEAK, text, re.I):
        errors.append("audio-tag/cough leaked into note")
    codes = parse_codes(note.get("draft_billing_codes") or [])
    code_hits = {name: bool(fn(codes)) for name, fn in EXPECTED_CODES.items()}
    odd_icd = [c for c in codes["icd"] if not c.startswith(ALLOWED_ICD_PREFIX)]
    if odd_icd:
        errors.append(f"unexpected diagnosis code(s): {', '.join(odd_icd)}")
    return {
        "facts_passed": sum(facts.values()),
        "facts_total": len(facts),
        "facts_failed": [k for k, v in facts.items() if not v],
        "errors": errors,
        "codes_found": sum(code_hits.values()),
        "codes_total": len(code_hits),
        "codes_missing": [k for k, v in code_hits.items() if not v],
        "codes": note.get("draft_billing_codes"),
    }


# --- API helpers ---------------------------------------------------------------
def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


async def post(client: httpx.AsyncClient, body: dict) -> httpx.Response:
    last = None
    for attempt in range(4):
        try:
            r = await client.post(f"{API}/v1/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503, 504):
                last = r
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return r
        except httpx.HTTPError:
            await asyncio.sleep(1.5 * (attempt + 1))
    if last is not None:
        return last
    raise RuntimeError("request failed after retries")


async def call(client, model: str, transcript: str, prev, state: dict):
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 16000 if THINKING else 3000,  # thinking tokens count against the cap
        "messages": rcn.build_messages(transcript, prev),
        "response_format": rcn.response_format(),
    }
    use_flag = model not in NO_REASONING_FLAG and state.get("flag", True) and not THINKING
    if use_flag:
        body["reasoning"] = {"enabled": False}
    t0 = time.perf_counter()
    r = await post(client, body)
    if r.status_code == 400 and use_flag and "reason" in r.text.lower():
        state["flag"] = False  # model rejects the switch: run it as-is
        body.pop("reasoning")
        t0 = time.perf_counter()
        r = await post(client, body)
    dt = time.perf_counter() - t0
    if r.status_code != 200:
        err = r.json().get("error", {}).get("message", r.text)[:80] if r.headers.get("content-type", "").startswith("application/json") else r.text[:80]
        return dt, None, None, f"HTTP {r.status_code}: {err}"
    data = r.json()
    content = data["choices"][0]["message"].get("content") or ""
    usage = data.get("usage") or {}
    try:
        note = rcn.ClinicalNote.model_validate_json(content)
    except Exception:
        return dt, None, usage, f"invalid JSON ({len(content)} chars)"
    return dt, note, usage, ""


async def run_visit(client, model: str, steps: list[str], state: dict) -> dict:
    prev, per_call, ptok, ctok, err = None, [], 0, 0, ""
    raw_review = None
    for i in range(1, len(steps) + 1):
        dt, note, usage, e = await call(client, model, " ".join(steps[:i]), prev, state)
        per_call.append(dt)
        if usage:
            ptok += usage.get("prompt_tokens", 0)
            ctok += usage.get("completion_tokens", 0)
        if note is None:
            err = e
            break
        raw_review = note.requires_human_review
        note.requires_human_review = True  # same guardrail as production
        prev = note
    return {"calls": per_call, "prompt_tokens": ptok, "completion_tokens": ctok, "note": prev.model_dump() if prev else None,
            "error": err, "raw_review_flag": raw_review, "reasoning_flag_sent": state.get("flag", True) and model not in NO_REASONING_FLAG}


async def evaluate(model: str, steps: list[str], tagged: str, runs: int, key: str) -> dict:
    out = {"model": model, "runs": [], "leak": None}
    state: dict = {}
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=180) as client:
        for _ in range(runs):
            out["runs"].append(await run_visit(client, model, steps, state))
        # robustness: one shot on the transcript that still contains [coughs] etc.
        dt, note, _, e = await call(client, model, tagged, None, state)
        if note is not None:
            out["leak"] = bool(re.search(AUDIO_TAG_LEAK, flatten(note.model_dump()), re.I))
    return out


async def fetch_context(key: str) -> dict:
    h = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(headers=h, timeout=60) as c:
        models = {m["id"]: m for m in (await c.get(f"{API}/v1/models")).json()}
        v2 = (await c.get(f"{API}/v2/supported-models", params={"limit": 500})).json().get("data", [])
        it = (await c.get(f"{API}/v2/public/inference-instance-types")).json()
    it = it.get("data", it) if isinstance(it, dict) else it
    gpu_price = {(x["gpuType"], x["gpuCount"]): x["priceCentsPerHour"] / 100 for x in it}
    profiles = {m["name"]: m.get("deploymentProfiles", []) for m in v2}
    return {"models": models, "profiles": profiles, "gpu_price": gpu_price}


def dedicated(model: str, ctx: dict) -> tuple[str, float | None]:
    best = None
    for p in ctx["profiles"].get(model, []):
        price = ctx["gpu_price"].get((GPU_KEY.get(p.get("gpuType"), ""), p.get("gpuCount")))
        if price is None:
            continue
        label = f'{p["gpuCount"]}x {p["gpuType"].replace("NVIDIA-", "")} {p.get("quantization", "")}'.strip()
        if best is None or price < best[1]:
            best = (label, price)
    return best if best else ("not in v2", None)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--thinking", action="store_true", help="leave reasoning models in their default thinking mode (default: switch it off)")
    ap.add_argument("--transcript", type=Path, default=ROOT / "eval/golden-dictation.clean.txt")
    ap.add_argument("--tagged", type=Path, default=ROOT / "eval/golden-dictation.txt")
    ap.add_argument("--regrade", type=Path, help="re-score the notes saved in this results file (no model calls)")
    ap.add_argument("--out", type=Path, default=ROOT / f"eval/results/llm-{date.today():%Y-%m-%d}.json")
    a = ap.parse_args()
    global THINKING
    THINKING = a.thinking
    key = os.environ.get("TOGETHER_API_KEY") or sys.exit("set TOGETHER_API_KEY")

    steps = sentences(a.transcript.read_text())
    tagged = a.tagged.read_text().strip()
    print(f"{len(steps)} incremental structuring calls per visit, {a.runs} visit(s) per model\n", file=sys.stderr)
    ctx = await fetch_context(key)
    if a.regrade:
        results = json.loads(a.regrade.read_text())["raw"]
        a.out = a.regrade
    else:
        results = await asyncio.gather(*(evaluate(m, steps, tagged, a.runs, key) for m in a.models))

    rows = []
    for r in results:
        m = r["model"]
        ok = [x for x in r["runs"] if x["note"]]
        price = ctx["models"].get(m, {}).get("pricing", {})
        pin, pout = price.get("input") or 0, price.get("output") or 0
        label, gpu_hr = dedicated(m, ctx)
        row = {"model": m, "valid_runs": f"{len(ok)}/{len(r['runs'])}", "dedicated": label, "dedicated_usd_hr": gpu_hr,
               "usd_per_1m_in": pin, "usd_per_1m_out": pout, "leak": r["leak"]}
        if ok:
            grades = [grade(x["note"]) for x in ok]
            calls = [c for x in ok for c in x["calls"]]
            visit_lat = [sum(x["calls"]) for x in ok]
            pt = statistics.mean(x["prompt_tokens"] for x in ok)
            ct = statistics.mean(x["completion_tokens"] for x in ok)
            cost = (pt * pin + ct * pout) / 1e6
            row.update({
                "call_latency_mean_s": statistics.mean(calls), "call_latency_max_s": max(calls),
                "visit_latency_s": statistics.mean(visit_lat), "prompt_tokens": pt, "completion_tokens": ct,
                "usd_per_visit": cost, "facts": statistics.mean(g["facts_passed"] for g in grades),
                "facts_total": grades[0]["facts_total"], "errors": [e for g in grades for e in g["errors"]],
                "codes": statistics.mean(g["codes_found"] for g in grades), "codes_total": grades[0]["codes_total"],
                "facts_failed": sorted({f for g in grades for f in g["facts_failed"]}),
                "codes_missing": sorted({c for g in grades for c in g["codes_missing"]}),
                "breakeven_visits_per_hr": (gpu_hr / cost) if (gpu_hr and cost) else None,
                "raw_review_flag_ok": all(x["raw_review_flag"] is True for x in ok),
                "reasoning_flag_sent": ok[0]["reasoning_flag_sent"],
            })
        else:
            row["error"] = next((x["error"] for x in r["runs"] if x["error"]), "no valid run")
        rows.append(row)

    rows.sort(key=lambda r: (-(r.get("facts") or -1), r.get("call_latency_mean_s") or 999))
    print(f"{'model':<40}{'valid':>6}{'facts/'+str(rows[0].get('facts_total', 20)):>9}{'codes/4':>8}{'err':>5}{'s/call':>8}{'max':>6}{'$/visit':>10}{'  dedicated footprint':<26}{'$/hr':>8}{'break-even visits/hr':>22}")
    print("-" * 150)
    for r in rows:
        if "error" in r:
            print(f"{r['model']:<40}{r['valid_runs']:>6}  FAILED: {r['error']}")
            continue
        be = f"{r['breakeven_visits_per_hr']:,.0f}" if r["breakeven_visits_per_hr"] else "—"
        hr = f"{r['dedicated_usd_hr']:.2f}" if r["dedicated_usd_hr"] else "—"
        print(f"{r['model']:<40}{r['valid_runs']:>6}{r['facts']:>9.1f}{r['codes']:>8.1f}{len(r['errors']):>5}{r['call_latency_mean_s']:>8.2f}{r['call_latency_max_s']:>6.1f}"
              f"{r['usd_per_visit']:>10.5f}  {r['dedicated']:<24}{hr:>8}{be:>22}")
    print("\nfacts failed / errors / missing codes (union over runs):")
    for r in rows:
        if "error" in r:
            continue
        print(f"  {r['model']}")
        print(f"     failed facts : {r['facts_failed'] or '-'}")
        print(f"     errors       : {sorted(set(r['errors'])) or '-'}")
        print(f"     missing codes: {r['codes_missing'] or '-'} | tag-leak (tagged input): {r['leak']} | raw review flag true: {r['raw_review_flag_ok']} | reasoning flag: {r['reasoning_flag_sent']}")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"date": str(date.today()), "steps": len(steps), "runs": a.runs, "summary": rows,
                                 "raw": results, "gpu_prices_usd_hr": {f"{k[0]}x{k[1]}": v for k, v in ctx["gpu_price"].items()}}, indent=1, default=str))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
