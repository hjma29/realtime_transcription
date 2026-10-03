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

## Listing models with the CLI and this repo's tool

Together's CLI is `tg` (alias `together`). Install it as Together documents:

```bash
brew install uv                       # if uv is missing
uv tool install "together[cli]"       # installs `tg` and `together` (v2.39.0 tested)
uv tool update-shell                  # if ~/.local/bin isn't on PATH
export TOGETHER_API_KEY=...
tg --version
```

Add `--json` (a **global** option, before the subcommand) for machine output:

| Goal | Command | Notes |
| --- | --- | --- |
| Catalog + pricing (legacy) | `tg --json models list` | Pages of 20 — follow `next_cursor` with `--after`. Pricing included; no serverless flag |
| Dedicated catalog, **v2** | `tg --json beta models public --product dedicated --limit 500` | 47 models, each with `deploymentProfiles` (GPU type/count, quantization) |
| Is a model in v2? | `tg --json beta models public --search whisper` | Empty `data` = no certified v2 profile |
| Dedicated GPU cost (legacy) | `tg --json endpoints hardware --model <id>` | `cents_per_minute`; legacy catalog |
| Deployable configs for a model | `tg beta models configs <model-id>` | v2 |

**What the CLI cannot do:** answer "is it serverless?" — no command or catalog
field exposes it (`--product serverless` returns only 8 models and is not the
callable serverless catalog) — and it cannot join pricing with dedicated status.
`examples/list_models.py` does both, using a real 1-token call (chat) or
1-second silent clip (ASR) as the serverless probe, with retries on transient
5xx (a healthy `gpt-oss-120b` returned 200, 200, 503 in a row):

```bash
python3 examples/list_models.py --kind asr
python3 examples/list_models.py --kind llm --search llama
python3 examples/list_models.py --kind llm --json | jq '.[] | select(.serverless=="yes") | {id, usd_per_1m_input_tokens, dedicated_v2}'
```

Each JSON record carries `serverless` (`yes` / `no` / `unknown`),
`serverless_note`, `dedicated_v2`, `dedicated_v2_profiles`,
`dedicated_legacy_v1`, and pricing (`usd_per_audio_minute` for ASR;
`usd_per_1m_input_tokens` / `usd_per_1m_output_tokens` and `context_length` for
LLMs). The probe is validated against hand-checked models: Llama-3.3-70B-Turbo,
`gpt-oss-120b`, `MiniMax-M3` and `Qwen3.7-Plus` (stream-only) → `yes`;
`gemma-4-31B-it` and `Qwen3.5-397B-A17B` → `no`.

### What it shows (2026-10-02)

Of 172 chat models in the catalog, **only 21 are callable on serverless**; 151
are dedicated-only or have a stopped dedicated endpoint. Of the 21, ten are in
the v2 dedicated catalog and eleven are not — including
`Llama-3.3-70B-Instruct-Turbo`, `Qwen3.7-Plus` and `Qwen3.8-Flash`, which have
**no dedicated path on the current platform**. For ASR, the four open models
are serverless (Nemotron ×2 are WebSocket-streaming-only and reject batch
calls); the three Deepgram models are not; **none of the seven is in the v2
catalog**.

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
   billed. H100 is **$5.49/hr on v2** ($21.96/hr for the 4x config the BF16
   Llama needs; Together's docs page says $3.99, which the pricing API does not
   reproduce), versus $5.40/hr per GPU on legacy v1.
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

> **Correction (2026-10-02).** The latencies above were measured with the
> models' default "thinking" mode on, and I never retried with it off. Together's
> SDK has a switch (`reasoning: {"enabled": false}`); with it off,
> `Qwen/Qwen3.5-9B` runs at about **2.5 s per call, 20/20 on the golden
> dictation** (see [`eval/golden-dictation.md`](eval/golden-dictation.md)). The
> "too slow" verdict was an artifact of one configuration, and it wrongly ruled
> out a model that is now a lead candidate. The same applies to the 13.2 s
> `gpt-oss-120b` figure in the table below, which also had thinking on (4.4 s per
> call with it off). The shortlist's *availability* findings still stand:
> `gemma-4-31B-it` and `Qwen3.5-397B-A17B` are dedicated-only, and
> `Qwen3.7-Plus` and `Qwen3.8-Flash` have no v2 profile, so none passes the
> serverless-and-dedicated rule regardless of speed.

