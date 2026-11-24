"""git, for the skills and notes store: one runner, and what it will not do.

The store is a git repository in the data folder, and it may have a remote an
administrator named. Everything a remote can send is somebody else's input, so
the runner is built around what must never happen:

- **Nothing of a repository runs.** Hooks are pointed at nothing, no pager, no
  editor, no prompt (`GIT_TERMINAL_PROMPT=0`), no credential helper, no
  askpass, the system's and the account's git configuration are not read, and
  `fsmonitor`, attributes' filters and submodules have nothing to start.
- **Only two transports.** An address is `https://…`, `ssh://…` or the
  scp-like `user@host:path`. `ext::`, `file://`, `git://`, `http://`, a local
  path and anything that begins with `-` are refused before git sees them, and
  git itself is told the same (`protocol.allow=never`, then `https` and `ssh`).
- **Every command has a timeout**, and a fetch is stopped when the repository
  grows past a bound while it runs.
- **A secret is never an argument.** A token goes to git through the
  environment's configuration (`GIT_CONFIG_*`), a key through a file only this
  account can read, removed after the command.
- **ssh trusts a host the first time and never a changed key**
  (`StrictHostKeyChecking=accept-new`, against the store's own known-hosts
  file): the first connection remembers the host's key, and a host that later
  shows another key is refused until an administrator forgets the old one.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import re
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from lembas.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT = 20.0
NET_TIMEOUT = 90.0
# What a fetch may add to the repository before it is stopped, and what a
# repository may hold in files it hands over.
MAX_FETCH_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 20_000

_SCP = re.compile(
    r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*:(?!//)[A-Za-z0-9~/][A-Za-z0-9._~/-]*$"
)
_URL = re.compile(
    r"^(https|ssh)://(?:[A-Za-z0-9._~-]+@)?(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(?::\d{1,5})?"
    r"(/[A-Za-z0-9._~/%+-]*)?$"
)


class BadAddress(ValueError):
    """An address the store will not hand to git."""


def local_allowed() -> bool:
    """Whether a local path counts as an address: only where the deployment
    says so (`LEMBAS_SKILLS_LOCAL_REMOTE=1`), which is what the tests' bare
    repositories in temporary folders need and nothing else does."""
    return bool(getattr(get_settings(), "skills_local_remote", False))


def check_address(address: str) -> str:
    """The address as git gets it, or `BadAddress` saying what one must be."""
    address = (address or "").strip()
    if not address:
        raise BadAddress("An address is needed.")
    if address.startswith("-") or any(c in address for c in "\n\r\t\0 \\"):
        raise BadAddress("That is not a repository address.")
    if ".." in address.replace("\\", "/").split("/") or "/-" in address or ":-" in address:
        raise BadAddress("That is not a repository address.")
    lowered = address.lower()
    if "::" in address and not lowered.startswith(("https://[", "ssh://[")):
        raise BadAddress("Only https:// and ssh:// addresses are taken.")
    if _URL.match(address):
        return address
    if "://" not in address and _SCP.match(address) and not address.startswith("/"):
        return address
    if local_allowed() and address.startswith("/") and ".." not in address.split("/"):
        return address
    raise BadAddress(
        "Only https:// and ssh:// addresses are taken (or user@host:path for ssh)."
    )


def transport(address: str) -> str:
    """`https`, `ssh` or `local`, for an address that passed `check_address`."""
    if address.startswith("https://"):
        return "https"
    if address.startswith("/"):
        return "local"
    return "ssh"


@dataclass(frozen=True)
class Credentials:
    """What a remote is reached with. Decrypted only for the one command."""

    username: str = ""
    token: str = ""
    ssh_key: str = ""


def known_hosts() -> Path:
    return get_settings().data_dir / "skills-known-hosts"


def _base_env() -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(get_settings().data_dir),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_EDITOR": ":",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ALLOW_PROTOCOL": "https:ssh" + (":file" if local_allowed() else ""),
    }
    return env


# Given on every command line, so nothing a repository's own configuration
# says can put any of it back.
_FIXED = (
    "-c", "core.hooksPath=" + os.devnull,
    "-c", "core.fsmonitor=false",
    "-c", "core.symlinks=false",
    "-c", "core.pager=cat",
    "-c", "core.sshCommand=false",  # replaced by GIT_SSH_COMMAND when ssh is meant
    "-c", "credential.helper=",
    "-c", "protocol.allow=never",
    "-c", "protocol.https.allow=always",
    "-c", "protocol.ssh.allow=always",
    "-c", "submodule.recurse=false",
    "-c", "fetch.recurseSubmodules=false",
    "-c", "transfer.fsckObjects=true",
    "-c", "gc.auto=0",
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
    "-c", "advice.detachedHead=false",
    "-c", "http.followRedirects=false",
    "-c", "uploadpack.allowFilter=false",
)


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = TIMEOUT,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    grow_limit: int = 0,
) -> tuple[int, str]:
    """One git command: (code, output). Never raises -- git failing is a thing
    to say on a page. `grow_limit`: stop it once `cwd` has grown by that much."""
    full_env = _base_env()
    full_env.update(env or {})
    argv = ["git", *_FIXED]
    if local_allowed():
        argv += ["-c", "protocol.file.allow=always"]
    argv += args
    before = _dir_bytes(cwd) if grow_limit else 0
    try:
        proc = subprocess.Popen(  # fixed argv, no shell
            argv,
            cwd=str(cwd),
            env=full_env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError:
        return 127, "git is not installed on this machine."
    except OSError as exc:
        return 1, str(exc)
    deadline = time.monotonic() + timeout
    out = ""
    try:
        if stdin is not None or not grow_limit:
            out, _ = proc.communicate(stdin, timeout=timeout)
        else:
            while True:
                try:
                    out, _ = proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        raise
                    if _dir_bytes(cwd) - before > grow_limit:
                        _kill(proc)
                        return 125, "The remote sends more than this store takes."
    except subprocess.TimeoutExpired:
        _kill(proc)
        return 124, f"git took longer than {int(timeout)}s."
    if grow_limit and _dir_bytes(cwd) - before > grow_limit:
        return 125, "The remote sends more than this store takes."
    return proc.returncode, (out or "").strip()


def _kill(proc: subprocess.Popen) -> None:
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()
    with contextlib.suppress(Exception):
        proc.communicate(timeout=5)


class Remote:
    """The environment one command to a remote runs in; a key's file lives
    only inside the `with`."""

    def __init__(self, address: str, credentials: Credentials | None = None) -> None:
        self.address = check_address(address)
        self.credentials = credentials or Credentials()
        self._key_dir: tempfile.TemporaryDirectory | None = None
        self.env: dict[str, str] = {}

    def __enter__(self) -> Remote:
        kind = transport(self.address)
        creds = self.credentials
        if kind == "https" and creds.token:
            pair = f"{creds.username or 'token'}:{creds.token}".encode()
            self.env.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": "Authorization: Basic " + base64.b64encode(pair).decode(),
            })
        if kind == "ssh":
            options = [
                "ssh", "-F", os.devnull,
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={known_hosts()}",
                "-o", "GlobalKnownHostsFile=" + os.devnull,
                "-o", "IdentitiesOnly=yes",
                "-o", "IdentityAgent=none",
                "-o", "ForwardAgent=no",
                "-o", "ClearAllForwardings=yes",
                "-o", "PermitLocalCommand=no",
                "-o", "ConnectTimeout=15",
            ]
            if creds.ssh_key:
                self._key_dir = tempfile.TemporaryDirectory(prefix="lembas-key-")
                key_path = Path(self._key_dir.name) / "key"
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR)
                with os.fdopen(fd, "w") as handle:
                    handle.write(creds.ssh_key.strip() + "\n")
                options += ["-i", str(key_path)]
            else:
                options += ["-o", "IdentityFile=" + os.devnull]
            self.env["GIT_SSH_COMMAND"] = " ".join(_quote(part) for part in options)
            self.env["GIT_SSH_VARIANT"] = "ssh"
        return self

    def __exit__(self, *exc) -> None:
        if self._key_dir is not None:
            self._key_dir.cleanup()
            self._key_dir = None

    def run(self, args: list[str], *, cwd: Path, grow_limit: int = 0) -> tuple[int, str]:
        code, out = run(args, cwd=cwd, timeout=NET_TIMEOUT, env=self.env, grow_limit=grow_limit)
        return code, scrub(out, self.credentials)


def _quote(part: str) -> str:
    return "'" + part.replace("'", "'\\''") + "'"


def scrub(text: str, credentials: Credentials | None = None) -> str:
    """git's words with nothing secret in them: a token, and any `user:pass@`."""
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1", text or "")
    if credentials is not None and credentials.token:
        text = text.replace(credentials.token, "•••")
    return text[:2000]


def forget_host_keys() -> None:
    """Forget every host key the store remembered (after a host changed its
    key on purpose)."""
    with contextlib.suppress(FileNotFoundError):
        known_hosts().unlink()


__all__ = [
    "MAX_FETCH_BYTES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "BadAddress",
    "Credentials",
    "Remote",
    "check_address",
    "forget_host_keys",
    "known_hosts",
    "local_allowed",
    "run",
    "scrub",
    "transport",
]
