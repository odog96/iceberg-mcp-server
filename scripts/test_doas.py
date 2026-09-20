"""B1: prove the machine-user + doAs impersonation pattern against Impala.

Run from the repo root with connection details in the environment (or in
impala_details.txt (KEY=VALUE or key: value lines; gitignored)):

    IMPALA_HOST, IMPALA_PORT (443), IMPALA_USER (machine user),
    IMPALA_PASSWORD_B64 or IMPALA_PASSWORD, IMPALA_DATABASE (default)

    python scripts/test_doas.py --allowed ozarate,jgaragorry,fcobo,jcaseiro \\
        --denied someone_not_in_allowlist --explore

Checks:
  (0) the machine user can open a plain connection (no doAs) -- separates
      "bad host/password" from "doAs problem"
  (a) each --allowed user connects and `SELECT effective_user()` returns that user
  (b) each --denied user (not in authorized_proxy_user_config) is rejected
  (c) --explore: as each allowed user, list databases and tables, and count rows
      in a few tables, so you can see what data (and Ranger access) exists
  (d) manual: the Impala query log / Cloudera audit must show the allowed users,
      not the machine user
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iceberg_mcp_server.tools import impala_tools  # noqa: E402
from iceberg_mcp_server.tools.impala_tools import get_db_connection  # noqa: E402

SYSTEM_DATABASES = {"information_schema", "sys", "_impala_builtins"}


# Accepted spellings in the details file -> the env var the server code reads.
KEY_ALIASES = {
    "impala_host": "IMPALA_HOST",
    "impala_port": "IMPALA_PORT",
    "impala_username": "IMPALA_USER",
    "impala_user": "IMPALA_USER",
    "impala_password": "IMPALA_PASSWORD",
    "impala_password_b64": "IMPALA_PASSWORD_B64",
    "impala_database": "IMPALA_DATABASE",
}


def load_details_file(path: Path) -> bool:
    """Load `KEY=value` or `key: value` lines into os.environ (existing env wins)."""
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = min((i for i in (line.find("="), line.find(":")) if i > 0), default=-1)
        if sep < 0:
            continue
        key, value = line[:sep].strip(), line[sep + 1 :].strip()
        os.environ.setdefault(KEY_ALIASES.get(key.lower(), key), value)
    return True


def query(conn, sql: str) -> list:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return cur.fetchall()
    finally:
        cur.close()


def plain_connection_check() -> bool:
    """Connect as the machine user with no doAs."""
    original = impala_tools.connect
    try:
        # get_db_connection always appends ?doAs=..., so strip it for this one check
        def no_doas(**kw):
            kw["http_path"] = kw["http_path"].split("?", 1)[0]
            return original(**kw)

        impala_tools.connect = no_doas
        conn = get_db_connection("placeholder")
        try:
            rows = query(conn, "SELECT effective_user()")
        finally:
            conn.close()
        print(f"[PASS] (0) plain machine-user connection works: effective_user()={rows[0][0]!r}")
        return True
    except Exception as e:
        print(f"[FAIL] (0) plain machine-user connection failed (check host/port/user/password): {e}")
        return False
    finally:
        impala_tools.connect = original


def check_allowed(user: str) -> bool:
    try:
        conn = get_db_connection(user)
        try:
            effective, actual = query(conn, "SELECT effective_user(), user()")[0]
        finally:
            conn.close()
        ok = effective == user
        print(f"[{'PASS' if ok else 'FAIL'}] (a) {user}: effective_user()={effective!r} user()={actual!r}")
        return ok
    except Exception as e:
        print(f"[FAIL] (a) {user}: could not connect: {str(e)[:300]}")
        return False


def check_denied(user: str) -> bool:
    try:
        conn = get_db_connection(user)
        try:
            rows = query(conn, "SELECT effective_user()")
        finally:
            conn.close()
        print(f"[FAIL] (b) {user} was NOT rejected; got {rows}")
        return False
    except Exception as e:
        print(f"[PASS] (b) {user} rejected: {str(e)[:200]}")
        return True


def explore(user: str, max_tables: int) -> dict:
    """Return {table: rowcount or error} visible to this user; print a summary."""
    seen = {}
    try:
        conn = get_db_connection(user)
    except Exception as e:
        print(f"[----] (c) {user}: cannot connect: {str(e)[:200]}")
        return seen
    try:
        databases = [r[0] for r in query(conn, "SHOW DATABASES") if r[0] not in SYSTEM_DATABASES]
        print(f"[INFO] (c) {user}: databases visible: {databases}")
        count = 0
        for db in databases:
            try:
                tables = [r[0] for r in query(conn, f"SHOW TABLES IN `{db}`")]
            except Exception as e:
                print(f"         {db}: cannot list tables: {str(e)[:120]}")
                continue
            print(f"         {db}: {len(tables)} tables")
            for t in tables:
                if count >= max_tables:
                    break
                count += 1
                try:
                    n = query(conn, f"SELECT COUNT(*) FROM `{db}`.`{t}`")[0][0]
                    seen[f"{db}.{t}"] = n
                    print(f"           {db}.{t}: {n} rows")
                except Exception as e:
                    seen[f"{db}.{t}"] = f"ERROR {str(e)[:100]}"
                    print(f"           {db}.{t}: ERROR {str(e)[:120]}")
    finally:
        conn.close()
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allowed", required=True, help="comma-separated users in authorized_proxy_user_config")
    parser.add_argument("--denied", default="", help="comma-separated users NOT in the allowlist; must be rejected")
    parser.add_argument("--explore", action="store_true", help="list databases/tables and row counts per allowed user")
    parser.add_argument("--max-tables", type=int, default=10, help="row-count at most this many tables per user")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    if not load_details_file(root / "impala_details.txt"):
        print("[note] no impala_details.txt found; using environment variables only")
    allowed = [u.strip() for u in args.allowed.split(",") if u.strip()]
    denied = [u.strip() for u in args.denied.split(",") if u.strip()]

    if not plain_connection_check():
        return 1

    failures = sum(not check_allowed(u) for u in allowed)
    failures += sum(not check_denied(u) for u in denied)

    if args.explore:
        results = {u: explore(u, args.max_tables) for u in allowed}
        with_data = {u for u, r in results.items() if any(isinstance(v, int) and v > 0 for v in r.values())}
        print()
        if with_data:
            print(f"[INFO] users who can see at least one non-empty table: {sorted(with_data)}")
            print(f"[INFO] users with no readable data (Ranger?): {sorted(set(allowed) - with_data)}")
        else:
            print("[WARN] no user can read any non-empty table: load data and grant Ranger select before MCP testing")

    print("[MANUAL] confirm the Impala query log / Cloudera audit shows these users, not the machine user")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
