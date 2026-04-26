---
name: add-to-plan
description: Update this repository's plan.md without implementing the requested work. Use when the user asks to add, capture, track, defer, backlog, or remember future work in plan.md, especially when they explicitly say not to implement yet or want the plan updated smartly.
---

# Add To Plan

## Core Rule

Edit only the plan document. Do not implement the feature, change product code, write tests, run migration commands, or start servers unless the user explicitly expands the task beyond planning.

Default target: `plan.md` in the repository root. If it is missing, search for a likely plan file with `rg --files | rg '(^|/)(plan|roadmap|todo|backlog)\.md$'`. Ask only if there are multiple plausible targets and no local convention decides it.

## Workflow

1. Read the current plan structure before editing.
   - Use `rg -n '^##|^- \[[ x]\]' plan.md` for a map.
   - Open only the relevant nearby sections with `sed`.

2. Decide whether to update or add.
   - Update an existing unchecked item when the request is the same work in sharper words.
   - Split an existing unchecked item only when the new request adds a separable outcome.
   - Add a new unchecked item when no existing entry captures the intent.
   - Do not duplicate completed `[x]` entries. If a completed item reveals a follow-up gap, add a fresh `[ ]` follow-up.

3. Choose the home deliberately.
   - `Status Snapshot`: concise cross-cutting backlog items that should stay visible now.
   - `Current Direction Change: ...`: active strategy or design decisions for the current product direction.
   - `Implementation tasks`: concrete work items under that direction.
   - `Browser And Design Fix Queue`: frontend UX polish, layout, interaction, keyboard, responsiveness, or performance intake.
   - `Known remaining work`: deferred verification, follow-up checks, or non-blocking polish after a checkpoint.
   - `Next Planned Feature`: feature ideas that are not the current active pass but have enough shape to preserve.
   - Avoid adding new checkpoint sections for plan-only changes. Checkpoints are for completed implementation summaries.

4. Write the entry clearly.
   - Use unchecked `- [ ]` for future work.
   - Prefer one actionable sentence with outcome, scope, and any important constraints.
   - Keep detail in the most specific existing section; avoid scattering the same idea across multiple sections.
   - Preserve the plan's voice: product decisions first, implementation details second, verification notes only after work is done.
   - If the user gave rough wording, refine it into a clean product/engineering task without losing their intent.

5. Verify the edit.
   - Run `git diff -- plan.md` or the selected plan file.
   - Confirm no non-plan files changed, unless the user also asked to create or update this skill.
   - Do not run test suites for a markdown-only planning edit.

6. Report briefly.
   - State what was added or updated and where.
   - Say explicitly that no implementation was done.
   - If the repo workflow requires committing and the user has asked for commits along the way, make a small docs-only commit; otherwise leave it unstaged.

## Plan Layout Opinions

Keep the plan navigable for a future agent:

- Put the hottest work near the top so it is visible after opening the file.
- Prefer fewer, richer bullets over many tiny bullets when the work is one coherent feature.
- Prefer specific verbs: `Add`, `Expose`, `Track`, `Show`, `Persist`, `Verify`, `Decide`.
- Keep completed history factual and stable; do not rewrite old checkpoints just to fit a new idea.
- When adding UX work, include expected states and failure modes if known.
- When adding backend work, include the observable contract: logs, task states, API shape, or persistence behavior.
- When adding research/agent work, include dedupe, provenance, progress reporting, and safety boundaries when relevant.

## Examples

User: "Add to plan: status / progress for delete."

Good:
`- [ ] Add delete status/progress UX: show a clear deleting state after confirmation, expose recursive folder/source cleanup progress when possible, keep affected rows/modal/status text honest during long deletes, and report deleted/failed counts at completion.`

User: "Remember we need keyboard shortcuts for rename/backspace/delete."

Good when an explorer UX bullet already exists:
Update that bullet to include `F2 rename`, `Backspace up`, and `Delete remove selected` instead of adding a separate shortcuts section.

User: "I want this later, don't build it: research evidence mode with citations."

Good:
Add it under the relevant research or ChatKit direction, not under a completed checkpoint, with an unchecked task that names the user flow and citation/evidence expectations.
