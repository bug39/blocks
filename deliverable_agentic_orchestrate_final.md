# Agentic AI and watsonx Orchestrate Usage

## Overview

Unblocker Lite uses watsonx Orchestrate as the workflow runtime and watsonx.ai for specific AI tasks. This document explains what each component does and how they fit together.

## The Orchestrate Agent: "blocks"

We have one agent in Orchestrate called "blocks". It handles three commands:

- `why <PR_URL>` — analyze a PR and request reviewers
- `scan` — find stalled PRs in a repo
- `wizard <rule>` — parse a natural language rule into config

The agent has four tools registered (imported via OpenAPI spec):
- `analyze_pr_or_scan_for_stalled_prs` — POST /analyze
- `execute_approved_plan` — POST /act
- `pattern_wizard` — POST /wizard
- `health_check` — GET /healthz

When you message the bot, Orchestrate routes it to the blocks agent, which decides which tool to call based on the input.

## How run_id Works

This is the key to the whole thing.

When the agent starts processing a request, it generates a run_id like `orch_1a2b3c4d`. That ID gets passed to every backend call:

```json
{"pr_url": "https://github.com/...", "run_id": "orch_1a2b3c4d", "mode": "why"}
```

The backend requires run_id—it'll reject requests without it. This forces all requests to come through Orchestrate.

The same run_id appears in:
- The Slack preview message (footer shows "Run ID: orch_1a2b3c4d")
- The backend's cached plan (keyed by run_id)
- The Orchestrate run trace (searchable by run_id)
- The outcome message

So if someone asks "why did the bot assign these reviewers?"—you look up that run_id in Orchestrate and see the full trace: what evidence was fetched, what the AI returned, what the policy gate decided.

## AI Components

We use watsonx.ai (Granite 3.3 8B Instruct) for four specific tasks:

### 1. PR Summarization
Takes the PR title and list of changed files, returns 2-3 sentences for reviewers.

```
Input: title="Fix payment retry logic", files=["payments/api.py", "payments/retry.py"]
Output: "Updates payment retry handling. Modifies the core API and adds new retry logic."
```

If the API is down, we fall back to "This PR modifies N file(s) including X."

### 2. Reviewer Ranking
Takes the candidate list and PR context, returns ranked candidates with rationale.

```
Input: candidates=[@alice, @bob], files=["payments/api.py"]
Output:
  1. @alice — "CODEOWNER for /payments, edited api.py 3x in last 30 days"
  2. @bob — "Recent contributor, median review time 2h"
```

The ranking combines AI judgment with deterministic scores (ownership weight, recent edits, response time from seeded data). If AI fails, we use just the deterministic scores.

### 3. Confidence Explanation
Generates human-readable explanation of why confidence is high/low.

```
Input: confidence=high, source=codeowners, candidate_count=2
Output: "High confidence: 2 CODEOWNERS match modified paths; PR age (3.7h) exceeds threshold (1h)"
```

### 4. Pattern Wizard Parsing
Parses natural language rules when regex doesn't match.

```
Input: "request reviews from code owners if no reviewers after a day"
Output: {"threshold_hours": 24, "source": "codeowners"}
```

We try regex first (faster, more reliable). AI is the fallback for non-standard phrasing.

## The Policy Gate

This is deliberately not AI. The policy gate is deterministic:

```python
approval_required = confidence in ("low", "none")
auto_execute = confidence == "high" and matched
```

Confidence comes from evidence, not AI self-assessment:
- **High**: 2+ candidates from CODEOWNERS or recent contributors
- **Low**: 1 candidate, or all candidates from fallback defaults
- **None**: 0 candidates

The backend returns these flags. Orchestrate reads them and decides whether to auto-execute or wait for approval. The backend doesn't make that decision—it just provides the data.

## End-to-End Flow

Here's what happens when you send `why https://github.com/org/repo/pull/1`:

1. **Orchestrate** receives the message, generates `run_id=orch_abc123`
2. **Orchestrate** calls POST /analyze with the PR URL and run_id
3. **Backend** fetches PR data from GitHub (files, labels, age, reviewers)
4. **Backend** checks S2 rule: no reviewers + age > threshold + not draft + no excluded labels
5. **Backend** builds candidate list: CODEOWNERS → recent contributors → defaults
6. **Backend** calls watsonx.ai to rank candidates and generate rationale
7. **Backend** calls watsonx.ai to summarize the PR
8. **Backend** computes confidence (high/low/none) based on candidate sources
9. **Backend** returns: matched=true, confidence=high, approval_required=false, auto_execute=true, candidates=[...], preview_text="..."
10. **Orchestrate** posts preview to Slack with approve/cancel buttons
11. **User** either approves, cancels, or lets the 20s countdown expire
12. **Orchestrate** calls POST /act with run_id=orch_abc123, approved=true
13. **Backend** retrieves cached plan by run_id
14. **Backend** calls GitHub API to request reviewers
15. **Backend** verifies reviewers were added (re-fetches PR)
16. **Backend** returns: status=reviewers_requested, verified=true, execution_time_s=2.37
17. **Orchestrate** posts outcome to Slack: "Reviewers requested. Metric: stalled 3.7h → assigned in 2.37s. Run ID: orch_abc123"

## What Orchestrate Owns vs. What the Backend Owns

**Orchestrate owns:**
- Generating run_id
- Routing messages to tools
- Posting to Slack
- Approval gating (reading approval_required, waiting for user input)
- Audit trail / run traces

**Backend owns:**
- GitHub API calls
- S2 rule evaluation
- Candidate selection logic
- AI calls to watsonx.ai
- Computing confidence and risk
- Executing the plan (requesting reviewers)
- Verifying the action succeeded

The backend is stateless except for a short-lived plan cache (keyed by run_id). It doesn't know about Slack, doesn't make approval decisions, and can't function without Orchestrate providing the run_id.

## Viewing the Audit Trail

In Orchestrate, click "Show Reasoning" on any agent response. You'll see:

```
Step 1
Tool: analyze_pr_or_scan_for_stalled_prs
Input: {"pr_url": "...", "run_id": "orch_abc123", "mode": "why"}
Output: {"matched": true, "confidence": "high", "approval_required": false, ...}

Step 2
Tool: execute_approved_plan
Input: {"run_id": "orch_abc123", "approved": true}
Output: {"status": "reviewers_requested", "verified": true, "execution_time_s": 2.37}
```

Same run_id in both steps. That's the audit trail.

## Summary

- One Orchestrate agent ("blocks") with four tools
- run_id generated by Orchestrate, required by backend, visible everywhere
- AI used for ranking/summarization/parsing—not for decisions
- Policy gate is deterministic, approval lives in Orchestrate
- Full trace available in Orchestrate run history
