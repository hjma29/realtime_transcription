#!/usr/bin/env python3
"""Benchmark Together AI streaming ASR models on accuracy *and* latency.

Produces the two numbers you actually need to pick an ASR model for a
near-real-time clinical product, measured on the same audio in the same
run so the pair is comparable:

    x-axis  WER            -- normalized word error rate vs. ground truth
    y-axis  TTFS p95       -- time-to-final-segment, the finalization lag

Plus the metric that matters more than either for a clinical product:

    CCER    clinical-critical error rate -- error rate restricted to
            tokens that change patient meaning (dosages, laterality,
            negation, drug/condition terms). See `CRITICAL_TERMS`.

Why not plain WER alone: WER weights every token equally, so
"hypertension" -> "hypotension" (clinically inverted) scores the same as
"the" -> "a" (harmless). A model can win on WER and still be unsafe. CCER
is reported alongside, never instead -- they answer different questions.

Why normalization matters: ASR output has no agreed casing/punctuation,
and spells numbers inconsistently ("148" vs "one forty eight"). Raw WER
against a written reference mostly measures formatting, not hearing. Both
sides are normalized identically before scoring; raw WER is also reported
so you can see how much of the delta was formatting.

Usage:
    export TOGETHER_API_KEY=...

    # 1. build a synthetic smoke-test corpus (exact ground truth, TTS audio)
    python3 examples/benchmark_asr.py --make-sample corpus/

    # 2. sweep models over it
    python3 examples/benchmark_asr.py --manifest corpus/manifest.jsonl \
        --models openai/whisper-large-v3,nvidia/nemotron-3.5-asr-streaming-0.6b \
        --plot asr_tradeoff.png

    # 3. all streaming-capable STT models in the catalog
    python3 examples/benchmark_asr.py --manifest corpus/manifest.jsonl --all

IMPORTANT -- what a TTS corpus can and cannot tell you:
    `--make-sample` renders text with macOS `say`, so ground truth is
    exact and free. That validates the harness and gives a rough relative
    ranking. It is NOT a credible absolute WER: TTS has no disfluency,
    no accent, no crosstalk, no room noise, no mic variation. Publishable
    clinical WER needs real audio -- PriMock57 (open, mock primary-care
    consultations) or your own de-identified recordings. See --help-corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import jiwer
from together import AsyncTogether
from together.realtime import RealtimeSessionEvent, TranscriptCompleted

sys.path.insert(0, str(Path(__file__).parent))
import realtime_clinical_note as rcn  # noqa: E402  (SAMPLE_RATE, load_pcm)

# --- clinical-critical tokens ----------------------------------------------
# Tokens whose corruption changes clinical meaning. Extend per specialty --
# this list is deliberately small and auditable rather than a drug database.
CRITICAL_TERMS: set[str] = {
    # laterality / site
    "left", "right", "bilateral", "upper", "lower", "proximal", "distal",
    # negation and hedging -- flipping these inverts the finding
    "no", "not", "without", "denies", "denied", "negative", "absent",
    "positive", "present", "nil",
    # dose units
    "mg", "mcg", "ml", "g", "units", "unit", "milligrams", "micrograms",
    "daily", "twice", "once", "bid", "tid", "qid", "prn", "hourly",
    # high-confusion clinical pairs
    "hypertension", "hypotension", "hyperglycemia", "hypoglycemia",
    "hyperkalemia", "hypokalemia", "tachycardia", "bradycardia",
    "hypothyroidism", "hyperthyroidism",
    # common high-risk drugs
    "aspirin", "warfarin", "heparin", "insulin", "metformin", "lisinopril",
    "atorvastatin", "amlodipine", "metoprolol", "penicillin", "morphine",
}

_CONTRACTIONS = {
    "can't": "cannot", "won't": "will not", "n't": " not", "'re": " are",
    "'s": " is", "'d": " would", "'ll": " will", "'ve": " have", "'m": " am",
}
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_UNIT_ALIASES = {
    "milligram": "mg", "milligrams": "mg", "microgram": "mcg",
    "micrograms": "mcg", "millilitre": "ml", "milliliter": "ml",
    "millilitres": "ml", "milliliters": "ml", "percent": "%",
}


def _words_to_digits(tokens: list[str]) -> list[str]:
    """Collapse spelled-out number words into digit strings.

    "one hundred forty eight" -> "148", "ninety two" -> "92". Without this,
    WER is dominated by how each model chose to render numbers rather than
    whether it heard them -- which for dosages is exactly the thing you
    care about getting right.
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] not in _UNITS and tokens[i] not in _TENS:
            out.append(tokens[i])
            i += 1
            continue
        total, current, consumed = 0, 0, 0
        while i + consumed < len(tokens):
            t = tokens[i + consumed]
            if t in _UNITS:
                current += _UNITS[t]
            elif t in _TENS:
                current += _TENS[t]
            elif t == "hundred" and current:
                current *= 100
            elif t == "thousand":
                total += (current or 1) * 1000
                current = 0
            elif t == "and" and consumed and i + consumed + 1 < len(tokens) \
                    and tokens[i + consumed + 1] in (_UNITS | _TENS).keys():
                pass  # "a hundred and five"
            else:
                break
            consumed += 1
        if consumed == 0:
            out.append(tokens[i])
            i += 1
        else:
            out.append(str(total + current))
            i += consumed
    return out


