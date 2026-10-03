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

Seven models have `type: transcribe`. Serverless status verified live
2026-10-02. The dedicated column comes from the **legacy v1 catalog only** — the
current v2 catalog lists none of them; see
[Dedicated endpoint readiness](#dedicated-endpoint-readiness).

| Model                                    | Serverless streaming | Dedicated (legacy v1 catalog) | In v2 catalog |
| ---------------------------------------- | -------------------- | ----------------------------- | ------------- |
| `openai/whisper-large-v3`                | ✅                   | `1x_nvidia_h100_80gb_sxm`     | ❌            |
| `nvidia/parakeet-tdt-0.6b-v3`            | ✅                   | `1x_nvidia_h100_80gb_sxm`     | ❌            |
| `nvidia/nemotron-3-asr-streaming-0.6b`   | ✅                   | `1x_nvidia_h100_80gb_sxm`     | ❌            |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | ✅                   | `1x_nvidia_h100_80gb_sxm`     | ❌            |
| `deepgram/flux`                          | ❌                   | `1x_nvidia_h100_80gb_sxm`     | ❌            |
| `deepgram/nova-3-en`                     | ❌                   | `1x_nvidia_h100_80gb_sxm`     | ❌            |
| `deepgram/nova-3-multi`                  | ❌                   | `1x_nvidia_h100_80gb_sxm`     | ❌            |

The three Deepgram models refuse serverless calls, and if the v2 catalog is the
whole story they are not deployable at all. Either way they can't be
benchmarked, so they are absent from the measured results below.

---

## Cross-check: Together's own STT reference

[`togethercomputer/skills` → `stt-models.md`](https://github.com/togethercomputer/skills/blob/main/skills/together-audio/references/stt-models.md)
(last changed 2026-08-07; catalog last synced 2026-06-06, before dedicated v2
launched). Each claim below was tested against the live API on 2026-10-02.

### Where it is stale or incomplete

| In the reference | Live result | Verdict |
| --- | --- | --- |
| Deepgram IDs `deepgram/deepgram-flux`, `deepgram-nova-3`, `deepgram-nova-3-multilingual` | Return *"Unable to access model"* (unknown ID). The live IDs `deepgram/flux`, `nova-3-en`, `nova-3-multi` return *"Unable to access **non-serverless** model"* | **Wrong IDs** |
| Supported formats: wav, mp3, m4a, webm, flac | All of wav, mp3, m4a, flac, webm **and ogg, opus, aac** transcribed correctly on Whisper | **Incomplete** (Together's docs page lists all eight) |
| Access column: single value per model | Coarse; says nothing about v2 dedicated (see [readiness](#dedicated-endpoint-readiness)) | **Stale** |
| Nemotron "Streaming transcription" | Correct, but batch calls are *rejected* (`This model only supports WebSocket streaming`), which the reference never says | **Missing caveat** |
| Parakeet "Realtime, diarization" | Realtime works; **batch** also works, with diarization and word timestamps | Correct, understated |

Everything else checked out: the 80 MB / 1 GB / 4 h limits match Together's
docs page, and Whisper and Parakeet both accept `diarize=true`.

### What it adds that matters for a clinical product

- **Diarization works, but only on the batch endpoint.** On a two-voice clip
  (clinician question, patient answer) Whisper and Parakeet both returned two
  correctly attributed speakers (`SPEAKER_00` / `SPEAKER_01`) via
  `response_format=verbose_json`, `diarize=true`. The SDK's realtime path has
  **no diarization option**. For *dictation* (one speaker, this demo) that is
  irrelevant; for an *ambient* doctor-and-patient scribe it means speaker
  labels would come from a batch pass or a second step, not the live stream.
  Tested on one synthetic clip with two TTS voices; real overlapping speech is
  untested.
- **Word timestamps differ in quality.** Both return them
  (`timestamp_granularities=word`). On a 12-word clip Whisper had 0
  zero-length words; **Parakeet had 3 of 12** (start equals end) and its times
  snap to 80 ms steps. Fine for captions, risky if you align words to audio for
  audit or playback.
- **Realtime audio formats are 8, 16 or 24 kHz.** `pcm_s16le_8000` exists, which
  matters for telephony-sourced audio.
- **`pcm_s16le` is ambiguous.** In ffmpeg it is a codec name (sample rate set
  separately). In Together's realtime API the **bare string `pcm_s16le` means
  24 kHz**; the 16 kHz format is the explicit `pcm_s16le_16000`. The SDK defaults
  to the explicit one, so this repo is safe, but a hand-rolled WebSocket client
  that sends 16 kHz audio under the bare name would be played back at the wrong
  speed.
- **Interim results replace each other.** Each realtime `delta` can replace the
  previous one; only `completed` is final. The demo's interim line relies on this.

## Pricing

**Serverless: $0.0015 per audio minute ($0.09 per audio hour), identical for
all four serverless STT models.** Sources agree: the model catalog's
`pricing.transcribe.price_per_minute`, and the "Speech-to-Text" rows of the
[serverless models docs](https://docs.together.ai/docs/serverless-models).
Because the four models cost the same, price does not separate them — the
choice is accuracy and latency.

**Dedicated GPU pricing differs between v1 and v2, and v2 is cheaper.** Per
Together's docs, one H100 costs **$3.99/hour on dedicated v2** (the current
platform; [pricing](https://docs.together.ai/docs/dedicated-endpoints/pricing))
versus **$5.40/hour on legacy v1**. Multi-GPU configs scale linearly (4x H100 =
$15.96/hr on v2). v2 bills per ready replica per minute, and a deployment
scaled to zero or stopped costs nothing — so a short evaluation costs only the
minutes it runs. Provisioning and cold-start time are not billed.

These are GPU prices, not STT prices: the v2 catalog lists no STT models (see
[Dedicated endpoint readiness](#dedicated-endpoint-readiness)), so there is no
confirmed way to buy dedicated ASR at all yet. The figures are what an H100
would cost *if* it becomes available.

### Break-even

`$3.99/hr ÷ $0.09 per audio-hour ≈ 44`. A dedicated H100 would only beat
serverless at about **44 audio-hours transcribed per wall-clock hour** —
roughly 44 live streams sustained around the clock (60 at the legacy $5.40
rate). A clinic with morning-peak dictation sits far below that, so **on cost
alone the ASR tier stays serverless for a long time.** Dedicated ASR would be
justified by a BAA/compliance requirement or by tail latency under load, not by
price.

Caveats on that arithmetic:

- It assumes one H100 can serve ~44 concurrent streams. **Not measured**, so
  the true break-even is probably higher.
- v2's scale-to-zero billing narrows the idle-time penalty, but a deployment
  that must stay warm for low latency still bills continuously.

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

## Which base URL to use

**Use `https://api.together.ai/v1`.** It is the SDK default, the base URL in
Together's API examples, and the one every command in this repo uses. Tested
2026-10-02 with the same serverless Whisper and chat calls:

| Base URL                                | Chat | Transcription | `GET /models`     |
| --------------------------------------- | ---- | ------------- | ----------------- |
| `https://api.together.ai/v1`            | 200  | 200           | 200 (264 models)  |
| `https://api.together.xyz/v1`           | 200  | 200           | 200 (264 models)  |
| `https://api-inference.together.ai/v1`  | 200  | 200           | **404**           |
| `https://api-inference.together.ai/v2`  | **404** | **404**    | **404**           |

What the table means:

- **`api.together.xyz` is an older domain** that still works identically. New
  code should use `.ai`.
- **`api-inference.together.ai` is an inference-only host** (no model listing).
  Together's dedicated-endpoints quickstart tells customers to point at it with
  `/v1`, while the general "shared inference API" page sends dedicated traffic
  to `api.together.ai/v1`. Both work for inference; the docs simply disagree.
- **`https://api-inference.together.ai/v2` returned 404 on every call tried.**
  The OpenAPI spec lists it ("Optimized environment for inference") as a
  second server on 153 of 168 reference pages, so it is a spec-wide
  declaration rather than something specific to audio — and as of this date it
  does not serve requests. Do not build on it. This is the `/v2` shown in the
  docs' server dropdown on the transcription page.

Three different things are called "v2" in Together's docs, which is the
likely source of confusion:

1. **Dedicated endpoints v2** (launched **July 16, 2026**) — a new resource
   model (endpoint / deployment / config / traffic split) with its own
   control-plane API at **`https://api.together.ai/v2`** (this one is real:
   `GET /v2/supported-models` returns 200). Creating new v1 dedicated
   endpoints, or restarting stopped ones, is disabled
   (`endpoints_v1_create_access_disabled`, HTTP 403); the docs give no date for
   the cutoff. The *inference* API is unchanged: same `/v1/...` request paths.
2. **Python SDK v2** (GA **February 4, 2026**; release candidate December 12,
   2025) — the `pip install together` client this repo uses.
3. **The `api-inference…/v2` server URL** above — declared, not working.

**v1 vs. v2 for dedicated:** v1 is "still supported, but … will be deprecated by
the end of 2026" (Together's v1 docs). Running v1 endpoints keep serving, but
new ones can't be created, so **v2 is the only way to start a dedicated
evaluation** — and it is cheaper ($3.99/hr per H100 vs. $5.40). Prefer v2; there
is no reason to touch v1 for new work.

**What a customer should use:** `https://api.together.ai/v1` for all inference
calls (serverless or dedicated); the **v2** dedicated-endpoints API/CLI to
create and manage dedicated deployments; and the current Python SDK.

---

## How to verify a candidate (reproducible)

### 1. Does it exist, and what is the exact ID?

```bash
curl -s -H "Authorization: Bearer $TOGETHER_API_KEY" \
  https://api.together.ai/v1/models \
  | python3 -c "import json,sys;[print(m['id']) for m in json.load(sys.stdin) if m.get('type')=='transcribe']"
```

### 2. Is it dedicated-capable?

**Ask the v2 catalog first.** New dedicated endpoints can only be created on
dedicated model inference v2, so this is the check that matters:

```bash
curl -s -G -H "Authorization: Bearer $TOGETHER_API_KEY" \
  --data-urlencode "search=<name>" https://api.together.ai/v2/supported-models
# empty "data" array => no certified v2 deployment profile
```

Each hit carries `deploymentProfiles` (GPU type/count, quantization). The list is
small (48 models on 2026-10-02) and unpaginated at that size.

The legacy check below still answers, but reflects the pre-v2 catalog and
**disagreed with v2 for every STT model** — don't rely on it alone:

```bash
curl -s -G -H "Authorization: Bearer $TOGETHER_API_KEY" \
  --data-urlencode "model=<id>" https://api.together.ai/v1/hardware
```

Returns GPU configurations, or `No GPU configurations found for model` if there
is no legacy dedicated path. `GET /v1/models` carries no dedicated flag.
Control-tested: bogus IDs return the error, so a non-empty result is meaningful
— but meaningful about the *legacy* catalog.

### 3. Is it serverless?

There is no catalog flag. You have to make a call — and **the realtime endpoint
gives a misleading answer.** For a dedicated-only model it returns a bare
`HTTP 404`, which the SDK surfaces as "check that base_url /
TOGETHER_BASE_URL points at an API" — sending you to debug the wrong thing.

Ask the batch endpoint instead; it returns the real reason:

```bash
curl -s -X POST https://api.together.ai/v1/audio/transcriptions \
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

**Status: most likely *not* available for new dedicated deployments. Not
confirmed by an actual deployment attempt.** Two Together catalogs disagree, and
the one that governs new deployments says no.

Since dedicated endpoints v2 launched (July 16, 2026), new dedicated
deployments must be created on v2; v1 creation is disabled. So the question is
what the *v2* catalog contains.

| Source                                                              | Whisper / Parakeet / Nemotron / Deepgram on dedicated?        |
| ------------------------------------------------------------------- | ------------------------------------------------------------- |
| `GET api.together.ai/v2/supported-models` (current, 48 models)      | **Absent.** Only two `audio` entries, both TTS (Kokoro, Qwen3-TTS). `search=whisper` and `search=parakeet` return 0 |
| `tg beta models configs openai/whisper-large-v3` (v2 deployable configs) | **0 configs.** Control: `meta-llama/Llama-3.3-70B-Instruct` returns 1. Without a config there is nothing to pass to a v2 deploy |
| `GET /v1/hardware?model=<id>` (legacy)                              | ✅ `1x_nvidia_h100_80gb_sxm` for all seven                    |
| `GET /v1/models?dedicated=true` (legacy, 194 models)                | ✅ all seven listed as `transcribe`                           |
| Together's [speech-to-text docs](https://docs.together.ai/docs/speech-to-text) table | ✅ Whisper, Parakeet, Nemotron ×2; Deepgram ×3 dedicated-only |
| `togethercomputer/skills` GitHub STT reference                      | "Serverless" for the four open models; "Dedicated / Reserved" for Deepgram |

The legacy v1 endpoints and the docs table say yes; the v2 catalog — the
platform where new endpoints are actually created — says no. The simplest
reading, now backed by the empty v2 config list, is that the v1 catalog and the
docs table are stale and **STT is currently serverless-only for new
deployments**, which would match the GitHub
skills repo and the original claim in `agent-handover.md` that this file
previously "corrected." That reading is strongly supported but **not confirmed**: nobody
has attempted a v2 deployment of an STT model, and the v2 "supported models"
list is described as Together-hosted base models with certified profiles, so
absence may mean "no certified profile yet" rather than "impossible." The
legacy v1 route (`/v1/hardware` still reports a 1x H100 for Whisper) cannot be
used to create one: v1 endpoint creation is disabled.

**Do not tell a customer that dedicated ASR is available** until Together
confirms it or a v2 deployment succeeds. Serverless STT is solid and priced;
that is the safe claim.

### Resolving it

Any one of these settles it:

- Ask Together (a Solutions Engineer or support) whether any STT model is
  deployable on dedicated model inference v2, and whether a dedicated STT
  endpoint serves the realtime WebSocket API.
- Attempt `tg beta endpoints deploy` for `openai/whisper-large-v3` on v2. If
  supported it creates a billed endpoint (an H100 is ≈ $0.067/min on v2, and a
  stopped deployment bills nothing), so only with deliberate intent; if
  unsupported it should fail without cost.
- Re-check `GET api.together.ai/v2/supported-models?search=whisper` periodically
  — it is free and will show the model the day it is added.

### Sources that mislead

- **The legacy catalog is not the current platform.** `/v1/hardware` and
  `/v1/models?dedicated=true` still answer, and look authoritative, but
  reflect the pre-v2 catalog. The "Dedicated (legacy v1 catalog)" column of the
  candidate table comes from that source.
- **The GitHub skills repo is a drifted snapshot.** It uses Deepgram IDs
  `deepgram/deepgram-flux` and `deepgram/deepgram-nova-3`, while the live API
  uses `deepgram/flux`, `deepgram/nova-3-en` and `deepgram/nova-3-multi`. Its
  single-valued *Access* column is a coarse label, though here it happens to
  agree with the v2 catalog.
- Neither the GitHub repos nor their trackers say anything about realtime
  streaming on a dedicated endpoint.

### If dedicated STT does turn out to be available

- **If a BAA forces dedicated deployment, the audio tier would be the cheap
  half of that bill** — one H100, versus 2–8 for the structuring LLM.
- **The three Deepgram models are dedicated-only** (confirmed: serverless calls
  return `non-serverless model`), so they cannot be trialled first.

If dedicated STT is *not* available, a BAA requirement has to be met some other
way (a BAA covering serverless, or a different vendor) — which is a materially
different conversation from "reserve a GPU."

The general argument for wanting the same model ID in both serving modes —
hybrid reserved-plus-overflow serving — is in
[`model-selection-llm.md`](model-selection-llm.md#the-strongest-case-for-availability-in-both-serving-modes)
and only applies if both modes exist.

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

---

# Reference

https://huggingface.co/datasets/ekacare/eka-medical-asr-evaluation-dataset

https://huggingface.co/datasets/ekacare/eka-medical-asr-evaluation-dataset/viewer/en/test?
sort%5Bcolumn%5D=audio&sort%5Bdirection%5D=desc&sort%5Btransform%5D=duration