Implementation Checklist - Unblocker Lite v3.1

Purpose: make plan_v3_1.md executable for a coding agent with explicit build order, inputs/outputs, and Definition of Done (DoD).

Quickstart for Agents (3 commands)
1) Start backend:
   - uvicorn app.main:app --reload
2) Health check:
   - curl http://localhost:8000/healthz
   - Expect: {"status":"ok"}
3) Smoke test:
   - curl -X POST http://localhost:8000/analyze -H "Content-Type: application/json" -d '{"pr_url":"<PR_URL>"}'
   - Expect: evidence + S2 match + candidate list + AI plan + policy decision

Guardrails (do not build)
- No Jira integration
- No automated/scheduled Flow B (background queue scanning). Manual /unblock scan IS in scope.
- No S3 unless MVP is complete
- No database migrations (use simple CREATE TABLE IF NOT EXISTS)
- No UI beyond Slack messages

Step 0: Orchestrate connectivity spike
Input: Slack command /unblock why <PR_URL>
Output: Orchestrate flow triggers and posts a Slack response
DoD:
- Slack -> Orchestrate -> backend -> Slack round-trip works
- run_id shown in Slack matches Orchestrate run trace
- Backend receives run_id in /analyze payload

Step 1: Evidence fetch (GitHub)
Input: PR URL
Output: evidence JSON (pr metadata, labels, draft, reviewers, last activity, changed files)
DoD:
- /fetch_evidence returns valid JSON for a seeded PR
- Includes: created_at, draft, labels, requested_reviewers, last_activity_at, files[]

Step 2: S2 rule evaluation
Input: evidence JSON
Output: {matched: bool, reason, evidence_keys}
DoD:
- Detects stall on seeded PR (no reviewers, older than threshold)
- Suppresses if recent activity within window

Step 3: Reviewer candidate ladder (deterministic)
Input: files[], repo config
Output: candidate list + source tag (CODEOWNERS / recent_contributors / default)
DoD:
- Candidate list is non-empty for seeded repo
- Source tag is set and logged

Step 4: AI reviewer ranking + PR summary
Input: candidate list, PR metadata, file paths
Output: ranked list + short PR summary + rationale
DoD:
- Returns ranked list (max 2–3 reviewers)
- Summary <= 3 sentences
- Rationale references evidence (file paths or ownership)

Step 5: Policy gate (deterministic)
Input: plan + evidence
Output: {auto_execute: bool, needs_approval: bool, confidence: high|low}
DoD:
- Low-confidence fallback requires approval
- High confidence allows auto-execute

Step 6: Orchestrate Flow A (end-to-end)
Input: PR URL via /unblock why <PR_URL>
Output: Slack preview -> execution -> verification -> outcome
DoD:
- Preview posted in Slack with run_id
- Reviewers requested on PR
- Outcome posted with verification status

Step 7: Verification
Input: PR id
Output: pass/fail
DoD:
- Pass if requested_reviewers >= 1

Step 8: Orchestrate run trace demo
DoD:
- Orchestrate run trace visible with same run_id shown in Slack

Step 9: Pattern Wizard (MVP core)
Input: "If PR has no reviewers after {X} hours, request reviewers from {CODEOWNERS|recent|default}"
Output: Config preview + single-PR dry-run + activation
DoD:
- Config preview shown and activated in Orchestrate
- Dry-run preview shows detected PRs (single PR is fine)

Step 10: /unblock scan (manual proactive)
Input: /unblock scan
Output: Slack list of top 3 PRs needing reviewers
DoD:
- Scan returns a list with links to run Flow A

Endpoints (thin backend, simplified)
- POST /analyze (evidence + S2 match + candidate list + AI plan + policy decision; mode=scan for queue)
- POST /act (execute + verify)
- POST /wizard (Pattern Wizard: NL → config → activate)
- GET /healthz

Minimal file layout (suggested)
- app/main.py (FastAPI entry)
- app/github.py (GitHub API calls)
- app/rules.py (S2 + suppression)
- app/reviewers.py (candidate ladder)
- app/ai.py (ranking + summary)
- app/policy.py (policy gate)

Demo data checklist
- 1 PR with no reviewers, older than 24h
- Recent activity > 6h ago
- CODEOWNERS or default reviewers configured

Demo repo setup checklist (must be true)
- Repo has CODEOWNERS OR default reviewer list configured
- Seeded PR older than 24h with zero requested reviewers
- Recent activity > 6h ago (no new commits/comments)
- Candidate reviewers are valid GitHub users with permission to review
- Reset procedure documented (clear requested reviewers between runs)

Demo data setup procedure (step-by-step, demo-safe)
1) Create a dedicated demo repo (manual UI or gh CLI).
   - Repo name suggestion: unblocker-demo
   - Make it private if needed; ensure reviewer accounts have access.
2) Add CODEOWNERS (preferred) or set a default reviewer list in config.
   - CODEOWNERS example:
     - /payments/ @reviewer1 @reviewer2
3) Create a demo PR that touches CODEOWNERS-owned paths.
   - Create branch, modify a file under /payments/, push, open PR.
4) Ensure the PR has:
   - No requested reviewers
   - No recent activity in the configured activity window
5) Demo-time age constraint:
   - If you cannot wait 24h, override demo config:
     - S2 threshold: 1h (or 10m)
     - Activity window: 5–10m
   - Alternative: pre-seed pr_state.ready_at to simulate 24h age.
6) Validate with /unblock scan:
   - Scan should list the seeded PR.
7) Reset procedure (before each run):
   - Remove requested reviewers in GitHub UI or via API.
   - Ensure no new commits/comments are added in the activity window.

Demo override checklist (if time-constrained)
- Set S2 threshold to 1h (or 10m) for demo
- Set activity window to 5–10m
- Pre-seed pr_state.ready_at to simulate >=24h if needed

Definition of done (MVP)
- Pattern Wizard works (NL -> config -> activate)
- /unblock scan returns top 3 PRs
- Flow A works end-to-end in a 3–4 minute demo
- Orchestrate run trace correlates with Slack run_id
- Reviewer request action executes successfully