def normalize(text: str) -> str:
    """Whisper-style English normalization, applied identically to both sides."""
    t = text.lower()
    for a, b in _CONTRACTIONS.items():
        t = t.replace(a, b)
    # spoken punctuation artifacts the dictation pipeline also strips
    t = re.sub(r"\b(full stop|new paragraph|new para|comma|period)\b", " ", t)
    t = re.sub(r"[^a-z0-9%\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    toks = [_UNIT_ALIASES.get(w, w) for w in t.split()]
    return " ".join(_words_to_digits(toks))


def critical_error_rate(reference: str, hypothesis: str) -> tuple[float, int, int]:
    """Error rate restricted to clinically load-bearing tokens.

    Uses jiwer's alignment so a token only counts as correct when it aligns
    to an identical token -- substitutions, deletions and the reference side
    of an insertion all count against. Numeric tokens are always critical
    (they are dosages, vitals, ages).
    """
    ref_toks, hyp_toks = reference.split(), hypothesis.split()
    out = jiwer.process_words(reference, hypothesis)

    def is_critical(tok: str) -> bool:
        return tok in CRITICAL_TERMS or any(c.isdigit() for c in tok)

    total = sum(1 for t in ref_toks if is_critical(t))
    if total == 0:
        return 0.0, 0, 0
    errors = 0
    for chunk in out.alignments[0]:
        if chunk.type == "equal":
            continue
        for t in ref_toks[chunk.ref_start_idx:chunk.ref_end_idx]:
            if is_critical(t):
                errors += 1
    return errors / total, errors, total


@dataclass
class ClipResult:
    clip: str
    reference: str
    hypothesis: str
    ttfs: list[float] = field(default_factory=list)
    audio_seconds: float = 0.0
    wall_seconds: float = 0.0
    error: str | None = None


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(len(s) * q) - 1))]


