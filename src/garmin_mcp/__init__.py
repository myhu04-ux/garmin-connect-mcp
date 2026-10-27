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
        # Kald Garmins officielle Personal API for at veksle nøglen til et token
        token_url = "https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token"
        headers = {
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json"
        }
        
        import requests
        response = requests.post(token_url, headers=headers)
        response.raise_for_status()
        token_data = response.json()

        # Initialiser Garmin-klienten og inject det nye token via Garth
        garmin = Garmin(is_cn=is_cn)
        
        # Sæt token direkte på den underliggende garth-klient
        auth = getattr(garmin, 'garth', None) or getattr(garmin, 'client', None)
        if auth is not None:
            auth.oauth2_token = token_data
        else:
            # Fallback hvis garmin-biblioteket håndterer det anderledes
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
