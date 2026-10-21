"""ask_genie tool: primary SQL generator. Sends a per-invariant prompt to Genie
and returns the answer + generated SQL. The orchestrator then validates the SQL
via dry_run_check.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...clients.genie_mcp import GenieAnswer, GenieClient, GenieUnavailable


@dataclass
class GenieToolResult:
    available: bool
    answer_text: str = ""
    generated_sql: str | None = None
    error: str | None = None
    conversation_id: str | None = None


def ask_genie(
    genie: GenieClient,
    prompt: str,
    *,
    conversation_id: str | None = None,
    timeout_seconds: int = 90,
) -> GenieToolResult:
    if not genie.available:
        return GenieToolResult(available=False, error="Genie space not configured")
    try:
        ans: GenieAnswer = genie.ask(prompt, conversation_id=conversation_id, timeout_seconds=timeout_seconds)
    except GenieUnavailable as e:
        return GenieToolResult(available=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        return GenieToolResult(available=False, error=f"{type(e).__name__}: {e}")
    cid = (ans.raw_message.get("conversation_id") if isinstance(ans.raw_message, dict) else None)
    return GenieToolResult(
        available=True,
        answer_text=ans.answer_text,
        generated_sql=ans.generated_sql,
        conversation_id=cid,
    )
