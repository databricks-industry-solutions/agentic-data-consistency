# Authorization: identities, app scopes, and grants

The DQ Proposal Agent can act as one of two identities. Choosing between them, and
setting up the matching scopes and grants, decides whether Genie, alerts, and reports
work. This guide covers:

- [The two identities](#the-two-identities)
- [Choosing a mode](#choosing-a-mode-obo-or-sp)
- [Setting up OBO (user authorization)](#setting-up-obo-user-authorization)
- [Setting up SP (app authorization)](#setting-up-sp-app-authorization)
- [Switching OBO ↔ SP](#switching-between-obo-and-sp)
- [Verifying which identity is in use](#verifying-which-identity-is-in-use)
- [Troubleshooting](#troubleshooting)

Genie space setup is covered in [ACTIVATING_GENIE.md](ACTIVATING_GENIE.md).

---

## The two identities

| | **OBO**: on behalf of the user | **SP**: app service principal |
|---|---|---|
| Who Databricks sees | The person signed in to the app | The app's own service principal (`app-xxxx <app-name>`) |
| Token source | `x-forwarded-access-token` header, sent by Databricks Apps **only when user-authorization scopes are configured** | `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`, injected by the Apps runtime |
| What limits it | The user's own UC and workspace permissions, **and** the app's scopes | The grants given to the SP |
| Row filters / column masks | Applied per user | Applied as the SP: every user sees the same data |
| Typical use | Customers who need per-user data governance | Demos and single-team deployments |

### How the code picks one

`DQ_AUTH_MODE` (in `backend/app.yaml`) controls `obo_token_from_request` in
`backend/src/auth.py`. When it returns no user token, `credential_source` picks the next
credential: the app SP in Databricks Apps, or `DQ_DATABRICKS_TOKEN` / `DQ_DATABRICKS_PROFILE`
when you run locally.

| `DQ_AUTH_MODE` | Behaviour |
|---|---|
| `obo` (default) | Uses the forwarded user token if one arrives (or a caller's `Authorization: Bearer` token, which is mainly for local `curl` testing). **If no token arrives, falls back to the SP**, so an app with no user scopes runs as the SP even in this mode. |
| `sp` | Ignores any forwarded token. Every request acts as the SP. |

Some operations always use the SP, whatever the mode:

| Operation | Identity | Where |
|---|---|---|
| Startup: create state tables, mark stale runs as failed | SP | `main.py` lifespan |
| Creating the DBSQL alert on **Accept** | SP (the requesting user is added as an email subscriber) | `routes/decisions.py` |
| MLflow trace logging | SP (runtime env credentials) | `mlflow_setup.py` |
| Everything else: catalog browsing, scans, **Genie calls**, dry-runs, state reads/writes, reports, findings refresh | **Depends on `DQ_AUTH_MODE`** | all other `routes/*` |

As a result, **the SP always needs grants on the state schema and the alert folder**,
even in OBO mode.

### Which identity is calling Genie?

Every Genie call runs as the identity that started the scan. A failed call is recorded
in the run's `ask_genie` steps. The `Config:` part of that error shows which credentials
were used:

- `token=***`: a forwarded or bearer token, so OBO.
- `client_id=…, client_secret=***`: the app SP.

---

## Choosing a mode: OBO or SP

| Question | If **yes** → |
|---|---|
| Must users only see proposals for data *they* can read (row filters, column masks, per-user grants)? | OBO |
| Is this a demo, POC, or single-team tool where one shared access level is fine? | SP |
| Is a workspace admin unable or unwilling to allow the `genie` scope for apps? | SP |
| Do you want the fewest moving parts (no consent prompts, one identity to grant)? | SP |

You can switch later without code changes. See [Switching between OBO and SP](#switching-between-obo-and-sp).

---

## Setting up OBO (user authorization)

### 1. Add user-authorization scopes to the app

Scopes are set **on the app**, not in `app.yaml`.

**UI:** Compute → Apps → `<app-name>` → **Edit** → **User authorization** →
**+ Add scope**, then save. You can check the result on the app's **Authorization** tab.

**CLI:**

```bash
databricks apps create-update <app-name> -p <cli-profile> --json '{
  "update_mask": "user_api_scopes",
  "app": {
    "user_api_scopes": [
      "sql",
      "genie",
      "catalog.catalogs:read",
      "catalog.schemas:read",
      "catalog.tables:read",
      "files"
    ]
  }
}'
```

**Bundle:** set `user_api_scopes:` under `resources.apps.<app-name>` in `databricks.yml`.

Scopes the agent needs:

| Scope | Used for | Symptom if missing |
|---|---|---|
| `sql` | Scans, dry-runs, state tables (`/api/2.0/sql/statements`) | Nothing works |
| **`genie`** | Calls to `/api/2.0/genie/spaces/...` | **Every proposal falls back to the templates.** `ask_genie` error: `Provided OAuth token does not have required scopes: genie` |
| `catalog.catalogs:read` | Catalog picker | Catalog dropdown fails |
| `catalog.schemas:read` | Schema picker | Schema dropdown fails |
| `catalog.tables:read` | Table picker, table schema lookups, Lakehouse Monitor lookup | Table list and schema discovery fail |
| `files` | Writing, reading, and deleting run reports in `/Volumes/<app-catalog>/<app-schema>/reports` | Reports fail to save or load |

`iam.access-control:read` and `iam.current-user:read` are added automatically.

`dashboards.genie` is the **deprecated** name for `genie`. Use `genie`.

> **Admin allow-list.** A workspace admin can limit the scopes apps may request:
> Settings → Development → Apps → *Restrict OAuth scopes for apps to selected values*.
> If `genie` isn't allowed, an admin must add it. Check from the CLI with
> `databricks workspace-settings-v2 get-public-workspace-setting allowedAppsUserApiScopes -p <cli-profile>`.

### 2. Set the mode and redeploy

In `backend/app.yaml`:

```yaml
  - name: DQ_AUTH_MODE
    value: "obo"
```

Redeploy, or at least restart, so the new scopes and env take effect:

```bash
databricks apps deploy <app-name> --source-code-path <workspace-source-path> -p <cli-profile>
```

Confirm the running app has the scopes:

```bash
databricks apps get <app-name> -p <cli-profile> -o json \
  | jq '{user_api_scopes, effective_user_api_scopes, oauth2_app_client_id}'
```

`effective_user_api_scopes` must include `genie`.

### 3. Make every user consent again

Users consent once per app **and set of scopes**. Anyone who opened the app before
`genie` was added may still hold a token without it. That's the most common cause of the
`required scopes: genie` error.

Each user revokes their old consent, using the app's `oauth2_app_client_id` from the
command above (also shown as *OAuth2 App Client ID* on the Authorization tab):

```bash
# view what was consented
databricks api get    /api/2.0/oauth-app-integrations/<oauth2-app-client-id>/user-consent/me -p <their-profile>
# revoke it
databricks api delete /api/2.0/oauth-app-integrations/<oauth2-app-client-id>/user-consent/me -p <their-profile>
```

They then sign out, reopen the app, and accept the new consent prompt, which should list Genie.

A private browser window **doesn't** reset consent, because consent is stored on the
Databricks side. Revoking also doesn't cancel tokens already issued; those can stay valid
for up to an hour.

### 4. Grant permissions to the users

Grant to a **group** that contains everyone who runs scans.

```sql
-- Data the agent scans
GRANT USE CATALOG ON CATALOG <target-catalog>                              TO `<users-group>`;
GRANT USE SCHEMA, SELECT ON SCHEMA <target-catalog>.<target-schema>        TO `<users-group>`;

-- App state (runs/proposals/findings tables + reports volume)
GRANT USE CATALOG ON CATALOG <app-catalog>                                 TO `<users-group>`;
GRANT USE SCHEMA, SELECT, MODIFY, READ VOLUME, WRITE VOLUME
  ON SCHEMA <app-catalog>.<app-schema>                                     TO `<users-group>`;

-- Optional: Lakehouse Monitor profile reuse
GRANT USE SCHEMA, SELECT ON SCHEMA <target-catalog>.<target-schema>_monitoring TO `<users-group>`;

-- Optional: column/table lineage (cross-system checks)
GRANT USE CATALOG ON CATALOG system TO `<users-group>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO `<users-group>`;
```

Non-UC permissions:

| Object | Permission | Where |
|---|---|---|
| The app | **Can Use** | App → Permissions |
| App warehouse (`DQ_DATABRICKS_WAREHOUSE_ID`) | **Can Use** | SQL Warehouses → Permissions |
| Genie space (`DQ_GENIE_SPACE_ID`) | **Can Run** | Genie space → Share |
| Warehouse attached to the Genie space, if different | **Can Use** | SQL Warehouses → Permissions |

### 5. Grant the SP what it always needs

Even in OBO mode, the SP does startup and alert creation. Run the grants in
[Setting up SP → step 2](#2-grant-permissions-to-the-sp), at least the **state schema**
and **alert folder** parts.

---

## Setting up SP (app authorization)

### 1. Set the mode and redeploy

```yaml
  - name: DQ_AUTH_MODE
    value: "sp"
```

You can also remove the user-authorization scopes from the app (Edit →
User authorization), but `DQ_AUTH_MODE=sp` alone is enough: forwarded tokens are ignored.

Redeploy:

```bash
databricks apps deploy <app-name> --source-code-path <workspace-source-path> -p <cli-profile>
```

### 2. Grant permissions to the SP

Get the SP's application ID:

```bash
databricks apps get <app-name> -p <cli-profile> -o json | jq -r .service_principal_client_id
```

UC grants (use the application ID, in backticks, as the principal):

```sql
-- Data the agent scans
GRANT USE CATALOG ON CATALOG <target-catalog>                              TO `<app-sp-client-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA <target-catalog>.<target-schema>        TO `<app-sp-client-id>`;

-- App state: the app creates its tables and reports volume on startup
GRANT USE CATALOG ON CATALOG <app-catalog>                                 TO `<app-sp-client-id>`;
GRANT USE SCHEMA, CREATE TABLE, CREATE VOLUME, SELECT, MODIFY, READ VOLUME, WRITE VOLUME
  ON SCHEMA <app-catalog>.<app-schema>                                     TO `<app-sp-client-id>`;

-- Optional: Lakehouse Monitor profile reuse
GRANT USE SCHEMA, SELECT ON SCHEMA <target-catalog>.<target-schema>_monitoring TO `<app-sp-client-id>`;

-- Optional: lineage
GRANT USE CATALOG ON CATALOG system TO `<app-sp-client-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO `<app-sp-client-id>`;
```

Non-UC permissions:

| Object | Permission | How |
|---|---|---|
| App warehouse | **Can Use** | Granted automatically when the warehouse is bound to the app as resource `warehouse` (App → Edit → App resources) |
| Genie space | **Can Run** | Genie space → Share → add the SP by name (`app-xxxx <app-name>`) |
| Warehouse attached to the Genie space, if different | **Can Use** | SQL Warehouses → Permissions → add the SP |
| `DQ_ALERT_PARENT_PATH` folder | **Can Manage** (or own it) | Default: the SP's home, `/Workspace/Users/<app-sp-client-id>` |
| `DQ_MLFLOW_EXPERIMENT_NAME` path | **Can Manage** (or own it) | Default: under the SP's home |

Users still need **Can Use** on the app itself.

---

## Switching between OBO and SP

You don't need to change any code. The switch is one env var plus the matching grants.

### SP → OBO

1. Add the user-authorization scopes listed in [OBO step 1](#1-add-user-authorization-scopes-to-the-app).
2. Set `DQ_AUTH_MODE: "obo"` in `backend/app.yaml`.
3. Redeploy.
4. Grant the **users' group** the permissions in [OBO step 4](#4-grant-permissions-to-the-users), especially **Can Run** on the Genie space.
5. Have users reopen the app and accept the consent prompt. If they had consented before, revoke first, as in [OBO step 3](#3-make-every-user-consent-again).
6. Keep the SP's state-schema and alert-folder grants, which are still used for startup and alerts.
7. Verify: `/api/health` shows `"effective_identity": "user"`.

### OBO → SP

1. Set `DQ_AUTH_MODE: "sp"` in `backend/app.yaml`.
2. Redeploy.
3. Grant the **SP** everything in [SP step 2](#2-grant-permissions-to-the-sp), especially **Can Run** on the Genie space and `SELECT` on the target schema.
4. Optional: remove the user-authorization scopes from the app. The code ignores forwarded tokens in this mode either way.
5. Verify: `/api/health` shows `"effective_identity": "app_sp"`.

Existing runs, proposals, and alerts aren't affected by switching.

---

## Verifying which identity is in use

Open `https://<app-url>/api/health` in a browser while signed in to the app:

```json
{
  "status": "ok",
  "genie_configured": "yes",
  "auth_mode": "obo",
  "effective_identity": "user"
}
```

| `auth_mode` | `effective_identity` | Meaning |
|---|---|---|
| `obo` | `user` | OBO is working. Scans and Genie calls run as you. |
| `obo` | `app_sp` | OBO is configured but no user token is arriving, usually because the app has no user-authorization scopes. Requests run as the SP. |
| `sp` | `app_sp` | SP mode, as intended. |
| any | `pat`, `profile`, `sdk_default` | Local development: requests run as `DQ_DATABRICKS_TOKEN`, `DQ_DATABRICKS_PROFILE`, or the SDK's default credentials. |

`effective_identity` comes from the same function that builds the client
(`credential_source` in `backend/src/auth.py`), so it always matches what requests use.

Also useful:

- **Genie Monitor.** Conversations from the agent are titled *"In the `<catalog>.<schema>` schema, find rows where…"* and appear under the identity that ran the scan. Show all users to see SP-owned ones.
- **App logs** (app → Logs). Every Accept or Deny logs `auth_mode=… identity=…` and
  whether a forwarded token arrived. The alert itself is always created as the SP.

---

## Troubleshooting

| `ask_genie` error / symptom | Cause | Fix |
|---|---|---|
| `Provided OAuth token does not have required scopes: genie` | OBO token without the `genie` scope | Add `genie` → redeploy → users revoke consent and consent again ([OBO step 3](#3-make-every-user-consent-again)). Or switch to SP. |
| `PermissionDenied` / `does not have permission` on the space | The calling identity lacks **Can Run** | Share the space with the users' group (OBO) or the SP (SP mode) |
| `RESOURCE_DOES_NOT_EXIST` / 404 | `DQ_GENIE_SPACE_ID` is wrong or from another workspace | Fix `app.yaml` and redeploy |
| `Genie message failed: …` | Genie can't run SQL: no warehouse on the space, no **Can Use** on it, or no `SELECT` on the tables | Attach a warehouse and grant **Can Use** and `SELECT` to the calling identity |
| `timed out after 90s` | Cold or slow warehouse on the space | Use a warm or serverless warehouse |
| `available: true` but no SQL, proposal shows `template` | Genie answered in text only (clarifying question) | Add the scanned tables and instructions to the space. See [ACTIVATING_GENIE.md](ACTIVATING_GENIE.md#step-1--create-a-genie-space) |
| Catalog or schema dropdown empty (OBO) | Missing `catalog.*:read` scopes | Add the scopes, redeploy, consent again |
| Report won't save or load (OBO) | Missing `files` scope or `WRITE VOLUME` | Add the scope or the grant |
| Accept fails creating the alert | The SP can't write to `DQ_ALERT_PARENT_PATH` or can't use the warehouse | Grant the SP on that folder and warehouse (needed in both modes) |
