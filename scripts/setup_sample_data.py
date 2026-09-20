"""Load the MovieLens "small" dataset into Impala as a demo database.

Setup only: this writes to the warehouse, which the MCP server itself never does.
It connects as the machine user impersonating --user (so that user needs
CREATE on the database in Ranger) and reads connection details from
impala_details.txt just like test_doas.py.

    python scripts/setup_sample_data.py --user ozarate
    python scripts/setup_sample_data.py --user ozarate --recreate   # drop and reload

Creates database `mcp_demo` (override with --database) with four tables:
movies (~9.7k rows), ratings (~100k), tags (~3.7k), links (~9.7k).

Data: MovieLens latest-small, GroupLens Research, https://grouplens.org/datasets/movielens/
Its terms allow non-commercial research/demo use only; do not redistribute it or use it
in a commercial product.
"""

import argparse
import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from test_doas import load_details_file, query  # noqa: E402

from iceberg_mcp_server.tools.impala_tools import get_db_connection  # noqa: E402

URL = "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip"
BATCH_ROWS = 5000


def esc(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def num_or_null(s: str) -> str:
    return s if s.strip() else "NULL"


# name -> (csv file, DDL column list, function turning a csv row into a SQL VALUES tuple)
TABLES = {
    "movies": (
        "movies.csv",
        "movie_id INT, title STRING, genres STRING",
        lambda r: f"({r[0]},{esc(r[1])},{esc(r[2])})",
    ),
    "ratings": (
        "ratings.csv",
        "user_id INT, movie_id INT, rating DOUBLE, rated_at TIMESTAMP",
        lambda r: f"({r[0]},{r[1]},{r[2]},CAST({r[3]} AS TIMESTAMP))",
    ),
    "tags": (
        "tags.csv",
        "user_id INT, movie_id INT, tag STRING, tagged_at TIMESTAMP",
        lambda r: f"({r[0]},{r[1]},{esc(r[2])},CAST({r[3]} AS TIMESTAMP))",
    ),
    "links": (
        "links.csv",
        "movie_id INT, imdb_id STRING, tmdb_id INT",
        lambda r: f"({r[0]},{esc(r[1])},{num_or_null(r[2])})",
    ),
}


def download() -> zipfile.ZipFile:
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as resp:
        return zipfile.ZipFile(io.BytesIO(resp.read()))


def read_rows(zf: zipfile.ZipFile, filename: str) -> list:
    with zf.open(f"ml-latest-small/{filename}") as f:
        reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
        next(reader)  # header
        return list(reader)


def execute(conn, sql: str) -> None:
    cur = conn.cursor()
    try:
        cur.execute(sql)
    finally:
        cur.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="Cloudera user to impersonate; needs CREATE rights")
    ap.add_argument("--database", default="mcp_demo")
    ap.add_argument("--format", default="ICEBERG", choices=["ICEBERG", "PARQUET"])
    ap.add_argument("--recreate", action="store_true", help="drop and reload the tables if they exist")
    args = ap.parse_args()

    load_details_file(Path(__file__).resolve().parent.parent / "impala_details.txt")
    db = args.database
    zf = download()
    conn = get_db_connection(args.user)
    try:
        execute(conn, f"CREATE DATABASE IF NOT EXISTS `{db}`")
        print(f"[ok] database {db} ready (as {args.user})")

        for table, (csv_name, columns, to_values) in TABLES.items():
            full = f"`{db}`.`{table}`"
            if args.recreate:
                execute(conn, f"DROP TABLE IF EXISTS {full}")
            execute(conn, f"CREATE TABLE IF NOT EXISTS {full} ({columns}) STORED AS {args.format}")

            existing = query(conn, f"SELECT COUNT(*) FROM {full}")[0][0]
            if existing:
                print(f"[skip] {table}: already has {existing} rows (use --recreate to reload)")
                continue

            rows = read_rows(zf, csv_name)
            for i in range(0, len(rows), BATCH_ROWS):
                values = ",".join(to_values(r) for r in rows[i : i + BATCH_ROWS])
                execute(conn, f"INSERT INTO {full} VALUES {values}")
            loaded = query(conn, f"SELECT COUNT(*) FROM {full}")[0][0]
            status = "ok" if loaded == len(rows) else "MISMATCH"
            print(f"[{status}] {table}: loaded {loaded} of {len(rows)} rows")
    finally:
        conn.close()

    print(f"\nNext: set IMPALA_DATABASE={db} in impala_details.txt, and grant SELECT on {db}.* to the other users in Ranger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
