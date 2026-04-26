---
name: implement-plan
description: Iteratively implement this repository's plan.md. Use when the user asks Codex to work through the plan, continue the plan, implement planned items, make as much progress as possible, update/clean plan.md while working, and create properly formatted commits and pushes along the way.
---

# Implement Plan

## Core Rule

Work through `plan.md` end to end in useful vertical slices. Keep implementing until the current turn reaches a real blocker: missing product input, unavailable credentials or services, a failing external dependency, unsafe ambiguity, or a verification failure that cannot be responsibly fixed without user direction.

This is not a plan-only skill. Make code, test, documentation, and plan edits as needed.

## Workflow

1. Orient before editing.
   - Read `AGENTS.md` if present.
   - Map the plan with `rg -n '^##|^- \[[ x]\]' plan.md`, then read the active nearby sections.
   - Check `git status --short`, branch, and remote state.
   - Identify unrelated dirty files. Never revert user changes. Do not stage unrelated changes unless the user explicitly asked for an all-changes commit.

2. Choose the next slice.
   - If the user named a plan item, implement that item.
   - Otherwise choose the hottest unchecked item near the top of `Status Snapshot`, then the active `Current Direction Change` section, then `Browser And Design Fix Queue` or `Near-Term Follow-Ups`.
   - Prefer a small end-to-end slice with behavior, tests, and plan updates over a broad partial refactor.
   - If an item is too large, refine `plan.md` into smaller unchecked tasks before implementing the first one.

3. Implement iteratively.
   - Inspect the surrounding code before changing it.
   - Follow existing architecture and repo style.
   - Add or update tests at the integration level unless the changed logic is narrow and tricky enough to need focused unit coverage.
   - Update `plan.md` in the same slice: mark completed items only after verification, add new follow-ups when discovered, and move stale or over-broad text into clearer active tasks.
   - Keep completed history factual. Clean the active plan by removing duplicates or sharpening vague items, not by deleting useful context.

4. Verify each slice.
   - Run the smallest meaningful checks first.
   - For backend changes, usually run relevant `ruff`, `pytest`, and `pyright` checks.
   - For frontend changes, usually run `npm run typecheck`, `npm run build`, and targeted Playwright or test commands when behavior changed.
   - For ChatKit, logging, OpenAI, MCP, or task-workflow changes, inspect local logs when possible and make sure observability remains useful.
   - Do not mark work complete in the plan if verification failed or was skipped; record the gap instead.

5. Commit and push meaningful checkpoints.
   - Review `git status --short` and the staged diff before committing.
   - Stage only the files that belong to the current coherent slice unless the user asked for all current changes.
   - Use emoji conventional commit style. Keep the subject short and put extra topics in the body when useful:

```text
✨ feat(chatkit): add inline evidence annotations

- 🧪 test(chatkit): cover annotation reveal flow
- 📝 docs(plan): mark annotation task complete
```

   - Use `📝 docs(plan): ...` for plan-only commits.
   - Push after each committed checkpoint when the repository has a configured remote and the user has asked for commits along the way.

6. Continue or stop cleanly.
   - After a checkpoint, return to step 1 and choose the next useful slice if time and context allow.
   - Stop only when blocked or when no further safe progress can be made in the current turn.
   - Before stopping, leave `plan.md` honest: completed work checked, active next work visible, blockers or open questions recorded.

## Commit Hygiene

- Commit code and the corresponding plan/test updates together when they are part of the same completed slice.
- Avoid giant mixed commits. Split independent topics into separate commits when they can be verified independently.
- Do not commit generated logs, local databases, `.local/`, `.codex/`, build output, or credentials.
- If a commit fails because hooks or checks changed files, inspect the diff, rerun the relevant check, and commit again only when the result is understood.

## Status Updates

Keep the user oriented while working: say what slice you chose, what you are learning, what verification is running, and when a commit/push lands. If a blocker appears, explain the concrete blocker and the exact plan state left behind.
