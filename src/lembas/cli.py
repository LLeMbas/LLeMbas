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
        # Access logs duplicate what the application already logs and drown out
        # anything useful during development.
        access_log=settings.log_level == "debug",
    )


@app.command()
def backup(
    label: str = typer.Option("", help="A word in the file name: before-update, by-hand."),
    keep: int = typer.Option(10, help="How many backups to keep; 0 keeps all."),
) -> None:
    """Back the database up while it runs (SQLite's online backup, checked)."""
    from lembas.db.session import init_db
    from lembas.services import backup as backup_service

    init_db()
    path = backup_service.take(label, keep=keep)
    typer.echo(str(path))


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
    typer.echo(f"  signup open    : {settings.allow_signup}")
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
