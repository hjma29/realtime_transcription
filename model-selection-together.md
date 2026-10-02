# Model Selection — Together AI

Decision record for the two model slots in this pipeline, and the evidence
behind them. Last verified **2026-10-02** against the live Together API.

| Slot | Model ID | Status |
| --- | --- | --- |
| ASR | `openai/whisper-large-v3` | **Current.** Streaming, finalized-segment output |
| Structuring | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | **Current.** Sync JSON schema, ~2.3s |

Both are defined in `examples/realtime_clinical_note.py` (`ASR_MODEL`,
`STRUCTURING_MODEL`).

---

## The constraint that decides everything

This product is **near-real-time**: a physician dictates and expects the
structured note to keep up. The structuring call runs on every finalized
transcript increment, not once at the end.

That makes **per-call latency the binding constraint**, ahead of model size,
benchmark scores, or price. A model that produces better billing codes in 38
seconds is not a better model *for this system* — it is a disqualified one.

Three properties must all hold before latency is even worth measuring:

1. **Serverless-available** — otherwise it can't be evaluated without
   committing GPU spend up front.
2. **Sync (non-streaming) JSON schema** — the pipeline blocks on a validated
   `ClinicalNote`; streaming-only models force a rewrite of the call path.
3. **Dedicated-capable** — the compliance/BAA path and the cost-crossover
   story both depend on being able to reserve the same model later.

### These three are not equally binding

Worth stating plainly, because it changes how hard to hold each line:

- **Dedicated-capable is a genuine requirement.** The risk is asymmetric. If
  a BAA becomes a hard requirement, a serverless-only model leaves *no path
  at all* — you are forced into a model change under compliance pressure,
  which in a clinical setting means re-validating billing-code accuracy and
  re-baselining on a deadline. That is the expensive failure mode.
- **Serverless-available is cost and friction, not architecture.** A
  dedicated-only model is not untestable — you can stand up an endpoint for
  a day and tear it down. It is only disqualifying when nothing suggests the
  model would clear the latency bar, so the spend has no expected payoff.
  That was the case for the Gemma-4 / Qwen-3.5+ shortlist below.
- **Sync JSON is a today-constraint, not a law.** It reflects how the call
  path is currently written. A streaming-only model is a rewrite, not an
  impossibility — but it buys nothing unless the model is also fast.

### The strongest case for availability on *both* tiers

Not migration mechanics — **hybrid serving**. Identical weights on both tiers
let you reserve dedicated capacity for steady-state load and spill over to
serverless at peaks, instead of over-provisioning GPUs for the worst hour of
the day. For a clinic with a sharp daily demand curve, that is a better cost
story than a one-time migration, and it only works if the same model ID runs
in both places.

### Two caveats, stated honestly

- **Same model ID does not guarantee identical behavior.** Serving stack,
  batching, and speculative-decoding settings can differ between serverless
  and dedicated. Run a billing-code regression check before cutover — much
  smaller than a full re-validation, but not zero.
- **Catalog availability is not a contract.** Configurations can be added or
  dropped. "Verified today" is not "guaranteed at migration time." The
  durable mitigation is keeping the model genuinely swappable — today it is
  a single constant in `examples/realtime_clinical_note.py` — plus a
  validated second choice.

---

## How to verify a candidate (reproducible)

### 1. Does it exist, and what is the exact ID?

```bash
curl -s -H "Authorization: Bearer $TOGETHER_API_KEY" \
  https://api.together.xyz/v1/models \
  | python3 -c "import json,sys;[print(m['id']) for m in json.load(sys.stdin)]" \
  | grep -i qwen
```

Model names in the wild are usually approximations — always resolve the real ID.

### 2. Is it dedicated-capable?

```bash
curl -s -G -H "Authorization: Bearer $TOGETHER_API_KEY" \
  --data-urlencode "model=<id>" https://api.together.xyz/v1/hardware
```

Returns GPU configurations, or `{"error": {... "No GPU configurations found
..."}}` if the model has no dedicated path.

> **This check is control-tested.** Bogus model IDs and
> `Llama-4-Maverick-17B-128E-Instruct-FP8` both return the error, so a
> non-empty result is meaningful. Note that `GET /v1/models` has **no**
> dedicated flag — `/v1/hardware` is the only way to answer this.

### 3. Is it serverless, and does sync JSON schema work?

There is no catalog flag for this. You must make a real call — a model can be
listed, and dedicated-capable, and still refuse serverless requests:

```
400 model_not_available — "Unable to access non-serverless model <id>.
Please visit ... to create and start a new dedicated endpoint."
```

Some models also reject non-streamed calls outright:

```
400 streaming_required — "This model only supports streaming."
```

