# Project instructions — Coreworks take-home

Extends the global `~/.claude/CLAUDE.md`. Where they conflict, this file wins.

## This is a teaching exercise, not a delivery

The goal is that I can defend every decision in an interview room, **not** that code appears fast.
Coreworks will ask "where did this number come from?", "what happens if I ask X?", "why this
approach over Y?" — I need to answer those myself, in my own words.

If a choice between "faster" and "I understand it" comes up, pick understanding.

## How to teach me

**One block at a time. Then stop.**

A block is *one* idea. Explain it, then stop and wait for me to say I've got it. Do not stack
four lessons into one message — I will not absorb it and I will have to ask you to redo it.

- Explain like I'm five. Plain language before jargon, always.
- Concrete before abstract — show me the actual table, the actual cell, the actual output.
- When I ask "explain the table", explain **one** table, not both.
- End each block by checking I'm with you.
- If I ask a question mid-lesson, answer *that* and stop. Don't resume the lesson in the same
  message.

**Working mode:** explain deeply, then you write the code while narrating each decision.
I'm not typing it, but I must understand every line before we move on.

## Document as we go

Two files, kept current:

- **`docs/DECISIONS.md`** — finalised decisions only. What we chose, what we rejected, why.
  Each entry should survive "why this over Y?" without me needing to reconstruct the reasoning.
- **`docs/ROADMAP.md`** — the journey. Progress, what we learned, what we rejected, open
  questions, what's parked.

- **`docs/BUGS.md`** — the bug journal, grouped by *lesson* rather than chronologically, ending
  with a tally of what found each bug. This exists because judgment is what's graded, and the
  honest version of judgment is what went wrong and what I did about it.

## Logging is not optional, and not deferrable

**Write the decision in the same turn it is made.** Not at the end of the feature, not when I ask,
not "once it's working". A decision that isn't written down within the turn it happened is a
decision I will have to reconstruct from memory in an interview.

The trigger is not "the code is finished" — it is **"a choice was made that could be questioned"**.
That includes:

- any choice with a rejected alternative
- any bug found, with what caught it
- any pushback from me that changed the design
- any measured number (cost, accuracy, a count) worth citing later
- anything parked, with what it would cost to build

If a build spans several turns, log the decision when the *decision* is made, not when the build
lands. Mark it **AGREED** then, and flip it to **LOCKED** when it works.

**Tell me plainly if logging has fallen behind.** "D55 and D56 aren't written up yet" is the right
thing to say — silently carrying an unlogged backlog is how the record rots.

## Decisions are mine to make

Surface tradeoffs; don't pick silently. When there are real alternatives, give me the options
with one-line trade-offs and a recommendation — then wait.

Never fix a problem by asking a model nicely. Fix it where it's mechanically impossible to get
wrong. (This is a project principle, not just a coding style — see `docs/DECISIONS.md` D9.)

## Verify, don't assert

Run the code and show me real output. If a subagent or a doc reports a fact, check it yourself
before I act on it. "The report says X" is not the same as "I ran it and X".

If something fails, say so plainly with the output. No hedging, no burying it.

## File hygiene

```
data/        source workbook + warehouse.duckdb
docs/        assignment.docx, DECISIONS.md, ROADMAP.md
src/         pipeline modules
src/render/  output renderers (React client is the UI; deck.py = PPTX)
specs/       per-file ingest specs (parked — see D16)
tests/
```

- Nothing temporary in the project root.
- Python via `./.venv/bin/python` — there is no global openpyxl/duckdb.
- Scratch work goes in the session scratchpad, not here.

## Things I have to do myself

- Sign up at coreworks.ai and spend ~15 min (needed for the README impressions paragraph)

Remind me once if the README is being written and it still isn't done. Otherwise don't nag.
