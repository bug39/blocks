# Unblocker Lite

AI-powered PR reviewer recommendation system built on IBM watsonx Orchestrate.

## Overview

Unblocker Lite automatically detects stalled pull requests and intelligently suggests reviewers using a combination of deterministic rules and AI ranking. It integrates with Slack for user interaction and GitHub for PR analysis and reviewer assignment.

## Features

- **Flow A (`/unblock why <PR_URL>`)**: Analyze a stalled PR, get AI-powered reviewer recommendations with rationale
- **Pattern Wizard (`/unblock wizard`)**: Configure rules using natural language
- **Scan (`/unblock scan`)**: Find top stalled PRs in a repository

## Architecture

```
Slack Command → watsonx Orchestrate → FastAPI Backend → GitHub API
                      ↓
                 Approval Gate
                      ↓
               Execute & Verify
```

## Quick Start

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. Start the server:
   ```bash
   uvicorn app.main:app --reload
   ```

4. Test the health endpoint:
   ```bash
   curl http://localhost:8000/healthz
   ```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub PAT with repo access |
| `IBM_API_KEY` | IBM watsonx.ai API key |
| `WATSONX_PROJECT_ID` | watsonx.ai project ID |
| `SLACK_BOT_OAUTH_TOKEN` | Slack bot token |
| `DEFAULT_REPO` | Default repository for `/unblock scan` |
| `DEFAULT_REVIEWERS` | Fallback reviewers (comma-separated) |
| `S2_THRESHOLD_HOURS` | Hours before PR is considered stalled |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Health check |
| `/analyze` | POST | Analyze PR or scan for stalled PRs |
| `/act` | POST | Execute approved reviewer request |
| `/wizard` | POST | Parse and activate NL rules |

## Project Structure

```
├── app/
│   ├── main.py          # FastAPI endpoints and core logic
│   ├── ai.py            # watsonx.ai integration
│   └── reviewers.py     # Reviewer ranking logic
├── orchestrate/
│   ├── flow_a.yaml      # Orchestrate flow definition
│   ├── agent_instructions.md
│   └── tool_config.json
├── data/
│   └── reviewer_stats.json
└── scripts/
    └── reset_demo.sh
```

## Documentation

- [Implementation Plan](plan_v3_1.md)
- [Implementation Checklist](implementation_checklist.md)
- [Demo Runbook](demo_runbook.md)
- [Manual Setup Guide](manual_setup.md)

## License

Internal use only - IBM Hackathon 2025
