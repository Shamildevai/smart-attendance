"""Generate the Ed25519 key pair used to sign application updates.

The private key stays in secrets/ and must never be committed or shared.
Back it up offline: losing it prevents signing future updates for existing installs.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
PRIVATE_PATH = SECRETS / "update_signing_key.pem"
PUBLIC_PATH = SECRETS / "update_public_key.txt"


def main() -> int:
    SECRETS.mkdir(parents=True, exist_ok=True)
    if PRIVATE_PATH.exists():
        print(f"Refusing to overwrite existing key: {PRIVATE_PATH}")
        print("Remove it manually only if you intend to invalidate all previous update signatures.")
        return 1

    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    PRIVATE_PATH.write_bytes(private_bytes)
    PUBLIC_PATH.write_text(base64.b64encode(public_bytes).decode("ascii") + "\n", encoding="ascii")
    try:
        os.chmod(PRIVATE_PATH, 0o600)
    except OSError:
        pass

    print(f"Private key: {PRIVATE_PATH}")
    print(f"Public key:  {PUBLIC_PATH}")
    print("Public key (base64, safe to embed):")
    print(PUBLIC_PATH.read_text(encoding="ascii").strip())
    print("\nBack up the private key offline. It is excluded from git by .gitignore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
