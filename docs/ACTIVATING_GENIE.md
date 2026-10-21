# Activating the DQ Proposal Agent (Genie-powered mode)

## What you'll need before you start

Gather these values first — the rest of the doc references them as placeholders.
Everything with the `DQ_` prefix is an app environment variable (set in `app.yaml`,
or `backend/.env` for local dev; keys use Pydantic's `DQ_` prefix).

| Placeholder | What it is | Where it comes from |
|---|---|---|
| `<workspace-url>` | The customer's Databricks workspace host | e.g. `https://adb-<workspace-id>.<n>.azuredatabricks.net` |
| `<app-name>` | Name of the Databricks App | `dq-proposal-agent` unless you named it differently |
| `<app-url>` | The app's own URL (not the workspace URL) | Shown on the app's page in the workspace, e.g. `https://<app-name>-<workspace-id>.<n>.azure.databricksapps.com` |
| `<source-code-path>` | Workspace folder the app is deployed from | The folder you synced the `backend/` code to, e.g. `/Workspace/Users/<deployer>/dq-proposal-agent` |
| `<genie-space-id>` → `DQ_GENIE_SPACE_ID` | The Genie space the agent asks for SQL | Created in Step 1; the 32-char hex in the space URL |
| `<warehouse-id>` | SQL warehouse for the agent's queries, also attached to the Genie space | Workspace → SQL Warehouses. Bound to the app as a resource with key `warehouse`, which feeds `DQ_DATABRICKS_WAREHOUSE_ID` |
| `<target-catalog>.<target-schema>` | The data the agent scans | Chosen by you; the user needs `SELECT` on it |
| `<app-catalog>` / `<app-schema>` → `DQ_APP_CATALOG` / `DQ_APP_SCHEMA` | Where the app stores its state tables | A schema the running identity (user or service principal, see Step 2) can `CREATE TABLE` in |
| `<app-user-or-group>` | Who runs scans in OBO mode (the human user/group) | Needs CAN RUN on the space + table access. In service-principal mode, the app's service principal instead (Step 2) |
| `<alert-parent-path>` → `DQ_ALERT_PARENT_PATH` | Workspace folder where created alerts live | e.g. `/Workspace/Users/<app-user>` |
| `<mlflow-experiment-path>` → `DQ_MLFLOW_EXPERIMENT_NAME` | Optional. Enables trace logging | A workspace path the caller owns |
| `<alert-subscribers>` → `DQ_DEFAULT_ALERT_SUBSCRIBERS` | Default email(s) for created alerts | Comma-separated |
| `<cli-profile>` | Databricks CLI profile for sync/deploy commands | `databricks auth login --profile <cli-profile>` |

> The repo ships with demo-specific values hardcoded in `app.yaml` (a demo Genie
> space ID and author-specific workspace paths). **All of them must be replaced**
> with the placeholders above for the customer's tenant.

---

## Background: what "activating" means

The agent always runs. It has two SQL-authoring paths:

| Mode | SQL source | When it's used |
|---|---|---|
| **Fallback (standard checks)** | Hand-authored templates in `agent/invariant_catalog.py` | `DQ_GENIE_SPACE_ID` empty, **or** Genie calls fail (missing scope, no permission, wrong space ID, timeout) |
| **Genie (activated)** | Genie generates SQL per invariant from NL prompts | A valid `<genie-space-id>` is set **and** the app's identity (user or service principal) can call that space |

"Activating the agent" = getting it onto the Genie path. The app does **not** create
a Genie space — you must point it at one that already exists.

---

## Step 1 — Create a Genie space

In the customer workspace (`<workspace-url>`):

1. **Genie → New.**
2. Add the tables the agent will scan — the same `<target-catalog>.<target-schema>`
   you run the agent against. The space must cover those tables so Genie can author
   validation SQL for them.
3. Attach a **SQL warehouse** to the space (can be the same `<warehouse-id>` the app uses).
4. Save and copy the **space ID** from the URL:
   `…/genie/rooms/<genie-space-id>` → that 32-char hex string is your `DQ_GENIE_SPACE_ID`.

---

## Step 2 — Give the right identity access to Genie

Genie calls run as whichever identity the app runs as: the **signed-in user** (OBO) or
the app's **service principal** (SP). Two things decide which:

- **`DQ_AUTH_MODE`** in `backend/app.yaml` (Step 3): `obo` (default) or `sp`.
- **User authorization** on the app (**Compute → Apps → `<app-name>` → Edit → User
  authorization**). Databricks Apps only forwards a user token when scopes are set
  there, so `obo` with no scopes still runs as the SP.

The quickest check is `<app-url>/api/health`: `effective_identity` is `user` for OBO and
`app_sp` for the service principal. [AUTHORIZATION.md](AUTHORIZATION.md) has the full
setup for both modes, including every grant and how to switch.

**SP mode** (`DQ_AUTH_MODE=sp`, or `obo` with no scopes). All calls run as the app's
service principal (shown on the app's page).

- Grant the **service principal CAN RUN** on the Genie space (Genie space → Share).
- If the app has no scopes and you want to stay in SP mode, set `DQ_AUTH_MODE=sp` before
  adding any. Otherwise adding a scope switches every call (SQL, catalog listing, alerts)
  to the user's token, which then needs all the scopes in
  [AUTHORIZATION.md](AUTHORIZATION.md#1-add-user-authorization-scopes-to-the-app), not just `genie`.

**OBO mode** (`DQ_AUTH_MODE=obo` and scopes set). Calls run as the **end user driving the app**.

1. **Add the `genie` scope**, keeping the scopes already there. (`dashboards.genie` is
   the older, deprecated name.) Without it every Genie call fails with a "required
   scopes: genie" error and the run silently falls back to templates.
2. **Make sure users get a token with the new scope.** If a user still sees the scopes
   error after you've added `genie`, they need to revoke their old consent and accept
   the new prompt. See [AUTHORIZATION.md → step 3](AUTHORIZATION.md#3-make-every-user-consent-again).
3. **Grant CAN RUN on the Genie space** to every user who runs a scan
   (`<app-user-or-group>`). The scope lets the token call the Genie API; it doesn't
   grant access to the space itself. Grant to a group to keep it simple.

In OBO mode users also need `SELECT` on `<target-catalog>.<target-schema>` and access to
`<warehouse-id>` (already true if the agent's SELECTs work).

---

## Step 3 — Configure the app and redeploy

**3a. Set the environment variables in `backend/app.yaml`.** `app.yaml` only supports
`command` and `env`; replace the demo values in `env`:

```yaml
env:
  - name: DQ_GENIE_SPACE_ID
    value: "<genie-space-id>"
  - name: DQ_AUTH_MODE                  # "obo" or "sp", see Step 2
    value: "obo"
  - name: DQ_DATABRICKS_WAREHOUSE_ID
    valueFrom: "warehouse"              # the app resource key from 3b
  - name: DQ_APP_CATALOG
    value: "<app-catalog>"
  - name: DQ_APP_SCHEMA
    value: "<app-schema>"
  - name: DQ_ALERT_PARENT_PATH
    value: "<alert-parent-path>"
  - name: DQ_MLFLOW_EXPERIMENT_NAME     # optional
    value: "<mlflow-experiment-path>"
  - name: DQ_DEFAULT_ALERT_SUBSCRIBERS
    value: "<alert-subscribers>"
```

> If your copy of `app.yaml` has a `resources:` block (older versions of the repo did),
> Databricks Apps ignores it. The warehouse binding lives on the app itself (3b).

**3b. Bind the warehouse to the app.** In **Compute → Apps → `<app-name>` → Edit →
App resources**, add the SQL warehouse `<warehouse-id>` with permission **Can use** and
resource key **`warehouse`** (the key must match `valueFrom` above). The README shows the
CLI equivalent (`databricks apps update`); if you use it, include the app's existing
`user_api_scopes` (if any, see Step 2) in the same JSON so you don't risk dropping them.

**3c. Sync the edited code, then deploy.** `databricks apps deploy` reads `app.yaml` from
`<source-code-path>` in the workspace, not from your laptop, so push your edits first
(or edit `app.yaml` directly in the workspace folder). From `backend/`:

```bash
databricks sync . <source-code-path> \
  --exclude .venv --exclude __pycache__ --exclude .env --exclude '*.pyc' \
  -p <cli-profile>

databricks apps deploy <app-name> \
  --source-code-path <source-code-path> \
  -p <cli-profile>
```

`databricks sync` skips the built frontend (`backend/static/` is gitignored). That's fine
for a config-only redeploy; for a first-time deploy, follow the README's
"Deploy as a Databricks App" section, which also pushes the frontend.

(For local dev, set the same keys in `backend/.env` instead of `app.yaml`.)

---

## Step 4 — Verify it's activated

**A. Health endpoint** — fastest check. Open this in a browser where you're signed in
to the workspace:

```
<app-url>/api/health
→ { "status": "ok", "genie_configured": "yes", "auth_mode": "obo", "effective_identity": "user", ... }
```

`effective_identity` shows which identity your requests run as (Step 2).
`"genie_configured": "yes"` only means the space ID is set. It does **not** prove the
space is reachable, that the `genie` scope is present (OBO mode), or that the app's identity has CAN RUN —
do B.

**B. Run a scan and read the provenance badges** — the real proof. Each proposal card
shows a provenance badge. The badge records *why* the check was proposed, and the runner
only changes it when Genie wrote the SQL for a profiling-based check
(`backend/src/agent/runner.py`, `_materialise`):

| Badge | Why it was proposed | Who wrote the SQL |
|---|---|---|
| `llm` | Profiling (naming patterns, column stats) | **Genie** |
| `profile` | Profiling | **Template** (Genie unavailable, call cap reached, or its SQL failed the dry-run) |
| `lineage` | UC lineage (FK parent, cross-system source) | **Either** — the badge doesn't say |
| `ontology`, `hybrid` | Not produced today (ontology mappings aren't loaded yet; see `docs/ROADMAP.md`) | n/a |

How to read a run:

- **Some `llm` badges** → Genie is working.
- **No `llm` badges, but `profile` badges** → the run is in fallback mode; see troubleshooting.
- **Only `lineage` badges** → the badges can't tell you. Use the queries below.

`lineage` badges only appear when Unity Catalog has lineage for the scanned tables
(`system.access.column_lineage` / `table_lineage`, e.g. tables built by UC jobs,
notebooks or pipelines) and the user can read `system.access`. Without lineage,
name-matched FK checks are labelled `profile`, and checks that come only from lineage
(lineage-only FKs, cross-system FKs) aren't proposed at all.

**C. Count Genie vs template for a run.** The run's agent steps record every dry-run and
its SQL source, and each proposal comes from exactly one successful dry-run. So for a
completed run, counting successful dry-runs by source gives the number of proposals by
source. Run this in a SQL editor, with `<run-id>` taken from the `/runs/<run-id>` URL:

```sql
SELECT
  CASE
    WHEN step.arguments['retry'] = 'true'    THEN 'genie_retry'
    WHEN step.arguments['fallback'] = 'true' THEN 'template_fallback'
    ELSE step.arguments['source']
  END       AS sql_source,
  COUNT(*)  AS proposals
FROM <app-catalog>.<app-schema>.proposal_run r
LATERAL VIEW explode(from_json(
  r.agent_steps_json,
  'ARRAY<STRUCT<tool: STRING, arguments: MAP<STRING, STRING>, result: STRING>>'
)) t AS step
WHERE r.id = '<run-id>'
  AND step.tool = 'dry_run_check'
  AND get_json_object(step.result, '$.ok') = 'true'
GROUP BY 1
ORDER BY 1;
```

| `sql_source` | Meaning |
|---|---|
| `genie` | Genie's SQL passed the dry-run first time |
| `genie_retry` | Genie's SQL passed after one retry with a shape hint |
| `template` | Genie wasn't used (not configured, call failed, returned no SQL, or call cap reached) |
| `template_fallback` | Genie's SQL failed the dry-run twice, so the template was used |

These are counts for the whole run. The steps can't be matched to individual proposals,
because a table usually has several proposals and dry-runs run in parallel.

**D. See why Genie wasn't used.** Same query shape, reading the `ask_genie` steps:

```sql
SELECT
  get_json_object(step.result, '$.available') AS genie_answered,
  get_json_object(step.result, '$.error')     AS genie_error,
  COUNT(*)                                    AS calls
FROM <app-catalog>.<app-schema>.proposal_run r
LATERAL VIEW explode(from_json(
  r.agent_steps_json,
  'ARRAY<STRUCT<tool: STRING, arguments: MAP<STRING, STRING>, result: STRING>>'
)) t AS step
WHERE r.id = '<run-id>'
  AND step.tool = 'ask_genie'
GROUP BY 1, 2
ORDER BY calls DESC;
```

`genie_answered = false` rows carry the error (missing scope, permission denied, space not
found, timeout). `genie_answered = true` with no error means Genie replied; if those calls
still ended up as `template`, Genie answered without SQL.

---

## Troubleshooting: configured but still falling back

The code **silently** degrades to templates when a Genie call fails
(`backend/src/agent/tools/ask_genie.py` catches every exception → `available=False` →
the runner uses `fallback_sql`). Use query D from Step 4 to see the actual error.

| Symptom | Cause | Fix |
|---|---|---|
| `genie_configured: no` | Env var empty, or edits not synced/redeployed | Set `DQ_GENIE_SPACE_ID`, sync, redeploy (Step 3) |
| No `llm` badges; error mentions **scopes** / `genie` | OBO mode, and the user's token lacks the `genie` scope | Add the scope (Step 2, OBO mode); if the error persists, the user revokes and re-consents ([AUTHORIZATION.md](AUTHORIZATION.md#3-make-every-user-consent-again)) |
| No `llm` badges; **permission denied** on the space | The running identity lacks **CAN RUN** on the space | Grant CAN RUN to `<app-user-or-group>` (OBO) or the app's service principal (Step 2) |
| No `llm` badges; space **not found** | Stale demo space ID still in `app.yaml` | Replace with `<genie-space-id>`, sync, redeploy |
| Genie answers but proposals are still `template` | Space doesn't include the scanned tables, or has no warehouse | Add `<target-catalog>.<target-schema>` + a warehouse to the space |
| Mix of `llm` and `profile` on a large schema | Per-run Genie call cap reached (default 200, retries count) | Raise `DQ_GENIE_CALL_CAP` in `app.yaml`, or scan fewer tables per run |
| Slow runs, then templates | Genie message timed out (90s cap per call) | Attach a faster/warm warehouse to the space |

Note the retry logic: if Genie returns SQL with the wrong shape, the runner retries once
with an explicit column hint (`violations BIGINT, sample_ids ARRAY<STRING>`), and only
then falls back to the template — so some `template_fallback` results are legitimate
per-check fallbacks even when Genie is active.

---

## One-glance checklist

- [ ] Genie space created in `<workspace-url>`, covering `<target-catalog>.<target-schema>`
- [ ] `<warehouse-id>` attached to the space
- [ ] Auth mode chosen (`DQ_AUTH_MODE`, Step 2): SP mode → CAN RUN for the app's service principal; OBO mode → `genie` scope added and CAN RUN for `<app-user-or-group>`
- [ ] `<app-url>/api/health` → `effective_identity` is what you expect (`user` or `app_sp`)
- [ ] `DQ_GENIE_SPACE_ID = <genie-space-id>` set in `app.yaml` (demo ID removed)
- [ ] Tenant-specific values fixed (`<app-catalog>`/`<app-schema>`, `<alert-parent-path>`, `<mlflow-experiment-path>`)
- [ ] `<warehouse-id>` bound to the app as resource key `warehouse`
- [ ] Edits synced to `<source-code-path>` and redeployed with `<cli-profile>`
- [ ] `<app-url>/api/health` → `genie_configured: yes`
- [ ] Test run shows `llm` badges, or `genie` / `genie_retry` in the Step 4 count query
