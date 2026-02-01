from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import uuid
import os
import re
import time
from datetime import datetime, timezone

from app.ai import (
    summarize_pr,
    rank_reviewers_with_rationale,
    normalize_wizard_input,
    generate_confidence_explanation,
    assess_risk,
    explain_non_match,
)
from app.reviewers import load_reviewer_stats, rank_candidates, explain_top_choice

try:
    import requests
except ImportError as exc:  # pragma: no cover - dependency hint
    raise RuntimeError("Missing dependency: requests. Install with `pip install requests`.") from exc

app = FastAPI()

# Simple in-memory cache for demo: run_id -> plan + metadata
PLAN_CACHE: Dict[str, Dict[str, Any]] = {}

# Runtime config for Pattern Wizard (in-memory, demo only)
WIZARD_CONFIG: Dict[str, Any] = {
    "threshold_hours": int(os.getenv("S2_THRESHOLD_HOURS", "1")),
    "source": "codeowners",
    "excluded_labels": None,  # Use default if None
}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _load_env_if_needed():
    if os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN"):
        return
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)


def _gh_headers():
    _load_env_if_needed()
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Missing GITHUB_PERSONAL_ACCESS_TOKEN")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "unblocker-lite",
    }


def _parse_pr_url(pr_url: str):
    try:
        parts = pr_url.split("github.com/")[1].split("/")
        owner, repo, _, number = parts[0], parts[1], parts[2], parts[3]
        return owner, repo, int(number)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid pr_url format") from exc


