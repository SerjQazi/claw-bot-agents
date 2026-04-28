"""FastAPI backend for the local multi-agent dashboard."""

import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent_core.config import settings
from agent_core.controller import AgentController


app = FastAPI(title=settings.app_name)
controller = AgentController()
BASE_DIR = Path(__file__).resolve().parent
GIT_HELPER = BASE_DIR / "scripts" / "git_helper.sh"
BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9/_\.-]+$")
DANGEROUS_MESSAGE_CHARS = re.compile(r"[;&|$`<>\"'\\\n\r]")


class CommandRequest(BaseModel):
    input: str = ""


class CommandApprovalRequest(BaseModel):
    action: str
    args: dict[str, Any] = {}


def run_command(args: list[str]) -> dict:
    try:
        result = subprocess.run(
            args,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "stdout": error.stdout or "",
            "stderr": (error.stderr or "") + "\nCommand timed out.",
            "exit_code": 124,
        }
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


def sanitize_commit_message(message: str) -> str:
    cleaned = DANGEROUS_MESSAGE_CHARS.sub("", message).strip()
    return cleaned[:120] or "Agent update"


def validate_branch_name(branch_name: str) -> str:
    branch = branch_name.strip()
    blocked_tokens = [";", "&&", "||", "$", "`"]
    if (
        not branch
        or any(token in branch for token in blocked_tokens)
        or any(char.isspace() for char in branch)
        or not BRANCH_NAME_RE.fullmatch(branch)
        or branch.startswith("-")
    ):
        raise ValueError("Invalid branch name. Use letters, numbers, slash, dash, underscore, or dot only.")
    return branch


def system_health_summary() -> dict:
    stats = controller.system_agent.stats()
    return {
        "agent": controller.system_agent.name,
        "response": "System health summary generated.",
        "health": {
            "hostname": stats.get("hostname"),
            "cpu_percent": stats.get("cpu_percent"),
            "memory_percent": stats.get("memory_percent"),
            "disk_percent": stats.get("disk_percent"),
            "uptime": stats.get("uptime"),
            "load_avg": stats.get("load_avg"),
            "current_time": stats.get("current_time"),
        },
    }


def unknown_command_response(command: str) -> dict:
    return {
        "agent": "command_center",
        "response": f"Unknown command: {command or '(empty)'}.",
        "examples": [
            "/system health",
            "/git status",
            "/git push update dashboard controls",
            "/git branch feature/control-panel",
            "/code plan improve dashboard",
        ],
    }


def route_slash_command(raw_input: str) -> dict:
    command = raw_input.strip()
    if not command:
        return unknown_command_response(command)

    if command == "/system health":
        return system_health_summary()

    if command == "/git status":
        result = run_command([str(GIT_HELPER), "status"])
        return {
            "agent": "command_center",
            "response": "Git status completed.",
            "command": "./scripts/git_helper.sh status",
            **result,
        }

    if command == "/git push" or command.startswith("/git push "):
        message = sanitize_commit_message(command.removeprefix("/git push"))
        return {
            "requires_approval": True,
            "action": "git_push",
            "args": {"message": message},
            "command_preview": f'./scripts/git_helper.sh push "{message}"',
            "risk": "Pushes committed/local changes to GitHub.",
        }

    if command.startswith("/git branch "):
        try:
            branch_name = validate_branch_name(command.removeprefix("/git branch "))
        except ValueError as error:
            return {
                "agent": "command_center",
                "response": str(error),
                "examples": ["/git branch feature/test", "/git branch fix/login-state"],
            }
        return {
            "requires_approval": True,
            "action": "git_branch",
            "args": {"branch_name": branch_name},
            "command_preview": f"./scripts/git_helper.sh branch {branch_name}",
            "risk": "Creates or switches git branch.",
        }

    if command.startswith("/code plan "):
        request = command.removeprefix("/code plan ").strip()
        if not request:
            return {
                "agent": "command_center",
                "response": "Provide a planning request after /code plan.",
                "examples": ["/code plan improve dashboard"],
            }
        return controller.local_coding_agent.handle(request)

    return unknown_command_response(command)


