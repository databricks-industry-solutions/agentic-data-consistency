"""DBSQL Alerts v2 client. Creates (or refreshes) the alert for an accepted proposal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import AlreadyExists


@dataclass
class CreatedAlert:
    alert_id: str
    raw: dict[str, Any]
    # True when an alert with the same name already existed and was updated
    # in place instead of created.
    reused: bool = False


def _workspace_relative(path: str | None) -> str:
    """`/Workspace/Users/x` and `/Users/x` name the same folder."""
    path = (path or "").rstrip("/")
    return path[len("/Workspace"):] if path.startswith("/Workspace/") else path


class DbsqlAlertsClient:
    def __init__(self, ws: WorkspaceClient, warehouse_id: str, parent_path: str) -> None:
        self.ws = ws
        self.warehouse_id = warehouse_id
        self.parent_path = parent_path  # e.g. /Workspace/Users/<email>

    def _alert_body(
        self,
        *,
        display_name: str,
        query_text: str,
        subscribers: list[str],
        schedule_cron: str,
        timezone_id: str,
    ) -> dict[str, Any]:
        return {
            "display_name": display_name,
            "query_text": query_text,
            "warehouse_id": self.warehouse_id,
            "parent_path": self.parent_path,
            "evaluation": {
                "source": {"name": "violations"},
                "comparison_operator": "GREATER_THAN",
                "threshold": {"value": {"double_value": 0}},
                "notification": {
                    "subscriptions": [{"user_email": e} for e in subscribers],
                },
            },
            "schedule": {
                "quartz_cron_schedule": schedule_cron,
                "timezone_id": timezone_id,
                "pause_status": "UNPAUSED",
            },
        }

    def create_v2_alert(
        self,
        *,
        display_name: str,
        query_text: str,
        subscribers: list[str],
        schedule_cron: str,
        timezone_id: str = "UTC",
    ) -> CreatedAlert:
        """Create the alert. If one with the same name already exists in
        parent_path (the same check approved again, e.g. from a later run),
        update it with the new SQL, subscribers and schedule and reuse it,
        so a check never ends up with two alerts emailing the same people."""
        body = self._alert_body(
            display_name=display_name, query_text=query_text,
            subscribers=subscribers, schedule_cron=schedule_cron, timezone_id=timezone_id,
        )
        try:
            resp = self.ws.api_client.do("POST", "/api/2.0/alerts", body=body)
            return CreatedAlert(alert_id=resp["id"], raw=resp)
        except AlreadyExists:
            existing_id = self.find_alert_id(display_name)
            if not existing_id:
                raise
        update = {k: body[k] for k in ("query_text", "warehouse_id", "evaluation", "schedule")}
        resp = self.ws.api_client.do(
            "PATCH", f"/api/2.0/alerts/{existing_id}",
            query={"update_mask": ",".join(update)}, body=update,
        )
        return CreatedAlert(alert_id=existing_id, raw=resp, reused=True)

    def find_alert_id(self, display_name: str) -> str | None:
        """ID of the active alert called display_name in parent_path, if any.
        The list endpoint omits parent_path, so name matches are confirmed
        with a GET."""
        want = _workspace_relative(self.parent_path)
        page_token: str | None = None
        while True:
            query: dict[str, Any] = {"page_size": 100}
            if page_token:
                query["page_token"] = page_token
            page = self.ws.api_client.do("GET", "/api/2.0/alerts", query=query)
            for a in page.get("alerts", []):
                if a.get("display_name") != display_name or a.get("lifecycle_state") != "ACTIVE":
                    continue
                full = self.ws.api_client.do("GET", f"/api/2.0/alerts/{a['id']}")
                if _workspace_relative(full.get("parent_path")) == want:
                    return a["id"]
            page_token = page.get("next_page_token")
            if not page_token:
                return None

    def delete_alert(self, alert_id: str) -> None:
        self.ws.api_client.do("DELETE", f"/api/2.0/alerts/{alert_id}")
