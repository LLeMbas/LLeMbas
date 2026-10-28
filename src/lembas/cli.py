"""Command line entry points."""

from __future__ import annotations

import secrets as secrets_module

import typer
import uvicorn
from sqlalchemy import func, select

from lembas import __version__
from lembas.config import settings

app = typer.Typer(
    help="LLeMbas - a Middle-earth themed web UI for your language models.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def serve(
    host: str = typer.Option(None, help="Bind address. Defaults to LEMBAS_HOST."),
    port: int = typer.Option(None, help="Port. Defaults to LEMBAS_PORT."),
    reload: bool = typer.Option(None, "--reload/--no-reload", help="Autoreload on change."),
) -> None:
    """Run the web server."""
    uvicorn.run(
        "lembas.main:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=settings.reload if reload is None else reload,
        log_level=settings.log_level,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        # Access logs duplicate what the application already logs and drown out
        # anything useful during development.
        access_log=settings.log_level == "debug",
    )


def _passphrase(env: str) -> str:
    """A passphrase from the environment variable named, never from argv."""
    import os

    if not env:
        return ""
    value = os.environ.get(env, "")
    if not value:
        typer.secho(f"{env} is not set.", fg=typer.colors.RED)
        raise typer.Exit(1)
    return value


@app.command()
def backup(
    label: str = typer.Option("", help="A word in the file name: before-update, by-hand."),
    keep: int = typer.Option(
        None, help="Keep the newest N backups; 0 keeps all. Default: the schedule's rule."
    ),
    passphrase_env: str = typer.Option(
        "", help="Put the secret key in, encrypted under the passphrase in this variable."
    ),
    no_key: bool = typer.Option(False, "--no-key", help="Leave the secret key out."),
) -> None:
    """Back the instance up while it runs: both databases, uploads, a manifest."""
    from lembas.db.session import init_db
    from lembas.services import backup as backup_service

    init_db()
    passphrase = "" if no_key else (_passphrase(passphrase_env) or None)
    path = backup_service.take(label, keep=keep, passphrase=passphrase)
    typer.echo(str(path))


@app.command()
def restore(
    archive: str = typer.Argument(..., help="A backup archive, or the name of one in the "
                                  "backup directory."),
    passphrase_env: str = typer.Option(
        "", help="The variable holding the passphrase of the backup's secret key."
    ),
) -> None:
    """Check a backup and stage it; it is put in place at the next start."""
    from pathlib import Path

    from lembas.services import backup as backup_service

    found = backup_service.find(archive)
    path = found.path if found else Path(archive)
    if not path.is_file():
        typer.secho(f"No such backup: {archive}", fg=typer.colors.RED)
        raise typer.Exit(1)
    settings.ensure_dirs()
    try:
        staged = backup_service.stage(path, _passphrase(passphrase_env))
    except backup_service.Refused as refused:
        typer.secho(str(refused), fg=typer.colors.RED)
        raise typer.Exit(1) from refused
    for warning in staged.warnings:
        typer.secho(warning, fg=typer.colors.YELLOW)
    typer.secho(
        f"Staged the backup of {staged.created_at} ({staged.app_version}). Restart the service "
        "to put it in place; the current data is moved to pre-restore-<time>/.",
        fg=typer.colors.GREEN,
    )


import_app = typer.Typer(help="Bring data across from LLeMbas 1.x.", no_args_is_help=True)
app.add_typer(import_app, name="import")


@import_app.command("old")
def import_old(
    owner: str = typer.Option(..., help="Email of the 2.0 account the files go to."),
    from_email: str = typer.Option("", help="Only this 1.x account's library."),
    memories: bool = typer.Option(
        False, help="Also each 1.x account's memories, into the 2.0 account of the same email."
    ),
) -> None:
    """The 1.x library from lembas.db.old into files under the owner's ~/Imported/."""
    from lembas.db.models import User
    from lembas.db.session import init_db, session_scope
    from lembas.services import import_old as service

    init_db()
    with session_scope() as db:
        account = db.scalar(select(User).where(func.lower(User.email) == owner.strip().lower()))
        if account is None:
            typer.secho(f"No account {owner}.", fg=typer.colors.RED)
            raise typer.Exit(1)
        try:
            report = service.run(db, account, from_email=from_email, memories=memories)
        except service.Refused as refused:
            typer.secho(str(refused), fg=typer.colors.RED)
            raise typer.Exit(1) from refused
        for line in report.lines():
            typer.echo(line)


@import_app.command("admin")
def import_admin(
    file: str = typer.Argument(..., help="A lembas-export file of kind admin, from 1.7."),
    passphrase_env: str = typer.Option(
        "", help="The variable holding the passphrase the file's keys were exported with."
    ),
) -> None:
    """Connections, models and services from a 1.7 administrator's export."""
    from pathlib import Path

    from lembas.db.session import init_db, session_scope
    from lembas.services import export_format
    from lembas.services import import_admin as service

    init_db()
    try:
        document = service.load(Path(file).read_bytes())
        with session_scope() as db:
            report = service.run(db, document, passphrase=_passphrase(passphrase_env))
    except (export_format.Refused, OSError) as refused:
        typer.secho(str(refused), fg=typer.colors.RED)
        raise typer.Exit(1) from refused
    for line in report.lines():
        typer.echo(line)


@app.command("create-admin")
def create_admin(
    email: str = typer.Option(..., prompt=True),
    name: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Create an administrator, or promote an existing account to one.

    The web sign-up already makes the first account an admin. This is the way
    back in when that account is lost, or when scripting a deployment.
    """
    from lembas.db.models import ROLE_ADMIN, User
    from lembas.db.session import init_db, session_scope
    from lembas.security.passwords import hash_password, validate_password

    if (problem := validate_password(password)) is not None:
        typer.secho(problem, fg=typer.colors.RED)
        raise typer.Exit(1)

    init_db()
    with session_scope() as db:
        existing = db.scalar(select(User).where(User.email == email.strip().lower()))
        if existing is not None:
            existing.role = ROLE_ADMIN
            existing.password_hash = hash_password(password)
            existing.active = True
            typer.secho(f"Promoted {existing.email} to administrator.", fg=typer.colors.GREEN)
            return

        db.add(
            User(
                email=email.strip().lower(),
                name=name.strip(),
                password_hash=hash_password(password),
                role=ROLE_ADMIN,
            )
        )
        typer.secho(f"Created administrator {email}.", fg=typer.colors.GREEN)


@app.command("secret-key")
def secret_key() -> None:
    """Print a fresh value for LEMBAS_SECRET_KEY."""
    typer.echo(secrets_module.token_urlsafe(48))


@app.command()
def info() -> None:
    """Show where this instance keeps its data and what is configured."""
    from lembas.db.models import Chat, Connection, User
    from lembas.db.session import init_db, session_scope

    init_db()
    typer.echo(f"LLeMbas {__version__}")
    typer.echo(f"  data directory : {settings.data_dir.resolve()}")
    typer.echo(f"  database       : {settings.db_path.resolve()}")
    typer.echo(f"  bind           : {settings.host}:{settings.port}")
    typer.echo(f"  default theme  : {settings.default_theme}")
    if settings.secret_key_is_ephemeral:
        typer.secho(
            "  secret key     : GENERATED (set LEMBAS_SECRET_KEY for a real install)",
            fg=typer.colors.YELLOW,
        )

    with session_scope() as db:
        for label, model in (("users", User), ("connections", Connection), ("chats", Chat)):
            count = db.scalar(select(func.count()).select_from(model))
            typer.echo(f"  {label:<15}: {count}")


if __name__ == "__main__":
    app()
