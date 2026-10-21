"""DBSQL Alerts v2 client. Creates an alert from an accepted proposal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient


@dataclass
class CreatedAlert:
    alert_id: str
    raw: dict[str, Any]


class DbsqlAlertsClient:
    def __init__(self, ws: WorkspaceClient, warehouse_id: str, parent_path: str) -> None:
        self.ws = ws
        self.warehouse_id = warehouse_id
        self.parent_path = parent_path  # e.g. /Workspace/Users/<email>

    def create_v2_alert(
        self,
        *,
        display_name: str,
        query_text: str,
        subscribers: list[str],
        schedule_cron: str,
        timezone_id: str = "UTC",
    ) -> CreatedAlert:
        body = {
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
        resp = self.ws.api_client.do("POST", "/api/2.0/alerts", body=body)
        return CreatedAlert(alert_id=resp["id"], raw=resp)

    def delete_alert(self, alert_id: str) -> None:
        self.ws.api_client.do("DELETE", f"/api/2.0/alerts/{alert_id}")
