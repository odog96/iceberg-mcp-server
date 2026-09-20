## Copyright (c) 2025 Cloudera, Inc. All Rights Reserved.
##
## This file is licensed under the Apache License Version 2.0 (the "License").
## You may not use this file except in compliance with the License.
## You may obtain a copy of the License at http:##www.apache.org/licenses/LICENSE-2.0.
##
## This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
## OF ANY KIND, either express or implied. Refer to the License for the specific
## permissions and limitations governing your use of the file.

import base64
import json
import os
import re
from urllib.parse import quote

from impala.dbapi import connect

# The effective user lands in a URL query string (http_path=...?doAs=<user>), so
# it is restricted to a strict character set and URL-encoded as well.
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


def validate_username(user) -> str:
    if not isinstance(user, str) or not USERNAME_RE.fullmatch(user):
        raise ValueError("Invalid Cloudera username")
    return user


def _get_password():
    # Some deployment environments route env var injection through a shell
    # layer that interpolates unescaped "$" in the value, silently truncating
    # passwords that contain it. IMPALA_PASSWORD_B64 sidesteps that entirely.
    b64 = os.getenv("IMPALA_PASSWORD_B64")
    if b64:
        return base64.b64decode(b64).decode()
    return os.getenv("IMPALA_PASSWORD", "password")


# Helper to get Impala connection details from env vars. The machine user
# authenticates; the connection asks Impala to run as `effective_user` (doAs),
# so Ranger and the audit log see the end user. There is deliberately no default
# for effective_user: a missing user must never fall back to the machine user.
def get_db_connection(effective_user: str):
    effective_user = validate_username(effective_user)
    host = os.getenv("IMPALA_HOST", "coordinator-default-impala.example.com")
    port = int(os.getenv("IMPALA_PORT", "443"))
    user = os.getenv("IMPALA_USER", "username")
    password = _get_password()
    database = os.getenv("IMPALA_DATABASE", "default")
    auth_mechanism = os.getenv("IMPALA_AUTH_MECHANISM", "LDAP")
    use_http_transport = os.getenv("IMPALA_USE_HTTP_TRANSPORT", "true")
    http_path = os.getenv("IMPALA_HTTP_PATH", "cliservice").split("?", 1)[0]
    http_path = f"{http_path}?doAs={quote(effective_user, safe='')}"
    use_ssl = os.getenv("IMPALA_USE_SSL", "true")

    return connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        auth_mechanism=auth_mechanism,
        use_http_transport=use_http_transport,
        http_path=http_path,
        use_ssl=use_ssl,
    )


READONLY_PREFIXES = {"select", "show", "describe", "with"}
# A WITH clause can front an INSERT in Impala, so write keywords are rejected
# anywhere in a WITH/SELECT statement. SHOW and DESCRIBE cannot front a write
# (and SHOW CREATE TABLE legitimately contains "create"), so they skip this check.
WRITE_KEYWORDS = {"insert", "upsert", "update", "delete", "create", "drop", "alter", "truncate", "grant", "revoke", "load", "merge"}

_TOKEN_RE = re.compile(
    r"""
    --[^\n]*            # line comment
    | /\*.*?\*/          # block comment
    | '(?:[^'\\]|\\.)*'  # single-quoted string
    | "(?:[^"\\]|\\.)*"  # double-quoted string
    | `[^`]*`            # quoted identifier
    """,
    re.VERBOSE | re.DOTALL,
)


def check_readonly(query: str):
    """Return an error message if the query is not a single read-only statement, else None.

    Defence in depth only: the real gate is Ranger applied to the impersonated user.
    """
    if not isinstance(query, str) or not query.strip():
        return "Empty query."
    # Blank out comments, strings and quoted identifiers so their contents cannot
    # hide (or fake) keywords and semicolons.
    stripped = _TOKEN_RE.sub(" ", query)
    if re.search(r"/\*|\*/|['\"`]", stripped):
        return "Unterminated comment, string or identifier."
    stripped = stripped.strip().rstrip(";").strip()
    if ";" in stripped:
        return "Only a single statement is allowed."
    words = re.findall(r"[a-z_]+", stripped.lower())
    if not words or words[0] not in READONLY_PREFIXES:
        return "Only read-only queries are allowed."
    if words[0] in {"select", "with"} and WRITE_KEYWORDS.intersection(words):
        return "Only read-only queries are allowed."
    return None


def execute_query(query: str, effective_user: str) -> str:
    conn = None

    error = check_readonly(query)
    if error:
        return error

    try:
        conn = get_db_connection(effective_user)
        cur = conn.cursor()
        cur.execute(query)
        if cur.description:
            rows = cur.fetchall()
            result = json.dumps(rows, default=str)
        else:
            conn.commit()
            result = "Query executed successfully."
        cur.close()
        return result
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_schema(effective_user: str) -> str:
    conn = None
    try:
        conn = get_db_connection(effective_user)
        cur = conn.cursor()
        cur.execute("SHOW TABLES")
        tables = cur.fetchall()
        schema = [table[0] for table in tables]
        return json.dumps(schema)
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
