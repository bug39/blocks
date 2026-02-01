import json
import os
from typing import Dict, Any, List, Tuple


def load_reviewer_stats() -> Dict[str, Dict[str, Any]]:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(root, "data", "reviewer_stats.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _score_components(source: str, stats: Dict[str, Any]) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    ownership_score = 0.0
    if source == "codeowners":
        ownership_score = 0.5
        reasons.append("Owns touched paths (CODEOWNERS)")

    edits = int(stats.get("recent_file_edits", 0) or 0)
    if edits >= 3:
        recency_score = 0.3
        reasons.append(f"Edited touched files {edits}x in last 30 days")
    elif edits >= 1:
        recency_score = 0.15
        reasons.append("Edited touched files recently")
    else:
        recency_score = 0.0

    median_hours = stats.get("median_review_hours")
    response_score = 0.0
    if isinstance(median_hours, (int, float)):
        if median_hours <= 2:
            response_score = 0.2
            reasons.append(f"Median review time: {median_hours}h")
        elif median_hours <= 8:
            response_score = 0.1
            reasons.append(f"Median review time: {median_hours}h")
        else:
            response_score = 0.0
            reasons.append(f"Median review time: {median_hours}h (slow)")

    score = round(ownership_score + recency_score + response_score, 2)
    if not reasons:
        reasons.append("No historical signals; default fallback")
    return score, reasons


def rank_candidates(candidates: List[str], source: str, stats_map: Dict[str, Dict[str, Any]]):
    ranked = []
    for login in candidates:
        key = login.lstrip("@")
        stats = stats_map.get(key, {})
        score, reasons = _score_components(source, stats)
        ranked.append(
            {
                "login": login,
                "source": source,
                "score": score,
                "reasons": reasons,
            }
        )
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def explain_top_choice(ranked: List[Dict[str, Any]]) -> str:
    """Generate 'Why #1' comparing top candidates."""
    if not ranked:
        return ""
    if len(ranked) < 2:
        return f"{ranked[0]['login']} is the only candidate."

    top, second = ranked[0], ranked[1]
    diff_factors = []

    # Score comparison
    if top["score"] - second["score"] >= 0.2:
        diff_factors.append(f"significantly higher score ({top['score']:.2f} vs {second['score']:.2f})")

    # Ownership advantage
    if "CODEOWNERS" in str(top.get("reasons", [])):
        diff_factors.append("owns the modified paths")

    # Recency advantage
    if any("Edited" in r for r in top.get("reasons", [])):
        diff_factors.append("recently worked on these files")

    # Response time advantage
    top_reasons = top.get("reasons", [])
    top_has_fast_time = any(
        "review time" in r and "(slow)" not in r
        for r in top_reasons
    )
    if top_has_fast_time:
        diff_factors.append("faster response time")

    if not diff_factors:
        diff_factors.append("best combination of ownership and availability")

    return f"{top['login']} ranks #1: {', '.join(diff_factors[:2])}"
