"""Handing existing data to its owner: first-login bootstrap and the admin CLI."""
import sqlite3

import pytest

from xelto import cli, install, ownership, store

from .conftest import build_app, fork_db, new_client, sign_in


def _seed_unowned(path, *ids):
    conn = sqlite3.connect(str(path))
    for pid in ids:
        conn.execute("INSERT INTO voice_profiles(id, name) VALUES (?, 'v')", (pid,))
        conn.execute("INSERT INTO generation_history(id, text) VALUES (?, 't')", ("h-" + pid,))
    conn.commit()
    conn.close()


def _owners(path):
    conn = sqlite3.connect(str(fork_db(path)))
    try:
        return dict(conn.execute("SELECT row_id, owner FROM xelto_row_owner"))
    finally:
        conn.close()


def test_assign_unowned_skips_owned_rows_and_shared_demo(scoped_db):
    _seed_unowned(scoped_db, "p1")
    conn = store.connect_both(str(scoped_db))
    conn.execute("INSERT INTO xelto.xelto_row_owner VALUES ('voice_profiles', 'p0', 'bob')")
    conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('p0', 'bob voice')")
    counts = ownership.assign_unowned(conn, "alice")
    conn.commit()
    conn.close()
    assert counts["voice_profiles"] == 1 and counts["generation_history"] == 1
    assert _owners(scoped_db) == {"p0": "bob", "p1": "alice", "h-p1": "alice"}  # no 'demo'


def test_bootstrap_owner_gets_existing_data_once(multiuser_env, scoped_db, idp):
    multiuser_env.setenv("OMNIVOICE_MULTIUSER_BOOTSTRAP_OWNER", "alice")
    _seed_unowned(scoped_db, "old")
    app = build_app()
    install(app, oidc_transport=idp.transport)
    with new_client(app) as client:
        assert sign_in(client, idp, idp.bob).status_code == 302
        assert _owners(scoped_db) == {}  # only the named owner triggers it
    with new_client(app) as client:
        assert sign_in(client, idp, idp.alice).status_code == 302
    assert _owners(scoped_db) == {"old": idp.alice.sub, "h-old": idp.alice.sub}

    _seed_unowned(scoped_db, "later")
    with new_client(app) as client:
        assert sign_in(client, idp, idp.alice).status_code == 302
    assert "later" not in _owners(scoped_db)


def _register(path, sub, username):
    conn = sqlite3.connect(str(fork_db(path)))
    conn.execute("INSERT INTO xelto_users(sub, username, created_at) VALUES (?, ?, 0)", (sub, username))
    conn.commit()
    conn.close()


def test_cli_assign_and_purge(scoped_db, capsys):
    _register(scoped_db, "alice-sub", "alice")
    _seed_unowned(scoped_db, "p1")
    assert cli.main(["--db", str(scoped_db), "assign-unowned", "--to", "alice"]) == 0
    assert "voice_profiles: 1" in capsys.readouterr().out
    assert set(_owners(scoped_db).values()) == {"alice-sub"}

    assert cli.main(["--db", str(scoped_db), "purge-user", "alice-sub"]) == 0
    assert _owners(scoped_db) == {}
    conn = sqlite3.connect(str(scoped_db))
    assert [r[0] for r in conn.execute("SELECT id FROM voice_profiles")] == ["demo"]
    conn.close()


def test_cli_refuses_an_unknown_user(scoped_db):
    with pytest.raises(SystemExit, match="unknown"):
        cli.main(["--db", str(scoped_db), "assign-unowned", "--to", "nobody"])
