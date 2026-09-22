"""Validate a saved real Entra token against our server's identity code, with no server or
CML deploy needed. Fast way to catch issuer/audience/claim mismatches before touching app1.

    python scripts/verify_real_token.py [--token-file .entra_token]

Reads ENTRA_TENANT_ID / ENTRA_AUDIENCE / ENTRA_ISSUER / USER_MAP from the environment (or
impala_details.txt-style overrides aren't used here; export them or edit the defaults below).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iceberg_mcp_server import identity  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", default=".entra_token")
    args = ap.parse_args()

    token = Path(args.token_file).read_text().strip()

    verifier = identity.build_verifier()
    if verifier is None:
        print("ENTRA_TENANT_ID / ENTRA_AUDIENCE not set in the environment; nothing to verify against.")
        return 1

    access_token = await verifier.verify_token(token)
    if access_token is None:
        print("[FAIL] Signature/issuer/audience/expiry check failed (server would return 401).")
        print("Check ENTRA_TENANT_ID, ENTRA_AUDIENCE, ENTRA_ISSUER against the token's iss/aud.")
        return 1

    print("[PASS] Token verified (signature, issuer, audience, expiry all OK).")
    print("Claims:", access_token.claims)

    try:
        user = identity.map_claims_to_user(access_token.claims)
        print(f"[PASS] Mapped to Cloudera user: {user!r}")
    except identity.IdentityError as e:
        print(f"[FAIL] Claim mapping rejected: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
