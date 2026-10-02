# Model Selection — LLM (structuring tier)

Decision record for the **structuring** model slot — the LLM that turns a raw
transcript into a schema-constrained clinical note with draft billing codes —
and the evidence behind it. Last verified **2026-10-02** against the live
Together API.

The speech-to-text slot is covered separately in
[`model-selection-asr.md`](model-selection-asr.md). The two tiers are chosen
on different criteria: this one on latency alone (JSON validity is binary),
the ASR tier on a genuine accuracy-vs-latency trade-off.

| Slot        | Model ID                                  | Status                               |
| ----------- | ----------------------------------------- | ------------------------------------ |
| Structuring | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | **Current.** Sync JSON schema, ~2.3s |

Defined as `STRUCTURING_MODEL` in `examples/realtime_clinical_note.py`.

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

### The strongest case for availability in *both* serving modes

Not migration mechanics — **hybrid serving**. Identical weights in both
serverless and dedicated let you reserve dedicated capacity for steady-state load and spill over to
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
  https://api.together.ai/v1/models \
  | python3 -c "import json,sys;[print(m['id']) for m in json.load(sys.stdin)]" \
  | grep -i qwen
```

Model names in the wild are usually approximations — always resolve the real ID.

### 2. Is it dedicated-capable?

**Ask the v2 catalog first.** New dedicated endpoints can only be created on
dedicated model inference v2 (v1 creation is disabled since July 2026):

```bash
curl -s -G -H "Authorization: Bearer $TOGETHER_API_KEY" \
  --data-urlencode "search=<name>" https://api.together.ai/v2/supported-models
# empty "data" array => no certified v2 deployment profile
```

Each hit carries `deploymentProfiles` (GPU type/count, quantization).

The legacy `GET /v1/hardware?model=<id>` still answers but reflects the pre-v2
catalog, and **disagrees with v2** — e.g. it reports Llama-3.3-70B-**Turbo** as
deployable on 2/4/8x H100 while v2 lists only the BF16 sibling. Don't rely on it
alone. `GET /v1/models` carries no dedicated flag.

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

## Evaluating dedicated on v2: Together's recommended workflow

Per Together's dedicated-inference docs
([overview](https://docs.together.ai/docs/dedicated-endpoints/overview),
[concepts](https://docs.together.ai/docs/dedicated-endpoints/concepts),
[pricing](https://docs.together.ai/docs/dedicated-endpoints/pricing)):

1. **Prototype on serverless first.** Dedicated "uses the same inference APIs
   as serverless models, so you can prototype on serverless, then deploy on DMI
   without changing your application code" — only the `model` string changes
   (to the endpoint string `<project_slug>/<endpoint_name>`).
2. **Deploy on v2.** `tg beta endpoints deploy <model> --endpoint <name>`
   creates the endpoint, attaches a deployment and routes traffic in one step.
   Delete with `tg beta endpoints rm <id> --force`.
3. **Compare candidates with Together's built-in tools** rather than
   hand-rolled traffic splitting:
   - **A/B tests** — split live traffic between a baseline (control) and one or
     more candidates under one endpoint, with per-deployment metrics.
   - **Shadow experiments** — mirror a sampled fraction of live traffic to
     candidate deployments *without affecting the client response*, then compare
     each target's latency, throughput, error rate and outputs against the
     baseline. Together's example: "comparing fp8, fp4, and baseline builds on
     the same prompts" — i.e. exactly the Turbo-FP8-vs-BF16 question above.
4. **Mind the cost model.** v2 bills per ready replica per minute; stopped or
   scaled-to-zero deployments cost nothing, provisioning/cold-start time isn't
   billed. H100 is **$3.99/hr on v2** ($15.96/hr for the 4x config this model
   needs), versus $5.40/hr per GPU on legacy v1.
5. **For a production SLA without managing hardware**, Together points to
   *provisioned throughput* via sales rather than self-managed dedicated.

**v1 or v2?** v2. v1 is "still supported, but … will be deprecated by the end of
2026," and creating or restarting v1 endpoints already returns
`endpoints_v1_create_access_disabled`. Together's v2 launched **July 16,
2026**. There is nothing to evaluate on v1.

A shadow experiment is the natural way to answer this repo's open question —
does BF16-on-dedicated produce the same billing codes as FP8-on-serverless? —
because it replays real traffic against both without risking a user-visible
change. It requires a deployed v2 endpoint, which has not been done.

---

## Evaluated and rejected

### Gemma-4 / Qwen-3.5+ shortlist (2026-10-02)

A proposed shortlist arrived marked "Dedicated ✅ / JSON ✅" for all three
candidates. Verified live, **none of them are viable**, and the central claim
was wrong for each:


| Candidate                | Claimed              | Verified                                                                 |
| ------------------------ | -------------------- | ------------------------------------------------------------------------ |
| `google/gemma-4-31B-it`  | Dedicated ✅ JSON ✅ | Dedicated-**only**, 2x H100. Serverless → `model_not_available`         |
| `Qwen/Qwen3.5-397B-A17B` | Dedicated ✅ JSON ✅ | Dedicated-**only**, 4x **B200**. Serverless → `model_not_available`     |
| `Qwen/Qwen3.7-Plus`      | Dedicated ✅ JSON ✅ | **No dedicated config exists.** Streaming-only. JSON works via streaming |

The two dedicated-only models invert the evaluation order: you would have to
pay for an endpoint *before* you could find out whether the model is any
good. That is the wrong risk shape for a POC — though not an absolute bar. If
there were reason to believe either model would clear the latency budget, a
day of endpoint time to find out would be cheap. Nothing here suggested that:
every related serverless model in the same families was 15x+ over budget.

Measured latency, 3 runs each, identical system prompt and `ClinicalNote`
schema:


| Model                                     | Mode   | OK  | Mean latency |
| ----------------------------------------- | ------ | --- | ------------ |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | sync   | 3/3 | **2.3s**     |
| `Qwen/Qwen3.5-9B`                         | sync   | 2/3 | 34.0s        |
| `Qwen/Qwen3.7-Plus`                       | stream | 3/3 | 37.7s        |
| `Qwen/Qwen3.8-Flash`                      | stream | 3/3 | 48.0s        |

Billing-code quality was *comparable* across all of them — the Qwen models
produced sensible ICD-10/CPT sets and correctly held `requires_human_review`
true. They are simply reasoning models: **15–20x over the latency budget.**

**Takeaway: do not swap the structuring model on parameter count or
leaderboard position.** Check serverless availability, sync JSON, and measured
latency first — in that order.

### Earlier candidates


| Model                  | Result                                                                                                                                       |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `openai/gpt-oss-120b`  | 100% reliable but**13.2s mean / 30.6s p95**. Disqualified on latency despite being Together's documented "Top Model" for structured outputs  |
| `MiniMaxAI/MiniMax-M3` | Fast (1.46s mean) but only 1/3 runs completed. The 2 failures were ASR-side transients, not structuring failures —**worth a clean re-test** |

---

## Dedicated endpoint readiness

Two Together catalogs disagree, so this is stated per source (checked
2026-10-02). Dedicated endpoints v2 launched **July 16, 2026** and new
deployments must use it, so v2 governs.

| Model | v2 catalog (`/v2/supported-models`) | Legacy v1 (`/v1/hardware`) |
| --- | --- | --- |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` (FP8, current) | **Not listed** | 2x / 4x / 8x H100 |
| `meta-llama/Llama-3.3-70B-Instruct` (BF16) | Listed — 1 profile: **4x H100**, TP4 | 4x / 8x H100 |
| `openai/gpt-oss-120b`, `MiniMaxAI/MiniMax-M3` | Listed | — |
| `google/gemma-4-31B-it`, `Qwen/Qwen3.5-397B-A17B` | Listed | 2x H100 / 4x B200 |
| `Qwen/Qwen3.7-Plus` | **Not listed** | No config |

