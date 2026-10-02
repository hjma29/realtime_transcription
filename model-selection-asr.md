# Model Selection — ASR (speech-to-text tier)

Decision record for the **ASR** model slot — the streaming speech-to-text model
that turns a physician's dictation into transcript text — and the evidence
behind it. Last verified **2026-10-02** against the live Together API.

The structuring LLM is covered separately in
[`model-selection-llm.md`](model-selection-llm.md). The two tiers are chosen on
different criteria: the LLM on latency alone (JSON validity is binary), this
one on a genuine **accuracy-vs-latency trade-off** — a faster model that
mishears a dosage is worse than a slower one that doesn't.

| Slot | Model ID                  | Status                                           |
| ---- | ------------------------- | ------------------------------------------------ |
| ASR  | `openai/whisper-large-v3` | **Current.** Streaming, finalized-segment output |

Defined as `ASR_MODEL` in `examples/realtime_clinical_note.py`.

---

## Candidates in the Together catalog

Seven models have `type: transcribe`. Status verified live 2026-10-02:

| Model                                    | Serverless streaming | Dedicated config          |
| ---------------------------------------- | -------------------- | ------------------------- |
| `openai/whisper-large-v3`                | ✅                   | `1x_nvidia_h100_80gb_sxm` |
| `nvidia/parakeet-tdt-0.6b-v3`            | ✅                   | `1x_nvidia_h100_80gb_sxm` |
| `nvidia/nemotron-3-asr-streaming-0.6b`   | ✅                   | `1x_nvidia_h100_80gb_sxm` |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | ✅                   | `1x_nvidia_h100_80gb_sxm` |
| `deepgram/flux`                          | ❌ dedicated-only    | `1x_nvidia_h100_80gb_sxm` |
| `deepgram/nova-3-en`                     | ❌ dedicated-only    | `1x_nvidia_h100_80gb_sxm` |
| `deepgram/nova-3-multi`                  | ❌ dedicated-only    | `1x_nvidia_h100_80gb_sxm` |

The three Deepgram models can't be benchmarked without first standing up an
endpoint, so they are absent from the measured results below.

---

## Pricing

**Serverless: $0.0015 per audio minute ($0.09 per audio hour), identical for
all four serverless STT models.** Sources agree: the model catalog's
`pricing.transcribe.price_per_minute`, and the "Speech-to-Text" rows of the
[serverless models docs](https://docs.together.ai/docs/serverless-models).
Because the four models cost the same, price does not separate them — the
choice is accuracy and latency.

**Dedicated: $0.09 per minute of GPU time ($5.40/hour) for one H100**, from
`GET /v1/hardware` (`cents_per_minute: 9`). It is billed while the endpoint
runs, whether or not it is busy. Cross-checked for linear scaling against the
Llama configs (2x = 18, 4x = 36, 8x = 72). The three Deepgram models have no
serverless price; dedicated is their only option, at the same GPU rate.

### Break-even

`$5.40/hr ÷ $0.09 per audio-hour = 60`. A dedicated H100 only beats serverless
at about **60 audio-hours transcribed per wall-clock hour** — roughly 60 live
streams sustained around the clock. A clinic with morning-peak dictation sits
far below that, so **on cost alone the ASR tier stays serverless for a long
time.** Dedicated ASR is justified by a BAA/compliance requirement or by tail
latency under load, not by price.

Caveats on that arithmetic:

- It assumes one H100 can serve ~60 concurrent streams. **Not measured**, so
  the true break-even is probably higher.
- It ignores idle time, which only makes dedicated look worse.

### Pricing sources that disagree or mislead

- **Ignore the legacy token fields.** The model catalog also carries
  `input: 0.27 / output: 0.85` for Whisper and `input: 0.45` for Nemotron-3.
  These are not ASR pricing; the `transcribe` object is.
- **The marketing page differs.** `together.ai/pricing` lists "Parakeet TDT
  0.6B V3 Realtime" at **$0.0035** under a heading reading "Price per 1M
  Characters" (a TTS label — likely a table glitch, possibly a distinct
  realtime SKU). The docs and API agree with each other at $0.0015, so treat
  those as authoritative, but confirm with Together before quoting a customer.

---

## How to verify a candidate (reproducible)

### 1. Does it exist, and what is the exact ID?

```bash
curl -s -H "Authorization: Bearer $TOGETHER_API_KEY" \
  https://api.together.xyz/v1/models \
  | python3 -c "import json,sys;[print(m['id']) for m in json.load(sys.stdin) if m.get('type')=='transcribe']"
```

