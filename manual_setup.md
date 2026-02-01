Manual Setup Guide (Slack + Orchestrate + Demo Repo)

Purpose: document all manual setup steps completed in this session so the MVP can be recreated quickly.

Prereqs
- GitHub account with repo admin access
- At least one additional reviewer account (must accept repo invite)
- Slack workspace where you can install a custom app
- Access to watsonx Orchestrate (flow builder + connections)
- ngrok installed (or another HTTPS tunnel)

1) GitHub demo repo setup
1. Create repo: bug39/blocks-demo (private or public).
2. Add collaborator: @frogdog1 (must accept invite).
3. Add CODEOWNERS:
   - .github/CODEOWNERS
   - Example:
     /payments/ @frogdog1
     /billing/ @frogdog1
     /docs/ @frogdog1
4. Seed PR:
   - Create branch demo/payment-timeout.
   - Modify payments/api.py and open PR.
   - Do NOT request reviewers.
5. Verify PR:
   - Not draft, no excluded labels.
   - No requested reviewers.
6. Reset procedure:
   - Remove reviewer: 
     gh api -X DELETE repos/bug39/blocks-demo/pulls/1/requested_reviewers -f 'reviewers[]=frogdog1'

2) Backend + ngrok
1. Install deps:
   - pip install fastapi uvicorn requests pydantic
2. Set env vars in .env:
   - GITHUB_PERSONAL_ACCESS_TOKEN=...
   - DEFAULT_REPO=bug39/blocks-demo
   - DEFAULT_REVIEWERS=@frogdog1
   - S2_THRESHOLD_HOURS=1
   - ACTIVITY_WINDOW_HOURS=0
3. Run backend:
   - uvicorn app.main:app --reload --port 8003
4. Start ngrok:
   - ngrok http 8003
   - Copy the https URL (e.g., https://asymmetric-lemony-sandie.ngrok-free.dev)
5. Update openapi.yaml:
   - Replace server URL with current ngrok URL.

3) Slack app setup
1. Create Slack app → From scratch.
2. Add slash command:
   - /unblock
   - Request URL: temporary webhook.site or Orchestrate trigger URL (later)
3. Bot scopes:
   - chat:write
   - commands
   - app_mentions:read (optional)
4. Install app to workspace.

4) Orchestrate connections
1. Manage → Connections → Slack.
2. OAuth setup:
   - Authorization URL: https://slack.com/oauth/v2/authorize
   - Token URL: https://slack.com/api/oauth.v2.access
   - Client ID / Secret from Slack app
   - Scopes: chat:write, commands, app_mentions:read
3. Save Draft → Paste to Live.

5) Orchestrate agent + tools
1. Create agent: blocks.
2. Toolset → Import openapi.yaml.
   - Tools: Analyze PR or scan, Execute plan.
3. Behavior instructions (paste):
   - If user says "why <PR_URL>", call Analyze PR or scan with mode=why.
   - If user says "scan", call Analyze PR or scan with mode=scan.
   - If user says "approve <run_id>", call Execute plan.
   - Always respond using preview_text / scan_text / outcome_text from tool output.
4. Test in Talk to agent:
   - why https://github.com/bug39/blocks-demo/pull/1
   - approve <run_id>

6) Slack command wiring
1. Replace Slack slash command Request URL with Orchestrate trigger URL.
2. Confirm /unblock in Slack reaches Orchestrate and returns preview_text.

Notes
- Orchestrate run_id is echoed in Slack output; use it to open run trace during demo.
- IMPORTANT: run_id is REQUIRED by the backend. Orchestrate must generate and pass run_id with every tool call.
- The backend will reject requests without run_id (422 Validation Error).
- Use format: orch_{unique_id} for run_id values.
