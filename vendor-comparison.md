# Vendor comparison — Together AI vs. Fireworks vs. Baseten

Internal prep notes. **Not part of the presentation deck.** Verified
**2026-10-02** from each vendor's own docs and pricing pages, plus one
independent leaderboard (Artificial Analysis). Companion to
[`model-selection-asr.md`](model-selection-asr.md) and
[`model-selection-llm.md`](model-selection-llm.md).

## Bottom line

1. **Serverless LLM token prices are essentially identical across all three.**
   Together matches the competitor price exactly on six of eight models. It is
   cheaper only on Kimi K3 (−10%) and ~8% *more* expensive than Baseten on
   DeepSeek V4 Flash 0731. Price will not win or lose this deal.
2. **Dedicated GPU list prices differ a lot, and Together's is lowest.** One
   H100: Together **$3.99/hr**, Baseten $6.50/hr, Fireworks $8.00/hr.
3. **ASR is where the vendors really differ.** Together sells four managed STT
   models per audio minute. Baseten delivers ASR only as models *you deploy* on
   dedicated GPUs, and leads with a model Together does not host (Qwen3-ASR).
   Fireworks's current official docs and pricing page show **no ASR at all**.
4. **Healthcare:** all three state HIPAA support publicly, with different
   strength of wording. Together's is a paragraph in a SOC 2 blog post, not a
   dedicated announcement or docs page.