**Takeaway: do not swap the structuring model on parameter count or
leaderboard position.** Check serverless availability, sync JSON, and measured
latency first, in that order, **and test reasoning models with thinking both on
and off before ruling them out.**

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

## Models that satisfy "serverless AND dedicated" (2026-10-02)

If a model must be callable on serverless **and** deployable on the v2 dedicated
platform, `meta-llama/Llama-3.3-70B-Instruct-Turbo` **fails**: it is serverless
only, and the Llama 3.3 entry in v2 is a different model (the BF16
`Llama-3.3-70B-Instruct`, which is dedicated only). No Llama has both. Neither
do Gemma 4, Llama 3.1 8B, Llama 4 Scout/Maverick, or MiniMax M2.7: all are in the
v2 picker but refuse serverless calls (`non-serverless model`).

Of 172 chat models, **11** pass the rule: DeepSeek-V4-Flash-0731,
DeepSeek-V4.1-Flash, DeepSeek-V4-Pro-0813, gpt-oss-120b, GLM-5.2, GLM-5.3,
GLM-5.3-Flash, Qwen3.5-9B, MiniMax-M3, Muse-Glimmer-30B, Kimi-K3.

### Evaluation on the golden dictation (supersedes the first sweep)

The full method, rubric, raw results and limits are in
[`eval/golden-dictation.md`](eval/golden-dictation.md). In short: the real
production prompt, the incremental pipeline replayed (9 calls per visit on a 47 s
dictation), 3 visits per model, a 20-check rubric plus error flags and expected
billing codes, with token cost and dedicated GPU cost from Together's own pricing.

An earlier sweep in this file used a shorter prompt I had written and a
different dictation. It is **superseded**, and one conclusion from it is
**retracted**: that Turbo "dropped the pertinent negatives". With the production
prompt on the golden dictation Turbo scores 20/20, keeps every negative, and has
the best billing-code coverage.

| Model | Facts /20 | Codes /4 | Mean s/call | Max s | Serverless $/visit | Dedicated footprint | $/hr |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 20 | 3.0 | **0.49** | 0.8 | 0.0038 | 4x B200 FP8 | 35.96 |
| `zai-org/GLM-5.2` | 20 | 2.0 | 0.74 | 1.1 | 0.0162 | 4x B200 FP4 | 35.96 |
| *`Llama-3.3-70B-Turbo` (fails the rule)* | 20 | **4.0** | 1.47 | 2.3 | 0.0067 | **not in v2** | n/a |
| `Qwen/Qwen3.5-9B` | 20 | 2.0 | 2.55 | 3.2 | **0.0014** | **1x H100 BF16** | **5.49** |
| `deepseek-ai/DeepSeek-V4-Pro-0813` | 20 | 2.7 | 4.91 | 8.7 | 0.0123 | 8x B200 NVFP4 | 71.92 |
| `MiniMaxAI/MiniMax-M3` | 20 | 3.0 | 5.60 | 9.1 | 0.0048 | 4x B200 FP8 | 35.96 |
| `openai/gpt-oss-120b` | 19.7 | 3.3 | 4.41 | 9.2 | 0.0041 | 2x H100 MXFP4 | 10.98 |
| `zai-org/GLM-5.3` | **14.7** | 2.0 | 2.04 | **38.6** | 0.0130 | 8x B200 NVFP4 | 71.92 |

- **Quality does not separate most models.** Six of eight scored 20/20 with zero
  errors; none leaked the cough annotation; every raw output set
  `requires_human_review` true.
- **GLM-5.3 is unreliable**: two of three final notes had an empty assessment, and
  one call took 38.6 s.
- **Tail latency is unstable over time.** Qwen3.5-9B measured 14 s average and 43 s
  worst case in an earlier test, and 2.55 s here.
- The fast models need B200s; the cheapest *dedicated* footprint is Qwen3.5-9B on
  one H100, a 6.5x lower hourly cost than DeepSeek-V4.1-Flash on four B200s.

### Dedicated versus serverless economics