async def run_clip(model: str, audio: Path, reference: str) -> ClipResult:
    """Stream one clip at true real-time pace, capturing TTFS and transcript."""
    res = ClipResult(clip=audio.name, reference=reference, hypothesis="")
    client = AsyncTogether()
    pieces: list[str] = []
    stream_start: float | None = None

    def on_event(event: RealtimeSessionEvent) -> None:
        if isinstance(event, TranscriptCompleted):
            if event.text:
                pieces.append(event.text)
            if stream_start is not None and event.audio_end is not None:
                res.ttfs.append(time.monotonic() - stream_start - event.audio_end)

    try:
        async with client.beta.realtime.transcription(
            model=model,
            sample_rate=rcn.SAMPLE_RATE,
            event_callback=on_event,
        ) as session:
            pcm = rcn.load_pcm(audio)
            res.audio_seconds = len(pcm) / (rcn.SAMPLE_RATE * 2)
            chunk = rcn.SAMPLE_RATE * 2 // 10  # 100ms
            pos = 0
            stream_start = time.monotonic()
            while pos < len(pcm):
                await session.append(pcm[pos:pos + chunk])
                pos += chunk
                # Drift-corrected pacing: sleep to the deadline for audio sent
                # so far. A flat sleep(0.1) accumulates scheduling overhead and
                # silently inflates TTFS with sender lag instead of ASR lag.
                target = stream_start + pos / (rcn.SAMPLE_RATE * 2)
                await asyncio.sleep(max(0.0, target - time.monotonic()))
            await session.flush()
        res.wall_seconds = time.monotonic() - stream_start
    except Exception as exc:  # noqa: BLE001 -- report, don't abort the sweep
        res.error = _explain(model, audio, exc)
    res.hypothesis = " ".join(pieces).strip()
    return res


def _explain(model: str, audio: Path, exc: Exception) -> str:
    """Turn opaque connection errors into the actual reason.

    The realtime endpoint answers a bare HTTP 404 for a model that exists in
    the catalog but is not serverless-enabled, and the SDK surfaces that as
    "check your base_url" -- which sends you debugging the wrong thing. Re-ask
    the batch endpoint, which returns the real reason.
    """
    import os

    msg = f"{type(exc).__name__}: {exc}"
    if "404" not in msg:
        return msg[:160]
    try:
        import httpx
        with open(audio, "rb") as fh:
            r = httpx.post(
                "https://api.together.xyz/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {os.environ.get('TOGETHER_API_KEY', '')}"},
                files={"file": (audio.name, fh, "audio/wav")},
                data={"model": model},
                timeout=60,
            )
        detail = r.json().get("error", {}).get("message", "")
        if "non-serverless" in detail:
            return "DEDICATED-ONLY (not serverless) — needs a running endpoint to benchmark"
        if detail:
            return detail[:160]
    except Exception:  # noqa: BLE001 -- probe is best-effort
        pass
    return "realtime 404 — model not serverless/streaming-enabled"


def score(results: list[ClipResult]) -> dict:
    ok = [r for r in results if not r.error and r.hypothesis]
    if not ok:
        err = next((r.error for r in results if r.error), "no transcript returned")
        return {"ok": 0, "total": len(results), "error": err}
    refs_n = [normalize(r.reference) for r in ok]
    hyps_n = [normalize(r.hypothesis) for r in ok]
    ttfs = [t for r in ok for t in r.ttfs]
    ccer = [critical_error_rate(a, b)[0] for a, b in zip(refs_n, hyps_n)]
    audio = sum(r.audio_seconds for r in ok)
    return {
        "ok": len(ok),
        "total": len(results),
        "wer": jiwer.wer(refs_n, hyps_n),
        "wer_raw": jiwer.wer([r.reference for r in ok], [r.hypothesis for r in ok]),
        "cer": jiwer.cer(refs_n, hyps_n),
        "ccer": statistics.mean(ccer) if ccer else float("nan"),
        "ttfs_mean": statistics.mean(ttfs) if ttfs else float("nan"),
        "ttfs_p50": _pct(ttfs, 0.50),
        "ttfs_p95": _pct(ttfs, 0.95),
        "segments": len(ttfs),
        "rtf": (sum(r.wall_seconds for r in ok) / audio) if audio else float("nan"),
    }


