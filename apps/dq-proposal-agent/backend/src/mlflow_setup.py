"""MLflow tracing → Unity Catalog setup.

Configures an MLflow experiment whose traces land in a UC schema as Delta
tables (`<prefix>_otel_spans`, `_otel_annotations`, `_otel_logs`,
`_otel_metrics`). Idempotent — safe to call on every app startup; if the
experiment is already linked to the same UC schema, the call is a no-op.
"""
from __future__ import annotations

import os
from typing import Optional

import mlflow
from mlflow.entities import UnityCatalog


def configure(
    experiment_name: Optional[str],
    catalog: str,
    schema: str,
    table_prefix: str = "agent_trace",
    sql_warehouse_id: Optional[str] = None,
) -> Optional[str]:
    """Wire up MLflow tracing → UC.

    Returns the resolved experiment ID on success, or None if setup failed
    (we don't crash app startup on tracing setup failure — the agent still
    runs without traces).
    """
    if not experiment_name:
        print("[mlflow] MLFLOW_EXPERIMENT_NAME not set; tracing disabled", flush=True)
        return None

    try:
        mlflow.set_tracking_uri("databricks")
        if sql_warehouse_id:
            os.environ.setdefault("MLFLOW_TRACING_SQL_WAREHOUSE_ID", sql_warehouse_id)
        loc = UnityCatalog(
            catalog_name=catalog,
            schema_name=schema,
            table_prefix=table_prefix,
        )
        try:
            exp = mlflow.set_experiment(experiment_name=experiment_name, trace_location=loc)
        except Exception as e:
            # If experiment exists and is already linked to a different / same UC location
            # MLflow raises. Fall back to plain set_experiment (no trace_location change).
            print(f"[mlflow] set_experiment(trace_location=...) raised ({e!r}); using existing experiment as-is", flush=True)
            exp = mlflow.set_experiment(experiment_name=experiment_name)
        print(
            f"[mlflow] tracing wired: experiment={experiment_name} (id={exp.experiment_id}) "
            f"-> UC {catalog}.{schema}.{table_prefix}_*",
            flush=True,
        )
        return exp.experiment_id
    except Exception as e:  # noqa: BLE001
        print(f"[mlflow] setup failed; tracing disabled: {e!r}", flush=True)
        return None
