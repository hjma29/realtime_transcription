# Golden dictation: evaluation set

One real dictation, its audio, and a fixed rubric, used to compare ASR models and
structuring LLMs on the same input. Created 2026-10-02 from the script and audio
(`demo-final.mp3`) provided for the Luminary Health demo.

## Files

| File | What it is |
| --- | --- |
| [`golden-dictation.wav`](golden-dictation.wav) | The audio: 47.1 s, converted from `demo-final.mp3` (44.1 kHz stereo MP3) to **16 kHz mono 16-bit PCM WAV**, the only format this repo's tools accept |
| [`golden-dictation.txt`](golden-dictation.txt) | The script, verbatim, including the bracketed sound annotations |
| [`golden-dictation.clean.txt`](golden-dictation.clean.txt) | The same script with the bracketed annotations removed. This is the **reference** for scoring |
| [`manifest.jsonl`](manifest.jsonl) | `{audio, reference}` row for `examples/benchmark_asr.py` |
| [`run_llm_eval.py`](run_llm_eval.py) | LLM evaluation: incremental pipeline replay, rubric, cost |
| [`results/`](results) | Raw outputs of the runs reported below |

## The script

```text
Patient is a 52-year-old male for follow-up of hypertension and elevated cholesterol. [clears throat]  he's been taking his blood pressure medication regularly, [short pause] No chest pain, shortness of breath [thoughtful] His blood pressure in the office today is 138 over 86. [coughs] His weight is stable. He is well appearing and in no acute distress. Heart is regular rate and rhythm, no murmurs. Lungs are clear bilaterally. Assessment: hypertension, and hyperlipidemia. We discussed to Continue lisinopril 10 milligrams and atorvastatin 20 milligrams daily. [coughs]We'll repeat a basic metabolic panel and lipid panel before his next visit.
```

**Why two versions.** `[clears throat]`, `[coughs]` and similar are annotations, not
spoken words. Scoring a transcript against them would count the words "clears
throat" as errors. The scorer (`examples/benchmark_asr.py`) also strips `[...]` and
`(...)` from model output, so both sides are compared on speech only. The tagged
script is still used for one robustness check on the LLMs: does a model write
"patient coughs" into the note?

The script has deliberate tests built in: **"No chest pain, shortness of breath"**
must be read as *both* negated; two drugs with doses (**lisinopril 10 mg**,
**atorvastatin 20 mg**) where a dropped or misspelled drug name is a safety error;
a blood pressure; and a pertinent negative on the exam ("no murmurs").

I did not check how the audio was produced. Bracketed tags like `[coughs]` look like
annotations for synthetic speech. If it is synthetic, it is cleaner than real
dictation and the error rates below are optimistic.

## How to run

```bash
export TOGETHER_API_KEY=...

# ASR: all four serverless STT models (repeat 3x: one model varied between runs)
python3 examples/benchmark_asr.py --manifest eval/manifest.jsonl \
  --models openai/whisper-large-v3,nvidia/parakeet-tdt-0.6b-v3,nvidia/nemotron-3-asr-streaming-0.6b,nvidia/nemotron-3.5-asr-streaming-0.6b

# LLM: replays the pipeline (9 incremental calls per visit), grades the final note, prices it
python3 eval/run_llm_eval.py --runs 3
python3 eval/run_llm_eval.py --models zai-org/GLM-5.2 --runs 5
python3 eval/run_llm_eval.py --regrade eval/results/llm-2026-10-02.json   # re-score saved notes, no model calls
```

The LLM evaluation uses the **production** prompt and schema
(`build_messages` / `response_format` in `examples/realtime_clinical_note.py`) and
feeds the golden *reference* text, so ASR errors do not contaminate the LLM score.
Reasoning is switched off (`reasoning: {"enabled": false}`) for all models except
Llama Turbo, which does not take the flag.

## The rubric (what "correct" means here)

**20 content checks**, pass/fail, as regexes over the final note:

| Group | Checks |
| --- | --- |
| Presentation | age 52 and male; hypertension follow-up; elevated cholesterol; takes BP medication regularly |
| Pertinent negatives | **chest pain negated**; **shortness of breath negated**; **murmurs negated** |
| Findings | BP 138/86; weight stable; well appearing and no acute distress; heart regular rate and rhythm; lungs clear bilaterally |
| Assessment | lists hypertension; lists hyperlipidemia |
| Plan | lisinopril 10 mg; atorvastatin 20 mg; medications daily; basic metabolic panel; lipid panel; labs before next visit |

**Error flags** (each is a fabrication or corruption, not a missing fact): a drug,
condition, or number the dictation does not contain (aspirin, diabetes, angina...);
a wrong lisinopril, atorvastatin or blood-pressure value; a diagnosis code outside
the supported set; the cough or throat-clearing leaking into the note.

**Expected billing codes (4):** `I10` hypertension, any `E78.x` hyperlipidemia,
CPT `80048` basic metabolic panel, CPT `80061` lipid panel. An office-visit E/M code
is accepted but not required. **These expected codes are my coding judgement, not a
certified coder's.** Edit `EXPECTED_CODES` in `run_llm_eval.py` if a coder disagrees.

The scorer was validated with two controls before use: a hand-written correct note
scores 20/20 and 4/4; a note with planted flaws (a negation removed, a wrong dose,
an invented drug and condition, a leaked cough) trips every matching flag. It then
caught two bugs in itself (below).

## Results, 2026-10-02

### ASR: real audio, 3 runs per model

Whisper, Parakeet and Nemotron-3.5 gave identical results on all 3 runs; Nemotron-3 varied (7.3% to 9.4% word error).

| Model | Word error rate | Critical-term error rate | What went wrong |
| --- | --- | --- | --- |
| **`openai/whisper-large-v3`** | **2.1%** | **0.0%** | Stray "you" before "We'll"; "Assessment." split from the next phrase. All drugs and numbers correct |
| `nvidia/nemotron-3-asr-streaming-0.6b` | 8.0% (7.3 - 9.4) | 20.0% | "lacinopril", "a torvistatin"; "Hyper tension"; numbers spelled out |
| `nvidia/parakeet-tdt-0.6b-v3` | 10.4% | 13.3% | **"lisinopril" dropped entirely**; "torbostatin"; stray "Yeah." and "Ha ha."; "Assessment" lost |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | 22.9% | 26.7% | Garbled fragments ("His. His. well. . . . distress."), "licinipral", "turbostatin" |

Finalization delay was effectively zero for all four on a clip this short, so it
does not separate them.

### LLM: pipeline replay, 3 visits x 9 calls per model, production prompt

Scored after fixing the rubric (see the next section). "Visit" is this 47 s
dictation, which triggers 9 structuring calls; the transcript is resent on each
call, so tokens grow with dictation length.

| Model | Facts /20 | Codes /4 | Errors | Mean s/call | Max s | Serverless $/visit | Cheapest dedicated footprint | $/hr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 20 | 3.0 | 0 | **0.49** | 0.8 | 0.0038 | 4x B200 FP8 | 35.96 |
| `zai-org/GLM-5.2` | 20 | 2.0 | 0 | 0.74 | 1.1 | 0.0162 | 4x B200 FP4 | 35.96 |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | 20 | **4.0** | 0 | 1.47 | 2.3 | 0.0067 | **not in v2** | n/a |
| `Qwen/Qwen3.5-9B` | 20 | 2.0 | 0 | 2.55 | 3.2 | **0.0014** | **1x H100 BF16** | **5.49** |
| `deepseek-ai/DeepSeek-V4-Pro-0813` | 20 | 2.7 | 0 | 4.91 | 8.7 | 0.0123 | 8x B200 NVFP4 | 71.92 |
| `MiniMaxAI/MiniMax-M3` | 20 | 3.0 | 0 | 5.60 | 9.1 | 0.0048 | 4x B200 FP8 | 35.96 |
| `openai/gpt-oss-120b` | 19.7 | 3.3 | 0 | 4.41 | 9.2 | 0.0041 | 2x H100 MXFP4 | 10.98 |
| `zai-org/GLM-5.3` | **14.7** | 2.0 | 0 | 2.04 | **38.6** | 0.0130 | 8x B200 NVFP4 | 71.92 |