# --- synthetic corpus -------------------------------------------------------
SAMPLE_SCRIPTS = [
    ("cardiology", "Patient is a 54 year old male presenting with intermittent "
     "chest tightness on exertion for the past three weeks. He has a history of "
     "hypertension and type 2 diabetes. Blood pressure today is 148 over 92, "
     "heart rate 78. There is no radiation to the left arm and no associated "
     "shortness of breath. Plan is to order an ECG and start aspirin 81 mg "
     "daily, follow up in two weeks."),
    ("endocrine", "This is a 63 year old female with poorly controlled type 2 "
     "diabetes. She denies hypoglycemia episodes. Hemoglobin A1c is 9.2 percent, "
     "up from 8.1 percent. Current medication is metformin 1000 mg twice daily. "
     "We will add insulin glargine 10 units at bedtime and recheck in 3 months."),
    ("ortho", "A 41 year old male with right knee pain after a fall, no prior "
     "injury to the left knee. There is swelling over the lateral joint line "
     "and tenderness distal to the patella. X-ray shows no acute fracture. "
     "Plan is ibuprofen 600 mg three times daily and physical therapy."),
]


def make_sample(out: Path) -> Path:
    """Render TTS clips with exact ground truth. macOS `say` + `ffmpeg`."""
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.jsonl"
    rows = []
    for name, text in SAMPLE_SCRIPTS:
        aiff, wav = out / f"{name}.aiff", out / f"{name}.wav"
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
             "-ar", str(rcn.SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
            check=True,
        )
        aiff.unlink(missing_ok=True)
        rows.append({"audio": str(wav), "reference": text})
        print(f"  wrote {wav.name}")
    manifest.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"\nmanifest: {manifest}  ({len(rows)} clips)")
    print("NOTE: TTS audio validates the harness and gives a relative ranking.")
    print("      It is not a credible absolute WER -- see --help-corpus.")
    return manifest


