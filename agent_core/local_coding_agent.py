"""Local Ollama-powered coding planner.

This agent inspects a bounded set of repository files and asks local Ollama for
planning guidance only. It never edits files or runs project commands.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

import requests

from .config import settings


DEFAULT_MODEL = "qwen2.5-coder:7b"
TAGS_TIMEOUT_SECONDS = (5, 30)
CHAT_TIMEOUT_SECONDS = (10, 600)


class LocalCodingAgent:
    name = "local_coding_agent"
    description = "Uses local Ollama to inspect repo context and create coding plans only."

    ignored_names = {
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "node_modules",
        ".env",
        "secrets",
        "id_ed25519",
        "id_ed25519.pub",
    }
    ignored_patterns = ("*.key", ".env.*", "ssh-ed25519 *")
    important_root_files = ("api.py", "README.md", "requirements.txt")
    max_tree_entries = 300
    max_file_chars = 12000
    max_total_context_chars = 60000

    def __init__(
        self,
        repo_root: Path | None = None,
        model: str | None = None,
        ollama_url: str | None = None,
    ) -> None:
        self.repo_root = repo_root or Path.home() / "agents"
        self.model = model or DEFAULT_MODEL
        self.ollama_url = (ollama_url or settings.ollama_url).rstrip("/")

    def handle(self, message: str = "") -> dict:
        request = message.strip()
        if not request:
            return self._empty_response(
                summary="Missing coding request.",
                risks=["Pass a request such as: make dashboard better."],
            )

        repo_context = self._build_repo_context()
        if "error" in repo_context:
            return self._empty_response(
                summary=repo_context["error"],
                risks=["The local repository context could not be loaded."],
            )

        diagnostics = {}
        try:
            diagnostics = self._check_ollama()
            if not diagnostics["model_available"]:
                response = self._empty_response(
                    summary=f"Ollama is reachable, but model {self.model} was not found.",
                    risks=["Install the model with: ollama pull qwen2.5-coder:7b"],
                )
                response["ollama_diagnostics"] = diagnostics
                return response
            ollama_payload = self._ask_ollama(request, repo_context)
        except requests.RequestException as exc:
            return self._error_response(
                summary="Ollama request failed. See ollama_diagnostics for the real exception.",
                exc=exc,
                diagnostics=diagnostics,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            return self._empty_response(
                summary="Ollama returned a response that was not valid structured JSON.",
                risks=[str(exc)],
            )

        return self._normalize_response(ollama_payload, repo_context, diagnostics)

    def _build_repo_context(self) -> dict:
        if not self.repo_root.exists():
            return {"error": f"Repo root not found: {self.repo_root}"}

        files = self._important_files()
        total_chars = 0
        file_context: list[dict[str, str | bool]] = []

        for path in files:
            rel_path = path.relative_to(self.repo_root).as_posix()
            text = self._read_text_file(path)
            if text is None:
                continue

            remaining = self.max_total_context_chars - total_chars
            if remaining <= 0:
                break

            truncated = text[: min(len(text), self.max_file_chars, remaining)]
            total_chars += len(truncated)
            file_context.append(
                {
                    "path": rel_path,
                    "content": truncated,
                    "truncated": len(truncated) < len(text),
                }
            )

        return {
            "root": str(self.repo_root),
            "tree": self._project_tree(),
            "files": file_context,
        }

    def _project_tree(self) -> list[str]:
        entries: list[str] = []
        self._collect_tree_entries(self.repo_root, entries)
        return entries

    def _collect_tree_entries(self, directory: Path, entries: list[str]) -> None:
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return

        for path in children:
            if len(entries) >= self.max_tree_entries:
                if entries[-1:] != ["..."]:
                    entries.append("...")
                return

            if self._is_ignored(path):
                continue

            rel_path = path.relative_to(self.repo_root).as_posix()
            suffix = "/" if path.is_dir() else ""
            entries.append(f"{rel_path}{suffix}")

            if path.is_dir():
                self._collect_tree_entries(path, entries)

    def _important_files(self) -> list[Path]:
        paths: list[Path] = []

        for name in self.important_root_files:
            path = self.repo_root / name
            if path.is_file() and not self._is_ignored(path):
                paths.append(path)

        agent_core = self.repo_root / "agent_core"
        if agent_core.is_dir():
            for path in sorted(agent_core.glob("*.py")):
                if path.is_file() and not self._is_ignored(path):
                    paths.append(path)

        return paths

    def _is_ignored(self, path: Path) -> bool:
        try:
            rel_parts = path.relative_to(self.repo_root).parts
        except ValueError:
            rel_parts = path.parts

        for part in rel_parts:
            if part in self.ignored_names:
                return True
            if part.startswith(".env"):
                return True
            if "secrets" in part.lower():
                return True
            if any(fnmatch.fnmatch(part, pattern) for pattern in self.ignored_patterns):
                return True

        return False

    @staticmethod
    def _read_text_file(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        except OSError:
            return None

    def _check_ollama(self) -> dict:
        tags_url = f"{self.ollama_url}/api/tags"
        response = requests.get(tags_url, timeout=TAGS_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        models = sorted(
            model.get("name", "")
            for model in data.get("models", [])
            if isinstance(model, dict) and model.get("name")
        )
        return {
            "tags_url": tags_url,
            "chat_url": f"{self.ollama_url}/api/chat",
            "model": self.model,
            "model_available": self.model in models,
            "installed_models": models,
        }

    def _ask_ollama(self, request: str, repo_context: dict) -> dict:
        system_prompt = (
            "You are AgentOS Local Coding Agent v1. Use only the provided repo "
            "context. Do not claim to edit files. Do not suggest destructive "
            "commands. Return strict JSON with these keys: summary, "
            "files_to_review, proposed_plan, risks, suggested_tests, next_command. "
            "Use arrays for files_to_review, proposed_plan, risks, and "
            "suggested_tests. next_command must be one safe, non-destructive "
            "command or an empty string."
        )
        user_prompt = {
            "request": request,
            "repo_context": repo_context,
        }
        response = requests.post(
            f"{self.ollama_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_prompt)},
                ],
            },
            timeout=CHAT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            return {}
        return json.loads(content)

    def _normalize_response(self, payload: dict, repo_context: dict, diagnostics: dict) -> dict:
        if not isinstance(payload, dict):
            payload = {}

        response = self._empty_response(summary=str(payload.get("summary", "")).strip())
        response.update(
            {
                "summary": str(payload.get("summary") or "Coding plan generated locally."),
                "files_to_review": self._string_list(payload.get("files_to_review")),
                "proposed_plan": self._string_list(payload.get("proposed_plan")),
                "risks": self._string_list(payload.get("risks")),
                "suggested_tests": self._string_list(payload.get("suggested_tests")),
                "next_command": self._safe_next_command(payload.get("next_command")),
                "repo_root": repo_context["root"],
                "context_files": [item["path"] for item in repo_context["files"]],
                "ollama_diagnostics": diagnostics,
            }
        )
        return response

    def _empty_response(
        self,
        summary: str = "",
        risks: list[str] | None = None,
    ) -> dict:
        return {
            "agent": self.name,
            "model": self.model,
            "summary": summary,
            "files_to_review": [],
            "proposed_plan": [],
            "risks": risks or [],
            "suggested_tests": [],
            "next_command": "",
            "edits_performed": False,
            "ollama_required": True,
        }

    def _error_response(
        self,
        summary: str,
        exc: requests.RequestException,
        diagnostics: dict | None = None,
    ) -> dict:
        details = self._request_exception_details(exc)
        if diagnostics:
            details.update(diagnostics)
            details["exception_type"] = type(exc).__name__
            details["message"] = str(exc)
        response = self._empty_response(
            summary=summary,
            risks=[details["message"]],
        )
        response["ollama_diagnostics"] = details
        return response

    def _request_exception_details(self, exc: requests.RequestException) -> dict:
        details = {
            "tags_url": f"{self.ollama_url}/api/tags",
            "chat_url": f"{self.ollama_url}/api/chat",
            "model": self.model,
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
        response = getattr(exc, "response", None)
        request = getattr(exc, "request", None)
        if request is not None:
            details["request_url"] = getattr(request, "url", "")
            details["request_method"] = getattr(request, "method", "")
        if response is not None:
            details["status_code"] = response.status_code
            details["response_text"] = response.text[:1000]
        return details

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _safe_next_command(value: Any) -> str:
        command = str(value or "").strip()
        destructive_tokens = ("rm ", "rm -", "rmdir", "git reset", "git clean", "mkfs", "shutdown")
        lowered = command.lower()
        if any(token in lowered for token in destructive_tokens):
            return ""
        return command