def _get_json(url: str, params: Optional[Dict[str, Any]] = None):
    resp = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _post_json(url: str, payload: Dict[str, Any]):
    resp = requests.post(url, headers=_gh_headers(), json=payload, timeout=20)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _iso_to_dt(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _hours_since(dt: datetime):
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _load_codeowners(owner: str, repo: str) -> List[str]:
    # Return raw lines from CODEOWNERS if available
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/.github/CODEOWNERS"
    try:
        data = _get_json(url)
    except HTTPException:
        return []
    if "content" not in data:
        return []
    import base64
    decoded = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    lines = []
    for line in decoded.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _match_codeowners(lines: List[str], files: List[str]) -> List[str]:
    owners: List[str] = []
    for fpath in files:
        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                continue
            pattern = parts[0]
            # Simple prefix match for demo
            if pattern.endswith("/") and fpath.startswith(pattern.lstrip("/")):
                owners.extend(parts[1:])
            elif pattern.startswith("/") and fpath.startswith(pattern.lstrip("/")):
                owners.extend(parts[1:])
    # de-dupe while preserving order
    seen = set()
    deduped = []
    for o in owners:
        if o not in seen:
            seen.add(o)
            deduped.append(o)
    return deduped


def _get_pr(owner: str, repo: str, number: int):
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    return _get_json(url)


def _get_pr_files(owner: str, repo: str, number: int):
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/files"
    files = _get_json(url, params={"per_page": 100})
    return [f["filename"] for f in files]


def _get_contributors(owner: str, repo: str, limit: int = 5):
    url = f"https://api.github.com/repos/{owner}/{repo}/contributors"
    data = _get_json(url, params={"per_page": limit})
    return [c["login"] for c in data if "login" in c]


def _s2_match(pr: Dict[str, Any], excluded_labels: List[str], activity_window_hours: int, threshold_hours: int):
    if pr.get("draft"):
        return False, "draft"
    labels = [l["name"] for l in pr.get("labels", [])]
    if any(l in excluded_labels for l in labels):
        return False, "excluded_label"
    requested = pr.get("requested_reviewers", [])
    if requested:
        return False, "already_requested"
    created_at = _iso_to_dt(pr["created_at"])
    updated_at = _iso_to_dt(pr["updated_at"])
    age_hours = _hours_since(created_at)
    activity_hours = _hours_since(updated_at)
    if activity_hours < activity_window_hours:
        return False, "recent_activity"
    if age_hours < threshold_hours:
        return False, "too_new"
    return True, "s2_match"


def _confidence_for(source: str, count: int):
    if count == 0:
        return "none"
    if source in ("codeowners", "recent") and count >= 2:
        return "high"
    return "low"


def _default_reviewers():
    raw = os.getenv("DEFAULT_REVIEWERS", "")
    if not raw:
        return []
    return [r.strip() for r in raw.split(",") if r.strip()]


def _normalize_handles(handles: List[str]):
    return [h if h.startswith("@") else f"@{h}" for h in handles]


def _build_preview_blocks(
    run_id: str,
    pr_title: str,
    pr_url: str,
    ai_summary: str,
    confidence: str,
    confidence_explanation: str,
    risk_assessment: Dict[str, Any],
    ranked_candidates: List[Dict[str, Any]],
    matched: bool,
    plan: Optional[Dict[str, Any]],
    non_match_explanation: Optional[str],
    why_top: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Build Slack Block Kit JSON for rich message display.

    Returns a list of Slack blocks that can be used with Slack's Block Kit API.
    """
    blocks = []

    # Header block
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"🔓 Unblocker Analysis",
            "emoji": True
        }
    })

    # PR info section
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*<{pr_url}|{pr_title}>*"
        },
        "accessory": {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "View PR",
                "emoji": True
            },
            "url": pr_url,
            "action_id": "view_pr"
        }
    })

    # AI Summary section
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"📝 *AI Summary*\n{ai_summary}"
        }
    })

    blocks.append({"type": "divider"})

    # Confidence and Risk in columns
    conf_emoji = "🟢" if confidence == "high" else "🟡" if confidence == "low" else "⚪"
    risk_emoji = "🟢" if risk_assessment["level"] == "low" else "🟡" if risk_assessment["level"] == "medium" else "🔴"

    conf_factors = confidence_explanation.split(": ", 1)
    conf_details = conf_factors[1] if len(conf_factors) > 1 else confidence_explanation

    risk_factors_text = "\n".join([f"• {f}" for f in risk_assessment["factors"][:2]])

    blocks.append({
        "type": "section",
        "fields": [
            {
                "type": "mrkdwn",
                "text": f"*{conf_emoji} Confidence: {confidence.capitalize()}*\n{conf_details}"
            },
            {
                "type": "mrkdwn",
                "text": f"*{risk_emoji} Risk: {risk_assessment['level'].capitalize()}*\n{risk_factors_text}"
            }
        ]
    })

    blocks.append({"type": "divider"})

    # Reviewers section with explainable scores
    if ranked_candidates:
        reviewer_lines = []
        for i, c in enumerate(ranked_candidates[:3], 1):
            login = c.get("login", "unknown")
            score = c.get("score", 0)
            reasons = c.get("reasons", [])
            reasons_short = ", ".join(reasons[:2]) if reasons else ""
            reviewer_lines.append(f"{i}. *{login}* `{score:.2f}` — {reasons_short}")

        reviewer_text = "\n".join(reviewer_lines)
        if why_top:
            reviewer_text += f"\n\n:bulb: _{why_top}_"

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*👥 Recommended Reviewers*\n{reviewer_text}"
            }
        })

    # Action section
    if matched and plan:
        reviewers_str = ", ".join(plan.get("reviewers", []))
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*✅ Proposed Action*\nRequest reviewers: {reviewers_str}"
            }
        })

        # Action buttons (placeholders - require Orchestrate webhook setup)
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✓ Approve",
                        "emoji": True
                    },
                    "style": "primary",
                    "action_id": f"approve_{run_id}",
                    "value": run_id
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✗ Cancel",
                        "emoji": True
                    },
                    "style": "danger",
                    "action_id": f"cancel_{run_id}",
                    "value": run_id
                }
            ]
        })
    else:
        # No action needed
        reason_text = non_match_explanation or "No action required"
        # Truncate for block display
        if len(reason_text) > 200:
            reason_text = reason_text[:197] + "..."
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*ℹ️ Status*\n{reason_text}"
            }
        })

    # Context footer
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Run ID: `{run_id}` | Powered by watsonx Orchestrate"
            }
        ]
    })

    return blocks


class AnalyzeIn(BaseModel):
    pr_url: Optional[str] = None
    run_id: str  # REQUIRED - must come from Orchestrate
    mode: str = "why"


@app.post("/analyze")
def analyze(body: AnalyzeIn):
    _load_env_if_needed()
    run_id = body.run_id  # Required, no fallback generation - must come from Orchestrate
    mode = body.mode or "why"

    # Config defaults
    excluded = os.getenv("EXCLUDED_LABELS", "wip,blocked,parked,do-not-merge,waiting-on-external")
    excluded_labels = [e.strip() for e in excluded.split(",") if e.strip()]
    activity_window_hours = int(os.getenv("ACTIVITY_WINDOW_HOURS", "5"))
    threshold_hours = int(os.getenv("S2_THRESHOLD_HOURS", "1"))

    if mode == "scan":
        repo = os.getenv("DEFAULT_REPO", "")
        if not repo or "/" not in repo:
            raise HTTPException(status_code=400, detail="DEFAULT_REPO not set for scan")
        owner, repo_name = repo.split("/", 1)
        prs = _get_json(
            f"https://api.github.com/repos/{owner}/{repo_name}/pulls",
            params={"state": "open", "per_page": 20},
        )
        stalled = []
        for pr in prs:
            matched, reason = _s2_match(pr, excluded_labels, activity_window_hours, threshold_hours)
            if matched:
                created_at = _iso_to_dt(pr["created_at"])
                stalled.append(
                    {
                        "pr_url": pr["html_url"],
                        "title": pr["title"],
                        "age_hours": round(_hours_since(created_at), 1),
                        "reason": reason,
                    }
                )
        stalled.sort(key=lambda x: x["age_hours"], reverse=True)
        results = stalled[:3]
        lines = [f"Unblocker scan (run_id: {run_id})"]
        if not results:
            lines.append("No stalled PRs found.")
        else:
            for item in results:
                lines.append(f"- {item['title']} ({item['pr_url']}) age {item['age_hours']}h")
        return {"run_id": run_id, "mode": "scan", "results": results, "scan_text": "\n".join(lines)}

    if not body.pr_url:
        raise HTTPException(status_code=400, detail="pr_url required for mode=why")

    owner, repo, number = _parse_pr_url(body.pr_url)
    pr = _get_pr(owner, repo, number)
    matched, reason = _s2_match(pr, excluded_labels, activity_window_hours, threshold_hours)

    files = _get_pr_files(owner, repo, number)
    codeowners_lines = _load_codeowners(owner, repo)
    owners = _match_codeowners(codeowners_lines, files) if codeowners_lines else []
    source = "codeowners" if owners else "recent"
    candidates = owners

    if not candidates:
        candidates = _get_contributors(owner, repo, limit=3)
        source = "recent" if candidates else "fallback"

    if not candidates:
        candidates = _default_reviewers()
        source = "fallback" if candidates else "none"

    # Remove PR author if present
    author = pr.get("user", {}).get("login")
    candidates = [c for c in candidates if c != author]

    candidates = _normalize_handles(candidates)
    confidence = _confidence_for(source, len(candidates))

    # Build candidate list for AI ranking
    candidate_dicts = [{"login": c, "source": source} for c in candidates]

    # AI: Generate PR summary
    pr_title = pr.get("title", "")
    ai_summary = summarize_pr(pr_title, files)

    # Load seeded stats and compute deterministic scores
    stats_map = load_reviewer_stats()
    scored_candidates = rank_candidates(candidates, source, stats_map)

    # Generate "Why #1" explanation
    why_top = explain_top_choice(scored_candidates)

    # AI: Rank with rationale (merge with scored data)
    ranked_candidates, ai_rationale = rank_reviewers_with_rationale(
        pr_title, files,
        [{"login": c["login"], "source": c["source"]} for c in scored_candidates]
    )

    # Merge scores back into AI-ranked candidates
    for rc in ranked_candidates:
        match = next((sc for sc in scored_candidates if sc["login"] == rc["login"]), None)
        if match:
            rc["score"] = match["score"]
            rc["reasons"] = match["reasons"]

    plan = None
    if matched and confidence != "none":
        # Use ranked order for reviewers
        top_reviewers = [c["login"] for c in ranked_candidates[:3]]
        plan = {
            "action": "request_reviewers",
            "pr_url": body.pr_url,
            "reviewers": top_reviewers,
            "comment": f"🤖 Unblocker: {ai_summary}",
        }

    # Approval decision for Orchestrate - backend returns data, Orchestrate decides action
    approval_required = confidence in ("low", "none")
    auto_execute = confidence == "high" and matched

    created_at_dt = _iso_to_dt(pr["created_at"])
    updated_at_dt = _iso_to_dt(pr["updated_at"])
    age_hours = round(_hours_since(created_at_dt), 1)
    activity_hours = round(_hours_since(updated_at_dt), 1)
    metric = {"stalled_hours": age_hours}

    reviewer_count = len(pr.get("requested_reviewers", []))

    # Build evidence dict with computed values for AI explanations
    evidence = {
        "title": pr_title,
        "draft": pr.get("draft"),
        "labels": [l["name"] for l in pr.get("labels", [])],
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "requested_reviewers": [r["login"] for r in pr.get("requested_reviewers", [])],
        "files": files,
        "age_hours": age_hours,
        "activity_hours": activity_hours,
        "threshold_hours": threshold_hours,
        "activity_window_hours": activity_window_hours,
    }

    # Generate AI explanations
    confidence_explanation = generate_confidence_explanation(
        confidence, source, len(ranked_candidates), evidence
    )

    # Assess risk for the proposed action
    risk_assessment = assess_risk(evidence, plan)

    # Generate non-match explanation if applicable
    non_match_explanation = None
    if not matched:
        non_match_explanation = explain_non_match(reason, evidence)

    # Build AI-enhanced preview text with rich formatting
    preview_lines = [
        f"Unblocker preview (run_id: {run_id})",
        f"PR: {pr_title} ({body.pr_url})",
        "",
        f"📝 AI Summary: {ai_summary}",
        "",
        f"📊 Confidence: {confidence.capitalize()}",
    ]

    # Add confidence explanation details
    conf_parts = confidence_explanation.split(": ", 1)
    if len(conf_parts) > 1:
        for detail in conf_parts[1].split("; "):
            preview_lines.append(f"   → {detail}")

    preview_lines.append("")
    preview_lines.append(f"⚠️ Risk: {risk_assessment['level'].capitalize()}")
    for factor in risk_assessment["factors"][:3]:
        preview_lines.append(f"   → {factor}")

    preview_lines.append("")
    preview_lines.append("Recommended Reviewers:")
    for i, c in enumerate(ranked_candidates[:3], 1):
        login = c.get("login", "unknown")
        score = c.get("score", 0)
        reasons = c.get("reasons", [])
        preview_lines.append(f"  {i}. {login} (Score: {score:.2f})")
        for reason in reasons[:3]:
            preview_lines.append(f"     -> {reason}")

    if why_top:
        preview_lines.append("")
        preview_lines.append(f"[Why #1] {why_top}")

    preview_lines.append("")
    preview_lines.append("[POC: Scores from seeded data; production learns from outcomes]")

    if matched and plan:
        preview_lines.append("")
        preview_lines.append(f"Action: request reviewers {', '.join(plan['reviewers'])}")
    else:
        preview_lines.append("")
        preview_lines.append(non_match_explanation or f"No action. Reason: {reason}")

    preview_text = "\n".join(preview_lines)

    # Build Slack Block Kit blocks for richer display
    preview_blocks = _build_preview_blocks(
        run_id=run_id,
        pr_title=pr_title,
        pr_url=body.pr_url,
        ai_summary=ai_summary,
        confidence=confidence,
        confidence_explanation=confidence_explanation,
        risk_assessment=risk_assessment,
        ranked_candidates=ranked_candidates,
        matched=matched,
        plan=plan,
        non_match_explanation=non_match_explanation,
        why_top=why_top,
    )

    response = {
        "run_id": run_id,
        "mode": "why",
        "matched": matched,
        "reason": reason,
        "confidence": confidence,
        "approval_required": approval_required,  # Orchestrate reads this to decide gating
        "auto_execute": auto_execute,            # Orchestrate reads this for auto-approve
        "confidence_explanation": confidence_explanation,
        "risk_assessment": risk_assessment,
        "non_match_explanation": non_match_explanation,
        "candidates": ranked_candidates,
        "ai_summary": ai_summary,
        "ai_rationale": ai_rationale,
        "why_top": why_top,
        "plan": plan,
        "evidence": evidence,
        "metric": metric,
        "preview_text": preview_text,
        "preview_blocks": preview_blocks,
    }
    if plan:
        PLAN_CACHE[run_id] = {
            "plan": plan,
            "metric": metric,
            "title": pr.get("title"),
            "pr_url": body.pr_url,
            "reviewers": plan.get("reviewers", []),
            "created_at": time.time(),  # For expiration check
        }
    return response


class ActIn(BaseModel):
    run_id: str  # REQUIRED - must come from Orchestrate
    approved: bool = True
    plan: Optional[Dict[str, Any]] = None


@app.post("/act")
def act(body: ActIn):
    if not body.approved:
        return {"run_id": body.run_id, "status": "cancelled"}

    # Verify run_id format - must start with "orch_" to prove it came from Orchestrate
    if not body.run_id.startswith("orch_"):
        raise HTTPException(
            status_code=403,
            detail="Invalid run_id format. Actions must be initiated through Orchestrate."
        )

    # Require run_id for plan lookup - must come from Orchestrate
    cached = PLAN_CACHE.get(body.run_id, {})
    if not cached and not body.plan:
        raise HTTPException(status_code=400, detail="No cached plan for run_id; provide explicit plan")

    # Check plan expiration (5 minutes) - forces approval through Orchestrate flow
    PLAN_EXPIRY_SECONDS = 300  # 5 minutes
    if cached.get("created_at") and (time.time() - cached["created_at"]) > PLAN_EXPIRY_SECONDS:
        del PLAN_CACHE[body.run_id]
        raise HTTPException(
            status_code=410,
            detail="Plan expired. Re-run analysis through Orchestrate to get a fresh plan."
        )
    plan = body.plan or cached.get("plan")
    if not plan and body.run_id:
        plan = cached.get("plan")
    if not plan:
        raise HTTPException(status_code=400, detail="Missing plan data for execution.")
    action = plan.get("action")
    if action != "request_reviewers":
        raise HTTPException(status_code=400, detail="unsupported action")

    pr_url = plan.get("pr_url")
    if not pr_url:
        raise HTTPException(status_code=400, detail="plan missing pr_url")
    owner, repo, number = _parse_pr_url(pr_url)

    reviewers = [r.lstrip("@") for r in plan.get("reviewers", [])]
    if not reviewers:
        raise HTTPException(status_code=400, detail="no reviewers to request")

    start_time = time.time()

    _post_json(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
        {"reviewers": reviewers},
    )

    comment = plan.get("comment")
    if comment:
        _post_json(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments",
            {"body": comment},
        )

    # Verification: re-fetch PR to confirm reviewers were requested
    pr_after = _get_pr(owner, repo, number)
    actual_reviewers = [r["login"] for r in pr_after.get("requested_reviewers", [])]
    verified = len(actual_reviewers) >= 1

    exec_time = round(time.time() - start_time, 2)
    metric = cached.get("metric", {})
    stalled_hours = metric.get("stalled_hours")
    reviewers_fmt = ", ".join([f"@{r}" for r in reviewers])

    outcome_lines = [
        "✅ Reviewers requested" if verified else "⚠️ Reviewers request sent (unverified)",
        f"PR: {cached.get('title', '')} ({cached.get('pr_url', '')})",
        f"Reviewers: {reviewers_fmt}",
    ]
    if stalled_hours is not None:
        outcome_lines.append(f"Metric: stalled {stalled_hours}h → assigned in {exec_time}s")
    outcome_lines.append(f"Run ID: {body.run_id}")
    outcome_text = "\n".join([l for l in outcome_lines if l.strip()])

    return {
        "run_id": body.run_id,
        "status": "reviewers_requested",
        "reviewers": reviewers,
        "verified": verified,
        "execution_time_s": exec_time,
        "outcome_text": outcome_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pattern Wizard - "Idea to Deployment" demo feature
# ─────────────────────────────────────────────────────────────────────────────

# Regex for constrained NL template
WIZARD_PATTERN = re.compile(
    r"(?:If|When)\s+(?:a\s+)?PR\s+has\s+no\s+reviewers?\s+after\s+(\d+)\s*h(?:ours?)?,?\s*"
    r"request\s+reviewers?\s+from\s+(CODEOWNERS|recent|default)",
    re.IGNORECASE,
)


class WizardIn(BaseModel):
    input: str
    run_id: str  # REQUIRED - correlate wizard actions with Orchestrate
    activate: bool = False
    dry_run_pr_url: Optional[str] = None


@app.post("/wizard")
def wizard(body: WizardIn):
    """
    Pattern Wizard: Convert natural language to S2 rule config.

    Input examples:
    - "If PR has no reviewers after 2 hours, request reviewers from CODEOWNERS"
    - "When PR has no reviewers after 24h, request reviewers from recent"
    """
    run_id = body.run_id  # Required - must come from Orchestrate
    user_input = body.input.strip()

    # Step 1: Try regex parsing first (fast, reliable)
    match = WIZARD_PATTERN.search(user_input)
    parsed_config = None

    if match:
        threshold = int(match.group(1))
        source = match.group(2).lower()
        parsed_config = {
            "threshold_hours": threshold,
            "source": source,
            "excluded_labels": None,
        }
        parse_method = "regex"
    else:
        # Step 2: Fall back to AI normalization
        parsed_config = normalize_wizard_input(user_input)
        parse_method = "ai"

    if not parsed_config:
        return {
            "run_id": run_id,
            "status": "parse_failed",
            "message": "Could not parse input. Please use format: 'If PR has no reviewers after X hours, request reviewers from CODEOWNERS|recent|default'",
            "input": user_input,
        }

    # Normalize source
    source = parsed_config.get("source", "codeowners").lower()
    if source not in ("codeowners", "recent", "default"):
        source = "codeowners"
    parsed_config["source"] = source

    # Step 3: Config preview
    config_preview = {
        "rule": "S2_reviewer_missing",
        "threshold_hours": parsed_config.get("threshold_hours", 24),
        "reviewer_source": source,
        "excluded_labels": parsed_config.get("excluded_labels")
        or ["wip", "blocked", "parked", "do-not-merge", "waiting-on-external"],
    }

    response = {
        "run_id": run_id,
        "status": "preview",
        "parse_method": parse_method,
        "input": user_input,
        "config": config_preview,
    }

    # Step 4: Optional dry-run on a specific PR
    if body.dry_run_pr_url:
        try:
            owner, repo, number = _parse_pr_url(body.dry_run_pr_url)
            pr = _get_pr(owner, repo, number)

            # Temporarily apply config for dry-run
            old_threshold = os.environ.get("S2_THRESHOLD_HOURS")
            os.environ["S2_THRESHOLD_HOURS"] = str(config_preview["threshold_hours"])

            excluded = config_preview["excluded_labels"]
            activity_window = int(os.getenv("ACTIVITY_WINDOW_HOURS", "5"))
            matched, reason = _s2_match(pr, excluded, activity_window, config_preview["threshold_hours"])

            # Restore
            if old_threshold:
                os.environ["S2_THRESHOLD_HOURS"] = old_threshold

            response["dry_run"] = {
                "pr_url": body.dry_run_pr_url,
                "pr_title": pr.get("title"),
                "would_match": matched,
                "reason": reason,
            }
        except Exception as e:
            response["dry_run"] = {"error": str(e)}

    # Step 5: Activation (update runtime config)
    if body.activate:
        WIZARD_CONFIG["threshold_hours"] = config_preview["threshold_hours"]
        WIZARD_CONFIG["source"] = source
        WIZARD_CONFIG["excluded_labels"] = config_preview["excluded_labels"]

        # Also update env for analyze endpoint
        os.environ["S2_THRESHOLD_HOURS"] = str(config_preview["threshold_hours"])

        response["status"] = "activated"
        response["message"] = f"Rule activated: PRs without reviewers after {config_preview['threshold_hours']}h will trigger reviewer requests from {source}."

    # Generate wizard preview text
    preview_lines = [
        f"🧙 Pattern Wizard (run_id: {run_id})",
        "",
        f"Input: \"{user_input}\"",
        f"Parse method: {parse_method}",
        "",
        "Extracted Configuration:",
        f"  • Rule: {config_preview['rule']}",
        f"  • Threshold: {config_preview['threshold_hours']} hours",
        f"  • Reviewer source: {config_preview['reviewer_source']}",
        f"  • Excluded labels: {', '.join(config_preview['excluded_labels'])}",
    ]

    if "dry_run" in response and "would_match" in response["dry_run"]:
        dr = response["dry_run"]
        preview_lines.append("")
        preview_lines.append("Dry-run result:")
        preview_lines.append(f"  • PR: {dr['pr_title']}")
        preview_lines.append(f"  • Would match: {'Yes' if dr['would_match'] else 'No'} ({dr['reason']})")

    if response["status"] == "activated":
        preview_lines.append("")
        preview_lines.append("✅ Configuration activated!")
    else:
        preview_lines.append("")
        preview_lines.append("To activate, call with activate=true")

    response["preview_text"] = "\n".join(preview_lines)

    return response
