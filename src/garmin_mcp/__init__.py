import base64
import datetime
import logging
import os
import sys

import anyio
import uvicorn
from garminconnect import Garmin, GarminConnectAuthenticationError
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse

from garmin_mcp import (
    activity_management,
    challenges,
    data_management,
    devices,
    gear_management,
    health_wellness,
    nutrition,
    training,
    user_profile,
    weight_management,
    womens_health,
    workout_templates,
    workouts,
)
from garmin_mcp.github_oauth_provider import GitHubOAuthProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

is_cn = os.getenv("GARMIN_IS_CN", "false").lower() == "true"

SERVER_URL = os.getenv("SERVER_URL", "")

_MODULES = [
    activity_management,
    challenges,
    data_management,
    devices,
    gear_management,
    health_wellness,
    nutrition,
    training,
    user_profile,
    weight_management,
    womens_health,
    workouts,
]


def _build_app() -> tuple[FastMCP, GitHubOAuthProvider]:
    github_client_id = os.getenv("GITHUB_CLIENT_ID", "")
    github_client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")

    if not github_client_id or not github_client_secret:
        logger.error("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set.")
        sys.exit(1)

    if not SERVER_URL:
        logger.error("SERVER_URL must be set to the public base URL of this service.")
        sys.exit(1)

    oauth_provider = GitHubOAuthProvider(
        github_client_id=github_client_id,
        github_client_secret=github_client_secret,
        server_url=SERVER_URL,
    )

    auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl(SERVER_URL),
        resource_server_url=AnyHttpUrl(SERVER_URL),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp"],
            default_scopes=["mcp"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )

    mcp_app = FastMCP(
        "Garmin Connect MCP",
        auth_server_provider=oauth_provider,
        auth=auth_settings,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
    )

    # GitHub redirects here after user login
    @mcp_app.custom_route("/auth/callback", methods=["GET"])
    async def github_callback(request: Request):
        return await oauth_provider.handle_github_callback(request)

    @mcp_app.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp_app, oauth_provider


def _log_token_expiry(garmin: Garmin) -> None:
    try:
        auth = getattr(garmin, 'garth', None) or getattr(garmin, 'client', None)
        # Some versions nest the oauth2 token under auth.garth
        if auth is not None and not hasattr(auth, 'oauth2_token'):
            auth = getattr(auth, 'garth', auth)
        token = auth.oauth2_token
        expires_at = datetime.datetime.fromtimestamp(
            token.refresh_token_expires_at, tz=datetime.timezone.utc
        )
        days_left = (expires_at - datetime.datetime.now(datetime.timezone.utc)).days
        if token.refresh_expired:
            logger.error("Garmin refresh token has EXPIRED — all API calls will fail. Regenerate GARMINTOKENS_BASE64.")
        elif days_left <= 14:
            logger.warning("Garmin refresh token expires in %d day(s) on %s — regenerate GARMINTOKENS_BASE64 soon.", days_left, expires_at.date())
        else:
            logger.info("Garmin refresh token valid until %s (%d days).", expires_at.date(), days_left)
    except Exception:
        pass


def init_api() -> Garmin:
    b64 = os.getenv("GARMINTOKENS_BASE64")
    if b64:
        logger.info("Trying to login to Garmin Connect using token from environment...")
        token_json = base64.b64decode(b64).decode("utf-8")
        garmin = Garmin(is_cn=is_cn)
        try:
            garmin.login(token_json)
        except GarminConnectAuthenticationError as e:
            logger.error(
                "Garmin token is expired or invalid (%s). "
                "Regenerate GARMINTOKENS_BASE64 using: garmin-mcp-auth",
                e,
            )
            sys.exit(1)
        _log_token_expiry(garmin)
        logger.info("Login successful using GARMINTOKENS_BASE64.")
        return garmin

    local = os.path.expanduser("~/.garminconnect")
    if os.path.isdir(local):
        logger.info("Using local token files from %s", local)
        garmin = Garmin(is_cn=is_cn)
        try:
            garmin.login(local)
        except GarminConnectAuthenticationError as e:
            logger.error(
                "Garmin token is expired or invalid (%s). "
                "Regenerate tokens using: garmin-mcp-auth",
                e,
            )
            sys.exit(1)
        _log_token_expiry(garmin)
        logger.info("Garmin Connect client initialized successfully.")
        return garmin

    logger.error(
        "No Garmin credentials found. "
        "Set GARMINTOKENS_BASE64 in Render Environment Variables."
    )
    sys.exit(1)


async def _serve(mcp_app: FastMCP) -> None:
    starlette_app = mcp_app.streamable_http_app()
    config = uvicorn.Config(
        starlette_app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        log_level="info",
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    garmin = init_api()
    mcp_app, _ = _build_app()

    for module in _MODULES:
        module.configure(garmin)
        module.register_tools(mcp_app)

    workout_templates.register_resources(mcp_app)

    logger.info("GitHub OAuth enabled — only '%s' can authenticate.", os.getenv("GITHUB_ALLOWED_USER", "(not configured)"))
    anyio.run(_serve, mcp_app)