### 4. Latency, against the real schema

Benchmark with the **actual** `ClinicalNote` schema and a representative
dictation, not a toy prompt — schema complexity materially affects latency.
`examples/benchmark.py` does this end-to-end.

---

## Evaluated and rejected

### Gemma-4 / Qwen-3.5+ shortlist (2026-10-02)

A proposed shortlist arrived marked "Dedicated ✅ / JSON ✅" for all three
candidates. Verified live, **none of them are viable**, and the central claim
was wrong for each:

| Candidate | Claimed | Verified |
| --- | --- | --- |
| `google/gemma-4-31B-it` | Dedicated ✅ JSON ✅ | Dedicated-**only**, 2x H100. Serverless → `model_not_available` |
| `Qwen/Qwen3.5-397B-A17B` | Dedicated ✅ JSON ✅ | Dedicated-**only**, 4x **B200**. Serverless → `model_not_available` |
| `Qwen/Qwen3.7-Plus` | Dedicated ✅ JSON ✅ | **No dedicated config exists.** Streaming-only. JSON works via streaming |

The two dedicated-only models invert the evaluation order: you would have to
pay for an endpoint *before* you could find out whether the model is any
good. That is the wrong risk shape for a POC — though not an absolute bar. If
there were reason to believe either model would clear the latency budget, a
day of endpoint time to find out would be cheap. Nothing here suggested that:
every related serverless model in the same families was 15x+ over budget.

Measured latency, 3 runs each, identical system prompt and `ClinicalNote`
schema:

| Model | Mode | OK | Mean latency |
| --- | --- | --- | --- |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | sync | 3/3 | **2.3s** |
| `Qwen/Qwen3.5-9B` | sync | 2/3 | 34.0s |
| `Qwen/Qwen3.7-Plus` | stream | 3/3 | 37.7s |
| `Qwen/Qwen3.8-Flash` | stream | 3/3 | 48.0s |

Billing-code quality was *comparable* across all of them — the Qwen models
produced sensible ICD-10/CPT sets and correctly held `requires_human_review`
true. They are simply reasoning models: **15–20x over the latency budget.**

**Takeaway: do not swap the structuring model on parameter count or
leaderboard position.** Check serverless availability, sync JSON, and measured
latency first — in that order.

### Earlier candidates

| Model | Result |
| --- | --- |
| `openai/gpt-oss-120b` | 100% reliable but **13.2s mean / 30.6s p95**. Disqualified on latency despite being Together's documented "Top Model" for structured outputs |
| `MiniMaxAI/MiniMax-M3` | Fast (1.46s mean) but only 1/3 runs completed. The 2 failures were ASR-side transients, not structuring failures — **worth a clean re-test** |

---

## Dedicated endpoint readiness

Both tiers can move to dedicated. Re-verified 2026-10-02 via `/v1/hardware`:

| Tier | Model | Dedicated configurations |
| --- | --- | --- |
| ASR | `openai/whisper-large-v3` | `1x_nvidia_h100_80gb_sxm` |
| LLM | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | 2x / 4x / 8x H100 |
| LLM | `meta-llama/Llama-3.3-70B-Instruct` (BF16) | 4x / 8x H100 |

Two consequences worth stating explicitly, because both were previously
documented incorrectly in `agent-handover.md`:

- **The entire STT catalog is dedicated-capable at 1x H100** — Whisper,
  Parakeet, Nemotron-ASR, Deepgram Flux, Nova-3. There is no STT dedicated
  gap. If a BAA forces dedicated deployment, the audio tier is the cheap
  half of that bill.
- **Going dedicated on the LLM tier is a deployment change, not a model
  swap.** The Turbo ID deploys dedicated as-is, and because it is FP8 it
  reserves at *half* the GPU footprint of the BF16 variant. Migration is a
  `base_url` change with the same model ID.

---

## Current recommendation

Stay on `Llama-3.3-70B-Instruct-Turbo`. As of this evaluation it is the only
candidate that satisfies all four requirements simultaneously:

- serverless-testable (no spend to evaluate, and usable for burst overflow)
- sync JSON schema (no call-path rewrite)
- dedicated-ready on the same model ID (clean compliance path, and hybrid
  reserved-plus-overflow serving stays open)
- **inside the latency budget** (2.3s vs. a 15-20x penalty elsewhere)

The fourth is the one that actually eliminated every alternative tested so
far. The first three are cheap to check and should be screened *first*, since
they take minutes and latency benchmarking takes real time and tokens.

Revisit `MiniMax-M3` after a clean re-test — it is the only model measured so
far that was *faster* than the incumbent. Treat `gpt-oss-120b` and the
Gemma-4/Qwen-3.5+ shortlist as closed.
