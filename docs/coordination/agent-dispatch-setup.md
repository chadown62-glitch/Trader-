# TRADER AI — agent dispatch integration (proposal; not activated)

## Current state / non-negotiable distinction

The ChatGPT coordinator already watches GitHub with the two existing tasks: **Coordinateur TRADER AI** (hourly) and **Livraisons TRADER AI** (PR events). That does not start Claude Code or a GPT Work development run. A GitHub issue, a comment, a scheduled watch, and a successful GitHub webhook are not execution acknowledgements.

This branch proposes the **Claude Code GitHub Action** in `.github/workflows/trader-ai-claude-dispatch.yml`. A draft PR is not an active workflow. GitHub will only use the workflow for `issue_comment` events after a human-reviewed merge to the repo's **default branch**. No modification to `main` has been authorized. The existing user-launched Claude session is distinct from a future GitHub Action execution.

## One-time connection gate — requires repository owner's authorization

1. Owner reviews the draft PR, the GitHub Action permissions, price/usage implications, and the official setup documentation: https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md .
2. If approved, install the [Claude GitHub App](https://github.com/apps/claude) restricted to this repository. Configure **`CLAUDE_CODE_OAUTH_TOKEN`** in repo Actions Secrets from Claude Code setup-token according to Anthropic's official directions. Never paste the token in an issue, PR, chat, log, source file or prompt. This is a different authentication path from the user opening an existing Claude chat.
3. The owner explicitly approves merging only the workflow PR into `main`. Without that approval, stay blocked.
4. Verify a first **no-code** dispatch event on a throwaway `[CLAUDE]` issue: one owner-authored comment containing `[TRADER-AI-DISPATCH:CLAUDE]`. Require a real GitHub workflow run id, successful auth and safe diagnostic response; confirm the run has no code changes. Check quotas and billing.
5. Independently test a single small paper-only test-only task that creates a new branch and draft PR on a non-main base, with the coordinator reviewing exact HEAD, test results, changed-file ownership and idempotence. Only then mark `agent_channels.Claude.status = VERIFIED`.

## Gating of real dispatch

The coordinator must check before adding the trigger comment:
- A mission from the owner (e.g. `[CLAUDE] ...`) is final, reviewed, authorized and assigned to Claude; source HEAD and base ref match; the previous mission has a verified PR and no active Claude job.
- The existing claims/lock on `coordination/trader-ai-orchestrator` are acquired and stable; claim once with `repo + issue + base SHA + mission fingerprint` before sending. Replays of the same mission are forbidden.
- No *pending* GitHub Action run for the same agent and no unacknowledged dispatch; prevent comment loops.
- The coordinator comment includes `[TRADER-AI-DISPATCH:CLAUDE]` and the exact task ID, base branch, commit SHA, allowed files, and safety requirements; **only after** the channel has passed both smoke tests.
- Read actual GitHub workflow run id and eventual PR to mark `RECEIVED / RUNNING / FINISHED`. Lack of ACK is a blocker; a GITHUB comment merely published is not an ACK.

The Workflow additionally accepts only (a) an authorized owner comment, (b) on an authorized owner-created issue in this repository, (c) with a title starting `[CLAUDE]`, (d) with the explicit marker. It prevents ordinary reviews and comments from starting Claude. A global GitHub Actions concurrency group prevents parallel Claude jobs. The action is bounded at 60 minutes and 35 turns, but usage can still be billable; establish quota approvals.

**Limitations**: GitHub issues are untrusted text and a source of prompt injection. The Action has write permissions only because development requires draft PRs; do not grant broad additional rights. The workflow's verification gates are necessary but not sufficient to guarantee safe or correct code: the independent coordinator checks and test cases remain compulsory. If default branch is public, request review of workflow security before merge.

## GPT Work developer dispatch — separate route, NOT covered by this workflow

An independent **Work** development task triggered by an allowed GitHub PR event must be configured and authorized in Work. The existing `Livraisons TRADER AI` event task is a **review coordinator**, not a developer job. It cannot be reinterpreted as an authenticated start of a new Work session.

Desired Work event protocol (one-time configuration, not a copied prompt per mission):

- Event: owner-authored PR comment on repository `chadown62-glitch/Trader-` with `[TRADER-AI-DISPATCH:WORK]`, or another actually supported and tested event; the comment is on a dedicated, controlled PR used as delivery bus, and **never** on main.
- Condition: only act when an authorized mission issue ID and exact base SHA are present, and `agent_channels.GPT_Work.status=VERIFIED`. Ignore `[TRADER-AI-COORDINATOR]` review comments, test markers, nonowner comments, PR opens without a dispatch marker, and self-generated updates. Verify no active developer task/duplicate claim.
- Effect: open a Work development session, get code/test execution environment and GitHub connected, read the mission and current base SHA, code/test on own branch, produce **draft PR only**, report job/PR ID, refuse if preflight fails.
- First test: a **read-only** diagnostic dispatch, with a clear run ACK, then one restricted development task and actual PR. Only then update channel status to VERIFIED. Avoid recursion with the coordinator event task.

If the Work user account's trigger UI cannot filter by PR comment marker or start a full development run, keep the channel **BLOCKED**. Do not invent functionality by creating comments.

## Mission queue upon verified connection

- **Claude**: #41 RegimeRouter-R1 after PR #40, then future ErrorDiagnosisV1 and HypothesisRegistry from #42 *only after #41 is fully verified*. Already manually launched by the owner in Claude; don't double-start it.
- **GPT Work**: #39 recovery from `work/mission-control-v1-resume` and lost-local-work report, then future DecisionEvidenceV1 / OutcomeEnvelopeV1 / ObservationExportV1 from #42 after recovery and verification.
- **Integration**: separate draft PR only after both are reviewed; no merge of research branches into main by any automatic agent.

**Absolute invariants:** paper-only 30 EUR fictional, no private exchange keys, real trades, futures/leverage/shorts, changing frozen v2/v3, perpetual market service, surprise expenditures or automatic main merge. Human authorization is needed for installing apps, secrets and changes to main.

## Verification checklist

- [ ] Workflow draft PR reviewed for security.
- [ ] Explicit permission to merge workflow to default branch (NOT granted).
- [ ] Claude GitHub App restricted to this repository and one credential configured securely.
- [ ] Read-only Claude smoke-test: real run id/ACK, no code change.
- [ ] Code-enabled Claude smoke-test: branch + draft PR + real tests.
- [ ] Work development event task connected separately.
- [ ] Work read-only then code-enabled smoke-tests with run/PR IDs.
- [ ] Coordinator reads receipts, enforces locks/claims and prevents duplicate/simultaneous runs.
- [ ] No live trading, no changes to v2/v3, no service start, no auto merge main.

**Reference:** GitHub issues #39, #41, #42, #43; coordinator state branch `coordination/trader-ai-orchestrator`.
