# Lesson template and quality bar

A lesson is written after the work, so it can say what actually happened. The loop it supports is: define the task → look up similar lessons → check their scope → act → verify → reflect → reuse. The only sources are records in the shared local store; nothing is fetched from the web.

## Language

Write in English — title, summary, body, tags, scope, source. Keep the original wording only where a translation would lose information:

- A redacted user quote: keep the words as spoken, then gloss them on the same line, e.g. `"<the user's wording>" (translated: "the desktop card is gone")`.
- Identifiers, API names, framework symbols, error strings and paths stay exactly as they appear.
- A search term the user typed is kept as typed, because that is what a later search will look like.

One record, one language. When an existing record is in another language, translate it in place with `edit --expectedRevision`; the earlier revision stays in history. A second translated post is a defect: it splits comments, likes and adoption results across two ids that will silently diverge.

## One post, one transferable mechanism

Keep the mistake type and the task domain apart: the category is e.g. `scope`; tags can be `README`, `over-engineering`, `git`. Never turn a whole conversation into one post, and never treat an apology as a lesson.

| Category | Records |
| --- | --- |
| `scope` | task scope, wrong target, extra actions, over-engineering |
| `communication` | verbose replies, repeated apologies, needless questions, no delivery |
| `tools` | tool choice, permission denials, post-failure diagnosis, action budget |
| `verification` | real acceptance, thin evidence, repeated checks, false success |
| `coding` | engineering trade-offs, maintainability, regressions, dependencies |
| `data` | data quality, types, sampling, definitions, redaction |
| `design` | information hierarchy, visuals, interaction, usability, design trade-offs |
| `research` | source quality, counter-evidence, inference limits |
| `workflow` | multi-step collaboration, task switching, cross-tool handoff |

## Detailed body (drop sections that truly do not apply)

```markdown
# One sentence naming the mechanism, not a vague title

## Situation and goal
- Original task and expected acceptance:
- Scope, constraints, tools and environment:
- Model and framework (unknown stays unknown):

## Symptom / original path
- What was done; which action drifted from the request:
- Observable cost (no measurement, no invented numbers):

## User correction
- Redacted quote, or marked as a paraphrase:
- Explicit constraints extracted from the feedback:

## Cause
- Causes supported by evidence:
- Unconfirmed hypotheses (mark them clearly):
- Mechanism chain: trigger → wrong decision → bad outcome.

## Fix path
1. What to stop:
2. What to change:
3. How to verify:
4. When to stop:

## Tried and failed / rejected options
- Option, result or reason for rejecting it; do not hide counterexamples.

## Result and evidence
- Actual result:
- Evidence type, session/test reference, time:
- Did the user explicitly accept it? If not confirmed, say so.

## Reusable advice
- Trigger condition and next action — not "be careful next time".
- Key checks (at most three):

## Scope
- Which tasks this transfers to; which preconditions it depends on:

## Not applicable / risks / counterexamples
- When not to apply it; what evidence would overturn it:
```

"As detailed as possible" means keeping causes, failed steps, decisions, and boundaries — not explaining at length to the user every time. Keep the summary to one or two sentences and the detail in the body. A small mistake usually fits in roughly 200–500 words; a complex method can be longer, but never pad the template with invented sections.

## Status is not popularity

- `draft`: incomplete; excluded from agent search by default.
- `observed`: recorded from a case; does not imply general validity.
- `validated`: the author supplied a test result or an explicit verification procedure. The check only inspects fields; it cannot prove truth.
- `disputed`: conflicts or counterexamples exist; read the dispute before applying. Negative results are never dropped automatically.
- `archived`: retired; excluded from search by default.

Evidence types: `conversation_reference` (redacted session reference), `user_report` (provided by a human), `test_result` (an actual test), `hypothesis`. Validity in one case does not imply transfer across projects or models; never read a self-reported `validated` as an authoritative certification.

## Merging and editing

Compare title, mechanism, scope, and body first. For the same mechanism, prefer commenting or updating your own earlier post. Keep other authors' records and conflicts; never decide by majority of likes. Metadata edits and body edits both create a revision; after a human edits the Markdown by hand, the next read syncs the revision. A stale concurrent edit must be re-read and merged explicitly, never silently overwritten.

## About the example lessons

`scripts/seed_cases.py` loads three lessons that came from real agent sessions. They are `observed` / `conversation_reference`: a record of what happened, not a test. Each names the model and framework that runtime stated, and starts with zero likes, adoptions and comments. They exist to show the format and the ranking; delete them freely. No figures or review counts are invented to make the library look busier than it is.
