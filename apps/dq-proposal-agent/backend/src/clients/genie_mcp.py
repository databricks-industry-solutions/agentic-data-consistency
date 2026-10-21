"""Genie Conversation API client.

Used as the primary SQL generator for the agent. The orchestrator sends a
per-invariant prompt; Genie returns natural-language reasoning and (usually) a
generated SQL query attached to the message. We surface both; the orchestrator
passes the SQL through `dry_run_check` for safety before proposing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient


@dataclass
class GenieAnswer:
    answer_text: str
    generated_sql: str | None
    raw_message: dict[str, Any]


class GenieUnavailable(RuntimeError):
    """Raised when the Genie space is not configured or the call fails after retries."""


class GenieClient:
    def __init__(self, ws: WorkspaceClient, space_id: str | None) -> None:
        self.ws = ws
        self.space_id = space_id

    @property
    def available(self) -> bool:
        return bool(self.space_id)

    def ask(self, question: str, *, conversation_id: str | None = None,
            timeout_seconds: int = 90) -> GenieAnswer:
        """Send `question`; return Genie's answer + generated SQL once COMPLETED.

        Raises GenieUnavailable if the space is not configured.
        """
        if not self.space_id:
            raise GenieUnavailable("Genie space ID not configured")

        api = self.ws.api_client.do
        if conversation_id:
            resp = api(
                "POST",
                f"/api/2.0/genie/spaces/{self.space_id}/conversations/{conversation_id}/messages",
                body={"content": question},
            )
        else:
            resp = api(
                "POST",
                f"/api/2.0/genie/spaces/{self.space_id}/start-conversation",
                body={"content": question},
            )
            conversation_id = resp["conversation_id"]
        message_id = resp["message_id"]

        # Poll the message until COMPLETED or FAILED
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            msg = api(
                "GET",
                f"/api/2.0/genie/spaces/{self.space_id}/conversations/{conversation_id}/messages/{message_id}",
            )
            status = msg.get("status")
            if status in {"COMPLETED", "FAILED"}:
                if status == "FAILED":
                    raise GenieUnavailable(f"Genie message failed: {msg.get('error')}")
                return _parse(msg)
            time.sleep(2)
        raise GenieUnavailable(f"Genie message {message_id} timed out after {timeout_seconds}s")


def _parse(msg: dict[str, Any]) -> GenieAnswer:
    answer_text_parts: list[str] = []
    generated_sql: str | None = None
    for att in msg.get("attachments") or []:
        if "query" in att and isinstance(att["query"], dict):
            sql = att["query"].get("query")
            if sql and not generated_sql:
                generated_sql = sql
            desc = att["query"].get("description")
            if desc:
                answer_text_parts.append(desc)
        if "text" in att and isinstance(att["text"], dict):
            content = att["text"].get("content")
            if content:
                answer_text_parts.append(content)
    return GenieAnswer(
        answer_text="\n".join(answer_text_parts).strip(),
        generated_sql=generated_sql,
        raw_message=msg,
    )
