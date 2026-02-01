Setup Status (What’s been done so far)

Purpose: single source of truth for all work already completed (code + manual setup).

1) GitHub demo repo
- Repo created: https://github.com/bug39/blocks-demo
- CODEOWNERS file added and fixed:
  - /payments/ @frogdog1
  - /billing/ @frogdog1
  - /docs/ @frogdog1
- Demo PR created:
  - PR #1: https://github.com/bug39/blocks-demo/pull/1
  - Title: “Increase payment timeout”
  - No reviewers by default
- Collaborator added and accepted:
  - @frogdog1
- Reset command (verified):
  - gh api -X DELETE repos/bug39/blocks-demo/pulls/1/requested_reviewers -f 'reviewers[]=frogdog1'

2) Backend (FastAPI)
- File: app/main.py
- Endpoints implemented:
  - GET /healthz
  - POST /analyze (S2 rule + candidate ladder + preview_text)
  - POST /act (request reviewers + outcome_text)
- Plan cache in memory:
  - run_id -> plan, used for /act approval without passing plan
- Heuristic reviewer selection:
  - CODEOWNERS -> recent contributors -> fallback (DEFAULT_REVIEWERS)
- Evidence + metric:
  - stalled_hours computed from PR created_at
- Preview/outcome text:
  - preview_text (polished Slack-style message)
  - scan_text (for /scan)
  - outcome_text (polished outcome)

3) Environment configuration (local)
- .env contains (redacted):
  - GITHUB_PERSONAL_ACCESS_TOKEN=*** (present)
  - IBM_API_KEY=*** (present)
  - SLACK_BOT_OAUTH_TOKEN=*** (present)
- Demo overrides (set for speed):
  - DEFAULT_REPO=bug39/blocks-demo
  - DEFAULT_REVIEWERS=@frogdog1
  - S2_THRESHOLD_HOURS=1
  - ACTIVITY_WINDOW_HOURS=0

4) Ngrok tunnel
- ngrok running on port 8003
- Public URL in use:
  - https://asymmetric-lemony-sandie.ngrok-free.dev
- openapi.yaml updated to this URL.

5) Orchestrate tools + agent
- Agent created: “blocks”
- Tools imported via openapi.yaml:
  - Analyze PR or scan (POST /analyze)
  - Execute plan (POST /act)
- Behavior instructions configured to:
  - call /analyze on “why <PR_URL>”
  - call /analyze on “scan”
  - call /act on “approve <run_id>”
  - return preview_text/scan_text/outcome_text only

6) Slack connection (Orchestrate)
- Slack connector configured in Orchestrate (Draft + Live)
- OAuth URLs set manually:
  - Authorization: https://slack.com/oauth/v2/authorize
  - Token: https://slack.com/api/oauth.v2.access
- Slack app exists with /unblock command
- Request URL temporarily set to webhook.site (needs updating to Orchestrate trigger)

7) End-to-end verification (completed)
- /analyze returns matched=true and a plan
- /act requests reviewers in GitHub
- Orchestrate tool call returns preview_text and outcome_text
- Verified reviewer request appears on PR #1

8) Repo docs and scripts added
- plan_v3_1.md (updated scope + wiring)
- implementation_checklist.md (build steps + demo checks)
- manual_setup.md (manual setup steps)
- demo_runbook.md (timed demo script)
- scripts/reset_demo.sh (reset reviewers)
- deliverable_problem_solution.md
- deliverable_agentic_orchestrate.md
- openapi.yaml

9) Code updates completed
- run_id is now REQUIRED in /analyze, /act, /wizard (backend rejects without it)
- Added approval_required and auto_execute fields to /analyze response
- Created orchestrate/flow_a.yaml flow definition
- Updated openapi.yaml with required run_id fields
- Updated agent_instructions.md with run_id handling
- Updated tool_config.json with required run_id

10) Outstanding manual tasks (Orchestrate UI)
- [ ] Re-import openapi.yaml into Orchestrate (tools have new required fields)
- [ ] Update Orchestrate agent behavior to generate run_id for each request
- [ ] Get Orchestrate trigger URL from agent settings
- [ ] Update Slack slash command Request URL to Orchestrate trigger URL
- [ ] Test end-to-end: /unblock why <PR> in Slack → Orchestrate → Backend → Slack
- [ ] Decide whether Pattern Wizard is demoed live or pre-recorded
- [ ] Pre-demo reset of PR reviewers
