import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="Realtime Dictation Demo Metrics")


@app.cell
def _():
    import json
    import statistics
    from pathlib import Path

    import marimo as mo

    return Path, json, mo, statistics


@app.cell
def _(mo):
    mo.md(
        """
        # Realtime Clinical Dictation — Demo Metrics

        Companion notebook to the live web demo (`web/server.py`). Reads the
        JSON session log written automatically after each dictation run and
        renders the same kind of latency story a live demo would show, as a
        backup if mic/network conditions aren't cooperating during the panel
        discussion.

        **Pipeline measured**: browser mic &rarr; Together.AI streaming ASR
        (`openai/whisper-large-v3`) &rarr; per-utterance LLM structuring
        (`meta-llama/Llama-3.3-70B-Instruct-Turbo`) &rarr; structured
        clinical note, modeled on the same ambient-scribe pattern used by
        Nuance DAX / Dragon Copilot, Abridge, Suki AI, Nabla, and Ambience
        Healthcare (streaming ASR &rarr; structured SOAP note &rarr;
        clinician review &rarr; EHR write-back).
        """
    )
    return


@app.cell
def _(Path):
    LOGS_DIR = Path(__file__).resolve().parent.parent / "web" / "logs"
    session_files = sorted(LOGS_DIR.glob("session_*.json"), reverse=True)
    return LOGS_DIR, session_files


@app.cell
def _(mo, session_files):
    picker = mo.ui.dropdown(
        options={f.name: f for f in session_files},
        value=session_files[0].name if session_files else None,
        label="Session to analyze",
    )
    picker
    return (picker,)


@app.cell
def _(json, picker):
    if picker.value is None:
        session = None
    else:
        session = json.loads(picker.value.read_text())
    return (session,)


@app.cell
def _(mo, session):
    if session is None:
        mo.md("*No session logs yet — run a dictation in the web demo first (`python3 web/server.py`).*")
    else:
        mo.md(
            f"""
            **Started:** {session['started_at']}
            **Ended:** {session['ended_at']}
            **Segments finalized:** {len(session['segments'])}
            """
        )
    return


@app.cell
def _(mo, session):
    mo.stop(session is None)
    mo.md(f"## Transcript\n\n> {session['transcript']}")
    return


@app.cell
def _(mo, session):
    mo.stop(session is None)
    note = session.get("note") or {}
    billing = ", ".join(note.get("draft_billing_codes", [])) or "—"
    exam = "\n".join(f"- {f}" for f in note.get("exam_findings", [])) or "—"
    mo.md(
        f"""
        ## Structured Note (EHR-style preview)

        | Field | Value |
        |---|---|
        | Chief Complaint | {note.get('chief_complaint', '—')} |
        | HPI | {note.get('history_of_present_illness', '—')} |
        | Assessment | {note.get('assessment', '—') or '—'} |
        | Plan | {note.get('plan', '—')} |
        | Draft Billing Codes | {billing} |
        | Requires Human Review | {note.get('requires_human_review', True)} |

        **Exam Findings**
        {exam}
        """
    )
    return


@app.cell
def _(mo, session, statistics):
    mo.stop(session is None)

    def summarize(values):
        if not values:
            return None
        s = sorted(values)
        p95 = s[max(0, int(len(s) * 0.95) - 1)]
        return {
            "count": len(values),
            "mean_ms": round(statistics.mean(values) * 1000, 1),
            "median_ms": round(statistics.median(values) * 1000, 1),
            "p95_ms": round(p95 * 1000, 1),
            "min_ms": round(min(values) * 1000, 1),
            "max_ms": round(max(values) * 1000, 1),
        }

    ttfs_summary = summarize(session["ttfs_values_s"])
    structuring_summary = summarize(session["structuring_latencies_s"])
    return structuring_summary, summarize, ttfs_summary


@app.cell
def _(mo, structuring_summary, ttfs_summary):
    def stat_row(label, s):
        if s is None:
            return f"| {label} | n/a | n/a | n/a | n/a | n/a | n/a |"
        return (
            f"| {label} | {s['count']} | {s['mean_ms']}ms | {s['median_ms']}ms "
            f"| {s['p95_ms']}ms | {s['min_ms']}ms | {s['max_ms']}ms |"
        )

    mo.md(
        f"""
        ## Latency Summary

        | Stage | Segments | Mean | Median | p95 | Min | Max |
        |---|---|---|---|---|---|---|
        {stat_row("TTFS (mic &rarr; finalized transcript)", ttfs_summary)}
        {stat_row("LLM structuring (per utterance)", structuring_summary)}

        **TTFS** (Time To Final Segment) is the same industry-standard
        streaming-ASR latency metric Deepgram/AssemblyAI/Gladia publish:
        wall-clock delay between when a spoken segment ends and when the ASR
        API delivers its finalized (non-revisable) transcript. Measured here
        genuinely end-to-end (real mic capture through the browser, over the
        network, through Together's realtime API) rather than a paced
        synthetic simulation.
        """
    )
    return


@app.cell
def _(mo, session):
    mo.stop(session is None)
    mo.md("## Per-Segment TTFS")
    return


@app.cell
def _(mo, session):
    mo.stop(session is None)
    import matplotlib.pyplot as plt

    segments = session["segments"]
    indices = [s["index"] for s in segments if s["ttfs_ms"] is not None]
    ttfs = [s["ttfs_ms"] for s in segments if s["ttfs_ms"] is not None]

    fig, ax = plt.subplots(figsize=(7, 3.2))
    colors = ["#f87171" if v > 500 else "#4fd1c5" for v in ttfs]
    ax.bar(indices, ttfs, color=colors)
    ax.axhline(0, color="#8793ab", linewidth=0.8)
    ax.set_xlabel("Segment #")
    ax.set_ylabel("TTFS (ms)")
    ax.set_title("Time To Final Segment per utterance")
    fig.tight_layout()
    mo.mpl.interactive(fig) if hasattr(mo, "mpl") else fig
    return


if __name__ == "__main__":
    app.run()
