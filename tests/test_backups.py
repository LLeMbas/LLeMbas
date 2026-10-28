"""Backups: one archive, checked on the way back, put in place at the next start."""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from lembas import __version__
from lembas.config import settings
from lembas.db.models import Connection
from lembas.db.session import get_session_factory, init_db, reset_engine
from lembas.services import backup, crypto, export_format, settings_store


def _connection(db, name: str, key: str = "") -> None:
    db.add(Connection(name=name, base_url="http://127.0.0.1:1/v1",
                      api_key_encrypted=crypto.encrypt(key)))
    db.commit()


def _names(db) -> list[str]:
    return sorted(c.name for c in db.scalars(select(Connection)))


def test_an_archive_holds_both_databases_uploads_and_a_manifest(db):
    _connection(db, "Kept")
    (settings.uploads_dir / "attachments").mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / "attachments" / "a.txt").write_bytes(b"hello")
    archive = backup.take("test", passphrase="")
    assert archive.name.startswith("lembas-") and archive.name.endswith(
        f"-test.tar.{backup.extension()}"
    )
    assert archive.stat().st_mode & 0o777 == 0o600
    document = backup.manifest(archive)
    assert document["format"] == "lembas-backup" and document["app_version"] == __version__
    files = {f["path"]: f for f in document["files"]}
    assert {"lembas.db", "lembas-blobs.db", "uploads/attachments/a.txt"} <= set(files)
    assert files["uploads/attachments/a.txt"]["size"] == 5
    assert document["secret_key"] is None
    assert [a.name for a in backup.archives()] == [archive.name]


def test_the_secret_key_goes_in_only_under_a_passphrase(db):
    archive = backup.take(passphrase="mellon")
    block = backup.manifest(archive)["secret_key"]
    fernet = export_format.opener(block["encryption"], "mellon")
    assert fernet.decrypt(block["token"].encode()).decode() == settings.secret_key


def test_restore_is_staged_then_applied_at_the_next_start(db):
    _connection(db, "Before")
    archive = backup.take(passphrase="")
    _connection(db, "After")
    staged = backup.stage(archive)
    assert staged.app_version == __version__ and staged.warnings
    assert backup.pending() is not None
    db.close()
    init_db()  # the next start
    assert backup.pending() is None
    fresh = get_session_factory()()
    try:
        assert _names(fresh) == ["Before"]
    finally:
        fresh.close()
    aside = list(settings.data_dir.glob("pre-restore-*"))
    assert len(aside) == 1 and (aside[0] / "lembas.db").exists()
    assert (aside[0] / "restored-from.json").exists()


def test_a_restore_under_another_key_re_encrypts_the_stored_keys(db, monkeypatch):
    _connection(db, "Keyed", key="sk-real")
    archive = backup.take(passphrase="mellon")
    db.close()
    monkeypatch.setattr(settings, "secret_key", "a different key for this instance")
    crypto._fernet.cache_clear()
    try:
        backup.stage(archive, "mellon")
        init_db()
        fresh = get_session_factory()()
        try:
            row = fresh.scalar(select(Connection).where(Connection.name == "Keyed"))
            assert crypto.decrypt(row.api_key_encrypted) == "sk-real"
        finally:
            fresh.close()
    finally:
        crypto._fernet.cache_clear()


def test_a_wrong_passphrase_is_refused(db):
    archive = backup.take(passphrase="mellon")
    with pytest.raises(backup.Refused):
        backup.stage(archive, "wrong")
    assert backup.pending() is None


def _forged(tmp_path, manifest: dict, files: dict[str, bytes]):
    path = tmp_path / "forged.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, body in [("manifest.json", json.dumps(manifest).encode()), *files.items()]:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def test_a_backup_from_another_major_or_tampered_is_refused(db, tmp_path):
    archive = backup.take(passphrase="")
    document = backup.manifest(archive)
    another = f"{int(__version__.split('.')[0]) + 1}.0.0"
    other = {**document, "app_version": another}
    with pytest.raises(backup.Refused) as caught:
        backup.stage(_forged(tmp_path, other, {}))
    assert another in str(caught.value)
    tampered = _forged(tmp_path, document, {"lembas.db": b"not what the manifest says"})
    with pytest.raises(backup.Refused):
        backup.stage(tampered)
    sneaky = {**document, "files": [*document["files"],
                                    {"path": "../escape", "size": 1, "sha256": "0"}]}
    with pytest.raises(backup.Refused):
        backup.stage(_forged(tmp_path, sneaky, {}))
    not_one = tmp_path / "x.tar.gz"
    not_one.write_bytes(b"plain text")
    with pytest.raises(backup.Refused):
        backup.stage(not_one)
    assert backup.pending() is None
    assert not list(settings.data_dir.glob(".restore-staging-*"))


