# claw-bot-agents

Local agent prototypes and a clean FastAPI backend for an Ubuntu server.

The older prototype files `bubbles.py` and `mailman.py` are still present and
unchanged. The new backend lives in `agent_core/` with `api.py` as the FastAPI
entrypoint.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the backend

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

Then open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/control`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/system`
- `http://127.0.0.1:8000/agents`
- `http://127.0.0.1:8000/coding/plan?request=make%20dashboard%20better`

## Command Center

The control panel at `/control` supports safe slash commands through
`POST /command`.

Examples:

```text
/system health
/git status
/git push update dashboard controls
/git branch feature/test
/code plan improve dashboard
```

Safe read-only commands run immediately. Git push and branch commands return an
approval prompt first, then run only through `POST /command/approve`.

API examples:

```bash
curl -X POST http://127.0.0.1:8000/command \
  -H "Content-Type: application/json" \
  -d '{"input":"/git status"}'

curl -X POST http://127.0.0.1:8000/command \
  -H "Content-Type: application/json" \
  -d '{"input":"/git push test message"}'

curl -X POST http://127.0.0.1:8000/command/approve \
  -H "Content-Type: application/json" \
  -d '{"action":"git_branch","args":{"branch_name":"feature/test"}}'
```

## System Watcher

AgentOS starts a background `system_watcher` when the FastAPI app starts. It
checks CPU, RAM, disk, load average, and uptime every 60 seconds with `psutil`,
then stores heartbeat and warning events in an in-memory log buffer. It does not
send Telegram messages or run shell commands.

Warnings are logged when CPU or RAM exceed 85%, disk exceeds 90%, or the 1 minute
load average is higher than the CPU core count.

Watcher endpoints:

```bash
curl http://127.0.0.1:8000/agent-logs
curl http://127.0.0.1:8000/agents/system_watcher/status
curl -X POST http://127.0.0.1:8000/agents/system_watcher/start
curl -X POST http://127.0.0.1:8000/agents/system_watcher/stop
```

The System Logs panel in `/control` reads from `/agent-logs`, and the
`system_agent` Start/Stop button controls the watcher loop.

## Self-Healing Agent

AgentOS also starts `self_healing_agent` as a safe background monitor. It checks
AgentOS internal health, Ollama availability at
`http://127.0.0.1:11434/api/tags`, CPU, RAM, disk, and system load every 60
seconds. It writes warnings and suggestions into the shared agent logs.

Self-healing actions are not automatic. Recovery actions require explicit
approval through the API and are allow-listed to:

- `restart_ollama`
- `restart_agentos`

Endpoints:

```bash
curl http://127.0.0.1:8000/self-heal/status
curl http://127.0.0.1:8000/self-heal/suggestions
curl -X POST http://127.0.0.1:8000/self-heal/approve \
  -H "Content-Type: application/json" \
  -d '{"action":"restart_ollama"}'
```

## Local Coding Agent

The local coding agent uses Ollama only. It sends bounded repo context from
`~/agents` to `http://127.0.0.1:11434/api/chat` with the default model
`qwen2.5-coder:7b`.

Example:

```bash
curl "http://127.0.0.1:8000/coding/plan?request=make%20dashboard%20better"
```

This endpoint is planning-only and safe: it returns a summary, files to review,
a proposed plan, risks, suggested tests, and one safe next command. It does not
edit files, run project commands, or call OpenAI/Codex APIs.

## Notes

- The backend assumes Ollama may be available locally at
  `http://127.0.0.1:11434`.
- Ollama is required only for `/coding/plan`.
- No external paid APIs are called.
- The maintenance agent only suggests commands. It does not execute them.
- The coding agent returns planning guidance only. It does not edit files.
