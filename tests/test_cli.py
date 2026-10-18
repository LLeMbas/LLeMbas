"""The four commands, none of which had a test.

`create-admin` is the documented way back into an instance whose administrator
account has been lost, and the bootstrap step `README.md` tells somebody to run
when scripting a deployment. It had never been executed by anything but a
person, which is the worst place to discover that a command does not work: the
moment you cannot get in.

Driven through typer's `CliRunner` rather than by calling the functions, so the
options, the prompts and the exit codes are part of what is under test -- a
`typer.Exit(1)` that the shell reads as success is a script that carries on
after a failure.
"""

from __future__ import annotations

from typer.testing import CliRunner

from lembas import __version__
from lembas.cli import app

runner = CliRunner()


def test_secret_key_prints_something_usable():
    """It is pasted straight into `lembas.env`, so it has to be one line with no
    surprises in it. A key with a newline silently truncates the variable."""
    result = runner.invoke(app, ["secret-key"])

    assert result.exit_code == 0
    key = result.stdout.strip()
    assert len(key) >= 40
    assert "\n" not in key
    # Two calls must not agree, or it is not a secret.
    assert key != runner.invoke(app, ["secret-key"]).stdout.strip()


def test_info_says_where_the_data_is(db):
    """The command somebody runs when they cannot find the database, which is
    exactly when a wrong answer costs the most."""
    from lembas.config import settings

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert str(settings.db_path.resolve()) in result.stdout
    assert "users" in result.stdout


def test_info_warns_about_a_generated_secret_key(db):
    """An instance running on an ephemeral key loses every session and every
    stored API key on restart. It is the one thing on this screen that is a
    problem rather than a fact."""
    from lembas.config import settings

    if not settings.secret_key_is_ephemeral:  # pragma: no cover - depends on env
        return
    assert "GENERATED" in runner.invoke(app, ["info"]).stdout


def test_create_admin_makes_an_administrator(db):
    """The way back in. It has to produce an account that can actually sign in,
    so the password is checked by hashing rather than by trusting the message."""
    from sqlalchemy import select

    from lembas.db.models import ROLE_ADMIN, User
    from lembas.security.passwords import verify_password

    result = runner.invoke(
        app,
        [
            "create-admin",
            "--email", "Frodo@Shire.test",
            "--name", "Frodo",
            "--password", "speak-friend-and-enter",
        ],
    )

    assert result.exit_code == 0, result.stdout
    user = db.scalar(select(User).where(User.email == "frodo@shire.test"))
    assert user is not None, "the address is lowercased, so look for it that way"
    assert user.role == ROLE_ADMIN
    assert user.active
    assert verify_password("speak-friend-and-enter", user.password_hash)


def test_create_admin_promotes_an_account_that_already_exists(db):
    """Its whole purpose in the lost-password case: the account is there, it is
    just no longer an administrator or no longer has a password anybody knows.
    A second account with the same address would be no way back in at all."""
    from sqlalchemy import func, select

    from lembas.db.models import ROLE_ADMIN, ROLE_USER, User
    from lembas.security.passwords import verify_password

    db.add(
        User(
            email="sam@shire.test",
            name="Sam",
            password_hash="not-a-real-hash",
            role=ROLE_USER,
            active=False,
        )
    )
    db.commit()

    result = runner.invoke(
        app,
        [
            "create-admin",
            "--email", "sam@shire.test",
            "--name", "Samwise",
            "--password", "second-breakfast-please",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert db.scalar(select(func.count()).select_from(User)) == 1, "it made a second one"
    db.expire_all()
    user = db.scalar(select(User).where(User.email == "sam@shire.test"))
    assert user.role == ROLE_ADMIN
    assert user.active, "a deactivated account has to be let back in, or this fixes nothing"
    assert verify_password("second-breakfast-please", user.password_hash)


def test_create_admin_refuses_a_password_that_would_be_rejected_elsewhere(db):
    """And exits non-zero, so a deployment script stops rather than carrying on
    believing it has an administrator."""
    from sqlalchemy import select

    from lembas.db.models import User

    result = runner.invoke(
        app,
        ["create-admin", "--email", "x@shire.test", "--name", "X", "--password", "short"],
    )

    assert result.exit_code == 1
    assert db.scalar(select(User)) is None
