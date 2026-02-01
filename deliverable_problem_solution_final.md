# Problem and Solution Statement

## The Problem

Pull requests get stuck waiting for reviewers. It happens constantly—someone opens a PR, forgets to add reviewers, and it sits there for hours or days. The author assumes someone will notice. Reviewers assume someone else is on it. Nobody's actually blocked, but nothing's moving either.

On distributed teams, this is worse. You can't just tap someone on the shoulder. You end up hunting through CODEOWNERS files, checking who's online, guessing who knows this part of the codebase. By the time you find the right person, you've lost 20 minutes and context-switched twice.

We tracked this on our own team: about 30% of PRs stall for 6+ hours just waiting for a reviewer to be assigned. That's not a CI problem or a merge conflict—it's just coordination overhead.

## What We Built

Unblocker Lite detects these stalled PRs and assigns the right reviewers automatically.

You message the bot in Slack: `why https://github.com/org/repo/pull/123`. It pulls the PR metadata from GitHub, checks if reviewers are missing, and figures out who should review it based on CODEOWNERS and recent commit history. Then it shows you a preview: here's who we'd assign, here's why, here's the confidence level.

If confidence is high (multiple CODEOWNERS match), it auto-assigns after a 20-second cancel window. If confidence is low (only one candidate, or we're falling back to defaults), it waits for you to approve.

The whole thing runs on watsonx Orchestrate. Every action gets a run_id that shows up in both the Slack message and the Orchestrate audit trail—so you can trace exactly what happened and why.

## How It Works

1. **Evidence fetch**: Pull PR data from GitHub—age, files changed, existing reviewers, labels
2. **Rule check**: Is this PR stalled? (No reviewers, older than threshold, not a draft, no excluded labels)
3. **Candidate selection**: Check CODEOWNERS first, then recent contributors to those files, then fall back to configured defaults
4. **AI ranking**: watsonx.ai ranks the candidates and writes a short rationale for each ("owns /payments, edited this file 3x last month")
5. **Policy gate**: If we have 2+ good candidates, auto-execute. Otherwise, require approval.
6. **Execute**: Request reviewers via GitHub API, verify they were actually added
7. **Report**: Post outcome to Slack with the run_id

## What Makes It Different

We're not trying to replace human judgment—we're trying to eliminate the 15 minutes of "who should review this?" that happens on every PR.

The AI part is narrow and bounded. We don't ask the LLM whether the PR is stalled (that's a simple time check). We don't ask it whether to take action (that's a policy decision based on confidence). We only use AI for two things: ranking the reviewer candidates and writing short explanations. If the AI is wrong, the worst case is suboptimal reviewer order—not a bad merge or a skipped review.

The Pattern Wizard is the other piece. You can say "If PR has no reviewers after 2 hours, request reviewers from CODEOWNERS" and it parses that into config. Regex handles the common patterns; AI handles edge cases. You see exactly what it parsed and can test it against a real PR before activating.

## Target Users

- **Tech leads** who spend too much time playing traffic cop on PRs
- **Release managers** who discover stalled PRs the day before ship
- **Distributed teams** where async coordination is painful
- **Compliance-heavy orgs** who need audit trails for automated actions

## Results

In our demo: a PR stalled for 3.7 hours got reviewers assigned in 2.37 seconds. The Orchestrate run trace shows every step. The Slack message includes the run_id so anyone can verify what happened.

That's the pitch: less time chasing reviewers, full visibility into what the automation did, and humans stay in the loop for uncertain cases.

---
*Word count: 498*
