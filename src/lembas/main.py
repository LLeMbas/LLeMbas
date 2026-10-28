"""Application factory, lifespan and error handling."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from lembas import __version__
from lembas.api import (
    access_ui,
    admin,
    admin_agents,
    admin_audio,
    admin_backups,
    admin_brand,
    admin_data_groups,
    admin_extraction,
    admin_images,
    admin_import,
    admin_models,
    admin_prompts,
    admin_schedules,
    admin_search,
    admin_suggestions,
    admin_tools,
    admin_updates,
    admin_users,
    audio,
    auth,
    chats,
    devices,
    files,
    folders,
    invites,
    mcp_server,
    memories,
    memory_io,
    models,
    ordering,
    pages,
    palette,
    preferences,
    push,
    pyodide,
    schedules,
    search,
    terminal,
    tokens,
    tree,
)
from lembas.api.deps import RedirectToLogin, is_htmx, login_redirect
from lembas.api.v1 import admin as api_admin
from lembas.api.v1 import images as api_images
from lembas.api.v1 import me as api_me
from lembas.api.v1 import openai as api_openai
from lembas.api.v1 import services as api_services
from lembas.api.v2 import router as app_api_router
from lembas.config import settings
from lembas.db.session import init_db
from lembas.services import local_device
from lembas.web import i18n
from lembas.web.templating import STATIC_DIR, render

log = logging.getLogger("lembas")


def configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings.ensure_dirs()
    init_db()

    if settings.secret_key_is_ephemeral:
        log.warning(
            "No LEMBAS_SECRET_KEY set, so a temporary one was generated. Every "
            "restart will sign all users out and make stored API keys "
            "unreadable. Generate a permanent key with:\n"
            '    python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )

    # Files chosen in a composer that was never sent would otherwise sit on
    # disk forever. Cheap, and startup is the natural moment for it.
    try:
        from lembas.db.session import session_scope
        from lembas.services.chat import sweep_temporary
        from lembas.services.data_groups import sweep_unassigned
        from lembas.services.files import sweep_orphans
        from lembas.services.suggestions import seed_defaults as seed_suggestions

        with session_scope() as db:
            sweep_orphans(db)
            # Rows written before data groups existed, into the group they were
            # read in -- see services/data_groups.py.
            sweep_unassigned(db)
            # Temporary chats whose time is up. Also run by the ticker about
            # once a minute -- see services/chat.py:sweep_temporary.
            sweep_temporary(db)
            # Three starting points on the empty screen, written once ever.
            seed_suggestions(db)
            # Every organisation's `/home/` and `/skills/`, and a home for every
            # account that has none yet (services/fs.py).
            from lembas.services import fs

            fs.ensure_layout(db)
    except Exception:  # housekeeping must never block startup
        log.exception("orphaned upload sweep failed")

    # Schedules. `release_claims` first, because a firing interrupted by the
    # last shutdown left a claim stamp that would otherwise read as permanently
    # running. Then the ticker, started here rather than lazily like the
    # terminal reaper: a schedule can be due at startup with nobody logged in,
    # which is most of the point of having one. Inside the loop, so its tasks
    # land in this event loop.
    #
    # Catching up on what was missed is deliberately NOT done here. It lives in
    # the sweep, because a suspended laptop, a paused container and a long stall
    # all reproduce "its time passed while nothing was running" with no restart
    # for a startup hook to hang on.
    try:
        from lembas.services.schedule.ticker import release_claims
        from lembas.services.schedule.ticker import start as start_ticker

        released = release_claims()
        if released:
            log.info("released %s interrupted schedule claim(s)", released)
        start_ticker()
    except Exception:  # scheduling failing must not block startup
        log.exception("could not start the schedule ticker")

    # Replies the last shutdown cut off are taken up again, after the
    # devices have had a moment to dial back in.
    # Not on an API-only instance: nothing there starts a chat reply,
    # and a reply resumed now would be one nobody can open.
    from lembas.db.session import session_scope
    from lembas.services import settings_store
    from lembas.services.generation import resume_interrupted

    try:
        with session_scope() as db:
            api_only = settings_store.api_only(db)
    except Exception:  # the mode unreadable is the mode it was before
        api_only = False

    resuming = asyncio.get_running_loop().create_task(
        asyncio.sleep(0) if api_only else resume_interrupted()
    )

    # The file and chat search index (services/index/): reconciles everything
    # once, then follows the tree and the chats as they change.
    try:
        from lembas.services import index as index_service

        index_service.start()
    except Exception:  # search must not block startup
        log.exception("could not start the search indexer")

    log.info("LLeMbas %s starting on http://%s:%s", __version__, settings.host, settings.port)
    log.info("data directory: %s", settings.data_dir.resolve())
    yield

    # Replies still being written are cancelled and persisted with whatever
    # they have, rather than left as permanently unfinished rows.
    from lembas.services.agent.terminal import shutdown as stop_terminals
    from lembas.services.generation import shutdown as stop_generations
    from lembas.services.schedule.ticker import shutdown as stop_ticker

    # Before the generations, so nothing new is fired into a chat whose reply is
    # about to be cancelled and persisted.
    await stop_ticker()
    resuming.cancel()
    await stop_generations()
    # Open shells have nothing to persist: whatever was running on the far side
    # is cut off mid-command. Every deploy does this, and the panel is told why
    # rather than left to guess -- see deploy/README.md.
    await stop_terminals()
    # A chunk set is written whole or not at all, so cancelling loses nothing
    # a rebuild does not pick up again.
    from lembas.services import index as index_service

    await index_service.shutdown()
    log.info("LLeMbas stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="LLeMbas",
        version=__version__,
        lifespan=lifespan,
        # The API is an implementation detail of the UI, not a product surface.
        docs_url="/api/docs" if settings.log_level == "debug" else None,
        redoc_url=None,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # The language every request starts in. A `ContextVar` is per task, and a task
    # is reused between requests -- so without resetting it here, a signed-out page
    # would inherit whichever person was served last on that worker. The user's own
    # choice is applied later, by `get_current_user`, where they are already known.
    # And, for the same reason, which device is the computer the request comes
    # from: LLeMbas Desktop says so in its User-Agent, a browser does not. A label
    # only -- see services/local_device.py.
    @app.middleware("http")
    async def _language(request, call_next):
        i18n.activate(i18n.instance_default())
        local_device.activate(request)
        return await call_next(request)

    # One place that notices the file tree and chats changing, rather than a
    # call in every writer. Idempotent, because the factory is called per test.
    # See services/index/worker.py:install.
    from lembas.services import index as index_service

    index_service.install()

    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(preferences.router)
    app.include_router(chats.router)
    app.include_router(terminal.router)
    app.include_router(audio.router)
    app.include_router(files.router)
    app.include_router(folders.router)
    app.include_router(models.router)
    app.include_router(schedules.router)
    app.include_router(search.router)
    app.include_router(admin.router)
    app.include_router(admin_users.router)
    app.include_router(admin_updates.router)
    app.include_router(admin_backups.router)
    app.include_router(admin_import.router)
    app.include_router(admin_models.router)
    app.include_router(admin_audio.router)
    app.include_router(admin_extraction.router)
    app.include_router(admin_search.router)
    app.include_router(admin_schedules.router)
    app.include_router(admin_images.router)
    app.include_router(admin_prompts.router)
    app.include_router(admin_suggestions.router)
    app.include_router(admin_tools.router)
    app.include_router(admin_agents.router)
    app.include_router(admin_data_groups.router)
    app.include_router(ordering.router)
    app.include_router(tree.router)
    app.include_router(access_ui.router)
    app.include_router(palette.router)
    app.include_router(admin_brand.router)
    app.include_router(invites.router)
    app.include_router(pyodide.router)
    app.include_router(push.router)
    # The program's door: bearer tokens only, never the cookie.
    app.include_router(devices.router)
    app.include_router(tokens.router)
    app.include_router(mcp_server.router)
    # Before `memories`, whose `/api/memories/{id}` would take `import` for an id.
    app.include_router(memory_io.router)
    app.include_router(memories.router)
    # Before `/v1`'s own router, whose paths it does not share: `/v1/usage` and
    # `/v1/me/*`.
    app.include_router(api_me.router)
    app.include_router(api_openai.router)
    app.include_router(api_services.router)
    # Pictures, and the file a picture is fetched from.
    app.include_router(api_images.router)
    app.include_router(api_admin.router)
    # The app's door: the chats as JSON, bearer token with the `app` scope.
    app.include_router(app_api_router)

    # Added last, so they wrap everything above: the Origin check refuses a
    # foreign request before any route sees it, and every admin change is
    # recorded whichever of the twenty admin modules made it.
    from lembas.security.hardening import SecurityMiddleware
    from lembas.services.audit import AdminAuditMiddleware
    from lembas.services.instance_mode import InstanceModeMiddleware
    from lembas.services.updates_auto import ActivityMiddleware

    # Save in place: innermost, so the page it renders for a settings form goes
    # through the same doors as the browser's own GET would. See web/in_place.py.
    from lembas.web.in_place import InPlaceMiddleware

    app.add_middleware(InPlaceMiddleware)
    # API-only mode: innermost of the rest, so a foreign request is still
    # refused by the Origin check first. One gate on path prefixes rather than a
    # check in every product route -- see services/instance_mode.py.
    app.add_middleware(InstanceModeMiddleware)
    app.add_middleware(ActivityMiddleware)
    app.add_middleware(AdminAuditMiddleware)
    app.add_middleware(SecurityMiddleware)

    register_error_handlers(app)
    return app


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RedirectToLogin)
    async def _not_signed_in(request: Request, exc: RedirectToLogin) -> Response:
        # An htmx request must not swap a login page into a fragment of the
        # chat UI, so tell the browser to navigate instead.
        if is_htmx(request):
            response = Response(status_code=status.HTTP_204_NO_CONTENT)
            response.headers["HX-Redirect"] = "/auth/login"
            return response
        return login_redirect(exc.next_url)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        # JSON callers and htmx fragments want the bare status; humans loading a
        # page want a themed page they can navigate away from.
        wants_page = "text/html" in request.headers.get("accept", "") and not is_htmx(request)
        if not wants_page:
            # The exception's own headers go with it: a 401 from the API carries
            # `WWW-Authenticate`, a 429 `Retry-After`, and a client reads both.
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=getattr(exc, "headers", None),
            )

        return render(
            request,
            "error.html",
            {
                "status_code": exc.status_code,
                "detail": exc.detail,
                "flavour": error_flavour(exc.status_code),
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        log.exception("unhandled error at %s", request.url.path)
        if is_htmx(request) or "text/html" not in request.headers.get("accept", ""):
            return JSONResponse({"detail": "Internal server error"}, status_code=500)
        return render(
            request,
            "error.html",
            {"status_code": 500, "detail": i18n.t("Something went wrong."),
             "flavour": error_flavour(500)},
            status_code=500,
        )


# Flavour lives in error pages, empty states and theme names -- never in the
# functional UI. See the working notes.
#
# The three lines themselves live in `services/branding.py` with the other
# Middle-earth lines. What is left here is the mapping from a status code to
# which of them. `snapshot()` never raises, so an error page can still render its error
# on an instance whose database is the thing that broke.
def error_flavour(status_code: int) -> str:
    from lembas.services import branding

    text = branding.snapshot().text
    # Translated here, at the point of use: the table is per process and the
    # reader's language is per request (see `branding.FLAVOUR`).
    return i18n.t(text.get(f"error_{status_code}") or text["error_500"])


app = create_app()
