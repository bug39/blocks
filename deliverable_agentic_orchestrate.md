Deliverable: Written statement on agentic AI and IBM watsonx Orchestrate usage

Agentic AI usage

Unblocker Lite uses two coordinated agents:

1) Planner Agent (LLM-based)
Inputs: PR metadata, activity history, touched file paths, existing reviewers.
Outputs:
- Reviewer ranking within a deterministic candidate set
- A concise PR summary for reviewers
- A rationale that references evidence (for example: "Touches payments/ owned by A/B; recent changes by C")
Constraint: The agent cannot execute actions or alter state. It only proposes a plan and rationale.

2) Policy Gate Agent (deterministic)
Validates the Planner output against strict rules:
- Allowlisted actions only (request reviewers, optional comment)
- Evidence completeness required
- Write budget enforced
- Confidence derived from evidence, not LLM self-assessment
Tags plans as auto-execute or approval-required.

How IBM watsonx Orchestrate is used

watsonx Orchestrate is the workflow substrate and control plane:
- Flow runtime: executes the end-to-end flow: fetch evidence -> AI plan -> policy gate -> preview -> (auto-execute or approval) -> verify -> log outcome.
- Approval gating: handles approvals in Slack (explicit approval for low-confidence or risky actions; fast-by-default auto-exec for low-risk actions with a cancel window).
- Audit and traceability: Orchestrate run logs are the demo proof artifact. Each Slack preview includes the run_id, which correlates directly to the Orchestrate run trace.
- Tool orchestration: Orchestrate calls thin backend tools for GitHub data access and execution, but Orchestrate owns the state machine and decision flow.

How agents work together

- The Planner Agent proposes who to ask and why, based on evidence.
- The Policy Gate Agent ensures the plan is safe, grounded, and within budget.
- watsonx Orchestrate coordinates the agents, approvals, and execution, and provides the end-to-end audit trail.

This design makes the system both agentic and safe, turning stalled work into verified action with full governance and traceability.
