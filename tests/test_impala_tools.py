import json

import pytest

from iceberg_mcp_server.tools import impala_tools as t


@pytest.mark.parametrize(
    "q",
    [
        "select * from t",
        "SELECT 1;",
        "  with x as (select 1) select * from x",
        "show tables",
        "SHOW CREATE TABLE t",
        "describe t",
        "select 'a;b' from t",
        "select 1 -- ; drop table t",
        "select /* insert */ 1",
        'select "drop" from t',
        "select `update` from t",
    ],
)
def test_readonly_allowed(q):
    assert t.check_readonly(q) is None


@pytest.mark.parametrize(
    "q",
    [
        "",
        "   ",
        "insert into t values (1)",
        "drop table t",
        "select 1; drop table t",
        "select 1;select 2",
        "with x as (select 1) insert into t select * from x",
        "/* hi */ drop table t",
        "select 1 /* unterminated",
        "select 'unterminated",
        "create table t (a int)",
        "grant all on table t to user u",
        "-- select\ndrop table t",
        "SELECT * FROM t; DELETE FROM t",
    ],
)
def test_readonly_rejected(q):
    assert t.check_readonly(q)


@pytest.mark.parametrize("user", ["ozarate", "j.garagorry", "srv_x-1", "a"])
def test_username_ok(user):
    assert t.validate_username(user) == user


@pytest.mark.parametrize(
    "user", [None, "", "Ozarate", "1abc", "a b", "a&doAs=root", "a?x", "a%20b", "a/../b", "x" * 65, "ozarate\n", "é"]
)
def test_username_rejected(user):
    with pytest.raises(ValueError):
        t.validate_username(user)


def test_connection_uses_doas_path(monkeypatch):
    captured = {}
    monkeypatch.setattr(t, "connect", lambda **kw: captured.update(kw))
    monkeypatch.setenv("IMPALA_USER", "srv_machine")
    monkeypatch.setenv("IMPALA_PASSWORD_B64", "cGFzcyQ=")
    monkeypatch.setenv("IMPALA_HTTP_PATH", "cliservice?doAs=someone_else")  # stale override must not survive
    t.get_db_connection("ozarate")
    assert captured["http_path"] == "cliservice?doAs=ozarate"
    assert captured["user"] == "srv_machine"
    assert captured["password"] == "pass$"
    assert captured["auth_mechanism"] == "LDAP"


@pytest.mark.parametrize("host", ["coord.example.com", "https://coord.example.com/", " coord.example.com/ "])
def test_connection_host_pasted_as_url(monkeypatch, host):
    captured = {}
    monkeypatch.setattr(t, "connect", lambda **kw: captured.update(kw))
    monkeypatch.setenv("IMPALA_HOST", host)
    monkeypatch.setenv("IMPALA_PASSWORD_B64", "cGFzcyQ=")
    t.get_db_connection("ozarate")
    assert captured["host"] == "coord.example.com"


def test_connection_requires_user():
    with pytest.raises(ValueError):
        t.get_db_connection(None)


# --- get_schema -----------------------------------------------------------------


class FakeCursor:
    def __init__(self, tables, columns, log):
        self.tables, self.columns, self.log, self.rows = tables, columns, log, []

    def execute(self, sql):
        self.log.append(sql)
        if sql == "SHOW TABLES":
            self.rows = [(n,) for n in self.tables]
            return
        name = sql.removeprefix("DESCRIBE ").strip("`")
        if self.columns.get(name) is None:
            raise Exception(f"AuthorizationException: no access to {name}\nmore detail")
        self.rows = self.columns[name]

    def fetchall(self):
        return self.rows


def fake_schema_conn(monkeypatch, tables, columns):
    log, opened = [], []

    class Conn:
        def cursor(self):
            return FakeCursor(tables, columns, log)

        def close(self):
            pass

    def connect(user):
        opened.append(user)
        return Conn()

    monkeypatch.setattr(t, "get_db_connection", connect)
    return log, opened


def test_get_schema_lists_columns_on_one_connection(monkeypatch):
    log, opened = fake_schema_conn(
        monkeypatch,
        ["movies", "tags"],
        {"movies": [("movie_id", "int", ""), ("title", "string", "")], "tags": [("tag", "string", "")]},
    )
    out = json.loads(t.get_schema("mschuler"))
    assert out == {"tables": {"movies": ["movie_id int", "title string"], "tags": ["tag string"]}}
    assert opened == ["mschuler"]
    assert log == ["SHOW TABLES", "DESCRIBE `movies`", "DESCRIBE `tags`"]


def test_get_schema_reports_undescribable_table(monkeypatch):
    fake_schema_conn(monkeypatch, ["ok", "secret"], {"ok": [("a", "int", "")], "secret": None})
    out = json.loads(t.get_schema("mschuler"))
    assert out["tables"]["ok"] == ["a int"]
    assert out["tables"]["secret"].startswith("columns not available: AuthorizationException")
    assert "more detail" not in out["tables"]["secret"]


def test_get_schema_caps_described_tables(monkeypatch):
    names = [f"t{i}" for i in range(t.SCHEMA_MAX_DESCRIBED_TABLES + 3)]
    log, _ = fake_schema_conn(monkeypatch, names, {n: [("c", "int", "")] for n in names})
    out = json.loads(t.get_schema("mschuler"))
    assert len(out["tables"]) == t.SCHEMA_MAX_DESCRIBED_TABLES
    assert out["more_tables"] == names[-3:]
    assert len(log) == 1 + t.SCHEMA_MAX_DESCRIBED_TABLES