### 2. Is it dedicated-capable?

```bash
curl -s -G -H "Authorization: Bearer $TOGETHER_API_KEY" \
  --data-urlencode "model=<id>" https://api.together.xyz/v1/hardware
```

Returns GPU configurations, or `No GPU configurations found for model` if there
is no dedicated path. `GET /v1/models` carries **no** dedicated flag, so this is
the only way to answer the question. Control-tested: bogus IDs return the error,
so a non-empty result is meaningful.

### 3. Is it serverless?

There is no catalog flag. You have to make a call — and **the realtime endpoint
gives a misleading answer.** For a dedicated-only model it returns a bare
`HTTP 404`, which the SDK surfaces as "check that base_url /
TOGETHER_BASE_URL points at an API" — sending you to debug the wrong thing.

Ask the batch endpoint instead; it returns the real reason:

```bash
curl -s -X POST https://api.together.xyz/v1/audio/transcriptions \
  -H "Authorization: Bearer $TOGETHER_API_KEY" \
  -F "file=@clip.wav" -F "model=<id>"
# 400 — "Unable to access non-serverless model <id>. Please visit ...
#        to create and start a new dedicated endpoint."
```

`examples/benchmark_asr.py` does this re-probe automatically and reports
`DEDICATED-ONLY` instead of the SDK's generic connection error.

### 4. Accuracy and latency, together

See below.

---

## Testing WER vs. latency

`examples/benchmark_asr.py` measures accuracy and latency **in the same pass
over the same audio**. That matters: a WER number from one run and a latency
number from another are not a trade-off curve, just two unrelated facts.

```bash
# 1. build a synthetic corpus with exact ground truth (macOS `say`)
python3 examples/benchmark_asr.py --make-sample corpus/

# 2. sweep every streaming STT model in the catalog
python3 examples/benchmark_asr.py --manifest corpus/manifest.jsonl \
    --all --plot asr_tradeoff.png

# 3. latency under load
python3 examples/benchmark_asr.py --manifest corpus/manifest.jsonl \
    --models openai/whisper-large-v3 --concurrency 10
```

Needs `pip install jiwer` (plus `matplotlib` for `--plot`). Audio is streamed at
true real-time pace with drift-corrected scheduling, so the sender never falls
behind and inflates latency with its own lag.

### What it measures, and why each one

| Metric             | Definition                                              | Why it's there                                        |
| ------------------ | ------------------------------------------------------- | ----------------------------------------------------- |
| **WER**            | Word error rate on *normalized* text                    | The headline accuracy number                          |
| **raw WER**        | Same, unnormalized                                      | The gap vs. WER is pure formatting — see below        |
| **CER**            | Character error rate                                    | Catches near-misses WER rounds up to a full error     |
| **CCER**           | Error rate over **clinically load-bearing tokens only** | The number that actually predicts patient harm        |
| **TTFS p50 / p95** | Lag from end of spoken segment to finalized transcript  | Perceived responsiveness. p95 is the one users feel   |
| **RTF**            | Wall time ÷ audio duration                              | Must stay ≈1.0 or the stream falls behind live speech |

### Normalize before scoring, or you measure formatting

ASR output has no agreed casing or punctuation and renders numbers
inconsistently. Measured on Whisper over the sample corpus:

|                   | WER       |
| ----------------- | --------- |
| Raw, unnormalized | **13.2%** |
| Normalized        | **2.4%**  |

Over 80% of the apparent error was `148` vs. `one hundred forty-eight`,
trailing periods, and capitalization. The harness normalizes both sides
identically (casing, punctuation, contractions, spoken-punctuation artifacts,
and spelled-out numbers → digits) and reports raw alongside, so the gap stays
visible rather than hidden.

### Why plain WER is the wrong safety metric

WER weights every token equally. Measured on one reference sentence, with a
single token changed each time:

| Mutation                        | WER  | CCER      |
| ------------------------------- | ---- | --------- |
| inserted filler word "the"      | 7.7% | **0.0%**  |
| `hypertension` → `hypotension`  | 7.7% | **14.3%** |
| aspirin `81 mg` → `8 mg`        | 7.7% | **14.3%** |
| `denies` chest pain → `reports` | 7.7% | **14.3%** |
| `right` knee → `left` knee      | 7.7% | **14.3%** |

**Every row scores identically on WER.** One is harmless; four are a clinically
inverted finding, a 10x dose error, a negation flip, and wrong-site laterality.
CCER restricts scoring to dosages, numbers, laterality, negation and high-risk
drug/condition terms (`CRITICAL_TERMS` in the script — deliberately a small
auditable list, not a drug database).

