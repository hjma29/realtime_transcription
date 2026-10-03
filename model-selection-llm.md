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

### Benchmark on this repo's real `ClinicalNote` schema

One 55-word dictation, `json_schema` output, temperature 0. Most of these are
reasoning models, so they were run with `reasoning: {"enabled": false}`
(Together SDK v2 parameter). Without it they took 5-25 s. Runs for different
models were made in the same time window, because Turbo itself measured 2.3 s
earlier in the day and 4.7-5.3 s later.

| Model | Valid JSON | Mean latency | Max | Serverless $/1M in / out | v2 dedicated footprint |
| --- | --- | --- | --- | --- | --- |
| `zai-org/GLM-5.2` | 5/5 | **0.80 s** | 0.94 s | 1.40 / 4.40 | 4x B200 FP4 or 8x B200 NVFP4 |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 5/5 | **1.17 s** | 1.71 s | 0.30 / 1.20 | 4x B200 FP8 |
| `zai-org/GLM-5.3` | 5/5 | 1.37 s | 1.70 s | 1.40 / 4.40 | 8x B200 NVFP4 |
| `Qwen/Qwen3.5-9B` | 5/5 | 14.3 s | **43 s** | 0.17 / 0.25 | 1x H100 BF16 or FP8 |
| *Llama-3.3-70B-Turbo (fails the rule)* | 5/5 | 4.7 s | 5.2 s | 1.04 / 1.04 | **not in v2** |

Earlier single pass (3 runs, reasoning off): MiniMax-M3 6.4 s, DeepSeek-V4-Pro
5.6 s, DeepSeek-V4-Flash-0731 8.3 s, Kimi-K3 9.0 s, GLM-5.3-Flash 10.9 s (1 of 3
invalid), Muse-Glimmer-30B 17.4 s. gpt-oss-120b measured 5.0 s with reasoning on.
**Qwen3.5-9B** looked fast in the 3-run pass (2.5 s) and then averaged 14 s with a
43 s worst case in the 5-run pass: do not trust a 3-run number.

### Clinical correctness of the notes (read, not just scored)

The dictation included two pertinent negatives ("no radiation to the left arm,
no shortness of breath"), BP 148/92, aspirin 81 mg daily, and a two-week
follow-up.

- **Turbo's note omitted both negatives** and billed `I25.10 atherosclerotic
  heart disease`, which the dictation does not support, while missing the
  hypertension and diabetes codes.
- **GLM-5.2, GLM-5.3, DeepSeek-V4.1-Flash and Qwen3.5-9B** all kept both
  negatives ("denies radiation to the left arm and denies shortness of breath")
  and every number. DeepSeek-V4.1-Flash and GLM-5.3 also produced the
  hypertension, diabetes and ECG codes; GLM-5.2's code list varied between runs.

This is one dictation, one prompt and five runs, so it shows direction, not a
ranking. The billing-code check was a crude substring test.

### Dedicated cost (list prices, Together v2: H100 $3.99/hr, B200 $8.99/hr)

| Model | Dedicated footprint | $/hr |
| --- | --- | --- |
| `Llama-3.3-70B-Instruct` BF16 (a *different* model from Turbo) | 4x H100 | 15.96 |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 4x B200 | **35.96** |
| `zai-org/GLM-5.2` | 4x B200 FP4 | 35.96 |
| `zai-org/GLM-5.3` | 8x B200 | 71.92 |

The fast models need B200s, so a reserved endpoint costs roughly 2x the Llama
BF16 option. Serverless input is cheaper, though (DeepSeek-V4.1-Flash $0.30 vs
Turbo $1.04 per 1M), and this workload is input-heavy: every call resends the
transcript and the previous note.

## Current recommendation

**Turbo cannot be the answer if "serverless and dedicated" is a hard
requirement.** On that rule the lead candidate is
**`deepseek-ai/DeepSeek-V4.1-Flash`** (about 1.2 s, valid JSON 5/5, kept the
negatives, full code set, cheapest serverless input of the fast group), with
**`zai-org/GLM-5.2`** as the fast backup (0.8 s, but less consistent codes).

Caveats before switching:

- **Not yet validated beyond one dictation.** Run `examples/benchmark.py` style
  checks on several real or realistic dictations before committing.
- **`reasoning: {"enabled": false}` is required for these latencies.** The demo
  and `examples/realtime_clinical_note.py` do not send it, and it has not been
  confirmed to behave the same on a dedicated endpoint.
- **A dedicated v2 endpoint has not been created for any of these.** The v2
  catalog lists them; a deployment is what proves it.
- **Model provenance.** DeepSeek, GLM, Qwen and MiniMax are developed by
  Chinese labs. Some healthcare buyers have procurement policies about that,
  independent of where Together hosts the weights. Ask Luminary Health early.
- **Dedicated costs about 2x** the Llama BF16 footprint (above).

If the rule is relaxed to "dedicated-capable, with a different model ID on
dedicated", Turbo remains usable on serverless with BF16 `Llama-3.3-70B-Instruct`
(4x H100) as its dedicated counterpart, but the two are different numerics and
would need a regression check.

Treat `gpt-oss-120b` (5 s) as too slow, and the Gemma-4 / Qwen-3.5+ shortlist
as closed (dedicated-only or too slow).

For the speech-to-text recommendation, see
[`model-selection-asr.md`](model-selection-asr.md).