CORPUS_HELP = """\
Getting ground-truth audio that produces a defensible WER
=========================================================

TTS audio (--make-sample) is for validating this harness, not for quoting
numbers. It has no disfluency, accent, crosstalk, room noise or mic
variation, so every model scores unrealistically well and the ranking can
invert versus real speech.

Real options, in order of effort:

1. PriMock57 -- open, 57 mock primary-care consultations with human
   transcripts, released by Babylon Health under a permissive licence.
   The closest open proxy to clinical dictation.
       https://github.com/babylonhealth/primock57

2. Your own de-identified recordings. The only corpus that reflects your
   actual mics, accents, specialties and room acoustics -- and therefore
   the only one whose WER predicts production. Needs IRB/BAA review and
   PHI scrubbing before any audio leaves your environment.

3. Generic open ASR corpora (LibriSpeech, Common Voice) to sanity-check
   the harness against published baselines. They contain no clinical
   vocabulary, so they will understate error on drug and condition terms
   -- precisely the tokens that matter.

Whatever you use, hold the corpus fixed across models. A WER delta is only
meaningful when both models heard identical audio.
"""


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Benchmark Together ASR models on WER and latency together.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--manifest", type=Path, help="JSONL of {audio, reference}")
    ap.add_argument("--models", help="comma-separated model IDs")
    ap.add_argument("--all", action="store_true", help="sweep catalog STT models")
    ap.add_argument("--make-sample", type=Path, metavar="DIR",
                    help="generate a synthetic TTS corpus and exit")
    ap.add_argument("--help-corpus", action="store_true",
                    help="how to get real ground-truth audio")
    ap.add_argument("--out", type=Path, help="write raw results JSON")
    ap.add_argument("--plot", type=Path, help="write WER-vs-latency scatter PNG")
    ap.add_argument("--concurrency", type=int, default=1, metavar="N",
                    help="run N simultaneous streams per model (default 1). "
                         "Accuracy is concurrency-independent; latency is not — "
                         "raise this to find where serverless degrades.")
    args = ap.parse_args()

    if args.help_corpus:
        print(CORPUS_HELP)
        return
    if args.make_sample:
        make_sample(args.make_sample)
        return
    if not args.manifest:
        ap.error("--manifest is required (or use --make-sample / --help-corpus)")

    rows = [json.loads(ln) for ln in args.manifest.read_text().splitlines() if ln.strip()]
    if args.all:
        models = [
            "openai/whisper-large-v3",
            "nvidia/parakeet-tdt-0.6b-v3",
            "nvidia/nemotron-3.5-asr-streaming-0.6b",
            "nvidia/nemotron-3-asr-streaming-0.6b",
            "deepgram/flux",
            "deepgram/nova-3-en",
            "deepgram/nova-3-multi",
        ]
    elif args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        models = [rcn.ASR_MODEL]

    print(f"{len(rows)} clip(s) x {len(models)} model(s), streamed at real-time pace")
    if args.concurrency > 1:
        print(f"concurrency: {args.concurrency} simultaneous streams per model")
    print()
    summary: dict[str, dict] = {}
    raw: dict[str, list] = {}
    for model in models:
        print(f"-> {model}")
        results = []
        if args.concurrency > 1:
            # Replay the corpus N times in parallel. Serverless latency degrades
            # under contention; this is the measurement that tells you whether
            # reserved capacity is actually buying anything.
            tasks = [
                run_clip(model, Path(row["audio"]), row["reference"])
                for _ in range(args.concurrency)
                for row in rows
            ]
            results = await asyncio.gather(*tasks)
            fails = sum(1 for r in results if r.error)
            print(f"     {len(results)} streams, {fails} failed")
        else:
            for row in rows:
                r = await run_clip(model, Path(row["audio"]), row["reference"])
                print(f"     {r.clip:24} {r.error or f'{len(r.ttfs)} segs'}")
                results.append(r)
        summary[model] = score(results)
        raw[model] = [vars(r) for r in results]

    hdr = (f"\n{'model':<42}{'WER':>8}{'raw':>8}{'CER':>8}{'CCER':>8}"
           f"{'TTFSp50':>10}{'TTFSp95':>10}{'RTF':>7}")
    print(hdr)
    print("-" * len(hdr.strip("\n")))
    for model, s in summary.items():
        if not s.get("ok"):
            print(f"{model:<42}  FAILED  {s.get('error','')[:60]}")
            continue
        print(f"{model:<42}{s['wer']:>7.1%}{s['wer_raw']:>8.1%}{s['cer']:>8.1%}"
              f"{s['ccer']:>8.1%}{s['ttfs_p50'] * 1000:>9.0f}ms"
              f"{s['ttfs_p95'] * 1000:>9.0f}ms{s['rtf']:>7.2f}")
    print("\nWER/CER/CCER on normalized text; 'raw' is unnormalized (formatting-sensitive).")
    print("CCER = error rate over dosage/laterality/negation/drug tokens only.")
    print("TTFS = lag from end of spoken segment to finalized transcript.")

    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "raw": raw}, indent=2))
        print(f"\nwrote {args.out}")
    if args.plot:
        plot(summary, args.plot)


def plot(summary: dict[str, dict], path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("plot skipped: pip install matplotlib")
        return
    pts = [(m, s) for m, s in summary.items() if s.get("ok")]
    if not pts:
        print("plot skipped: no successful models")
        return
    fig, ax = plt.subplots(figsize=(9, 6))
    for model, s in pts:
        ax.scatter(s["wer"] * 100, s["ttfs_p95"] * 1000, s=90, zorder=3)
        ax.annotate(model.split("/")[-1], (s["wer"] * 100, s["ttfs_p95"] * 1000),
                    textcoords="offset points", xytext=(8, 4), fontsize=9)
    ax.set_xlabel("WER % (lower is better)")
    ax.set_ylabel("TTFS p95 — finalization lag, ms (lower is better)")
    ax.set_title("ASR streaming: accuracy vs. latency (single concurrency)")
    ax.grid(alpha=0.3, zorder=0)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    print(f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
