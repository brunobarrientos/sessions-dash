# ai-usage - Project Instructions

## Token Emergency: Switching to a Cheaper Model

When Claude Sonnet tokens are exhausted, use one of these wrappers in a **new terminal or tmux session** (they are bash functions — source `~/.bashrc` first):

| Command | Model | Notes |
|---------|-------|-------|
| `minimax` | MiniMax M2.5 | **Preferred fallback.** Fast, cheap, good code quality |
| `claude-glm` | GLM 4.7 | Second choice |
| `claude` | Claude Sonnet 4.6 | Vanilla — uses subscription tokens |

**To start a new interactive session with MiniMax:**
```bash
source ~/.bashrc
cd ~/AI/ai-usage
minimax
```

---

## Overview

AI Usage Dashboard — lightweight token cost and session tracking for Claude Code.
Parses `~/.claude/projects/` JSONL session files and displays per-model token usage and estimated costs.
Runs as a systemd user service on port 8766, accessible on Tailscale at `http://100.88.85.93:8766`.

## File Structure

```
ai-usage/
  server.py       # Python HTTP backend (stdlib only, ~310 lines)
  index.html      # Frontend SPA (single file, dark theme)
  CLAUDE.md       # This file (agent instructions)
```

Related files outside this project:
- `~/.claude/projects/` — Claude Code session JSONL files (token usage source)
- `~/.config/systemd/user/ai-usage.service` — systemd service definition

## Key Design Constraints

- **No build step.** Single index.html with inline CSS and JS. No npm, no bundler, no framework.
- **No pip dependencies.** Python stdlib only (http.server, json, glob, etc).
- **Dark theme.** Ubuntu orange (#E95420) as accent, terminal-inspired palette. See CSS variables at top of index.html.
- **CORS enabled.** All API responses include `Access-Control-Allow-Origin: *`.
- **Mobile-first responsive.** Must work on mobile (≤640px). Bottom navigation on mobile.
- **Safe area insets.** Must respect `env(safe-area-inset-*)` for notched phones.

## Development Workflow

```bash
# After editing server.py or index.html, restart the service:
systemctl --user restart ai-usage

# Watch logs:
journalctl --user -u ai-usage -f

# Test endpoints:
curl -s http://localhost:8766/api/usage?days=7 | python3 -m json.tool
curl -s http://localhost:8766/api/sessions?days=7 | python3 -m json.tool
curl -s http://localhost:8766/health | python3 -m json.tool
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/usage?days=N` | GET | Aggregated usage by model and by day |
| `/api/sessions?days=N&limit=N` | GET | Per-session cost breakdown |
| `/health` | GET | Health check |
| `/` | GET | Serve index.html |

## Model Pricing

Model pricing rates are defined in two places (keep in sync):
- `server.py` → `MODEL_PRICING` dict (backend cost calculation)
- `index.html` → `RATES` const in `<script>` section (frontend daily cost calculation)

When adding a new model, update BOTH files.

## Common Maintenance

| Task | How |
|------|-----|
| Update model pricing | Edit `MODEL_PRICING` in server.py + `RATES` in index.html |
| Service not starting | `journalctl --user -u ai-usage -e` |
| Port conflict | `fuser 8766/tcp` |
| Force restart | `systemctl --user restart ai-usage` |

## Always Keep Service Running

The systemd service should always be running. After making changes:

1. Restart service: `systemctl --user restart ai-usage`
2. Verify it started: `systemctl --user status ai-usage`
3. Test endpoints: `curl -s http://localhost:8766/health`
4. Only then consider the task complete
