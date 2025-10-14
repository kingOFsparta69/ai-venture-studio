import base64, hmac, hashlib, json, time, os
from typing import Optional, Tuple

def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def _unb64url(s: str) -> bytes:
    import base64
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)

def sign_license(payload: dict, secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return _b64url(body) + "." + _b64url(sig)

def verify_license(license_token: str, secret: str) -> Tuple[bool, Optional[dict], str]:
    try:
        body_b64, sig_b64 = license_token.split(".", 1)
        body = _unb64url(body_b64)
        expected = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64url(sig_b64)):
            return False, None, "invalid-signature"
        payload = json.loads(body.decode())
        if payload.get("exp") and time.time() > int(payload["exp"]):
            return False, payload, "expired"
        return True, payload, "ok"
    except Exception:
        return False, None, "malformed"

def plan_limits(plan: str) -> dict:
    if plan == "agency":
        return {"max_runs_per_day": 200, "max_ideas": 15, "allow_export": True}
    if plan == "pro":
        return {"max_runs_per_day": 20, "max_ideas": 12, "allow_export": True}
    return {"max_runs_per_day": 2, "max_ideas": 5, "allow_export": False}