Report both. WER is comparable to the published literature; CCER is the one to
gate a clinical release on.

---

## Measured results (synthetic corpus, 2026-10-02)

Three TTS clinical dictations, streamed at real-time pace, single concurrency:

| Model                                    | WER      | CCER     | TTFS p50 | TTFS p95  |
| ---------------------------------------- | -------- | -------- | -------- | --------- |
| `openai/whisper-large-v3`                | **2.4%** | 3.7%     | −28ms    | 239ms     |
| `nvidia/parakeet-tdt-0.6b-v3`            | 2.9%     | 3.7%     | −46ms    | 237ms     |
| `nvidia/nemotron-3-asr-streaming-0.6b`   | 3.5%     | **1.9%** | −5ms     | **115ms** |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | 7.1%     | 3.7%     | −13ms    | 231ms     |

These are single runs. **Treat the ordering as a hypothesis, not a result** —
see the variance section next.

The one finding worth carrying forward: `nemotron-3` beats `whisper-large-v3` on
CCER and p95 while losing on WER. That is exactly the inversion that makes a
single-metric comparison dangerous, and it justifies re-testing on real audio.

### How much noise is in these numbers

Repeated runs of the identical audio and model show how large a difference has
to be before it means anything:

| Measurement                 | Observed range               | Note                                    |
| --------------------------- | ---------------------------- | --------------------------------------- |
| Whisper WER, concurrency 1  | 2.4% in all 4 repeats        | Stable                                  |
| Whisper p95, concurrency 1  | 139–239ms over 7 runs (median 156ms) | A 100ms spread from noise alone |
| `nemotron-3` WER            | 3.5% and 4.7% across 2 runs  | 1.2 points apart on identical audio     |

Consequences:

- **p95 differences under ~100ms are not distinguishable** from run-to-run
  noise at this sample size. The tail percentile of a few dozen segments is
  inherently unstable.
- **The CCER gaps above are not established.** Each clip contains only a
  handful of critical tokens, so one token moves CCER by several points. The
  3.7% vs. 1.9% difference is roughly one token.
