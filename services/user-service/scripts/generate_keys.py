"""
generate_keys.py — generate RSA-2048 key pair for JWT signing.

Usage:
    python scripts/generate_keys.py

Writes:
    keys/private.pem  (keep secret — never commit)
    keys/public.pem   (safe to share with other services)
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    keys_dir = Path(__file__).parent.parent / "keys"
    keys_dir.mkdir(exist_ok=True)

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = keys_dir / "private.pem"
    pub_path = keys_dir / "public.pem"

    priv_path.write_bytes(private_pem)
    # Restrict permissions on Unix
    try:
        os.chmod(priv_path, 0o600)
    except NotImplementedError:
        pass

    pub_path.write_bytes(public_pem)

    print(f"RSA key pair written to {keys_dir}/")
    print(f"  private: {priv_path}")
    print(f"  public : {pub_path}")
    print()
    print("Add keys/ to .gitignore to avoid committing the private key!")


if __name__ == "__main__":
    main()
