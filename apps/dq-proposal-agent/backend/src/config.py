from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DQ_", env_file=".env", extra="ignore")

    app_catalog: str = "dq_proposals"
    app_schema: str = "app"

    databricks_host: str = ""
    databricks_warehouse_id: str = ""
    databricks_token: str = ""
    databricks_profile: str = ""

    # Which identity request handlers act as (see docs/AUTHORIZATION.md):
    #   "obo" — the signed-in user, via the x-forwarded-access-token header
    #           Databricks Apps sends when user-authorization scopes are set.
    #           Without a token, falls back like "sp" (see auth.credential_source).
    #   "sp"  — ignore forwarded user tokens; use the app SP in Databricks Apps
    #           (or DQ_DATABRICKS_TOKEN / DQ_DATABRICKS_PROFILE in local dev).
    auth_mode: Literal["obo", "sp"] = "obo"

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _normalise_auth_mode(cls, v: object) -> object:
        # Accept "SP", " obo " etc. A typo still fails, but a case or
        # whitespace slip in app.yaml shouldn't stop the app from starting.
        return v.strip().lower() if isinstance(v, str) else v

    genie_space_id: str = ""
    genie_call_cap: int = 200
    genie_concurrent_cap: int = 4

    default_alert_subscribers: str = ""
    default_alert_cron: str = "0 0 6 * * ?"
    default_output_backends: str = "dbsql_alert"
    alert_parent_path: str = "/Workspace/Shared"

    # DEFERRED: DQX rule emission (see docs/ROADMAP.md). Not honoured today.
    dqx_enabled: bool = False
    dqx_rules_path: str = ""

    mlflow_experiment_name: str = ""
    mlflow_trace_table_prefix: str = "agent_trace"

    # DEFERRED: Lakehouse Monitor profile re-use (see docs/ROADMAP.md).
    # profile_column always issues a fresh sampled SELECT today.
    use_lakehouse_monitor_profiles: bool = True

    max_iterations: int = 12

    # If True, candidates whose (invariant, table, columns) signature is already
    # accepted are dropped before materialisation. Defaults to False so re-runs
    # on the same schema produce a stable proposal set.
    dedup_already_accepted: bool = False

    # Candidates with base_confidence below this floor are skipped before the
    # (expensive) Genie + dry-run materialisation. 0.0 emits everything.
    confidence_floor: float = 0.7

    @property
    def app_namespace(self) -> str:
        return f"{self.app_catalog}.{self.app_schema}"

    @property
    def default_subscribers_list(self) -> list[str]:
        if not self.default_alert_subscribers:
            return []
        return [s.strip() for s in self.default_alert_subscribers.split(",") if s.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
