"""POST /api/proposals/{id}/decision — accept or deny a proposal.
On accept, calls DBSQL Alerts v2 to create the alert.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import credential_source, obo_token_from_request, user_email_from_request, workspace_client_for_user
from ..clients.dbsql_alerts import DbsqlAlertsClient
from ..clients.sql_warehouse import SqlClient
from ..config import Settings, get_settings
from ..db.schema import Acceptance, DecisionRequest, Proposal, ProposalStatus
from ..db.store import Store, new_id
from ..findings import evaluate_proposal

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@router.get("/{proposal_id}", response_model=Proposal)
def get_proposal(proposal_id: str, request: Request, settings: Settings = Depends(get_settings)) -> Proposal:
    ws = workspace_client_for_user(obo_token_from_request(request))
    store = Store(SqlClient(ws, settings.databricks_warehouse_id), settings)
    p = store.get_proposal(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    return p


@router.post("/{proposal_id}/decision", response_model=Proposal)
def decide(
    proposal_id: str,
    body: DecisionRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Proposal:
    if body.decision not in {ProposalStatus.APPROVED, ProposalStatus.DENIED}:
        raise HTTPException(400, "decision must be APPROVED or DENIED")
    token = obo_token_from_request(request)
    email = user_email_from_request(request)
    print(
        f"[decision {proposal_id}] auth_mode={settings.auth_mode} "
        f"identity={credential_source(token)} "
        f"obo_token={'set' if token else 'MISSING'} "
        f"email={email or 'MISSING'} "
        f"x-forwarded-access-token={'set' if request.headers.get('x-forwarded-access-token') else 'MISSING'}",
        flush=True,
    )
    ws = workspace_client_for_user(token)
    sql = SqlClient(ws, settings.databricks_warehouse_id)
    store = Store(sql, settings)
    p = store.get_proposal(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p.status not in {ProposalStatus.IN_REVIEW, ProposalStatus.QUEUED}:
        raise HTTPException(409, f"Proposal is {p.status.value}; cannot decide again")

    if body.decision == ProposalStatus.APPROVED:
        subscribers = (body.alert_email_overrides or settings.default_subscribers_list or ([email] if email else []))
        if not subscribers:
            raise HTTPException(400, "No alert subscribers provided and none configured")
        cron = body.alert_schedule_cron or settings.default_alert_cron

        # Create the v2 alert as the app's service principal, whatever
        # DQ_AUTH_MODE is: the alert is owned by the app, lives under
        # DQ_ALERT_PARENT_PATH, and notifications reach the user via
        # `subscribers`. The reviewer's identity is kept on the proposal row.
        # (The rest of this request, e.g. evaluate_proposal below, uses `ws`,
        # i.e. the user in OBO mode; see auth.credential_source.)
        sp_ws = workspace_client_for_user(None)
        sp_home = settings.alert_parent_path  # e.g. /Workspace/Users/<sp-id>
        client = DbsqlAlertsClient(sp_ws, settings.databricks_warehouse_id, sp_home)

        display_name = (
            f"[dq-agent] {p.invariant_type.value} on {p.target_table_fqn}"
            f"({','.join(p.target_columns)})"
        )
        created = client.create_v2_alert(
            display_name=display_name,
            query_text=p.generated_sql,
            subscribers=subscribers,
            schedule_cron=cron,
        )
        store.insert_acceptance(Acceptance(
            id=new_id(),
            proposal_id=p.id,
            dbsql_alert_id=created.alert_id,
            email_subscribers=subscribers,
            schedule_cron=cron,
            created_at=datetime.now(timezone.utc),
        ))
        # Fire-on-create surrogate: v2 alerts has no public "run now" REST API,
        # so we execute the validation SQL ourselves and record the first
        # finding. The customer sees a result immediately rather than waiting
        # for the next scheduled tick.
        try:
            evaluate_proposal(
                sql=sql, store=store,
                proposal_id=p.id,
                invariant_type=p.invariant_type.value,
                target_table_fqn=p.target_table_fqn,
                target_columns=p.target_columns,
                generated_sql=p.generated_sql,
                alert_id=created.alert_id,
                source="agent_initial",
            )
        except Exception as e:  # noqa: BLE001
            print(f"[decide {p.id}] initial finding capture failed: {e!r}", flush=True)

    store.update_proposal_decision(
        proposal_id=p.id, status=body.decision, reviewer=email, comment=body.comment,
    )
    return store.get_proposal(p.id)
