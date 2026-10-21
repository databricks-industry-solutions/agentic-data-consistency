"""Auth helpers: workspace client construction with OBO / PAT / profile fallback."""
from __future__ import annotations

import os
from typing import Literal

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from fastapi import Request

from .config import get_settings


CredentialSource = Literal["user", "pat", "profile", "app_sp", "sdk_default"]


def credential_source(user_token: str | None = None) -> CredentialSource:
    """Which credential workspace_client_for_user(user_token) will use.

    Single source of truth for the priority below, so /api/health reports the
    identity requests actually run as.
      1. "user"    — user_token (OBO token from the request).
      2. "pat"     — settings.databricks_token (local dev only).
      3. "profile" — settings.databricks_profile, a ~/.databrickscfg profile (local dev).
      4. "app_sp"  — default SDK auth inside Databricks Apps, i.e. the app's service
                     principal (the runtime injects DATABRICKS_CLIENT_ID/SECRET).
         "sdk_default" — default SDK auth anywhere else (env vars, DEFAULT profile).
    """
    settings = get_settings()
    if user_token:
        return "user"
    if settings.databricks_token:
        return "pat"
    if settings.databricks_profile:
        return "profile"
    return "app_sp" if os.environ.get("DATABRICKS_CLIENT_ID") else "sdk_default"


def workspace_client_for_user(user_token: str | None = None) -> WorkspaceClient:
    """Build a WorkspaceClient for the credential chosen by credential_source().

    For a user token, force auth_type='pat' so the SDK doesn't pick up the
    DATABRICKS_CLIENT_ID/SECRET env vars the Databricks Apps runtime injects
    (which would trigger oauth-m2m / SP auth and silently override the token).
    """
    settings = get_settings()
    host = settings.databricks_host or os.environ.get("DATABRICKS_HOST") or None
    source = credential_source(user_token)
    if source == "user":
        return WorkspaceClient(config=Config(host=host, token=user_token, auth_type="pat"))
    if source == "pat":
        return WorkspaceClient(config=Config(host=host, token=settings.databricks_token, auth_type="pat"))
    if source == "profile":
        return WorkspaceClient(profile=settings.databricks_profile)
    return WorkspaceClient()


def obo_token_from_request(request: Request) -> str | None:
    """Extract the OBO token Databricks Apps forwards on every request.

    Returns None when DQ_AUTH_MODE=sp, so callers fall through to the next
    credential in credential_source() (the app SP in Databricks Apps).
    """
    if get_settings().auth_mode == "sp":
        return None
    forwarded = request.headers.get("x-forwarded-access-token")
    if forwarded:
        return forwarded
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    return None


def user_email_from_request(request: Request) -> str | None:
    """Databricks Apps forwards the caller's identity in X-Forwarded-Email."""
    return request.headers.get("X-Forwarded-Email") or request.headers.get("x-forwarded-email")
