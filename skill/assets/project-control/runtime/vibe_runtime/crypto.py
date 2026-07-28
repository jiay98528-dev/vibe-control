from __future__ import annotations

import base64
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .common import ControlError, canonical_bytes


def signed_payload(value: dict[str, Any]) -> bytes:
    payload = {key: item for key, item in value.items() if key != "signature"}
    return canonical_bytes(payload)


def verify_signature(value: dict[str, Any], public_key_b64: str, check_id: str) -> None:
    signature = value.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "Ed25519":
        raise ControlError(check_id, "missing Ed25519 signature")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        public_key.verify(base64.b64decode(signature["value"], validate=True), signed_payload(value))
    except (ValueError, KeyError, InvalidSignature) as exc:
        raise ControlError(check_id, "invalid Ed25519 signature") from exc