def _archive(stamp: datetime):
    return backup.Archive(f"lembas-{stamp:%Y%m%d-%H%M%S}.tar.gz", None, 1, stamp)


def test_keep_n_daily_and_n_weekly():
    start = datetime(2026, 3, 31, 3, 0, tzinfo=UTC)
    found = [_archive(start - timedelta(days=d)) for d in range(60)]  # newest first
    kept = backup.keepers(found, 7, 4)
    days = {found[d].name for d in range(7)}
    assert days <= kept
    # Weeks 14 and 13 are already among the days; 12 and 11 add one each.
    assert len(kept) == 7 + 2
    assert backup.keepers(found, 0, 0) == {a.name for a in found}
    assert backup.keepers(found, 1, 0) == {found[0].name}


def test_the_schedule_runs_once_a_day(db):
    settings_store.update(db, {"enabled": True, "time": "03:00"}, key=settings_store.BACKUPS)
    values = settings_store.backups(db)
    early = datetime(2026, 3, 1, 2, 59).astimezone()
    late = datetime(2026, 3, 1, 3, 1).astimezone()
    assert not backup.due(values, early)
    assert backup.due(values, late)
    taken = asyncio.run(backup.tick(now=late))
    assert taken is not None and taken.exists()
    db.expire_all()
    assert settings_store.backups(db)["last_day"] == "2026-03-01"
    assert asyncio.run(backup.tick(now=late)) is None


def test_the_admin_page(client, registered):
    page = client.get("/admin/backups")
    assert page.status_code == 200 and "Back up now" in page.text
    made = client.post("/admin/backups/now", data={"passphrase": ""}, follow_redirects=False)
    assert made.status_code == 303
    [archive] = backup.archives()
    assert archive.name in client.get("/admin/backups").text
    download = client.get(f"/admin/backups/file/{archive.name}")
    assert download.status_code == 200 and download.content == archive.path.read_bytes()
    assert client.get("/admin/backups/file/..%2Flembas.db").status_code == 404
    staged = client.post("/admin/backups/restore", data={"name": archive.name},
                         follow_redirects=False)
    assert staged.status_code == 303 and backup.pending() is not None
    assert "restore-pending" in client.get("/admin/backups").text
    client.post("/admin/backups/restore/cancel")
    assert backup.pending() is None
    saved = client.post("/admin/backups/settings", data={"enabled": "true", "time": "04:30",
                                                         "keep_daily": "3", "keep_weekly": "2",
                                                         "key_passphrase": "pp"},
                        follow_redirects=False)
    assert saved.status_code == 303
    client.post(f"/admin/backups/file/{archive.name}/delete")
    assert backup.archives() == []


@pytest.fixture(autouse=True)
def _engine_back():
    init_db()  # as a start does: marks the database 2.x, which a restore checks
    yield
    reset_engine()


def test_the_command_line_backs_up_and_stages(db, monkeypatch):
    from typer.testing import CliRunner

    from lembas.cli import app as cli_app

    monkeypatch.setenv("BACKUP_PASS", "mellon")
    made = CliRunner().invoke(cli_app, ["backup", "--passphrase-env", "BACKUP_PASS"])
    assert made.exit_code == 0, made.output
    [archive] = backup.archives()
    assert backup.manifest(archive.path)["secret_key"] is not None
    staged = CliRunner().invoke(cli_app, ["restore", archive.name,
                                          "--passphrase-env", "BACKUP_PASS"])
    assert staged.exit_code == 0, staged.output
    assert backup.pending() is not None


def test_without_zstandard_it_is_gzip_and_still_restores(db, monkeypatch):
    monkeypatch.setattr(backup, "zstandard", None)
    archive = backup.take(passphrase="")
    assert archive.name.endswith(".tar.gz")
    assert backup.stage(archive).app_version == __version__
