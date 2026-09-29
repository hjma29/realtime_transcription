# Architecture

```mermaid
flowchart LR
    subgraph Capture["Capture"]
        A["Physician speaks"] --> B["Mic / AirPods (16kHz mono PCM)"]
    end

    subgraph ASR["Real-time ASR — Together AI"]
        B --> C["openai/whisper-large-v3\n(streaming transcription session)"]
        C -->|"interim deltas"| D["Live caption UI\n(optional, for physician feedback)"]
        C -->|"finalized per utterance"| E["Raw transcript text"]
    end

    subgraph Structure["Structured Extraction — Together AI"]
        E --> F["Llama-3.3-70B-Instruct-Turbo\nresponse_format: json_schema\n(Pydantic-defined schema)"]
        F --> G["Clinical Note JSON\n+ draft billing codes"]
    end

    subgraph Review["Human-in-the-loop"]
        G --> H{"requires_human_review = true"}
        H --> I["Coder / physician review UI"]
        I --> J["EHR / billing system"]
    end
```

## Deployment phasing

```mermaid
flowchart TB
    P1["Phase 1: Serverless\npay-per-token, fast to ship"] -->|"volume grows past\ncost crossover point"| P2["Phase 2: Dedicated Endpoint\nprivate deployment, BAA-eligible"]
```

## Design principles

1. **Two-model pipeline, not one model doing everything.** ASR (Whisper) transcribes speech; a separate LLM call (Llama 3.3 70B) corrects ASR artifacts, structures the note, and drafts billing codes. Whisper has no concept of JSON schemas — structuring belongs in the LLM layer.
2. **Schema-constrained JSON, not "please output JSON" prompting.** `response_format: json_schema` (backed by a Pydantic model) guarantees the LLM's output matches our exact schema — no parsing failures in production.
3. **Billing codes are always drafts.** Every response includes `requires_human_review: true` — this is a compliance/liability boundary, not a UX detail. No code reaches the billing system without a certified coder sign-off.
4. **Cost-phased infrastructure.** Start serverless for MVP speed; migrate to a Dedicated Endpoint once volume crosses the cost-justification line — unless PHI/BAA requirements force Dedicated from day one regardless of cost.
5. **Near-real-time via streaming + per-utterance structuring.** The LLM structuring pass runs on each finalized utterance rather than waiting for the whole visit, keeping the pipeline responsive.