def approve_command(action: str, args: dict[str, Any]) -> dict:
    if action == "git_push":
        message = sanitize_commit_message(str(args.get("message", "")))
        result = run_command([str(GIT_HELPER), "push", message])
        return {
            "agent": "command_center",
            "action": action,
            "command": f'./scripts/git_helper.sh push "{message}"',
            **result,
        }

    if action == "git_branch":
        try:
            branch_name = validate_branch_name(str(args.get("branch_name", "")))
        except ValueError as error:
            return {
                "agent": "command_center",
                "action": action,
                "response": str(error),
                "exit_code": 2,
                "stdout": "",
                "stderr": str(error),
            }
        result = run_command([str(GIT_HELPER), "branch", branch_name])
        return {
            "agent": "command_center",
            "action": action,
            "command": f"./scripts/git_helper.sh branch {branch_name}",
            **result,
        }

    return {
        "agent": "command_center",
        "response": "Unsupported approval action.",
        "exit_code": 2,
        "stdout": "",
        "stderr": f"Unsupported action: {action}",
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>AgentOS Dashboard</title>
        <style>
          :root {
            color-scheme: dark;
            --bg: #050914;
            --panel: rgba(12, 21, 37, 0.74);
            --panel-strong: rgba(15, 27, 47, 0.9);
            --panel-soft: rgba(8, 16, 31, 0.62);
            --border: rgba(125, 211, 252, 0.2);
            --border-strong: rgba(125, 211, 252, 0.42);
            --text: #eef7ff;
            --muted: #a8b7cc;
            --soft: #6f8098;
            --cyan: #00d4ff;
            --blue: #6ecbff;
            --purple: #ff4fd8;
            --green: #37d67a;
            --track: #142238;
            --danger: #ff6370;
          }

          * {
            box-sizing: border-box;
          }

          body {
            margin: 0;
            min-height: 100vh;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background:
              radial-gradient(circle at 12% 8%, rgba(64, 224, 208, 0.17), transparent 31%),
              radial-gradient(circle at 88% 4%, rgba(106, 167, 255, 0.18), transparent 30%),
              linear-gradient(145deg, #050914 0%, #08111f 52%, #0d1728 100%);
            color: var(--text);
          }

          button,
          input {
            font: inherit;
          }

          .app-shell {
            display: grid;
            grid-template-columns: 220px minmax(0, 1fr);
            min-height: 100vh;
          }

          .sidebar {
            position: sticky;
            top: 0;
            height: 100vh;
            padding: 18px 12px;
            border-right: 1px solid rgba(110, 203, 255, 0.16);
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.018)),
              rgba(5, 9, 20, 0.76);
            box-shadow: 12px 0 36px rgba(0, 0, 0, 0.18);
            backdrop-filter: blur(18px);
          }

          .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px 18px;
            color: var(--text);
            font-size: 18px;
            font-weight: 600;
            letter-spacing: 0;
          }

          .brand-mark {
            display: inline-grid;
            place-items: center;
            width: 34px;
            height: 34px;
            border: 1px solid rgba(0, 212, 255, 0.34);
            border-radius: 10px;
            background: rgba(0, 212, 255, 0.12);
            box-shadow: 0 0 22px rgba(0, 212, 255, 0.14);
          }

          .side-nav {
            display: grid;
            gap: 5px;
          }

          .nav-item {
            display: flex;
            align-items: center;
            gap: 10px;
            min-height: 40px;
            padding: 9px 10px;
            border: 1px solid transparent;
            border-radius: 10px;
            color: var(--muted);
            text-decoration: none;
            font-size: 14px;
            font-weight: 450;
          }

          .nav-item svg {
            width: 17px;
            height: 17px;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.9;
            stroke-linecap: round;
            stroke-linejoin: round;
          }

          .nav-item.active,
          .nav-item:hover {
            border-color: rgba(0, 212, 255, 0.24);
            background: rgba(0, 212, 255, 0.09);
            color: var(--text);
          }

          .shell {
            width: min(1240px, 100%);
            margin: 0 auto;
            padding: 16px;
          }

          .main-panel {
            min-width: 0;
          }

          .topbar {
            position: sticky;
            top: 0;
            z-index: 8;
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 14px;
            align-items: center;
            margin-bottom: 14px;
            padding: 10px 12px;
            border: 1px solid rgba(110, 203, 255, 0.16);
            border-radius: 16px;
            background: rgba(5, 9, 20, 0.72);
            box-shadow: 0 10px 32px rgba(0, 0, 0, 0.18);
            backdrop-filter: blur(18px);
          }

          .top-command {
            width: min(620px, 100%);
            justify-self: center;
          }

          .input,
          .command-input {
            width: 100%;
            min-height: 42px;
            border: 1px solid rgba(110, 203, 255, 0.2);
            border-radius: 12px;
            padding: 0 13px;
            background: rgba(2, 6, 23, 0.48);
            color: var(--text);
            outline: none;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
          }

          .input:focus,
          .command-input:focus {
            border-color: rgba(0, 212, 255, 0.58);
            box-shadow: 0 0 0 3px rgba(0, 212, 255, 0.1);
          }

          .glass {
            position: relative;
            overflow: hidden;
            border: 1px solid var(--border);
            border-radius: 17px;
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0.025)),
              var(--panel);
            box-shadow: 0 14px 36px rgba(0, 0, 0, 0.26), inset 0 1px 0 rgba(255, 255, 255, 0.07);
            backdrop-filter: blur(18px);
            transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
          }

          .glass:hover {
            border-color: var(--border-strong);
            transform: translateY(-2px);
            box-shadow: 0 18px 46px rgba(0, 0, 0, 0.3), 0 0 24px rgba(64, 224, 208, 0.06);
          }

          .hero {
            display: flex;
            justify-content: space-between;
            gap: 16px;
            min-height: 118px;
            margin-bottom: 14px;
            padding: 18px 20px;
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.065), rgba(255, 255, 255, 0.018)),
              rgba(8, 17, 31, 0.78);
          }

          .hero::before {
            content: "";
            position: absolute;
            inset: 0;
            background:
              linear-gradient(90deg, rgba(125, 211, 252, 0.05) 1px, transparent 1px),
              linear-gradient(0deg, rgba(125, 211, 252, 0.04) 1px, transparent 1px),
              radial-gradient(circle at 78% 30%, rgba(64, 224, 208, 0.16), transparent 34%);
            background-size: 28px 28px, 28px 28px, 100% 100%;
            opacity: 0.55;
            pointer-events: none;
          }

          .hero::after {
            content: "";
            position: absolute;
            inset: 0;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 620 150'%3E%3Cdefs%3E%3ClinearGradient id='w' x1='0' x2='1' y1='0' y2='0'%3E%3Cstop offset='0' stop-color='%236aa7ff' stop-opacity='0'/%3E%3Cstop offset='.45' stop-color='%2340e0d0' stop-opacity='.62'/%3E%3Cstop offset='1' stop-color='%236aa7ff' stop-opacity='.12'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cg fill='none' stroke-linecap='round'%3E%3Cpath d='M210 108 C270 42 340 40 406 74 S516 116 612 44' stroke='url(%23w)' stroke-width='2.3'/%3E%3Cpath d='M178 82 C262 20 336 28 414 58 S520 96 618 26' stroke='%236aa7ff' stroke-width='1.55' opacity='.42'/%3E%3Cpath d='M240 132 C312 80 368 88 438 114 S548 130 620 86' stroke='%23a8eaff' stroke-width='1.25' opacity='.3'/%3E%3Cpath d='M286 28 C346 54 410 28 464 48 S552 78 620 36' stroke='%2340e0d0' stroke-width='1.1' opacity='.22'/%3E%3C/g%3E%3Cg fill='%237dd3fc'%3E%3Ccircle cx='402' cy='24' r='1.1' opacity='.38'/%3E%3Ccircle cx='432' cy='48' r='1.3' opacity='.5'/%3E%3Ccircle cx='468' cy='28' r='1' opacity='.36'/%3E%3Ccircle cx='500' cy='60' r='1.2' opacity='.48'/%3E%3Ccircle cx='538' cy='36' r='1.1' opacity='.4'/%3E%3Ccircle cx='584' cy='64' r='1.2' opacity='.46'/%3E%3Ccircle cx='606' cy='104' r='1' opacity='.34'/%3E%3Ccircle cx='456' cy='110' r='1' opacity='.3'/%3E%3Ccircle cx='526' cy='124' r='1.15' opacity='.38'/%3E%3Ccircle cx='588' cy='18' r='1' opacity='.34'/%3E%3C/g%3E%3C/svg%3E");
            background-position: right center;
            background-repeat: no-repeat;
            background-size: min(78%, 620px) 100%;
            opacity: 0.82;
            mask-image: linear-gradient(90deg, transparent 0%, black 28%, black 100%);
            pointer-events: none;
          }

          .hero > * {
            position: relative;
            z-index: 1;
          }

          h1 {
            margin: 0;
            font-size: clamp(36px, 9vw, 54px);
            line-height: 0.95;
            letter-spacing: 0;
            font-weight: 700;
          }

          .subtitle {
            margin: 10px 0 0;
            color: var(--muted);
            font-size: 17px;
          }

          .hostname {
            margin: 8px 0 0;
            color: var(--soft);
            font-size: 14px;
            overflow-wrap: anywhere;
          }


          .hero-actions {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            justify-content: flex-end;
          }

          .hero-actions .button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            text-decoration: none;
          }

          .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            align-self: flex-start;
            min-height: 34px;
            padding: 7px 12px;
            border: 1px solid rgba(55, 214, 122, 0.35);
            border-radius: 999px;
            background: rgba(21, 128, 61, 0.16);
            color: #d9ffe9;
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
          }

          .status-pill.offline {
            border-color: rgba(255, 99, 112, 0.4);
            background: rgba(127, 29, 29, 0.2);
            color: #ffd6da;
          }

          .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--green);
            box-shadow: 0 0 16px rgba(55, 214, 122, 0.75);
          }

          .status-pill.offline .dot {
            background: var(--danger);
            box-shadow: 0 0 16px rgba(255, 99, 112, 0.75);
          }

          .dashboard-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 14px;
          }

          .card {
            min-height: 236px;
            padding: 16px;
          }

          .command-card,
          .logs-panel {
            margin-bottom: 14px;
            padding: 16px;
          }

          .command-card {
            min-height: 0;
          }

          .command-form {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 10px;
            align-items: center;
          }

          .button {
            min-height: 42px;
            border: 1px solid rgba(0, 212, 255, 0.38);
            border-radius: 12px;
            padding: 0 16px;
            background:
              linear-gradient(135deg, rgba(0, 212, 255, 0.28), rgba(255, 79, 216, 0.18)),
              rgba(2, 6, 23, 0.52);
            color: var(--text);
            cursor: pointer;
            font-weight: 550;
            box-shadow: 0 0 20px rgba(0, 212, 255, 0.11);
          }

          .button:hover {
            border-color: rgba(255, 79, 216, 0.45);
          }

          .button.secondary {
            min-height: 34px;
            padding: 0 12px;
            border-color: rgba(110, 203, 255, 0.22);
            background: rgba(2, 6, 23, 0.34);
            color: var(--muted);
            font-size: 13px;
          }

          .command-output {
            display: none;
            margin-top: 12px;
            padding: 12px;
            border: 1px solid rgba(148, 163, 184, 0.14);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.36);
            color: var(--muted);
            font-size: 13px;
            line-height: 1.45;
            white-space: pre-wrap;
          }

          .command-output.visible {
            display: block;
          }

          .card-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 14px;
            color: var(--muted);
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
          }

          .icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border: 1px solid rgba(125, 211, 252, 0.24);
            border-radius: 8px;
            background: rgba(96, 165, 250, 0.11);
            color: #9bdcff;
            flex: 0 0 auto;
          }

          .icon svg {
            width: 16px;
            height: 16px;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.9;
            stroke-linecap: round;
            stroke-linejoin: round;
          }

          .gauge-wrap {
            display: grid;
            place-items: center;
            min-height: 174px;
            overflow: visible;
          }

          .gauge {
            width: 174px;
            height: 174px;
            overflow: visible;
          }

          .gauge-track,
          .gauge-progress {
            fill: none;
            stroke-width: 12;
            transform: rotate(-90deg);
            transform-origin: 80px 80px;
          }

          .gauge-track {
            stroke: var(--track);
          }

          .gauge-progress {
            stroke: var(--cyan);
            stroke-linecap: round;
            stroke-dasharray: 364.42;
            stroke-dashoffset: 364.42;
            filter: drop-shadow(0 0 8px rgba(64, 224, 208, 0.38));
            transition: stroke-dashoffset 520ms ease;
          }

          .ram-gauge .gauge-progress {
            stroke: url(#ramGradient);
            filter: drop-shadow(0 0 8px rgba(167, 139, 250, 0.38));
          }

          .disk-gauge .gauge-progress {
            stroke: url(#diskGradient);
          }

          .gauge-dot {
            fill: #e7fff9;
            filter: drop-shadow(0 0 7px rgba(64, 224, 208, 0.9));
            transform-origin: 80px 80px;
            transition: transform 520ms ease;
          }

          .gauge-content {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            width: 100%;
            height: 100%;
            color: var(--text);
            line-height: 1.05;
            text-align: center;
          }

          .gauge-main {
            font-size: 23px;
            font-weight: 600;
            white-space: nowrap;
          }

          .gauge-sub {
            margin-top: 3px;
            color: var(--muted);
            font-size: 14px;
            font-weight: 600;
            white-space: nowrap;
          }

          .gauge-label {
            margin-top: 2px;
            color: var(--soft);
            font-size: 12px;
          }

          .below-note {
            margin: 10px 0 0;
            color: var(--muted);
            font-size: 14px;
            text-align: center;
          }

          .stat-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 9px;
            margin-top: 14px;
          }

          .stat {
            border: 1px solid rgba(148, 163, 184, 0.13);
            border-radius: 10px;
            padding: 9px;
            background: rgba(2, 6, 23, 0.28);
          }

          .stat-label {
            display: block;
            color: var(--soft);
            font-size: 11px;
            letter-spacing: 0.05em;
            text-transform: uppercase;
          }

          .stat-value {
            display: block;
            margin-top: 4px;
            color: var(--text);
            font-size: 15px;
            font-weight: 600;
          }

          .disk-body {
            display: grid;
            grid-template-columns: 145px 1fr;
            gap: 18px;
            align-items: center;
          }

          .disk-body .gauge {
            width: 134px;
            height: 134px;
          }

          .disk-body .gauge-main {
            font-size: 21px;
          }

          .disk-stats {
            display: grid;
            gap: 9px;
          }

          .disk-row {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 12px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.12);
            padding-bottom: 7px;
          }

          .disk-row span:first-child {
            color: var(--muted);
            font-size: 13px;
          }

          .disk-row span:last-child {
            color: var(--text);
            font-size: 16px;
            font-weight: 600;
          }

          .bar {
            height: 8px;
            margin-top: 16px;
            overflow: hidden;
            border: 1px solid rgba(148, 163, 184, 0.13);
            border-radius: 999px;
            background: rgba(3, 7, 18, 0.72);
          }

          .fill {
            width: 0%;
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, var(--green), var(--cyan));
            box-shadow: 0 0 16px rgba(64, 224, 208, 0.45);
            transition: width 520ms ease;
          }

          .uptime-main,
          .time-main,
          .date-main {
            margin: 0;
            color: var(--text);
            font-size: clamp(30px, 7vw, 42px);
            line-height: 1.05;
            font-weight: 600;
            overflow-wrap: anywhere;
          }

          .time-card,
          .date-card {
            min-height: 150px;
          }

          .time-card,
          .date-card {
            display: flex;
            flex-direction: column;
          }

          .time-card .card-header,
          .date-card .card-header {
            margin-bottom: 12px;
          }

          .time-date-body {
            display: flex;
            align-items: center;
            justify-content: flex-start;
            gap: 18px;
            flex: 1;
            width: 100%;
          }

          .time-date-text {
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-width: 0;
          }

          .time-support,
          .date-support {
            display: grid;
            gap: 5px;
            margin-top: 10px;
            color: var(--muted);
            font-size: 13px;
          }

          .time-support span,
          .date-support span {
            color: var(--soft);
          }

          .clock-accent {
            position: relative;
            width: 78px;
            height: 78px;
            border: 1px solid rgba(125, 211, 252, 0.18);
            border-radius: 50%;
            background:
              radial-gradient(circle at center, rgba(64, 224, 208, 0.12), transparent 54%),
              rgba(2, 6, 23, 0.22);
            box-shadow: inset 0 0 22px rgba(64, 224, 208, 0.08);
            opacity: 0.9;
          }

          .clock-accent::before,
          .clock-accent::after {
            content: "";
            position: absolute;
            left: 50%;
            top: 50%;
            width: 2px;
            border-radius: 99px;
            background: rgba(157, 220, 255, 0.72);
            transform-origin: bottom center;
          }

          .clock-accent::before {
            height: 23px;
            transform: translate(-50%, -100%) rotate(35deg);
          }

          .clock-accent::after {
            height: 17px;
            transform: translate(-50%, -100%) rotate(118deg);
          }

          .calendar-accent {
            width: 78px;
            border: 1px solid rgba(125, 211, 252, 0.18);
            border-radius: 12px;
            overflow: hidden;
            background: rgba(2, 6, 23, 0.26);
            box-shadow: inset 0 0 22px rgba(64, 224, 208, 0.07);
            opacity: 0.92;
          }

          .calendar-accent .cal-top {
            height: 20px;
            background: linear-gradient(90deg, rgba(64, 224, 208, 0.26), rgba(106, 167, 255, 0.22));
          }

          .calendar-accent .cal-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 5px;
            padding: 10px;
          }

          .calendar-accent .cal-grid span {
            width: 10px;
            height: 10px;
            border-radius: 3px;
            background: rgba(125, 211, 252, 0.18);
          }

          .date-main {
            font-size: clamp(25px, 6vw, 34px);
          }

          .meta-list {
            display: grid;
            gap: 10px;
            margin-top: 18px;
          }

          .meta-line {
            display: flex;
            align-items: center;
            gap: 9px;
            color: var(--muted);
            font-size: 14px;
          }

          .mini-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            border-radius: 7px;
            background: rgba(96, 165, 250, 0.1);
            color: #9bdcff;
          }

          .mini-icon svg {
            width: 14px;
            height: 14px;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.9;
            stroke-linecap: round;
            stroke-linejoin: round;
          }

          .agents-panel {
            margin-top: 14px;
            padding: 18px;
          }

          .agents-title {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 0 0 14px;
            color: var(--text);
            font-size: 17px;
            font-weight: 600;
            letter-spacing: 0;
          }

          .agent-list {
            display: grid;
            grid-template-columns: 1fr;
            gap: 10px;
            margin: 0;
            padding: 0;
            list-style: none;
          }

          .agent-list li {
            display: grid;
            grid-template-columns: auto 1fr auto;
            gap: 10px;
            align-items: center;
            min-height: 76px;
            padding: 12px;
            border: 1px solid rgba(148, 163, 184, 0.15);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.3);
          }

          .agent-meta {
            min-width: 0;
          }

          .agent-name {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text);
            font-size: 15px;
            font-weight: 550;
          }

          .agent-description {
            display: block;
            margin-top: 4px;
            color: var(--muted);
            font-size: 13px;
            line-height: 1.35;
          }

          .agent-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--green);
            box-shadow: 0 0 14px rgba(55, 214, 122, 0.7);
            flex: 0 0 auto;
          }

          .logs-panel {
            margin-top: 14px;
          }

          .log-stream {
            display: grid;
            gap: 8px;
            max-height: 220px;
            overflow: auto;
            padding: 12px;
            border: 1px solid rgba(148, 163, 184, 0.13);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.34);
            color: var(--muted);
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 12px;
            line-height: 1.45;
          }

          .log-line {
            display: grid;
            grid-template-columns: 84px 124px 1fr;
            gap: 10px;
            align-items: baseline;
          }

          .log-time {
            color: var(--soft);
          }

          .log-source {
            color: var(--blue);
          }

          .log-message {
            color: var(--muted);
          }

          footer {
            display: flex;
            flex-direction: column;
            gap: 8px;
            padding: 14px 2px 2px;
            color: var(--muted);
            font-size: 13px;
          }

          .connection {
            color: var(--soft);
          }

          @media (min-width: 760px) {
            .shell {
              padding: 22px;
            }

            .dashboard-grid {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            .agent-list {
              grid-template-columns: repeat(3, minmax(0, 1fr));
            }

            footer {
              flex-direction: row;
              justify-content: space-between;
              align-items: center;
            }
          }

          @media (max-width: 860px) {
            .app-shell {
              grid-template-columns: 1fr;
            }

            .sidebar {
              position: sticky;
              top: 0;
              z-index: 9;
              display: flex;
              align-items: center;
              gap: 10px;
              height: auto;
              padding: 10px;
              overflow-x: auto;
              border-right: 0;
              border-bottom: 1px solid rgba(110, 203, 255, 0.16);
            }

            .brand {
              padding: 0 8px 0 0;
              white-space: nowrap;
            }

            .side-nav {
              display: flex;
              gap: 6px;
            }

            .nav-item {
              white-space: nowrap;
            }

            .topbar {
              position: static;
              grid-template-columns: 1fr;
            }

            .top-command {
              justify-self: stretch;
            }
          }

          @media (max-width: 520px) {
            .hero {
              min-height: 118px;
              padding: 16px;
            }

            .topbar .status-pill {
              position: static;
              justify-self: start;
            }

            .disk-body {
              grid-template-columns: 1fr;
              justify-items: center;
              gap: 12px;
            }

            .disk-stats {
              width: 100%;
            }

            .command-form {
              grid-template-columns: 1fr;
            }

            .agent-list li {
              grid-template-columns: auto 1fr;
            }

            .agent-list .button {
              grid-column: 1 / -1;
              width: 100%;
            }

            .log-line {
              grid-template-columns: 1fr;
              gap: 2px;
            }
          }

          @media (hover: none) {
            .glass:hover {
              transform: none;
            }
          }
        </style>
      </head>
      <body>
        <main class="shell">
          <header class="hero glass">
            <div>
              <h1>AgentOS</h1>
              <p class="subtitle">Premium system monitor</p>
              <p class="hostname">Host: <span id="hero-hostname">--</span></p>
            </div>
            <div class="hero-actions">
              <a class="button" href="/control">Open Control Panel</a>
              <div class="status-pill"><span class="dot"></span><span id="server-status">Online</span></div>
            </div>
          </header>

          <section class="dashboard-grid" aria-label="Server dashboard">
            <article class="card glass">
              <div class="card-header">
                <span class="icon"><svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"></rect><path d="M4 9h3M4 15h3M17 9h3M17 15h3M9 4v3M15 4v3M9 17v3M15 17v3"></path></svg></span>
                CPU Usage
              </div>
              <div class="gauge-wrap">
                <svg class="gauge" viewBox="0 0 160 160" aria-hidden="true">
                  <defs>
                    <linearGradient id="cpuGradient" x1="0" x2="1" y1="0" y2="1">
                      <stop offset="0%" stop-color="#37d67a"></stop>
                      <stop offset="100%" stop-color="#00d4ff"></stop>
                    </linearGradient>
                  </defs>
                  <circle class="gauge-track" cx="80" cy="80" r="58"></circle>
                  <circle class="gauge-progress" id="cpu-gauge" cx="80" cy="80" r="58" stroke="url(#cpuGradient)"></circle>
                  <circle class="gauge-dot" id="cpu-dot" cx="80" cy="22" r="4"></circle>
                  <foreignObject x="36" y="50" width="88" height="62">
                    <div class="gauge-content" xmlns="http://www.w3.org/1999/xhtml">
                      <div class="gauge-main"><span id="cpu-value">--</span>%</div>
                      <div class="gauge-sub"><span id="cpu-active-cores-value">--</span> / <span id="cpu-total-cores-value">--</span></div>
                      <div class="gauge-label">cores active</div>
                    </div>
                  </foreignObject>
                </svg>
              </div>
              <p class="below-note"><span id="cpu-total-note">--</span> Total Cores</p>
            </article>

            <article class="card glass">
              <div class="card-header">
                <span class="icon"><svg viewBox="0 0 24 24"><rect x="5" y="7" width="14" height="10" rx="2"></rect><path d="M8 3v4M12 3v4M16 3v4M8 17v4M12 17v4M16 17v4"></path></svg></span>
                RAM Usage
              </div>
              <div class="gauge-wrap">
                <svg class="gauge ram-gauge" viewBox="0 0 160 160" aria-hidden="true">
                  <defs>
                    <linearGradient id="ramGradient" x1="0" x2="1" y1="0" y2="1">
                      <stop offset="0%" stop-color="#6ecbff"></stop>
                      <stop offset="100%" stop-color="#ff4fd8"></stop>
                    </linearGradient>
                  </defs>
                  <circle class="gauge-track" cx="80" cy="80" r="58"></circle>
                  <circle class="gauge-progress" id="memory-gauge" cx="80" cy="80" r="58"></circle>
                  <circle class="gauge-dot" id="memory-dot" cx="80" cy="22" r="4"></circle>
                  <foreignObject x="36" y="50" width="88" height="62">
                    <div class="gauge-content" xmlns="http://www.w3.org/1999/xhtml">
                      <div class="gauge-main"><span id="memory-value">--</span>%</div>
                      <div class="gauge-sub"><span id="memory-used-center">--</span> GB</div>
                      <div class="gauge-label">used</div>
                    </div>
                  </foreignObject>
                </svg>
              </div>
              <div class="stat-grid">
                <div class="stat"><span class="stat-label">Used</span><span class="stat-value"><span id="memory-used-value">--</span> GB</span></div>
                <div class="stat"><span class="stat-label">Free</span><span class="stat-value"><span id="memory-free-value">--</span> GB</span></div>
                <div class="stat"><span class="stat-label">Total</span><span class="stat-value"><span id="memory-total-value">--</span> GB</span></div>
              </div>
            </article>

            <article class="card glass">
              <div class="card-header">
                <span class="icon"><svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7" ry="3"></ellipse><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"></path><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"></path></svg></span>
                Disk Usage
              </div>
              <div class="disk-body">
                <svg class="gauge disk-gauge" viewBox="0 0 160 160" aria-hidden="true">
                  <defs>
                    <linearGradient id="diskGradient" x1="0" x2="1" y1="0" y2="1">
                      <stop offset="0%" stop-color="#37d67a"></stop>
                      <stop offset="100%" stop-color="#00d4ff"></stop>
                    </linearGradient>
                  </defs>
                  <circle class="gauge-track" cx="80" cy="80" r="58"></circle>
                  <circle class="gauge-progress" id="disk-gauge" cx="80" cy="80" r="58"></circle>
                  <circle class="gauge-dot" id="disk-dot" cx="80" cy="22" r="4"></circle>
                  <foreignObject x="36" y="58" width="88" height="44">
                    <div class="gauge-content" xmlns="http://www.w3.org/1999/xhtml">
                      <div class="gauge-main"><span id="disk-value">--</span>%</div>
                    </div>
                  </foreignObject>
                </svg>
                <div class="disk-stats">
                  <div class="disk-row"><span>Used</span><span><span id="disk-used-value">--</span> GB</span></div>
                  <div class="disk-row"><span>Free</span><span><span id="disk-free-value">--</span> GB</span></div>
                  <div class="disk-row"><span>Total</span><span><span id="disk-total-value">--</span> GB</span></div>
                </div>
              </div>
              <div class="bar"><div class="fill" id="disk-bar"></div></div>
            </article>

            <article class="card glass">
              <div class="card-header">
                <span class="icon"><svg viewBox="0 0 24 24"><path d="M12 7v5l3 2"></path><circle cx="12" cy="12" r="8"></circle><path d="M12 2v3"></path></svg></span>
                Uptime
              </div>
              <p class="uptime-main" id="uptime-value">--</p>
              <div class="meta-list">
                <div class="meta-line"><span class="mini-icon"><svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 0 1 8-8v4l5-5-5-5v4A10 10 0 1 0 22 12"></path></svg></span>Booted: <span id="boot-time-value">--</span></div>
                <div class="meta-line"><span class="mini-icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"></circle><path d="M12 8v4l3 2"></path></svg></span>Updated: <span id="uptime-updated-value">--</span></div>
                <div class="meta-line"><span class="mini-icon"><svg viewBox="0 0 24 24"><path d="M4 16l4-4 3 3 5-7 4 5"></path></svg></span>Load: <span id="load-average-value">--</span></div>
              </div>
            </article>

            <article class="card glass time-card">
              <div class="card-header">
                <span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"></circle><path d="M12 8v4l3 2"></path></svg></span>
                Current Time
              </div>
              <div class="time-date-body">
                <div class="clock-accent" aria-hidden="true"></div>
                <div class="time-date-text">
                  <p class="time-main" id="time-value">--</p>
                  <div class="time-support">
                    <div>Server time</div>
                    <div>Seconds: <span id="time-seconds-value">--</span></div>
                  </div>
                </div>
              </div>
            </article>

            <article class="card glass date-card">
              <div class="card-header">
                <span class="icon"><svg viewBox="0 0 24 24"><rect x="5" y="6" width="14" height="13" rx="2"></rect><path d="M8 3v4M16 3v4M5 10h14"></path></svg></span>
                Current Date
              </div>
              <div class="time-date-body">
                <div class="calendar-accent" aria-hidden="true">
                  <div class="cal-top"></div>
                  <div class="cal-grid">
                    <span></span><span></span><span></span>
                    <span></span><span></span><span></span>
                  </div>
                </div>
                <div class="time-date-text">
                  <p class="date-main" id="date-value">--</p>
                  <div class="date-support">
                    <div id="weekday-value">--</div>
                    <div><span id="month-year-value">--</span> · Day <span id="day-year-value">--</span></div>
                  </div>
                </div>
              </div>
            </article>
          </section>

          <section class="agents-panel glass" id="agents" aria-label="Available agents">
            <h2 class="agents-title"><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7l7-4z"></path><path d="M9 12h6M12 9v6"></path></svg></span>Available Agents</h2>
            <ul class="agent-list" id="agents-list">
              <li><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7l7-4z"></path></svg></span><span class="agent-meta"><span class="agent-name"><span class="agent-status-dot"></span>Loading</span><span class="agent-description">Fetching local agent registry</span></span></li>
            </ul>
          </section>

          <footer>
            <span id="last-updated">Last updated: never</span>
            <span class="connection">Connection: <span id="connection-status">checking</span></span>
          </footer>

        </main>

        <script>
          const CIRCUMFERENCE = 364.42;

          const els = {
            serverStatus: document.getElementById("server-status"),
            statusPill: document.querySelector(".status-pill"),
            heroHostname: document.getElementById("hero-hostname"),
            cpuValue: document.getElementById("cpu-value"),
            cpuActiveCoresValue: document.getElementById("cpu-active-cores-value"),
            cpuTotalCoresValue: document.getElementById("cpu-total-cores-value"),
            cpuTotalNote: document.getElementById("cpu-total-note"),
            cpuGauge: document.getElementById("cpu-gauge"),
            cpuDot: document.getElementById("cpu-dot"),
            memoryValue: document.getElementById("memory-value"),
            memoryUsedCenter: document.getElementById("memory-used-center"),
            memoryUsedValue: document.getElementById("memory-used-value"),
            memoryFreeValue: document.getElementById("memory-free-value"),
            memoryTotalValue: document.getElementById("memory-total-value"),
            memoryGauge: document.getElementById("memory-gauge"),
            memoryDot: document.getElementById("memory-dot"),
            diskValue: document.getElementById("disk-value"),
            diskUsedValue: document.getElementById("disk-used-value"),
            diskTotalValue: document.getElementById("disk-total-value"),
            diskFreeValue: document.getElementById("disk-free-value"),
            diskGauge: document.getElementById("disk-gauge"),
            diskDot: document.getElementById("disk-dot"),
            diskBar: document.getElementById("disk-bar"),
            uptimeValue: document.getElementById("uptime-value"),
            bootTimeValue: document.getElementById("boot-time-value"),
            uptimeUpdatedValue: document.getElementById("uptime-updated-value"),
            loadAverageValue: document.getElementById("load-average-value"),
            timeValue: document.getElementById("time-value"),
            timeSecondsValue: document.getElementById("time-seconds-value"),
            dateValue: document.getElementById("date-value"),
            weekdayValue: document.getElementById("weekday-value"),
            monthYearValue: document.getElementById("month-year-value"),
            dayYearValue: document.getElementById("day-year-value"),
            agentsList: document.getElementById("agents-list"),
            lastUpdated: document.getElementById("last-updated"),
            connectionStatus: document.getElementById("connection-status"),
          };

          function formatPercent(value) {
            const number = Number(value);
            return Number.isFinite(number) ? number.toFixed(1) : "--";
          }

          function formatGb(value, decimals = 0) {
            const number = Number(value);
            return Number.isFinite(number) ? number.toFixed(decimals) : "--";
          }

          function setGauge(gauge, dot, value) {
            const number = Math.max(0, Math.min(100, Number(value) || 0));
            gauge.style.strokeDashoffset = CIRCUMFERENCE - (number / 100) * CIRCUMFERENCE;
            dot.style.transform = "rotate(" + (number * 3.6) + "deg)";
          }

          function setBar(element, value) {
            const number = Math.max(0, Math.min(100, Number(value) || 0));
            element.style.width = number + "%";
          }

          function formatBootTime(value) {
            if (!value) {
              return "--";
            }
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) {
              return "--";
            }
            return date.toLocaleString([], {
              month: "short",
              day: "numeric",
              year: "numeric",
              hour: "numeric",
              minute: "2-digit",
            });
          }

          function formatLoad(loadAvg) {
            if (!loadAvg) {
              return "--";
            }
            const one = Number(loadAvg["1m"]);
            const five = Number(loadAvg["5m"]);
            const fifteen = Number(loadAvg["15m"]);
            if (![one, five, fifteen].every(Number.isFinite)) {
              return "--";
            }
            return one.toFixed(2) + " / " + five.toFixed(2) + " / " + fifteen.toFixed(2);
          }

          function dayOfYear(date) {
            const start = new Date(date.getFullYear(), 0, 0);
            const diff = date - start + (start.getTimezoneOffset() - date.getTimezoneOffset()) * 60000;
            return Math.floor(diff / 86400000);
          }

          function setAgents(agents) {
            els.agentsList.innerHTML = "";
            if (!Array.isArray(agents) || agents.length === 0) {
              els.agentsList.innerHTML = agentCardHtml("unknown_agent", "No agents returned by the local registry.");
              return;
            }
            for (const agent of agents) {
              els.agentsList.insertAdjacentHTML(
                "beforeend",
                agentCardHtml(agent.name || "unknown_agent", agent.description || "Local agent available")
              );
            }
          }

          function agentCardHtml(name, description) {
            return '<li><span class="icon">' + agentIcon(name) + '</span><span class="agent-meta"><span class="agent-name"><span class="agent-status-dot"></span>' + escapeHtml(name) + '</span><span class="agent-description">' + escapeHtml(description) + '</span></span></li>';
          }

          function escapeHtml(value) {
            return String(value)
              .replaceAll("&", "&amp;")
              .replaceAll("<", "&lt;")
              .replaceAll(">", "&gt;")
              .replaceAll('"', "&quot;")
              .replaceAll("'", "&#039;");
          }

          function agentIcon(name) {
            if (name === "system_agent") {
              return '<svg viewBox="0 0 24 24"><rect x="5" y="6" width="14" height="10" rx="2"></rect><path d="M8 20h8M12 16v4"></path></svg>';
            }
            if (name === "maintenance_agent") {
              return '<svg viewBox="0 0 24 24"><path d="M14 6l4 4-8 8H6v-4l8-8z"></path><path d="M16 4l4 4"></path></svg>';
            }
            if (name === "coding_agent") {
              return '<svg viewBox="0 0 24 24"><path d="M8 9l-4 3 4 3M16 9l4 3-4 3M14 5l-4 14"></path></svg>';
            }
            return '<svg viewBox="0 0 24 24"><path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7l7-4z"></path></svg>';
          }

          async function refreshDashboard() {
            try {
              const [systemResponse, agentsResponse] = await Promise.all([
                fetch("/system", { cache: "no-store" }),
                fetch("/agents", { cache: "no-store" }),
              ]);

              if (!systemResponse.ok || !agentsResponse.ok) {
                throw new Error("Dashboard request failed");
              }

              const system = await systemResponse.json();
              const agents = await agentsResponse.json();
              const now = new Date();

              els.serverStatus.textContent = "Online";
              els.statusPill.classList.remove("offline");
              els.connectionStatus.textContent = "online";
              els.heroHostname.textContent = system.hostname || "--";

              els.cpuValue.textContent = formatPercent(system.cpu_percent);
              els.cpuActiveCoresValue.textContent = system.cpu_cores_active ?? "--";
              els.cpuTotalCoresValue.textContent = system.cpu_cores_total ?? "--";
              els.cpuTotalNote.textContent = system.cpu_cores_total ?? "--";
              setGauge(els.cpuGauge, els.cpuDot, system.cpu_percent);

              els.memoryValue.textContent = formatPercent(system.memory_percent);
              els.memoryUsedCenter.textContent = formatGb(system.memory_used_gb, 1);
              els.memoryUsedValue.textContent = formatGb(system.memory_used_gb, 1);
              els.memoryFreeValue.textContent = formatGb(system.memory_free_gb, 1);
              els.memoryTotalValue.textContent = formatGb(system.memory_total_gb, 1);
              setGauge(els.memoryGauge, els.memoryDot, system.memory_percent);

              els.diskValue.textContent = formatPercent(system.disk_percent);
              els.diskUsedValue.textContent = formatGb(system.disk_used_gb);
              els.diskTotalValue.textContent = formatGb(system.disk_total_gb);
              els.diskFreeValue.textContent = formatGb(system.disk_free_gb);
              setGauge(els.diskGauge, els.diskDot, system.disk_percent);
              setBar(els.diskBar, system.disk_percent);

              els.uptimeValue.textContent = system.uptime || "--";
              els.bootTimeValue.textContent = formatBootTime(system.boot_time);
              els.uptimeUpdatedValue.textContent = now.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              });
              els.loadAverageValue.textContent = formatLoad(system.load_avg);

              if (system.current_time) {
                const serverDate = new Date(system.current_time);
                els.timeValue.textContent = serverDate.toLocaleTimeString([], {
                  hour: "numeric",
                  minute: "2-digit",
                });
                els.timeSecondsValue.textContent = serverDate.toLocaleTimeString([], {
                  second: "2-digit",
                });
                els.dateValue.textContent = serverDate.toLocaleDateString([], {
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                });
                els.weekdayValue.textContent = serverDate.toLocaleDateString([], {
                  weekday: "long",
                });
                els.monthYearValue.textContent = serverDate.toLocaleDateString([], {
                  month: "long",
                  year: "numeric",
                });
                els.dayYearValue.textContent = dayOfYear(serverDate);
              } else {
                els.timeValue.textContent = "--";
                els.timeSecondsValue.textContent = "--";
                els.dateValue.textContent = "--";
                els.weekdayValue.textContent = "--";
                els.monthYearValue.textContent = "--";
                els.dayYearValue.textContent = "--";
              }

              setAgents(agents.agents);
              els.lastUpdated.textContent = "Last updated: " + now.toLocaleTimeString();
            } catch (error) {
              els.serverStatus.textContent = "Offline";
              els.statusPill.classList.add("offline");
              els.connectionStatus.textContent = "offline";
              els.lastUpdated.textContent = "Last updated: failed at " + new Date().toLocaleTimeString();
            }
          }

          refreshDashboard();
          setInterval(refreshDashboard, 5000);
        </script>

      </body>
    </html>
    """


@app.get("/control", response_class=HTMLResponse)
def control_panel() -> str:
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>AgentOS Control Panel</title>
        <style>
          :root {
            color-scheme: dark;
            --bg: #050914;
            --panel: rgba(12, 21, 37, 0.74);
            --panel-strong: rgba(15, 27, 47, 0.9);
            --panel-soft: rgba(8, 16, 31, 0.62);
            --border: rgba(125, 211, 252, 0.2);
            --border-strong: rgba(125, 211, 252, 0.42);
            --text: #eef7ff;
            --muted: #a8b7cc;
            --soft: #6f8098;
            --cyan: #00d4ff;
            --blue: #6ecbff;
            --purple: #ff4fd8;
            --green: #37d67a;
            --track: #142238;
            --danger: #ff6370;
          }

          * {
            box-sizing: border-box;
          }

          body {
            margin: 0;
            min-height: 100vh;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background:
              radial-gradient(circle at 12% 8%, rgba(64, 224, 208, 0.17), transparent 31%),
              radial-gradient(circle at 88% 4%, rgba(106, 167, 255, 0.18), transparent 30%),
              linear-gradient(145deg, #050914 0%, #08111f 52%, #0d1728 100%);
            color: var(--text);
          }

          button,
          input {
            font: inherit;
          }

          .app-shell {
            display: grid;
            grid-template-columns: 220px minmax(0, 1fr);
            min-height: 100vh;
          }

          .sidebar {
            position: sticky;
            top: 0;
            height: 100vh;
            padding: 18px 12px;
            border-right: 1px solid rgba(110, 203, 255, 0.16);
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.018)),
              rgba(5, 9, 20, 0.76);
            box-shadow: 12px 0 36px rgba(0, 0, 0, 0.18);
            backdrop-filter: blur(18px);
          }

          .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px 18px;
            color: var(--text);
            font-size: 18px;
            font-weight: 600;
            letter-spacing: 0;
          }

          .brand-mark {
            display: inline-grid;
            place-items: center;
            width: 34px;
            height: 34px;
            border: 1px solid rgba(0, 212, 255, 0.34);
            border-radius: 10px;
            background: rgba(0, 212, 255, 0.12);
            box-shadow: 0 0 22px rgba(0, 212, 255, 0.14);
          }

          .side-nav {
            display: grid;
            gap: 5px;
          }

          .nav-item {
            display: flex;
            align-items: center;
            gap: 10px;
            min-height: 40px;
            padding: 9px 10px;
            border: 1px solid transparent;
            border-radius: 10px;
            color: var(--muted);
            text-decoration: none;
            font-size: 14px;
            font-weight: 450;
          }

          .nav-item svg {
            width: 17px;
            height: 17px;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.9;
            stroke-linecap: round;
            stroke-linejoin: round;
          }

          .nav-item.active,
          .nav-item:hover {
            border-color: rgba(0, 212, 255, 0.24);
            background: rgba(0, 212, 255, 0.09);
            color: var(--text);
          }

          .shell {
            width: min(1240px, 100%);
            margin: 0 auto;
            padding: 16px;
          }

          .main-panel {
            min-width: 0;
          }

          .topbar {
            position: sticky;
            top: 0;
            z-index: 8;
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 14px;
            align-items: center;
            margin-bottom: 14px;
            padding: 10px 12px;
            border: 1px solid rgba(110, 203, 255, 0.16);
            border-radius: 16px;
            background: rgba(5, 9, 20, 0.72);
            box-shadow: 0 10px 32px rgba(0, 0, 0, 0.18);
            backdrop-filter: blur(18px);
          }

          .top-command {
            width: min(620px, 100%);
            justify-self: center;
          }

          .input,
          .command-input {
            width: 100%;
            min-height: 42px;
            border: 1px solid rgba(110, 203, 255, 0.2);
            border-radius: 12px;
            padding: 0 13px;
            background: rgba(2, 6, 23, 0.48);
            color: var(--text);
            outline: none;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
          }

          .input:focus,
          .command-input:focus {
            border-color: rgba(0, 212, 255, 0.58);
            box-shadow: 0 0 0 3px rgba(0, 212, 255, 0.1);
          }

          .glass {
            position: relative;
            overflow: hidden;
            border: 1px solid var(--border);
            border-radius: 17px;
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0.025)),
              var(--panel);
            box-shadow: 0 14px 36px rgba(0, 0, 0, 0.26), inset 0 1px 0 rgba(255, 255, 255, 0.07);
            backdrop-filter: blur(18px);
            transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
          }

          .glass:hover {
            border-color: var(--border-strong);
            transform: translateY(-2px);
            box-shadow: 0 18px 46px rgba(0, 0, 0, 0.3), 0 0 24px rgba(64, 224, 208, 0.06);
          }

          .hero {
            display: flex;
            justify-content: space-between;
            gap: 16px;
            min-height: 118px;
            margin-bottom: 14px;
            padding: 18px 20px;
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.065), rgba(255, 255, 255, 0.018)),
              rgba(8, 17, 31, 0.78);
          }

          .hero::before {
            content: "";
            position: absolute;
            inset: 0;
            background:
              linear-gradient(90deg, rgba(125, 211, 252, 0.05) 1px, transparent 1px),
              linear-gradient(0deg, rgba(125, 211, 252, 0.04) 1px, transparent 1px),
              radial-gradient(circle at 78% 30%, rgba(64, 224, 208, 0.16), transparent 34%);
            background-size: 28px 28px, 28px 28px, 100% 100%;
            opacity: 0.55;
            pointer-events: none;
          }

          .hero::after {
            content: "";
            position: absolute;
            inset: 0;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 620 150'%3E%3Cdefs%3E%3ClinearGradient id='w' x1='0' x2='1' y1='0' y2='0'%3E%3Cstop offset='0' stop-color='%236aa7ff' stop-opacity='0'/%3E%3Cstop offset='.45' stop-color='%2340e0d0' stop-opacity='.62'/%3E%3Cstop offset='1' stop-color='%236aa7ff' stop-opacity='.12'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cg fill='none' stroke-linecap='round'%3E%3Cpath d='M210 108 C270 42 340 40 406 74 S516 116 612 44' stroke='url(%23w)' stroke-width='2.3'/%3E%3Cpath d='M178 82 C262 20 336 28 414 58 S520 96 618 26' stroke='%236aa7ff' stroke-width='1.55' opacity='.42'/%3E%3Cpath d='M240 132 C312 80 368 88 438 114 S548 130 620 86' stroke='%23a8eaff' stroke-width='1.25' opacity='.3'/%3E%3Cpath d='M286 28 C346 54 410 28 464 48 S552 78 620 36' stroke='%2340e0d0' stroke-width='1.1' opacity='.22'/%3E%3C/g%3E%3Cg fill='%237dd3fc'%3E%3Ccircle cx='402' cy='24' r='1.1' opacity='.38'/%3E%3Ccircle cx='432' cy='48' r='1.3' opacity='.5'/%3E%3Ccircle cx='468' cy='28' r='1' opacity='.36'/%3E%3Ccircle cx='500' cy='60' r='1.2' opacity='.48'/%3E%3Ccircle cx='538' cy='36' r='1.1' opacity='.4'/%3E%3Ccircle cx='584' cy='64' r='1.2' opacity='.46'/%3E%3Ccircle cx='606' cy='104' r='1' opacity='.34'/%3E%3Ccircle cx='456' cy='110' r='1' opacity='.3'/%3E%3Ccircle cx='526' cy='124' r='1.15' opacity='.38'/%3E%3Ccircle cx='588' cy='18' r='1' opacity='.34'/%3E%3C/g%3E%3C/svg%3E");
            background-position: right center;
            background-repeat: no-repeat;
            background-size: min(78%, 620px) 100%;
            opacity: 0.82;
            mask-image: linear-gradient(90deg, transparent 0%, black 28%, black 100%);
            pointer-events: none;
          }

          .hero > * {
            position: relative;
            z-index: 1;
          }

          h1 {
            margin: 0;
            font-size: clamp(36px, 9vw, 54px);
            line-height: 0.95;
            letter-spacing: 0;
            font-weight: 700;
          }

          .subtitle {
            margin: 10px 0 0;
            color: var(--muted);
            font-size: 17px;
          }

          .hostname {
            margin: 8px 0 0;
            color: var(--soft);
            font-size: 14px;
            overflow-wrap: anywhere;
          }


          .hero-actions {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            justify-content: flex-end;
          }

          .hero-actions .button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            text-decoration: none;
          }

          .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            align-self: flex-start;
            min-height: 34px;
            padding: 7px 12px;
            border: 1px solid rgba(55, 214, 122, 0.35);
            border-radius: 999px;
            background: rgba(21, 128, 61, 0.16);
            color: #d9ffe9;
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
          }

          .status-pill.offline {
            border-color: rgba(255, 99, 112, 0.4);
            background: rgba(127, 29, 29, 0.2);
            color: #ffd6da;
          }

          .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--green);
            box-shadow: 0 0 16px rgba(55, 214, 122, 0.75);
          }

          .status-pill.offline .dot {
            background: var(--danger);
            box-shadow: 0 0 16px rgba(255, 99, 112, 0.75);
          }

          .dashboard-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 14px;
          }

          .card {
            min-height: 236px;
            padding: 16px;
          }

          .command-card,
          .logs-panel {
            margin-bottom: 14px;
            padding: 16px;
          }

          .command-card {
            min-height: 0;
          }

          .command-form {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 10px;
            align-items: center;
          }

          .button {
            min-height: 42px;
            border: 1px solid rgba(0, 212, 255, 0.38);
            border-radius: 12px;
            padding: 0 16px;
            background:
              linear-gradient(135deg, rgba(0, 212, 255, 0.28), rgba(255, 79, 216, 0.18)),
              rgba(2, 6, 23, 0.52);
            color: var(--text);
            cursor: pointer;
            font-weight: 550;
            box-shadow: 0 0 20px rgba(0, 212, 255, 0.11);
          }

          .button:hover {
            border-color: rgba(255, 79, 216, 0.45);
          }

          .button.secondary {
            min-height: 34px;
            padding: 0 12px;
            border-color: rgba(110, 203, 255, 0.22);
            background: rgba(2, 6, 23, 0.34);
            color: var(--muted);
            font-size: 13px;
          }

          .command-output {
            display: none;
            margin-top: 12px;
            padding: 12px;
            border: 1px solid rgba(148, 163, 184, 0.14);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.36);
            color: var(--muted);
            font-size: 13px;
            line-height: 1.45;
            white-space: pre-wrap;
          }

          .command-output.visible {
            display: block;
          }

          .card-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 14px;
            color: var(--muted);
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
          }

          .icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border: 1px solid rgba(125, 211, 252, 0.24);
            border-radius: 8px;
            background: rgba(96, 165, 250, 0.11);
            color: #9bdcff;
            flex: 0 0 auto;
          }

          .icon svg {
            width: 16px;
            height: 16px;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.9;
            stroke-linecap: round;
            stroke-linejoin: round;
          }

          .gauge-wrap {
            display: grid;
            place-items: center;
            min-height: 174px;
            overflow: visible;
          }

          .gauge {
            width: 174px;
            height: 174px;
            overflow: visible;
          }

          .gauge-track,
          .gauge-progress {
            fill: none;
            stroke-width: 12;
            transform: rotate(-90deg);
            transform-origin: 80px 80px;
          }

          .gauge-track {
            stroke: var(--track);
          }

          .gauge-progress {
            stroke: var(--cyan);
            stroke-linecap: round;
            stroke-dasharray: 364.42;
            stroke-dashoffset: 364.42;
            filter: drop-shadow(0 0 8px rgba(64, 224, 208, 0.38));
            transition: stroke-dashoffset 520ms ease;
          }

          .ram-gauge .gauge-progress {
            stroke: url(#ramGradient);
            filter: drop-shadow(0 0 8px rgba(167, 139, 250, 0.38));
          }

          .disk-gauge .gauge-progress {
            stroke: url(#diskGradient);
          }

          .gauge-dot {
            fill: #e7fff9;
            filter: drop-shadow(0 0 7px rgba(64, 224, 208, 0.9));
            transform-origin: 80px 80px;
            transition: transform 520ms ease;
          }

          .gauge-content {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            width: 100%;
            height: 100%;
            color: var(--text);
            line-height: 1.05;
            text-align: center;
          }

          .gauge-main {
            font-size: 23px;
            font-weight: 600;
            white-space: nowrap;
          }

          .gauge-sub {
            margin-top: 3px;
            color: var(--muted);
            font-size: 14px;
            font-weight: 600;
            white-space: nowrap;
          }

          .gauge-label {
            margin-top: 2px;
            color: var(--soft);
            font-size: 12px;
          }

          .below-note {
            margin: 10px 0 0;
            color: var(--muted);
            font-size: 14px;
            text-align: center;
          }

          .stat-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 9px;
            margin-top: 14px;
          }

          .stat {
            border: 1px solid rgba(148, 163, 184, 0.13);
            border-radius: 10px;
            padding: 9px;
            background: rgba(2, 6, 23, 0.28);
          }

          .stat-label {
            display: block;
            color: var(--soft);
            font-size: 11px;
            letter-spacing: 0.05em;
            text-transform: uppercase;
          }

          .stat-value {
            display: block;
            margin-top: 4px;
            color: var(--text);
            font-size: 15px;
            font-weight: 600;
          }

          .disk-body {
            display: grid;
            grid-template-columns: 145px 1fr;
            gap: 18px;
            align-items: center;
          }

          .disk-body .gauge {
            width: 134px;
            height: 134px;
          }

          .disk-body .gauge-main {
            font-size: 21px;
          }

          .disk-stats {
            display: grid;
            gap: 9px;
          }

          .disk-row {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 12px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.12);
            padding-bottom: 7px;
          }

          .disk-row span:first-child {
            color: var(--muted);
            font-size: 13px;
          }

          .disk-row span:last-child {
            color: var(--text);
            font-size: 16px;
            font-weight: 600;
          }

          .bar {
            height: 8px;
            margin-top: 16px;
            overflow: hidden;
            border: 1px solid rgba(148, 163, 184, 0.13);
            border-radius: 999px;
            background: rgba(3, 7, 18, 0.72);
          }

          .fill {
            width: 0%;
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, var(--green), var(--cyan));
            box-shadow: 0 0 16px rgba(64, 224, 208, 0.45);
            transition: width 520ms ease;
          }

          .uptime-main,
          .time-main,
          .date-main {
            margin: 0;
            color: var(--text);
            font-size: clamp(30px, 7vw, 42px);
            line-height: 1.05;
            font-weight: 600;
            overflow-wrap: anywhere;
          }

          .time-card,
          .date-card {
            min-height: 150px;
          }

          .time-card,
          .date-card {
            display: flex;
            flex-direction: column;
          }

          .time-card .card-header,
          .date-card .card-header {
            margin-bottom: 12px;
          }

          .time-date-body {
            display: flex;
            align-items: center;
            justify-content: flex-start;
            gap: 18px;
            flex: 1;
            width: 100%;
          }

          .time-date-text {
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-width: 0;
          }

          .time-support,
          .date-support {
            display: grid;
            gap: 5px;
            margin-top: 10px;
            color: var(--muted);
            font-size: 13px;
          }

          .time-support span,
          .date-support span {
            color: var(--soft);
          }

          .clock-accent {
            position: relative;
            width: 78px;
            height: 78px;
            border: 1px solid rgba(125, 211, 252, 0.18);
            border-radius: 50%;
            background:
              radial-gradient(circle at center, rgba(64, 224, 208, 0.12), transparent 54%),
              rgba(2, 6, 23, 0.22);
            box-shadow: inset 0 0 22px rgba(64, 224, 208, 0.08);
            opacity: 0.9;
          }

          .clock-accent::before,
          .clock-accent::after {
            content: "";
            position: absolute;
            left: 50%;
            top: 50%;
            width: 2px;
            border-radius: 99px;
            background: rgba(157, 220, 255, 0.72);
            transform-origin: bottom center;
          }

          .clock-accent::before {
            height: 23px;
            transform: translate(-50%, -100%) rotate(35deg);
          }

          .clock-accent::after {
            height: 17px;
            transform: translate(-50%, -100%) rotate(118deg);
          }

          .calendar-accent {
            width: 78px;
            border: 1px solid rgba(125, 211, 252, 0.18);
            border-radius: 12px;
            overflow: hidden;
            background: rgba(2, 6, 23, 0.26);
            box-shadow: inset 0 0 22px rgba(64, 224, 208, 0.07);
            opacity: 0.92;
          }

          .calendar-accent .cal-top {
            height: 20px;
            background: linear-gradient(90deg, rgba(64, 224, 208, 0.26), rgba(106, 167, 255, 0.22));
          }

          .calendar-accent .cal-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 5px;
            padding: 10px;
          }

          .calendar-accent .cal-grid span {
            width: 10px;
            height: 10px;
            border-radius: 3px;
            background: rgba(125, 211, 252, 0.18);
          }

          .date-main {
            font-size: clamp(25px, 6vw, 34px);
          }

          .meta-list {
            display: grid;
            gap: 10px;
            margin-top: 18px;
          }

          .meta-line {
            display: flex;
            align-items: center;
            gap: 9px;
            color: var(--muted);
            font-size: 14px;
          }

          .mini-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            border-radius: 7px;
            background: rgba(96, 165, 250, 0.1);
            color: #9bdcff;
          }

          .mini-icon svg {
            width: 14px;
            height: 14px;
            fill: none;
            stroke: currentColor;
            stroke-width: 1.9;
            stroke-linecap: round;
            stroke-linejoin: round;
          }

          .agents-panel {
            margin-top: 14px;
            padding: 18px;
          }

          .agents-title {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 0 0 14px;
            color: var(--text);
            font-size: 17px;
            font-weight: 600;
            letter-spacing: 0;
          }

          .agent-list {
            display: grid;
            grid-template-columns: 1fr;
            gap: 10px;
            margin: 0;
            padding: 0;
            list-style: none;
          }

          .agent-list li {
            display: grid;
            grid-template-columns: auto 1fr auto;
            gap: 10px;
            align-items: center;
            min-height: 76px;
            padding: 12px;
            border: 1px solid rgba(148, 163, 184, 0.15);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.3);
          }

          .agent-meta {
            min-width: 0;
          }

          .agent-name {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text);
            font-size: 15px;
            font-weight: 550;
          }

          .agent-description {
            display: block;
            margin-top: 4px;
            color: var(--muted);
            font-size: 13px;
            line-height: 1.35;
          }

          .agent-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--green);
            box-shadow: 0 0 14px rgba(55, 214, 122, 0.7);
            flex: 0 0 auto;
          }

          .logs-panel {
            margin-top: 14px;
          }

          .log-stream {
            display: grid;
            gap: 8px;
            max-height: 220px;
            overflow: auto;
            padding: 12px;
            border: 1px solid rgba(148, 163, 184, 0.13);
            border-radius: 12px;
            background: rgba(2, 6, 23, 0.34);
            color: var(--muted);
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 12px;
            line-height: 1.45;
          }

          .log-line {
            display: grid;
            grid-template-columns: 84px 124px 1fr;
            gap: 10px;
            align-items: baseline;
          }

          .log-time {
            color: var(--soft);
          }

          .log-source {
            color: var(--blue);
          }

          .log-message {
            color: var(--muted);
          }

          footer {
            display: flex;
            flex-direction: column;
            gap: 8px;
            padding: 14px 2px 2px;
            color: var(--muted);
            font-size: 13px;
          }

          .connection {
            color: var(--soft);
          }

          @media (min-width: 760px) {
            .shell {
              padding: 22px;
            }

            .dashboard-grid {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            .agent-list {
              grid-template-columns: repeat(3, minmax(0, 1fr));
            }

            footer {
              flex-direction: row;
              justify-content: space-between;
              align-items: center;
            }
          }

          @media (max-width: 860px) {
            .app-shell {
              grid-template-columns: 1fr;
            }

            .sidebar {
              position: sticky;
              top: 0;
              z-index: 9;
              display: flex;
              align-items: center;
              gap: 10px;
              height: auto;
              padding: 10px;
              overflow-x: auto;
              border-right: 0;
              border-bottom: 1px solid rgba(110, 203, 255, 0.16);
            }

            .brand {
              padding: 0 8px 0 0;
              white-space: nowrap;
            }

            .side-nav {
              display: flex;
              gap: 6px;
            }

            .nav-item {
              white-space: nowrap;
            }

            .topbar {
              position: static;
              grid-template-columns: 1fr;
            }

            .top-command {
              justify-self: stretch;
            }
          }

          @media (max-width: 520px) {
            .hero {
              min-height: 118px;
              padding: 16px;
            }

            .topbar .status-pill {
              position: static;
              justify-self: start;
            }

            .disk-body {
              grid-template-columns: 1fr;
              justify-items: center;
              gap: 12px;
            }

            .disk-stats {
              width: 100%;
            }

            .command-form {
              grid-template-columns: 1fr;
            }

            .agent-list li {
              grid-template-columns: auto 1fr;
            }

            .agent-list .button {
              grid-column: 1 / -1;
              width: 100%;
            }

            .log-line {
              grid-template-columns: 1fr;
              gap: 2px;
            }
          }

          @media (hover: none) {
            .glass:hover {
              transform: none;
            }
          }
        </style>
      </head>
      <body>
        <div class="app-shell">
          <aside class="sidebar" aria-label="Primary navigation">
            <div class="brand"><span class="brand-mark">AO</span><span>AgentOS</span></div>
            <nav class="side-nav">
              <a class="nav-item" href="/"><svg viewBox="0 0 24 24"><path d="M4 11l8-7 8 7"></path><path d="M6 10v10h12V10"></path></svg><span>Dashboard</span></a>
              <a class="nav-item active" href="#agents"><svg viewBox="0 0 24 24"><path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7l7-4z"></path></svg><span>Agents</span></a>
              <a class="nav-item" href="#control-panel"><svg viewBox="0 0 24 24"><path d="M4 7h16M7 7v10M17 7v10M4 17h16"></path></svg><span>Command</span></a>
              <a class="nav-item" href="#logs"><svg viewBox="0 0 24 24"><path d="M5 5h14v14H5z"></path><path d="M8 9h8M8 13h8M8 17h5"></path></svg><span>Logs</span></a>
            </nav>
          </aside>

          <main class="shell main-panel">
          <div class="topbar">
            <input class="top-command input" id="top-command-input" type="text" placeholder="Type a command..." autocomplete="off">
            <div class="status-pill"><span class="dot"></span><span id="server-status">Online</span></div>
          </div>

          <section class="agents-panel glass" id="agents" aria-label="Available agents">
            <h2 class="agents-title"><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7l7-4z"></path><path d="M9 12h6M12 9v6"></path></svg></span>Available Agents</h2>
            <ul class="agent-list" id="agents-list">
              <li><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7l7-4z"></path></svg></span><span class="agent-meta"><span class="agent-name"><span class="agent-status-dot"></span>Loading</span><span class="agent-description">Fetching local agent registry</span></span><button class="button secondary" type="button">Start</button></li>
            </ul>
          </section>

          <section class="command-card glass" id="control-panel" aria-label="Command center">
            <div class="card-header">
              <span class="icon"><svg viewBox="0 0 24 24"><path d="M4 7h16M7 7v10M17 7v10M4 17h16"></path></svg></span>
              Command Center
            </div>
            <form class="command-form" id="command-form">
              <input class="command-input" id="command-input" type="text" placeholder="Type a command..." autocomplete="off">
              <button class="button" type="submit">Run</button>
            </form>
            <div class="command-output" id="command-output" aria-live="polite"></div>
          </section>

          <section class="logs-panel glass" id="logs" aria-label="System logs">
            <div class="card-header">
              <span class="icon"><svg viewBox="0 0 24 24"><path d="M5 5h14v14H5z"></path><path d="M8 9h8M8 13h8M8 17h5"></path></svg></span>
              System Logs
            </div>
            <div class="log-stream" id="log-stream" role="log" aria-live="polite"></div>
          </section>

          <footer>
            <span id="last-updated">Last updated: never</span>
            <span class="connection">Connection: <span id="connection-status">checking</span></span>
          </footer>

        </main>
        </div>

        <script>
          const els = {
            serverStatus: document.getElementById("server-status"),
            statusPill: document.querySelector(".status-pill"),
            agentsList: document.getElementById("agents-list"),
            topCommandInput: document.getElementById("top-command-input"),
            commandForm: document.getElementById("command-form"),
            commandInput: document.getElementById("command-input"),
            commandOutput: document.getElementById("command-output"),
            logStream: document.getElementById("log-stream"),
            lastUpdated: document.getElementById("last-updated"),
            connectionStatus: document.getElementById("connection-status"),
          };

          const logs = [
            { source: "system_agent", message: "Agent controls ready." },
            { source: "coding_agent", message: "Command endpoint ready." },
          ];
          const agentStates = {};
          let pendingApproval = null;

          function setAgents(agents) {
            els.agentsList.innerHTML = "";
            if (!Array.isArray(agents) || agents.length === 0) {
              els.agentsList.innerHTML = agentCardHtml("unknown_agent", "No agents returned by the local registry.");
              return;
            }
            for (const agent of agents) {
              els.agentsList.insertAdjacentHTML(
                "beforeend",
                agentCardHtml(agent.name || "unknown_agent", agent.description || "Local agent available")
              );
            }
          }

          function agentCardHtml(name, description) {
            const label = agentStates[name] ? "Stop" : "Start";
            return '<li><span class="icon">' + agentIcon(name) + '</span><span class="agent-meta"><span class="agent-name"><span class="agent-status-dot"></span>' + escapeHtml(name) + '</span><span class="agent-description">' + escapeHtml(description) + '</span></span><button class="button secondary agent-toggle" type="button" data-agent="' + escapeHtml(name) + '">' + label + '</button></li>';
          }

          function escapeHtml(value) {
            return String(value)
              .replaceAll("&", "&amp;")
              .replaceAll("<", "&lt;")
              .replaceAll(">", "&gt;")
              .replaceAll('"', "&quot;")
              .replaceAll("'", "&#039;");
          }

          function agentIcon(name) {
            if (name === "system_agent") {
              return '<svg viewBox="0 0 24 24"><rect x="5" y="6" width="14" height="10" rx="2"></rect><path d="M8 20h8M12 16v4"></path></svg>';
            }
            if (name === "maintenance_agent") {
              return '<svg viewBox="0 0 24 24"><path d="M14 6l4 4-8 8H6v-4l8-8z"></path><path d="M16 4l4 4"></path></svg>';
            }
            if (name === "coding_agent") {
              return '<svg viewBox="0 0 24 24"><path d="M8 9l-4 3 4 3M16 9l4 3-4 3M14 5l-4 14"></path></svg>';
            }
            return '<svg viewBox="0 0 24 24"><path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7l7-4z"></path></svg>';
          }

          function appendLog(source, message) {
            logs.push({ source, message, time: new Date() });
            if (logs.length > 40) {
              logs.shift();
            }
            renderLogs();
          }

          function renderLogs() {
            els.logStream.innerHTML = logs.map((entry) => {
              const time = entry.time ? entry.time.toLocaleTimeString() : new Date().toLocaleTimeString();
              return '<div class="log-line"><span class="log-time">' + escapeHtml(time) + '</span><span class="log-source">' + escapeHtml(entry.source) + '</span><span class="log-message">' + escapeHtml(entry.message) + '</span></div>';
            }).join("");
            els.logStream.scrollTop = els.logStream.scrollHeight;
          }

          function renderCommandResult(data) {
            if (data && data.requires_approval) {
              pendingApproval = {
                action: data.action,
                args: data.args || {},
              };
              els.commandOutput.innerHTML =
                '<div><strong>Approval required</strong></div>' +
                '<div>Command: ' + escapeHtml(data.command_preview || "") + '</div>' +
                '<div>Risk: ' + escapeHtml(data.risk || "Review before running.") + '</div>' +
                '<div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;">' +
                '<button class="button secondary" type="button" data-command-action="approve">Approve</button>' +
                '<button class="button secondary" type="button" data-command-action="cancel">Cancel</button>' +
                '</div>';
              return;
            }

            pendingApproval = null;
            els.commandOutput.textContent = JSON.stringify(data, null, 2);
          }

          async function approvePendingCommand() {
            if (!pendingApproval) {
              return;
            }

            els.commandOutput.textContent = "Running approved command...";
            appendLog("command_center", "Approved action: " + pendingApproval.action);

            try {
              const response = await fetch("/command/approve", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(pendingApproval),
              });

              if (!response.ok) {
                throw new Error("Approval request failed");
              }

              const data = await response.json();
              pendingApproval = null;
              els.commandOutput.textContent = JSON.stringify(data, null, 2);
              appendLog(data.agent || "command_center", "Approved command finished with exit code " + data.exit_code + ".");
            } catch (error) {
              pendingApproval = null;
              els.commandOutput.textContent = "Approved command failed. Check server logs for details.";
              appendLog("command_center", "Approved command failed.");
            }
          }

          async function submitCommand(input) {
            const command = input.trim();
            if (!command) {
              return;
            }

            els.commandOutput.classList.add("visible");
            els.commandOutput.textContent = "Running command...";
            appendLog("coding_agent", "Command submitted: " + command);

            try {
              const response = await fetch("/command", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ input: command }),
              });

              if (!response.ok) {
                throw new Error("Command request failed");
              }

              const data = await response.json();
              renderCommandResult(data);
              appendLog(data.agent || "coding_agent", data.response || "Command completed.");
            } catch (error) {
              pendingApproval = null;
              els.commandOutput.textContent = "Command failed. Check server logs for details.";
              appendLog("coding_agent", "Command failed.");
            }
          }

          async function refreshControl() {
            try {
              const [systemResponse, agentsResponse] = await Promise.all([
                fetch("/system", { cache: "no-store" }),
                fetch("/agents", { cache: "no-store" }),
              ]);

              if (!systemResponse.ok || !agentsResponse.ok) {
                throw new Error("Control request failed");
              }

              const system = await systemResponse.json();
              const agents = await agentsResponse.json();
              const now = new Date();

              els.serverStatus.textContent = "Online";
              els.statusPill.classList.remove("offline");
              els.connectionStatus.textContent = "online";
              setAgents(agents.agents);
              els.lastUpdated.textContent = "Last updated: " + now.toLocaleTimeString();
              appendLog("system_agent", "Control heartbeat: " + (system.hostname || "local host") + ".");
            } catch (error) {
              els.serverStatus.textContent = "Offline";
              els.statusPill.classList.add("offline");
              els.connectionStatus.textContent = "offline";
              els.lastUpdated.textContent = "Last updated: failed at " + new Date().toLocaleTimeString();
              appendLog("system_agent", "Control refresh failed.");
            }
          }

          els.commandForm.addEventListener("submit", (event) => {
            event.preventDefault();
            submitCommand(els.commandInput.value);
          });

          els.commandOutput.addEventListener("click", (event) => {
            const action = event.target.dataset.commandAction;
            if (action === "approve") {
              approvePendingCommand();
            }
            if (action === "cancel") {
              pendingApproval = null;
              els.commandOutput.textContent = "Command approval canceled.";
              appendLog("command_center", "Command approval canceled.");
            }
          });

          els.topCommandInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              els.commandInput.value = els.topCommandInput.value;
              submitCommand(els.topCommandInput.value);
            }
          });

          els.agentsList.addEventListener("click", (event) => {
            const button = event.target.closest(".agent-toggle");
            if (!button) {
              return;
            }
            const agentName = button.dataset.agent;
            agentStates[agentName] = !agentStates[agentName];
            button.textContent = agentStates[agentName] ? "Stop" : "Start";
            appendLog("system_agent", agentName + " " + (agentStates[agentName] ? "started" : "stopped") + ".");
          });

          renderLogs();
          refreshControl();
          setInterval(refreshControl, 5000);
        </script>

      </body>
    </html>
    """

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "ollama_required": False,
    }


@app.get("/system")
def system() -> dict:
    return controller.system_agent.stats()


@app.get("/agents")
def agents() -> dict:
    return {
        "agents": controller.list_agents(),
        "intents": ["system", "maintenance", "code", "chat"],
    }


@app.post("/command")
def command(payload: CommandRequest) -> dict:
    return route_slash_command(payload.input)


@app.post("/command/approve")
def command_approve(payload: CommandApprovalRequest) -> dict:
    return approve_command(payload.action, payload.args)


@app.get("/coding/plan")
def coding_plan(request: str = "") -> dict:
    return controller.local_coding_agent.handle(request)
