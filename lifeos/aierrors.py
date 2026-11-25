"""What went wrong with the assistant, in plain English, with what to do about it.

Claude and Gemini both fail in a handful of ordinary ways (no credit, a wrong key, a model name that doesn't exist,
a daily limit). The app, Check everything, the live test and the speakers all say the same plain sentence instead
of an error code.
"""
from __future__ import annotations


def _status(e: Exception) -> int:
    for attr in ("status_code", "code", "status"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(e, "response", None)
    return getattr(resp, "status_code", 0) or 0


def explain(e: Exception, provider: str = "") -> str:
    msg = str(e) or type(e).__name__
    low = msg.lower()
    name = type(e).__name__
    code = _status(e)
    gem = provider == "gemini" or "google" in type(e).__module__ or "genai" in type(e).__module__
    who = "Gemini" if gem else "Claude"
    if "credit balance" in low or "billing" in low and "anthropic" in low or "purchase credits" in low:
        return ("Your Claude account has no credit left. Add some at console.anthropic.com > Billing (and set a "
                "monthly limit there), or switch to Gemini in Settings > Assistant.")
    if gem and ("resource_exhausted" in low or code == 429) and ("per day" in low or "perday" in low or "limit: 0" in low
                                                                  or "free_tier" in low or "free tier" in low):
        if "limit: 0" in low:
            return ("That Gemini model isn't included in Google's free tier. Choose another model in Settings > Keys "
                    "and addresses (Gemini model), or turn on billing in Google AI Studio.")
        return ("Gemini's free daily allowance is used up. It starts again at 8am UK time (midnight in California), "
                "or turn on billing in Google AI Studio for more.")
    if code == 429 or "rate limit" in low or "rate_limit" in low or "overloaded" in low or code == 529:
        return f"{who} is busy or limiting requests just now. Try again in a minute."
    if code == 401 or "authentication" in low or "invalid x-api-key" in low or "api key not valid" in low \
            or "api_key_invalid" in low:
        where = "aistudio.google.com/apikey" if gem else "console.anthropic.com > API keys"
        return (f"The {who} API key isn't right. Make a new one at {where} and paste it into Settings > Keys and "
                "addresses.")
    if code == 403 or "permission" in low and "denied" in low:
        return f"{who} refused this key (permission denied). Check the key's account and its billing."
    if code == 404 or "not_found" in low or "not found" in low and "model" in low:
        return (f"{who} doesn't recognise the model name. In Settings > Keys and addresses, choose a model from the "
                "list (for Gemini), or update Life OS.")
    if "connect" in low or "timed out" in low or "timeout" in low or name in ("APIConnectionError", "ConnectError"):
        return f"Couldn't reach {who}. Check this computer's internet connection."
    if code == 400 or "badrequest" in name.lower() or "invalid_request" in low:
        detail = msg.split("'message': '")[-1].split("'")[0] if "'message': '" in msg else msg
        return f"{who} turned the request down: {detail[:160]}"
    if code >= 500:
        return f"{who} had a problem on its side. Try again in a few minutes."
    return f"{who} couldn't answer ({name}: {msg[:120]})."