- **Quality barely separates the models.** Six of eight score 20/20 with zero
  errors, none leaked the cough annotation, and every model's raw output set
  `requires_human_review` to true. Billing-code coverage is the visible gap, and
  Turbo is best at it.
- **GLM-5.3 is unreliable:** two of its three final notes had an *empty
  assessment*, and one call took 38.6 s. Drop it.
- **Latency varies by time of day.** The same Qwen3.5-9B measured 14 s average
  with a 43 s worst case in an earlier test and 2.55 s here. Do not trust a
  single run for tail latency.

### Dedicated versus serverless cost

GPU prices are from Together's instance-types API (H100 $5.49/hr, B200 $8.99/hr per
GPU). Dedicated is billed whether or not requests arrive; serverless is per token.

| Model | Serverless $/visit | Dedicated $/hr | Dedicated $/visit at 100 visits/hr | Break-even visits/hr |
| --- | --- | --- | --- | --- |
| `Qwen/Qwen3.5-9B` | 0.0014 | 5.49 | 0.055 (40x serverless) | ~3,960 |
| `openai/gpt-oss-120b` | 0.0041 | 10.98 | 0.110 (27x) | ~2,710 |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 0.0038 | 35.96 | 0.360 (94x) | ~9,440 |
| `zai-org/GLM-5.2` | 0.0162 | 35.96 | 0.360 (22x) | ~2,220 |
| `MiniMaxAI/MiniMax-M3` | 0.0048 | 35.96 | 0.360 (75x) | ~7,550 |

Break-even is the number of visits per hour above which a reserved GPU config is
cheaper than paying per token. At any realistic clinic volume, serverless is far
cheaper; dedicated is justified by a compliance or latency-isolation requirement,
not by cost. **Throughput was not measured**: whether one H100 can actually serve
thousands of visits per hour is unknown, so the real break-even is at least this
high.

## Honest account of the earlier numbers

Before this set existed, the model comparisons were weaker than they looked:

| Earlier claim | How it was measured | Status |
| --- | --- | --- |
| ASR ranking (Whisper 2.4% WER; Nemotron-3 best on critical terms) | macOS text-to-speech clips I wrote | **Superseded.** Real audio: Whisper 2.1% / 0% and Nemotron-3 20% critical-term errors. The TTS ranking flipped, as warned |
| "Turbo's note dropped the pertinent negatives" | My own shorter system prompt, a different dictation | **Retracted.** With the production prompt Turbo keeps every fact |
| LLM latency, 11-model sweep | My short prompt, a single sentence | Directional only; the pipeline replay above is the better number |
| Billing-code "hit rate" | Substring tests on a made-up note | Replaced by the expected-code check above |

Latency numbers were always measured, not guessed; what was weak was the accuracy
checking.

## Bugs the scorer had, and the fixes

- Accepted `mg` but not `milligrams`: **wrongly failed Turbo** on both dose checks.
- Did not fold typographic characters (non-breaking hyphen in "well-appearing"):
  wrongly failed `gpt-oss-120b`.

Both were found by reading the notes behind a failure, not by trusting the score.
`--regrade` re-scores saved notes so a rubric fix needs no new model calls.

## Limits of this evaluation

- **One dictation, one speaker, 47 s.** Longer or noisier dictation, accents and
  overlapping speech are untested. The gaps between models are small and may not
  hold elsewhere.
- **The rubric is regexes.** It cannot judge clinical reasoning, and it can be too
  strict or too lenient on wording (`gpt-oss-120b` missed "takes BP medication
  regularly" in one run, possibly a phrasing difference). Read the saved notes.
- **LLMs were graded on the clean reference text**, not on any ASR model's output,
  so the ASR-to-LLM interaction (e.g. an LLM silently repairing "lacinopril") is
  not covered.
- **Expected billing codes are my judgement.**
- **Costs are for the dictation length tested.** Tokens, and so serverless cost,
  grow with dictation length because the transcript is resent each call.
