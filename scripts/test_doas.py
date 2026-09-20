"""B1: prove the machine-user + doAs impersonation pattern against Impala.

Run from the repo root with connection details in the environment (or in
impala_details.txt as KEY=VALUE lines, which is gitignored):

    IMPALA_HOST, IMPALA_PORT (443), IMPALA_USER (machine user),
    IMPALA_PASSWORD_B64 or IMPALA_PASSWORD, IMPALA_DATABASE (default)

    python scripts/test_doas.py --allowed ozarate --denied someone_not_in_allowlist

Checks:
  (a) --allowed user connects and `SELECT effective_user()` returns that user
  (b) --denied user (not in authorized_proxy_user_config) is rejected
  (c) look in the Impala query log / Cloudera audit and confirm the query is
      attributed to the allowed user, not the machine user (manual check)
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iceberg_mcp_server.tools.impala_tools import get_db_connection  # noqa: E402


def load_details_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def run_as(user: str) -> list:
    conn = get_db_connection(user)
    try:
        cur = conn.cursor()
        cur.execute("SELECT effective_user(), user()")
        return cur.fetchall()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allowed", required=True, help="user in the machine user's authorized_proxy_user_config")
    parser.add_argument("--denied", help="user NOT in the allowlist; connecting as them must fail")
    args = parser.parse_args()

    load_details_file(Path(__file__).resolve().parent.parent / "impala_details.txt")

    failures = 0

    try:
        rows = run_as(args.allowed)
        effective, actual = rows[0]
        ok = effective == args.allowed
        print(f"[{'PASS' if ok else 'FAIL'}] (a) allowed user: effective_user()={effective!r} user()={actual!r}")
        failures += not ok
    except Exception as e:
        print(f"[FAIL] (a) allowed user {args.allowed!r} could not connect: {e}")
        failures += 1

    if args.denied:
        try:
            rows = run_as(args.denied)
            print(f"[FAIL] (b) denied user {args.denied!r} was NOT rejected; got {rows}")
            failures += 1
        except Exception as e:
            print(f"[PASS] (b) denied user rejected: {str(e)[:200]}")

    print("[MANUAL] (c) confirm the Impala query log / Cloudera audit shows the allowed user, not the machine user")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
