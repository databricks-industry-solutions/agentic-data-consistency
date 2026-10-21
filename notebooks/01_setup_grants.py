# Databricks notebook source
# MAGIC %md
# MAGIC # Agentic Data Consistency — setup
# MAGIC
# MAGIC Prepares a workspace for the **dq-proposal-agent** app:
# MAGIC
# MAGIC 1. Creates the app's state schema and the `reports` volume (the app creates its tables on startup).
# MAGIC 2. Grants the app's service principal what it needs on the state schema.
# MAGIC 3. Optionally grants it read access on the schema you want the agent to scan (needed in SP mode,
# MAGIC    see `docs/AUTHORIZATION.md`), plus the matching `<schema>_monitoring` schema if one exists.
# MAGIC
# MAGIC The bundle job passes every parameter. When running by hand, fill in the widgets.
# MAGIC The catalog must already exist and you need permission to grant on it.

# COMMAND ----------

dbutils.widgets.text("app_name", "dq-proposal-agent", "App name")
dbutils.widgets.text("app_catalog", "", "State catalog")
dbutils.widgets.text("app_schema", "", "State schema")
dbutils.widgets.text("target_catalog", "", "Catalog to scan (optional)")
dbutils.widgets.text("target_schema", "", "Schema to scan (optional)")

app_name = dbutils.widgets.get("app_name").strip()
app_catalog = dbutils.widgets.get("app_catalog").strip()
app_schema = dbutils.widgets.get("app_schema").strip()
target_catalog = dbutils.widgets.get("target_catalog").strip()
target_schema = dbutils.widgets.get("target_schema").strip()

if not (app_name and app_catalog and app_schema):
    raise ValueError("app_name, app_catalog and app_schema are required")

# COMMAND ----------

from databricks.sdk import WorkspaceClient

sp_client_id = WorkspaceClient().apps.get(app_name).service_principal_client_id
print(f"App '{app_name}' runs as service principal {sp_client_id}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## State schema and grants

# COMMAND ----------

def run(sql: str) -> None:
    print(sql)
    spark.sql(sql)


state = f"`{app_catalog}`.`{app_schema}`"
run(f"CREATE SCHEMA IF NOT EXISTS {state}")
run(f"CREATE VOLUME IF NOT EXISTS {state}.reports")
run(f"GRANT USE CATALOG ON CATALOG `{app_catalog}` TO `{sp_client_id}`")
run(
    f"GRANT USE SCHEMA, CREATE TABLE, CREATE VOLUME, SELECT, MODIFY, READ VOLUME, WRITE VOLUME "
    f"ON SCHEMA {state} TO `{sp_client_id}`"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Target schema (optional)

# COMMAND ----------

if target_catalog and target_schema:
    run(f"GRANT USE CATALOG ON CATALOG `{target_catalog}` TO `{sp_client_id}`")
    run(f"GRANT USE SCHEMA, SELECT ON SCHEMA `{target_catalog}`.`{target_schema}` TO `{sp_client_id}`")

    # Lakehouse Monitor profile re-use: only if the monitoring schema exists.
    monitoring = f"{target_schema}_monitoring"
    if spark.sql(f"SHOW SCHEMAS IN `{target_catalog}` LIKE '{monitoring}'").count():
        run(f"GRANT USE SCHEMA, SELECT ON SCHEMA `{target_catalog}`.`{monitoring}` TO `{sp_client_id}`")
else:
    print("No target schema given; skipping data grants.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next steps
# MAGIC
# MAGIC - Put the service principal ID printed above into `DQ_ALERT_PARENT_PATH` and
# MAGIC   `DQ_MLFLOW_EXPERIMENT_NAME` in `apps/dq-proposal-agent/backend/app.yaml`, redeploy, then start
# MAGIC   the app (`databricks bundle run dq_proposal_agent`).
# MAGIC - If you use Genie, give the calling identity **Can Run** on the space (`docs/ACTIVATING_GENIE.md`).
# MAGIC - Check `/api/health` on the app URL.
