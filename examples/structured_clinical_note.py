#!/usr/bin/env python3
"""Turn a raw ASR transcript into a structured clinical note (JSON).

Ported from Together AI's official structured-outputs sample
(togethercomputer/skills: together-chat-completions/scripts/structured_outputs.py),
adapted to the Luminary Health use case: take the raw text produced by
realtime_transcription.py / realtime_mic_transcription.py and turn it into
a JSON clinical summary + draft billing codes, using response_format
"json_schema" so the model's output is constrained to match our schema
exactly (no prompt-only "please output JSON" guessing).

Usage:
    pip install "together>=2.0.0" pydantic
    export TOGETHER_API_KEY=...
    python3 examples/structured_clinical_note.py transcript.txt
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from together import Together
from pydantic import BaseModel, Field

MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"


class ClinicalNote(BaseModel):
    chief_complaint: str = Field(description="One-line reason for the visit")
    history_of_present_illness: str = Field(description="Narrative HPI summary")
    exam_findings: list[str] = Field(description="Bullet list of exam findings")
    assessment: str = Field(description="Clinical assessment / likely diagnosis")
    plan: str = Field(description="Treatment plan / next steps")
    draft_billing_codes: list[str] = Field(
        description="Candidate ICD-10/CPT codes. DRAFT ONLY — must be "
        "verified by a certified coder before submission."
    )
    requires_human_review: bool = Field(
        default=True,
        description="Always true; billing codes are model-suggested, not final.",
    )


def structure_transcript(transcript: str) -> ClinicalNote:
    client = Together()
    completion = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a clinical documentation assistant. The following is a "
                    "raw speech-to-text transcript of a physician dictating a patient "
                    "visit. It may contain ASR artifacts (e.g. spoken punctuation like "
                    "'full stop' or 'new para' transcribed literally) — normalize those "
                    "into real punctuation/paragraphs. Extract a structured clinical "
                    "note matching the given JSON schema. Billing codes are drafts only; "
                    "never fabricate a code you are not reasonably confident about."
                ),
            },
            {"role": "user", "content": transcript},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "clinical_note",
                "schema": ClinicalNote.model_json_schema(),
            },
        },
    )
    return ClinicalNote.model_validate_json(completion.choices[0].message.content)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    transcript = Path(sys.argv[1]).read_text()
    note = structure_transcript(transcript)
    print(json.dumps(note.model_dump(), indent=2))


if __name__ == "__main__":
    main()