**Implication: going dedicated probably *is* a model swap**, from the serverless
FP8 Turbo ID to the BF16 `Llama-3.3-70B-Instruct` ID at 4x H100 — which is what
`agent-handover.md` originally said. This file previously "corrected" that to
"same ID, half the GPUs" based on the legacy catalog; that correction rested on
the stale source. Not confirmed by an actual deployment, but v2 is the platform
where deployments are created.

Two consequences:

- **FP8 serverless and BF16 dedicated are different numerics**, so the
  "same weights in both modes" premise of hybrid serving doesn't hold. Budget
  a real billing-code regression check before cutover, not a smoke test.
- **The footprint is 4x H100, not 2x** — roughly double the cost estimate that
  the "half the GPUs" claim implied.

For the audio tier, where the conflict is sharper (v2 lists no STT models at
all), see
[`model-selection-asr.md`](model-selection-asr.md#dedicated-endpoint-readiness).

---

## Current recommendation

Stay on `Llama-3.3-70B-Instruct-Turbo`. As of this evaluation it is the only
candidate that satisfies all four requirements simultaneously:

- serverless-testable (no spend to evaluate, and usable for burst overflow)
- sync JSON schema (no call-path rewrite)
- a dedicated path exists — via the BF16 sibling `Llama-3.3-70B-Instruct` at
  4x H100, since the Turbo ID itself is not in the v2 catalog (so migration is
  a model swap needing a regression check, not a `base_url` change)
- **inside the latency budget** (2.3s vs. a 15-20x penalty elsewhere)

The fourth is the one that actually eliminated every alternative tested so
far. The first three are cheap to check and should be screened *first*, since
they take minutes and latency benchmarking takes real time and tokens.

Revisit `MiniMax-M3` after a clean re-test — it is the only model measured so
far that was *faster* than the incumbent. Treat `gpt-oss-120b` and the
Gemma-4/Qwen-3.5+ shortlist as closed.

For the speech-to-text recommendation, see
[`model-selection-asr.md`](model-selection-asr.md).
