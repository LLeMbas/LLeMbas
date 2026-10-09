# LLeMbas in a container.
#
# One stage, on purpose. There is nothing to build: no Node, no compiled assets,
# no wheel worth producing separately — the vendored browser libraries are
# committed and the templates are read at runtime. A multi-stage build here
# would be ceremony that saves nothing and hides where the files came from.
#
# **This image is not a deployment on its own.** It serves plain HTTP and expects
# a TLS reverse proxy in front, and that is a constraint rather than a
# preference: a service worker and a microphone both require HTTPS or localhost,
# so over plain http on a LAN address the app installs as nothing and cannot
# dictate. See deploy/README.md.

FROM python:3.12-slim

# `bash` and `git` earn their place: `git` is what /admin/updates reads to say
# what is running, and its absence there is reported rather than crashed on.
# `curl` is the healthcheck below. Everything else stays out.
RUN apt-get update \
    && apt-get install --no-install-recommends -y git curl \
    && rm -rf /var/lib/apt/lists/*

# A real account rather than root, and made before the install so the layers it
# owns are its own. 10001 rather than the first free id: a bind-mounted volume
# on the host is easier to reason about when the id is stated.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin lembas

WORKDIR /app

# The dependency install is its own layer, keyed on the files that decide it, so
# editing a template does not re-resolve the whole tree.
#
# LICENSE is in the list because `pyproject.toml` declares `license = { file =
# "LICENSE" }` and the build backend reads it -- without it the install fails
# with "License file does not exist", which reads like a packaging problem and
# is a missing COPY. README.md is there for the same reason (`readme = `).
COPY pyproject.toml README.md LICENSE ./
COPY src/lembas/__init__.py src/lembas/__init__.py
RUN pip install --no-cache-dir -e ".[search]"

COPY . .
# Again, because the first install ran against a source tree with one file in
# it. Cheap: everything is already resolved and cached above.
RUN pip install --no-cache-dir --no-deps -e "." \
    && chown -R lembas:lembas /app

# The database, the uploads and the encryption at rest all live here. Declared
# so that running without `-v` still works and says where the data went, rather
# than losing it silently at the first `docker rm`.
ENV LEMBAS_DATA_DIR=/data \
    LEMBAS_HOST=0.0.0.0 \
    LEMBAS_PORT=8080 \
    PYTHONUNBUFFERED=1
RUN install -d -o lembas -g lembas /data
VOLUME ["/data"]

# **No secret key is baked in.** One in an image is one every copy of the image
# shares, and rotating it signs everybody out *and* makes stored upstream API
# keys unreadable. Without LEMBAS_SECRET_KEY the app generates a temporary one
# and warns loudly at startup, which is the right failure: it works for a look
# and cannot be mistaken for a deployment.

USER lembas
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

CMD ["lembas-server", "serve"]
