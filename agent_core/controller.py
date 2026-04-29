"""Simple intent router for local agents."""

from .coding_agent import CodingAgent
from .local_coding_agent import LocalCodingAgent
from .maintenance_agent import MaintenanceAgent
from .system_agent import SystemAgent


class AgentController:
    def __init__(self) -> None:
        self.system_agent = SystemAgent()
        self.maintenance_agent = MaintenanceAgent()
        self.coding_agent = CodingAgent()
        self.local_coding_agent = LocalCodingAgent()

    def list_agents(self) -> list[dict]:
        return [
            {
                "name": self.system_agent.name,
                "description": self.system_agent.description,
                "intents": ["system"],
            },
            {
                "name": self.maintenance_agent.name,
                "description": self.maintenance_agent.description,
                "intents": ["maintenance"],
            },
            {
                "name": self.coding_agent.name,
                "description": self.coding_agent.description,
                "intents": ["code"],
            },
            {
                "name": "self_healing_agent",
                "description": "Monitors AgentOS and safely suggests recovery actions.",
                "intents": ["self_heal"],
            },
        ]

    def route(self, intent: str, message: str = "") -> dict:
        normalized = intent.strip().lower()

        if normalized == "system":
            return self.system_agent.handle(message)
        if normalized == "maintenance":
            return self.maintenance_agent.handle(message)
        if normalized in {"coding", "code", "local_code", "plan"}:
            return self.local_coding_agent.handle(message)
        if normalized == "chat":
            return {
                "agent": "controller",
                "response": "Chat intent received. Local Ollama integration can be added later.",
                "ollama_required": False,
            }

        return {
            "agent": "controller",
            "response": "Unknown intent. Use one of: system, maintenance, coding, code, local_code, plan, chat.",
            "available_intents": [
                "system",
                "maintenance",
                "coding",
                "code",
                "local_code",
                "plan",
                "chat",
            ],
        }
