"""
AI module for Unblocker Lite - watsonx.ai integration.

Provides:
- rank_reviewers_with_rationale(): AI-ranked reviewer list with explanations
- summarize_pr(): Short PR summary for reviewer context
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

# Configure module logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Cache for IAM token (valid ~1 hour)
_iam_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0}


def _load_env_if_needed():
    """Load .env file if not already loaded."""
    if os.getenv("IBM_API_KEY"):
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


def _get_iam_token() -> str:
    """Exchange IBM API key for IAM bearer token."""
    _load_env_if_needed()

    # Check cache
    if _iam_token_cache["token"] and time.time() < _iam_token_cache["expires_at"] - 60:
        return _iam_token_cache["token"]

    api_key = os.getenv("IBM_API_KEY")
    if not api_key:
        raise RuntimeError("Missing IBM_API_KEY environment variable")

    resp = requests.post(
        "https://iam.cloud.ibm.com/identity/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={api_key}",
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"IAM token exchange failed: {resp.status_code} {resp.text}")

    data = resp.json()
    _iam_token_cache["token"] = data["access_token"]
    _iam_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)

    return _iam_token_cache["token"]


def _generate(prompt: str, max_tokens: int = 300) -> str:
    """Call watsonx.ai text generation API."""
    _load_env_if_needed()

    token = _get_iam_token()
    project_id = os.getenv("WATSONX_PROJECT_ID")

    if not project_id:
        raise RuntimeError("Missing WATSONX_PROJECT_ID environment variable")

    url = "https://eu-de.ml.cloud.ibm.com/ml/v1/text/generation?version=2023-05-29"

    payload = {
        "input": prompt,
        "parameters": {
            "decoding_method": "greedy",
            "max_new_tokens": max_tokens,
            "temperature": 0.1,
            "stop_sequences": ["\n\n---", "```"],
        },
        "model_id": "ibm/granite-3-3-8b-instruct",
        "project_id": project_id,
    }

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=60,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"watsonx.ai generation failed: {resp.status_code} {resp.text}")

    data = resp.json()
    return data["results"][0]["generated_text"].strip()


def summarize_pr(title: str, files: List[str]) -> str:
    """Generate a 2-3 sentence PR summary for reviewer context."""
    files_str = ", ".join(files[:10])
    if len(files) > 10:
        files_str += f" (+{len(files) - 10} more)"

    prompt = f"""Summarize this pull request in 2-3 short sentences for a code reviewer.

PR Title: {title}
Files Changed: {files_str}

Write a concise summary focusing on what areas of the codebase are affected. Do not use markdown formatting."""

    try:
        return _generate(prompt, max_tokens=150)
    except RuntimeError as e:
        # Missing credentials or API error
        logger.warning("AI summarization unavailable: %s. Using fallback.", e)
        return f"This PR modifies {len(files)} file(s) including {files[0] if files else 'unknown paths'}."
    except requests.RequestException as e:
        # Network error
        logger.error("Network error during PR summarization: %s", e)
        return f"This PR modifies {len(files)} file(s) including {files[0] if files else 'unknown paths'}."
    except Exception as e:
        # Unexpected error - log full details
        logger.exception("Unexpected error in summarize_pr: %s", e)
        return f"This PR modifies {len(files)} file(s) including {files[0] if files else 'unknown paths'}."


def rank_reviewers_with_rationale(
    pr_title: str,
    files: List[str],
    candidates: List[Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], str]:
    """
    AI-rank reviewer candidates and generate rationale.

    Args:
        pr_title: The PR title
        files: List of changed file paths
        candidates: List of {"login": "@user", "source": "codeowners|recent|fallback"}

    Returns:
        Tuple of (ranked_candidates, overall_rationale)
        Each ranked candidate includes "rationale" field
    """
    if not candidates:
        return [], "No reviewer candidates available."

    # Build candidate descriptions for prompt
    candidate_lines = []
    for c in candidates:
        login = c.get("login", "unknown")
        source = c.get("source", "unknown")
        candidate_lines.append(f"- {login} (source: {source})")

    files_str = ", ".join(files[:8])
    if len(files) > 8:
        files_str += f" (+{len(files) - 8} more)"

    prompt = f"""You are ranking code reviewers for a pull request.

PR Title: {pr_title}
Files Changed: {files_str}

Candidates:
{chr(10).join(candidate_lines)}

For each candidate, write a brief rationale (1 sentence) explaining why they should review this PR.
Focus on their source (CODEOWNERS = owns the code, recent = recently modified these files, fallback = default reviewer).

Output format (one line per candidate):
@username - [rationale]

