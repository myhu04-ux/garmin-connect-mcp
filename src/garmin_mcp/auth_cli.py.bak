"""Pre-authentication CLI tool for Garmin MCP server using Personal API Key.

This tool authenticates with Garmin Connect using a Personal API Key
instead of email/password/MFA, making it stable for cloud environments like Render.
"""

import argparse
import os
import sys
import requests

from garmin_mcp.token_utils import (
    get_token_path,
    get_token_base64_path,
    token_exists,
    validate_tokens,
    get_token_info,
)


def authenticate_with_personal_api(token_path: str, token_base64_path: str, force_reauth: bool = False, is_cn: bool = False) -> bool:
    """Authenticate with Garmin Connect using Personal API Key and save tokens."""
    
    # Check if tokens already exist and are valid
    if not force_reauth and token_exists(token_path):
        print(f"\nChecking existing tokens in '{token_path}'...")
        is_valid, error_msg = validate_tokens(token_path, is_cn=is_cn)
        if is_valid:
            print("✓ Existing tokens are valid. Authentication not needed.")
            return True
        else:
            print(f"✗ Existing tokens are invalid: {error_msg}")

    # Hent Personal API-nøglen fra miljøvariabler
    api_key = os.environ.get("GARMIN_PERSONAL_API_KEY")
    if not api_key:
        print("\n✗ Error: GARMIN_PERSONAL_API_KEY environment variable is missing.", file=sys.stderr)
        print("  Please set GARMIN_PERSONAL_API_KEY in your Render environment variables.", file=sys.stderr)
        return False

    print("\nAuthenticating with Garmin Connect using Personal API Key...")

    try:
        # Kald Garmins officielle Personal API endpoint for at veksle nøglen til et token
        token_url = "https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token"
        headers = {
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(token_url, headers=headers)
        response.raise_for_status()
        token_data = response.json()

        # Garmin Personal API returnerer et token-sæt. Vi sørger for at gemme det 
        # i det format, som din MCP ellers forventer (via garth / token_utils).
        from garth import Client
        garth_client = Client(domain="garmin.com" if not is_cn else "garmin.cn")
        
        # Sæt token data ind i garth client og gem det
        # (Tilpas afhængigt af nøgle-navnene i Garmins API-respons, typisk 'access_token'/'refresh_token')
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        
        if not access_token:
            print(f"\n✗ Error: Could not retrieve access token from Garmin API response: {token_data}", file=sys.stderr)
            return False

        # Gem tokens til disk/filsti
        os.makedirs(token_path, exist_ok=True)
        
        # Brug Garth til at gemme sessionen korrekt
        garth_client.oauth2_token = token_data
        garth_client.dump(token_path)
        print(f"\n✓ OAuth tokens saved to: {os.path.expanduser(token_path)}")

        # Gem base64 version hvis mcp'en bruger det
        token_base64 = garth_client.dumps()
        expanded_base64_path = os.path.expanduser(token_base64_path)
        with open(expanded_base64_path, "w") as token_file:
            token_file.write(token_base64)
        print(f"✓ OAuth tokens (base64) saved to: {expanded_base64_path}")

        print("\n" + "=" * 60)
        print("SUCCESS: Authenticated via Garmin Personal API!")
        print("=" * 60)
        return True

    except requests.exceptions.HTTPError as e:
        print(f"\n✗ Network/API error: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"  Response: {e.response.text}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"\n✗ Authentication error: {e}", file=sys.stderr)
        return False


def verify_tokens(token_path: str) -> bool:
    """Verify existing tokens are valid."""
    print(f"\nVerifying tokens in '{token_path}'...")
    info = get_token_info(token_path)
    if not info["exists"]:
        print(f"✗ Tokens not found at: {info['expanded_path']}")
        return False
    if info["valid"]:
        print(f"✓ Tokens are valid!")
        return True
    else:
        print(f"✗ Tokens are invalid: {info['error']}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Authenticate Garmin MCP using Personal API Key")
    parser.add_argument("--token-path", type=str, default=None)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--force-reauth", action="store_true")
    parser.add_argument("--is-cn", action="store_true", default=None)

    args = parser.parse_args()

    token_path = args.token_path or get_token_path()
    token_base64_path = get_token_base64_path()

    if args.is_cn:
        is_cn = True
    else:
        is_cn = os.getenv("GARMIN_IS_CN", "false").lower() in ("true", "1", "yes")

    print("\n" + "=" * 60)
    print("Garmin MCP Personal API Authentication Tool")
    print("=" * 60)

    if args.verify:
        success = verify_tokens(token_path)
        sys.exit(0 if success else 1)

    success = authenticate_with_personal_api(token_path, token_base64_path, args.force_reauth, is_cn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