- A sign note on p50: it comes out slightly negative (finalization appears to
  arrive before the segment's `audio_end` offset elapses). The cause isn't
  verified — plausibly the server's `audio_end` includes trailing-silence
  padding. Read relative ordering, not the sign.

### The caveat that governs all of the above

These numbers come from **TTS audio**, which has no disfluency, accent,
crosstalk, room noise or mic variation. They validate the harness and give a
rough relative ranking. They are **not** a defensible absolute WER, and the
ranking can invert on real speech.

For numbers worth quoting, run the same harness against:

1. **PriMock57** — open, 57 mock primary-care consultations with human
   transcripts (Babylon Health). The closest open proxy to clinical dictation.
2. **Your own de-identified recordings** — the only corpus reflecting your
   actual mics, accents and specialties, and so the only one whose WER
   predicts production. Needs PHI scrubbing and BAA/IRB review first.

`--help-corpus` repeats this guidance in the tool. Hold the corpus fixed across
models, and repeat each run — a single pass is not enough.

---

## Latency under load

Vendor accuracy-vs-latency charts are typically **single-concurrency**, which
says nothing about behavior at a clinic's morning peak. The harness's
`--concurrency N` replays the corpus as N simultaneous streams.

Measured on Whisper, same corpus, repeated runs:

| Concurrency | TTFS p95 per run        | Median | TTFS p50 per run |
| ----------- | ----------------------- | ------ | ---------------- |
| 1           | 224, 146, 156, 156 ms   | ~156ms | −28 to −48 ms    |
| 10          | 250, 266, 273 ms        | 266ms  | −39 to −44 ms    |

What this does and doesn't show:

- **There is a tail-latency effect under load** — roughly **+110ms at p95** at
  10 concurrent streams. It is modest at this scale.
- **It is a tail effect only.** p50 doesn't move. Users feel p95, but the
  typical segment is unaffected.
- **A single comparison can't establish it.** Single-stream p95 alone spans
  139–239ms, overlapping the low end of the c=10 results. An earlier pass of
  this analysis compared one c=1 run against one c=5 run and reported a
  degradation that was actually within single-stream noise. Always repeat.
- **WER was not concurrency-independent in practice.** It held at 2.4% across
  four c=1 runs but ranged 1.8–3.8% across three c=10 runs. Whether that is
  batching nondeterminism or segment-boundary changes under load is not
  established; it argues for measuring accuracy at the concurrency you will
  deploy, not only at c=1.

To turn this into a capacity trigger, sweep `--concurrency` upward (10 is
shallow) with several repeats per level until p95 breaches your budget. That
number — not a list-price comparison — is what justifies reserved capacity.

---

## Dedicated endpoint readiness

All seven STT models deploy dedicated on a single GPU
(`1x_nvidia_h100_80gb_sxm`). There is no dedicated gap on the audio tier. (An
earlier version of `agent-handover.md` claimed there was; that was wrong.)

Three independent sources agree:

1. `GET /v1/hardware?model=<id>` returns a `1x_nvidia_h100_80gb_sxm` config for
   all seven, with `availability.status: available`.
2. `GET /v1/models?dedicated=true` lists all seven `transcribe` models in the
   dedicated catalog.
3. Together's own [speech-to-text docs](https://docs.together.ai/docs/speech-to-text)
   publish a Serverless / Dedicated table: Whisper, Parakeet, Nemotron-3 and
   Nemotron-3.5 are ✅ on both; the three Deepgram models are ❌ serverless,
   ✅ dedicated.

**One source reads differently.** The `togethercomputer/skills` GitHub repo
([`stt-models.md`](https://github.com/togethercomputer/skills/blob/main/skills/together-audio/references/stt-models.md))
has a single-valued *Access* column: Whisper, Parakeet and both Nemotrons are
labeled **"Serverless"**, and Deepgram **"Dedicated / Reserved"**. That is
very likely where the "ASR is serverless-only" impression comes from. It is a
coarse primary-access label, not an exclusivity claim — the same repo's audio
skill says to use the dedicated-inference skill "when the audio model itself
must be hosted on dedicated infrastructure," and the dedicated-inference skill
lists `transcribe` as a catalog category. It is also a snapshot that has drifted
from the live catalog: it uses Deepgram IDs `deepgram/deepgram-flux` and
`deepgram/deepgram-nova-3`, whereas the live API and docs use
`deepgram/flux`, `deepgram/nova-3-en` and `deepgram/nova-3-multi` (the IDs
this repo's harness uses, and that return the real `non-serverless` error).
Neither the GitHub repos nor their issue trackers say anything about realtime
streaming on a dedicated endpoint.

### What is still unverified

**No endpoint has been created or exercised.** Everything above is catalog
evidence. In particular, it is **not established that a dedicated STT endpoint
serves the realtime WebSocket API** this pipeline uses
(`client.beta.realtime.transcription`) rather than only batch
`/v1/audio/transcriptions`. The docs list batch and realtime streaming for the
model families but don't state which path a dedicated deployment exposes.
Confirming it takes one short-lived endpoint (≈ $0.09 per minute of GPU time)
— worth doing before promising a customer a dedicated streaming ASR path.

### Consequences

- **If a BAA forces dedicated deployment, the audio tier is the cheap half of
  that bill** — one H100, versus 2–8 for the structuring LLM.
- **For the three Deepgram models, dedicated is the *only* way in.** They
  cannot be trialled on serverless first, so evaluating them means paying for
  an endpoint before you know whether the model is any good.

The general argument for wanting the same model ID available in both serving
modes — hybrid reserved-plus-overflow serving — and its two caveats (same ID
doesn't guarantee identical behavior; catalog availability isn't a contract)
are written up in [`model-selection-llm.md`](model-selection-llm.md#the-strongest-case-for-availability-in-both-serving-modes)
and apply equally here.

---

## Current recommendation

**Stay on `openai/whisper-large-v3`.** It had the best WER on the sample corpus,
it is the model the live demo is built and rehearsed on, and nothing measured is
strong enough to justify changing it.

What the evidence does support:

- **`nvidia/nemotron-3-asr-streaming-0.6b` is the challenger worth testing** —
  best CCER and lowest p95 on the sample corpus, with a small 0.6B footprint.
  But the margins are inside measured noise, so it earns a real-audio
  re-test, not a switch.
- **`nvidia/parakeet-tdt-0.6b-v3`** is a close second on WER and worth including
  in that re-test.
- **`nemotron-3.5` is not competitive** on this data (7.1% WER, ~3x Whisper's).
  It was a single run, but the gap is several times larger than the ~1-point
  WER noise seen elsewhere.
- **Deepgram models: not evaluated.** Dedicated-only; test only if there is a
  reason to expect they'd win.

The decision rule for the re-test: gate on **CCER first, then WER, then p95** —
and only change models if the winner clears the run-to-run noise measured
above, on real audio, with repeats.