5. **The "Baseten and Fireworks are the two leaders" narrative is not what
   Baseten's posts say.** None of the eight relevant Baseten posts names
   Fireworks or Together in its text. See [§6](#6-what-baseten-actually-claims).

## How much to trust each source

| Source | What it is | Weight |
| --- | --- | --- |
| Each vendor's docs / pricing page | Primary, but self-reported | High for *prices and features*, low for *"fastest"* |
| Baseten blog benchmark posts | Marketing from an interested party | Treat as claims to check |
| Artificial Analysis provider pages | Independent measurement, point-in-time, one model per page | Medium: useful, but one model and one snapshot |
| Aggregator sites (`openbenchmarks.com`, `andrew.ooo`, "cheapest STT" blogs) | Secondary, undated or stale | **Not used for any number below** |
| AI search summaries | Generated; two of them invented details when checked against their cited pages | **Not used** |

## 1. ASR (speech-to-text)

| | Together | Baseten | Fireworks |
| --- | --- | --- | --- |
| **Managed per-minute ASR API** | ✅ 4 serverless models, **$0.0015 / audio min** | ❌ None found. ASR is **deployed by you** from a model library on dedicated GPUs | ❓ **Unverified.** No ASR page in current docs index, none on pricing page |
| Models | Whisper-large-v3, Parakeet-TDT-0.6B-v3, Nemotron-3 and 3.5 ASR streaming | Whisper, **Qwen3-ASR 1.7B** (batch + real-time), MOSS-Transcribe-Diarize, VibeVoice-ASR, Voxtral | — |
| Streaming | WebSocket (realtime API) | WebSocket, model-specific protocols | — |
| Diarization | Whisper, Parakeet: **verified**, batch endpoint only (not in the realtime SDK) | Dedicated models + Whisper pipeline | — |
| Billing | Per audio minute (serverless) | Per GPU-minute while replicas run | — |
| Dedicated | Not in the current v2 catalog (see ASR doc) | This **is** the model | — |

Points that matter for the interview:

- **Together does not host Qwen3-ASR**, which is the model Baseten uses to claim
  the accuracy-vs-latency frontier. Together's catalog has exactly seven
  `transcribe` models; none is Qwen3-ASR.
- **Baseten's ASR costs depend on throughput, not minutes.** A per-GPU-minute
  price only becomes a per-audio-minute price once you know how many streams one
  replica sustains, which Baseten's own performance guide tells you to measure
  yourself. Together's flat $0.0015/audio-min is simpler to budget.
- **Baseten's performance guide defines "finalization delay"** as *last speech
  sample → final transcript*, the same quantity this repo measures as TTFS. The
  metric is shared; the numbers are not comparable across harnesses.
- **Fireworks ASR is unproven, not disproven.** Their docs index has zero
  matches for ASR/Whisper/transcription, and the pricing page lists only text,
  vision and embedding models. I tried to confirm the endpoint by probing and it
  was **inconclusive**: `audio/*` returns 415 for *any* path, including a bogus
  one, and the audio host returns 401 for any path. Third-party sites list
  Whisper-class pricing from late 2025; I did not use it. **Ask before claiming
  Fireworks has no ASR.**

## 2. LLM serverless pricing

USD per 1M tokens, input / output, list price, 2026-10-02.

| Model | Together | Fireworks | Baseten |
| --- | --- | --- | --- |
| GLM-5.3 | 1.40 / 4.40 | 1.40 / 4.40 | 1.40 / 4.40 |
| GLM-5.3-Flash | 0.15 / 0.50 | 0.15 / 0.50 | 0.15 / 0.50 |
| DeepSeek V4.1 Flash | 0.30 / 1.20 | 0.30 / 1.20 | 0.30 / 1.20 |
| DeepSeek V4 Pro 0813 | 1.32 / 3.96 | — | 1.32 / 3.96 |
| DeepSeek V4 Flash 0731 | 0.14 / 0.28 | — | 0.13 / 0.26 |
| MiniMax M3 | 0.30 / 1.20 | 0.30 / 1.20 | — |
| gpt-oss-120b | 0.15 / 0.60 | 0.15 / 0.60 | — |
| **Kimi K3** | **2.70 / 13.50** | 3.00 / 15.00 | 3.00 / 15.00 |
| Llama-3.3-70B-Turbo (this repo's model) | 1.04 / 1.04 | not in headline table | not in headline table |

"—" means not retrieved or not listed, not unavailable.

Both competitors sell a **speed tier at a premium**: Fireworks Priority (+25%) and
"Fast"; Baseten "Fast" variants (e.g. GLM-5.3 Fast $2.10/$6.60 vs $1.40/$4.40,
+50%). Fireworks also prices **US-region variants at +50%**. Together serverless
offers no region selection; region control is a dedicated-endpoint feature.

## 3. Dedicated GPU pricing

| GPU | Together (DMI v2) | Baseten | Fireworks |
| --- | --- | --- | --- |
| A100 80GB | — | $0.06667/min = **$4.00/hr** | — |
| H100 80GB | **$3.99/hr** | $0.10833/min = **$6.50/hr** | **$8.00/hr** ($0.134/min) |
| H200 | contact sales | — | $8.00/hr |
| B200 | **$8.99/hr** | $0.16633/min = **$9.98/hr** | $13.00/hr |

On one H100, Together is **~39% cheaper than Baseten and 50% cheaper than
Fireworks**; on B200, ~10% and ~31% cheaper.

Billing nuances: Fireworks bills **per GPU-second with no start-up charge**.
Together v2 bills per ready replica per minute, does not bill provisioning or
cold start, and bills nothing while stopped or scaled to zero. Fireworks
region-restricted deployments carry a **1.5× premium**. All figures are list
prices; volume and commit discounts were not visible.

*Together's legacy v1 price was $5.40/hr per H100; v2 (the current platform) is
$3.99.*

## 4. Compliance

| | Statement | Where | Strength |
| --- | --- | --- | --- |
| **Baseten** | HIPAA compliant; SOC 2 Type II; ZDR by default for synchronous inference; regional deployments; self-hosted in your VPC; HIPAA reports on request | Dedicated blog post (2023, updated Oct 2025) + docs security page | Strongest: dedicated announcement, "internal audit driven by Drata" |
| **Fireworks** | SOC 2 Type II and HIPAA; ISO 27001 / 27701 / 42001; ZDR for open models | Blog post (Oct 2023) + docs FAQ ("SOC 2 Type II and HIPAA Certified") + docs security page | Strong: three places |
| **Together** | SOC 2 Type 2; "adheres to the stringent requirements of HIPAA, including data encryption in transit and at rest, audit logging, and strict business associate agreements (BAAs) with our partners" | One paragraph in the July 2025 SOC 2 blog post | **Weakest wording**: "adheres to", no HIPAA announcement, no docs page |

Together's docs separately cover private networking and VPC deployments on
dedicated endpoints, and note that serverless has no region selection.

**For a healthcare customer this is a real gap in presentation, not necessarily
in substance.** Together states HIPAA adherence and BAAs, but the public wording
is thinner than either competitor's. What it does **not** say is which products
(serverless, dedicated, both) the BAA covers. That scope is the question to take
to Together; do not promise it.

## 5. Independent speed data (Artificial Analysis)

Single model, single snapshot: **GLM-5.2**, 17 providers
([source](https://artificialanalysis.ai/models/glm-5-2/providers)).

| Provider | Endpoint accuracy | Output speed | First chunk | Notes |
| --- | --- | --- | --- | --- |
| Baseten (FAST) | n/a | **261 – 281 tok/s** (#1 on the page's speed leaderboard) | 0.69 s | Premium "Fast" tier. The table row is labeled plain "Baseten" with n/a accuracy; I infer it is the Fast endpoint, since its speed matches the leaderboard's Baseten (FAST) |
| Fireworks | **100%** | 236 tok/s | 2.72 s | Highest accuracy preserved |
| Baseten (standard) | 93% | 122 tok/s | 2.05 s | |
| Together AI | 97% | **not measured** (`--`) | `--` | Price shown, no speed row |

What it supports: Baseten's "fastest GLM" claim holds **for its paid Fast tier**;
standard Baseten is half as fast as Fireworks on this model. Together has no
speed number on this page, so nothing here shows Together slower; it shows
Together **unmeasured**.

Caveats: one model; one point-in-time snapshot; the "Fastest" leaderboard (top
five: Baseten FAST 281, Nebius 255, Databricks 239, Parasail 203, Mistral 198)
does not list Fireworks, yet the per-endpoint table gives Fireworks 236 tok/s,
which would rank fourth. The two views use different measurement configs and I
could not reconcile them. Don't generalize beyond GLM-5.2.

## 6. What Baseten actually claims

Eight Baseten posts were read in full (2025-10 to 2026-09). **None names
Fireworks or Together in its text.** The comparisons are made against
third-party leaderboards, not named rivals:

| Post | Date | Claim |
| --- | --- | --- |
| New fastest API for GLM-5.2 | 2026-07-26 | "state-of-the-art TTFT and TPS" on third-party benchmarks; peak 280 tok/s |
| World's fastest API for GLM-5.2 | 2026-06-23 | day-zero "fastest API in the world" |
| Fastest GLM 5 API; Fastest GLM-5 / Kimi K2.5 on Artificial Analysis | 2026-02/03 | "fastest" per Artificial Analysis |
| Fastest GPT-OSS on NVIDIA GPUs, 60% faster | 2025-10-24 | engineering write-up |
| **The fastest Whisper — with streaming and diarization** | 2026-08-27 | "fastest, most accurate, cost-efficient Whisper on the market" |
| **Baseten leads Coval's voice AI benchmark** | 2026-09 | Qwen3-ASR 1.7B Streaming on the "quality-latency Pareto frontier"; "~5× faster than OpenAI's" with the "best WER" |

The Coval post is the one that touches this project, and its own text limits it:
**"early-access benchmarks,"** results **"as of September 22, 2026,"** it
measures a Baseten-deployed open model (Qwen3-ASR), and the comparison set is
closed-source vendors, not Together or Fireworks. The accuracy-vs-latency chart
you shared earlier appears to come from this benchmark; it plots Azure, OpenAI
GPT-4o, Mistral Voxtral, Speechmatics, AssemblyAI, Deepgram, Soniox and NVIDIA
Nemotron. **Neither Together nor Fireworks appears on it.** It is also a
single-concurrency chart, which says little about behavior at peak load (see
[Latency under load](model-selection-asr.md#latency-under-load)).

So the "two leaders" framing is **plausible from outside evidence but not
something Baseten's own text states**. Artificial Analysis shows Baseten (Fast)
and Fireworks both near the top on GLM-5.2; that is one model. Chart images in
the posts may name competitors, but images are not machine-readable here, and I
did not verify them.

## 7. Where Together is stronger and weaker

**Stronger (evidenced):**

- **Lowest dedicated GPU list price** (§3), ~39% under Baseten on H100.
- **Managed per-minute ASR** at a flat $0.0015/min; Baseten requires you to
  operate ASR on GPUs, Fireworks shows none.
- **Built-in A/B tests and shadow experiments** on dedicated v2, which is
  Together's recommended way to validate a model swap
  ([`model-selection-llm.md`](model-selection-llm.md#evaluating-dedicated-on-v2-togethers-recommended-workflow)).
- **Kimi K3 is 10% cheaper** than either competitor.

**Weaker (evidenced):**

- **No Qwen3-ASR**, the model behind Baseten's leading voice-benchmark result.
- **Dedicated STT is not in the v2 catalog**, while Baseten's entire ASR offering
  *is* dedicated.
- **HIPAA story is the thinnest of the three** in public wording (§4).
- **No speed measurement on Artificial Analysis for GLM-5.2**, so it can't be
  shown competitive there.
- **Serverless has no region selection**; Baseten and Fireworks both sell
  regional placement (Fireworks at +50%).

**Not established either way:** latency and accuracy on *this* workload for any
of the three. The only head-to-head I could run was Together alone.

## 8. What I could not verify

- **Fireworks ASR**, whether it exists today, its models and price (§1).
- **Together's BAA scope**, which products and tiers it covers (§4).
- **Baseten ASR per-audio-minute cost**, which needs a throughput measurement.
- **Competitor latency on this repo's workload.** Would need accounts and keys.
- **Trust-center pages** (`trust.together.ai`, `trust.fireworks.ai`,
  `trust.baseten.co`) are JavaScript-rendered; I could not read them.
- **Chart contents** in Baseten's posts.
- **Fireworks and Baseten dedicated-endpoint model availability** for ASR.

## Reproduce

```bash
# Together pricing + serverless + dedicated status (this repo)
python3 examples/list_models.py --kind llm --json

# Fireworks serverless price table / Baseten per-minute GPU table are on their pricing pages
curl -sL https://docs.fireworks.ai/serverless/pricing.md
curl -sL https://www.baseten.co/pricing/          # GPU + Model API tables are in the static HTML

# Docs indexes (grep for audio/ASR/HIPAA)
curl -s https://docs.fireworks.ai/llms.txt
curl -s https://docs.baseten.co/llms.txt

# Independent speed data
open https://artificialanalysis.ai/models/glm-5-2/providers
```
