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


def test_connection_requires_user():
    with pytest.raises(ValueError):
        t.get_db_connection(None)
