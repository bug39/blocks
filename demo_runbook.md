Demo Runbook (3–4 minutes)

Goal: show NL config -> agentic plan -> governed execution -> audit trace.

Preflight (5 minutes before demo)
- Backend running (uvicorn on 8003).
- ngrok URL active and in openapi.yaml.
- Slack slash command points to Orchestrate.
- PR #1 has zero reviewers.
- ACTIVITY_WINDOW_HOURS=0, S2_THRESHOLD_HOURS=1.

Live demo script (timed)
1) Pattern Wizard (60–90s)
   - Input: "If PR has no reviewers after 1 hour, request reviewers from CODEOWNERS"
   - Show config preview and activate.

2) Proactive scan (20–30s)
   - Command: /unblock scan
   - Show list of stalled PRs (PR #1).

3) Why + approve flow (60–90s)
   - Command: /unblock why https://github.com/bug39/blocks-demo/pull/1
   - Show AI reasoning (candidates + rationale + plan).
   - Approve: /unblock approve <run_id>
   - Outcome shows reviewers requested and metric line.

4) Audit trace (20–30s)
   - Open Orchestrate run trace for run_id.
   - Show steps: analyze → approve → act.

Fallbacks
- If Pattern Wizard is unstable: show pre-generated config JSON and say “Wizard generated this.”
- If scan fails: skip to /unblock why flow.
- If approval fails: show tool output from /act via curl.

Reset between runs
- gh api -X DELETE repos/bug39/blocks-demo/pulls/1/requested_reviewers -f 'reviewers[]=frogdog1'