H100 $5.49/hr and B200 $8.99/hr per GPU (pricing API). "Visit" = the 47 s golden
dictation. Dedicated bills continuously; serverless bills per token.

| Model | Serverless $/visit | Dedicated $/hr | Dedicated $/visit at 100 visits/hr | at 1,000 visits/hr | Break-even visits/hr |
| --- | --- | --- | --- | --- | --- |
| `Qwen/Qwen3.5-9B` | 0.0014 | 5.49 | 0.055 | 0.0055 | ~3,960 |
| `openai/gpt-oss-120b` | 0.0041 | 10.98 | 0.110 | 0.011 | ~2,710 |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 0.0038 | 35.96 | 0.360 | 0.036 | ~9,440 |
| `MiniMaxAI/MiniMax-M3` | 0.0048 | 35.96 | 0.360 | 0.036 | ~7,550 |
| `zai-org/GLM-5.2` | 0.0162 | 35.96 | 0.360 | 0.036 | ~2,220 |
| `Llama-3.3-70B-Instruct` BF16 (*different model from Turbo*) | n/a | 4x H100 = 21.96 | | | n/a |

At any plausible clinic volume (tens to a few hundred visits per hour) serverless
is **22x to 94x cheaper** than a reserved config. Dedicated is a compliance or
isolation purchase, not a cost saving. **Throughput per GPU was not measured**, so
the real break-even is at least the figures above. Tokens grow with dictation
length because the transcript is resent on every call.

## Current recommendation

**Turbo cannot be the answer if "serverless and dedicated" is a hard
requirement**: it is serverless-only, and the v2 Llama 3.3 entry is a different,
dedicated-only model. On quality alone it is fine (20/20, best codes), so if the
rule is ever relaxed, Turbo on serverless remains a good model.

Under the rule, two choices, depending on what matters:

- **Performance: `deepseek-ai/DeepSeek-V4.1-Flash`.** Fastest by far (0.49 s/call,
  worst 0.8 s), 20/20, zero errors, low serverless cost ($0.0038/visit). The price
  is its dedicated footprint: 4x B200 at **$35.96/hr**.
- **Cheapest on both sides: `Qwen/Qwen3.5-9B`.** 20/20, zero errors, $0.0014/visit
  serverless, and a dedicated footprint of **1x H100 at $5.49/hr** (6.5x cheaper
  than DeepSeek-V4.1-Flash). In a 5-visit head-to-head against Turbo it matched
  Turbo on speed (2.46 s/call, p95 3.24 s) and quality (20/20) at one fifth of the
  serverless cost; the 43 s outlier from an earlier test did not recur, but is
  unexplained. It misses the lab billing codes (2/4 vs Turbo's 4/4), likely a
  prompt matter to test. See `eval/golden-dictation.md`.

Drop `GLM-5.3` (unreliable), `DeepSeek-V4-Pro` (slow and the most expensive
footprint), `MiniMax-M3` (5.6 s/call) and `gpt-oss-120b` (4.4 s/call).

**Start on serverless either way**; it is dramatically cheaper at realistic volume.
Reserve dedicated only if compliance or latency isolation requires it, and then
the GPU footprint is the number that decides the model.

**Thinking mode: leave it off.** On the golden dictation it added no quality
(20/20 either way) and cost 14x the latency for Qwen3.5-9B (33.9 s vs 2.5 s per
call) and 2.5x to 6x the price. See `eval/golden-dictation.md`.

Caveats before switching the demo:

- **One 47 s dictation, one speaker.** Run it on more and longer dictations.
- **Reasoning must be disabled** (`reasoning: {"enabled": false}`) for these
  latencies. The demo does not send it yet; `structure_transcript(..., extra_body=)`
  now accepts it. Not confirmed to behave the same on a dedicated endpoint.
- **No v2 endpoint has been created** for any of these; the catalog lists them.
- **Model provenance.** DeepSeek, GLM, Qwen and MiniMax are developed by Chinese
  labs. Some healthcare buyers have procurement policies about that regardless of
  where Together hosts the weights. Ask Luminary Health early.
- **Billing codes** need a coder's judgement; the expected-code set is mine.

For the speech-to-text recommendation, see
[`model-selection-asr.md`](model-selection-asr.md).
