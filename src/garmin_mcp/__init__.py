import base64
import datetime
import logging
import os
import sys

import anyio
import requests
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

    @mcp_app.custom_route("/auth/callback", methods=["GET"])
    async def github_callback(request: Request):
        return await oauth_provider.handle_github_callback(request)

    @mcp_app.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp_app, oauth_provider


def init_api() -> Garmin:
    """Initialize Garmin client using the official Personal API Key."""
    api_key = os.getenv("GARMIN_PERSONAL_API_KEY")
    
    if not api_key:
        logger.error(
            "No GARMIN_PERSONAL_API_KEY found in Environment Variables. "
            "Please add GARMIN_PERSONAL_API_KEY to Render."
        )
        sys.exit(1)

    logger.info("Authenticating with Garmin Connect using Personal API Key...")
    
    try:
        token_url = "https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token"
        headers = {
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(token_url, headers=headers)
        response.raise_for_status()
        token_data = response.json()

        garmin = Garmin(is_cn=is_cn)
        auth = getattr(garmin, 'garth', None) or getattr(garmin, 'client', None)
        if auth is not None:
            auth.oauth2_token = token_data
        else:
            garmin.login(token_data)

        logger.info("Garmin Connect client initialized successfully via Personal API.")
        return garmin

    except Exception as e:
        logger.error(
            "Failed to authenticate with Garmin Personal API (%s). "
            "Check if your GARMIN_PERSONAL_API_KEY is valid.",
            e,
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
    """Main entry point for the Garmin MCP server."""
    garmin = init_api()
    mcp_app, _ = _build_app()

    for module in _MODULES:
        module.configure(garmin)
        module.register_tools(mcp_app)

    workout_templates.register_resources(mcp_app)

    logger.info("GitHub OAuth enabled — only '%s' can authenticate.", os.getenv("GITHUB_ALLOWED_USER", "(not configured)"))
    anyio.run(_serve, mcp_app)