Only output the ranked list, nothing else."""

    try:
        result = _generate(prompt, max_tokens=250)

        # Parse AI response into ranked candidates with rationale
        ranked = []
        rationale_map = {}

        for line in result.split("\n"):
            line = line.strip()
            if not line or " - " not in line:
                continue
            parts = line.split(" - ", 1)
            if len(parts) == 2:
                handle = parts[0].strip()
                rationale = parts[1].strip()
                # Normalize handle
                if not handle.startswith("@"):
                    handle = f"@{handle}"
                rationale_map[handle] = rationale

        # Preserve AI ordering, match back to original candidates
        for c in candidates:
            login = c.get("login", "")
            if login in rationale_map:
                ranked.append({
                    **c,
                    "rationale": rationale_map[login],
                })
            else:
                # Fallback rationale based on source
                source = c.get("source", "unknown")
                fallback_rationale = _fallback_rationale(login, source, files)
                ranked.append({
                    **c,
                    "rationale": fallback_rationale,
                })

        overall = "AI-ranked reviewers based on code ownership and recent activity."
        return ranked, overall

    except RuntimeError as e:
        # Missing credentials or API error
        logger.warning("AI ranking unavailable: %s. Using deterministic fallback.", e)
        ranked = []
        for c in candidates:
            login = c.get("login", "")
            source = c.get("source", "unknown")
            ranked.append({
                **c,
                "rationale": _fallback_rationale(login, source, files),
            })
        return ranked, f"Reviewers selected based on {candidates[0].get('source', 'available')} data."
    except requests.RequestException as e:
        # Network error
        logger.error("Network error during reviewer ranking: %s", e)
        ranked = []
        for c in candidates:
            login = c.get("login", "")
            source = c.get("source", "unknown")
            ranked.append({
                **c,
                "rationale": _fallback_rationale(login, source, files),
            })
        return ranked, f"Reviewers selected based on {candidates[0].get('source', 'available')} data."
    except Exception as e:
        # Unexpected error - log full details
        logger.exception("Unexpected error in rank_reviewers_with_rationale: %s", e)
        ranked = []
        for c in candidates:
            login = c.get("login", "")
            source = c.get("source", "unknown")
            ranked.append({
                **c,
                "rationale": _fallback_rationale(login, source, files),
            })
        return ranked, f"Reviewers selected based on {candidates[0].get('source', 'available')} data."


def _fallback_rationale(login: str, source: str, files: List[str]) -> str:
    """Generate deterministic fallback rationale."""
    if source == "codeowners":
        # Try to find a directory from files
        if files:
            dirs = set()
            for f in files[:5]:
                if "/" in f:
                    dirs.add(f.split("/")[0] + "/")
            if dirs:
                return f"CODEOWNER for {', '.join(sorted(dirs)[:2])}"
        return "CODEOWNER for modified paths"
    elif source == "recent":
        return "Recently contributed to modified files"
    else:
        return "Default reviewer for this repository"


def generate_confidence_explanation(
    confidence: str,
    source: str,
    candidate_count: int,
    evidence: dict,
) -> str:
    """
    Generate human-readable explanation of confidence level.

    Args:
        confidence: "high", "low", or "none"
        source: "codeowners", "recent", or "fallback"
        candidate_count: Number of reviewer candidates found
        evidence: Dict with age_hours, activity_hours, threshold_hours, etc.

    Returns:
        Human-readable explanation string
    """
    reasons = []

    age_hours = evidence.get("age_hours", 0)
    threshold_hours = evidence.get("threshold_hours", 1)
    activity_hours = evidence.get("activity_hours", 0)

    # Source-based reasons
    if source == "codeowners":
        if candidate_count >= 2:
            reasons.append(f"{candidate_count} CODEOWNERS match modified paths")
        elif candidate_count == 1:
            reasons.append("1 CODEOWNER matches modified paths")
    elif source == "recent":
        reasons.append(f"{candidate_count} recent contributor(s) identified")
    elif source == "fallback":
        reasons.append("Using fallback/default reviewers")

    # Age-based reasons
    if age_hours > threshold_hours:
        if age_hours > threshold_hours * 10:
            reasons.append(f"PR age ({age_hours:.1f}h) significantly exceeds threshold ({threshold_hours}h)")
        else:
            reasons.append(f"PR age ({age_hours:.1f}h) exceeds threshold ({threshold_hours}h)")

    # Activity-based reasons
    if activity_hours > 24:
        reasons.append(f"No recent activity in last {activity_hours:.0f}h")

    # Confidence-level prefix
    if confidence == "high":
        prefix = "High confidence"
    elif confidence == "low":
        prefix = "Low confidence"
    else:
        prefix = "No candidates"

    if not reasons:
        if confidence == "none":
            return "No candidates: Unable to identify suitable reviewers"
        return f"{prefix}: Based on available evidence"

    return f"{prefix}: {'; '.join(reasons)}"


def assess_risk(pr_data: dict, plan: dict) -> dict:
    """
    Simple risk assessment for the proposed action.

    Args:
        pr_data: PR metadata (labels, files, etc.)
        plan: The proposed action plan

    Returns:
        {"level": "low"|"medium"|"high", "factors": [...]}
    """
    factors = []
    risk_score = 0

    action = plan.get("action") if plan else None
    reviewers = plan.get("reviewers", []) if plan else []

    # Action reversibility
    if action == "request_reviewers":
        factors.append("Action (request_reviewers) is easily reversible")
    elif action == "comment":
        factors.append("Action (comment) is easily deletable")
    else:
        factors.append("No action proposed")

    # Check for sensitive files
    files = pr_data.get("files", [])
    sensitive_patterns = [
        ".env", "secret", "credential", "password", "key",
        "config/prod", "production", ".pem", ".key"
    ]
    sensitive_files = [f for f in files if any(p in f.lower() for p in sensitive_patterns)]
    if sensitive_files:
        risk_score += 2
        factors.append(f"Sensitive files detected: {', '.join(sensitive_files[:3])}")
    else:
        factors.append("No sensitive files modified")

    # Check reviewer count
    if len(reviewers) > 3:
        risk_score += 1
        factors.append(f"Requesting {len(reviewers)} reviewers (above typical)")
    elif len(reviewers) > 0:
        factors.append(f"Requesting {len(reviewers)} reviewer(s)")

    # Check labels for risk indicators
    labels = pr_data.get("labels", [])
    risky_labels = ["breaking-change", "security", "critical", "urgent"]
    found_risky = [l for l in labels if any(r in l.lower() for r in risky_labels)]
    if found_risky:
        risk_score += 1
        factors.append(f"Risk-indicating labels: {', '.join(found_risky)}")

    # Determine level
    if risk_score >= 3:
        level = "high"
    elif risk_score >= 1:
        level = "medium"
    else:
        level = "low"

    return {"level": level, "factors": factors}


def explain_non_match(reason: str, evidence: dict) -> str:
    """
    Generate 'why not' explanation for PRs that don't trigger action.

    Args:
        reason: The reason code from _s2_match (e.g., "draft", "recent_activity")
        evidence: Dict with relevant metrics

    Returns:
        Human-readable explanation of why no action was taken
    """
    explanations = {
        "draft": (
            "PR is marked as draft",
            "Draft PRs are excluded from automated reviewer requests. "
            "Mark as ready for review when you want to request reviewers."
        ),
        "excluded_label": (
            "PR has an excluded label",
            "PRs with labels like 'wip', 'blocked', or 'do-not-merge' are excluded. "
            "Remove the label to enable reviewer requests."
        ),
        "already_requested": (
            "Reviewers already requested",
            "This PR already has reviewers requested. No additional action needed."
        ),
        "recent_activity": (
            "PR has recent activity",
            f"Last activity was {evidence.get('activity_hours', 0):.1f}h ago "
            f"(threshold: {evidence.get('activity_window_hours', 5)}h). "
            "Check again after the activity window passes."
        ),
        "too_new": (
            "PR is too new",
            f"PR age is {evidence.get('age_hours', 0):.1f}h "
            f"(threshold: {evidence.get('threshold_hours', 1)}h). "
            "Give the author time to self-review and request reviewers."
        ),
    }

    title, detail = explanations.get(
        reason,
        ("Unknown reason", f"Reason code: {reason}")
    )

    return f"No action required.\n\nReason: {title}\n   → {detail}"


def normalize_wizard_input(user_input: str) -> Optional[Dict[str, Any]]:
    """
    Use AI to normalize free-form wizard input to structured config.

    Returns None if input cannot be parsed into valid config.
    """
    prompt = f"""Parse this natural language rule into a JSON config.

Input: "{user_input}"

Expected format:
{{"threshold_hours": <number>, "source": "<CODEOWNERS|recent|default>", "excluded_labels": [<list or null>]}}

If the input doesn't describe a reviewer rule, respond with: INVALID

Output only the JSON or INVALID, nothing else."""

    try:
        result = _generate(prompt, max_tokens=100)
        result = result.strip()

        if result.upper() == "INVALID":
            logger.info("Wizard input parsed as INVALID by AI: %s", user_input[:100])
            return None

        # Clean up potential markdown
        if result.startswith("```"):
            result = result.split("```")[1]
            if result.startswith("json"):
                result = result[4:]

        return json.loads(result)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse AI wizard response as JSON: %s", e)
        return None
    except RuntimeError as e:
        logger.warning("AI wizard parsing unavailable: %s", e)
        return None
    except requests.RequestException as e:
        logger.error("Network error during wizard parsing: %s", e)
        return None
    except Exception as e:
        logger.exception("Unexpected error in normalize_wizard_input: %s", e)
        return None
