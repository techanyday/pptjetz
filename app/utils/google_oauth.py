"""Utility to build Google OAuth Credentials from the logged-in user's session.

Assumes `google_access_token` and `google_refresh_token` are stored in the Flask
session at login callback.  Uses the same Slides / Drive scopes required by the
Slides helper.
"""
from __future__ import annotations

from typing import List

from flask import current_app, session
from google.oauth2.credentials import Credentials  # type: ignore

SCOPES: List[str] = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]

def user_google_credentials() -> Credentials:  # pragma: no cover
    """Return an authorised ``google.oauth2.credentials.Credentials`` object for
    the currently logged-in user.

    Raises KeyError if the access / refresh token is missing from the session.
    """
    access_token = session["google_access_token"]
    refresh_token = session.get("google_refresh_token")
    import os  # local import to avoid circular during early app creation
    client_id = current_app.config.get("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_OAUTH_CLIENT_SECRET") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
