"""Knowing where one command ends and the next begins, in the terminal panel.

Without this the panel can offer "the last forty rows of the screen", which is
hard-wrapped at the terminal's width with no way to tell a wrap from a newline.
That is not something to hand a model and call it the output of a command.

So the shell is given hooks that emit invisible markers around the prompt, the
command and its result -- OSC 133, which is what VS Code, WezTerm and Ghostty
all use, plus two of VS Code's private codes for the things 133 has no room
for. A real terminal that understands 133 is not confused by ours, and one that
does not consumes and discards them, which is why the bytes are fanned out to
the browser unchanged.

**Three things about the mechanism.**

The integration is written by the PTY command string itself, with `printf`.
The PTY runs that string through `$SHELL -c`, so it can branch on the shell's own
name and needs no probe, no second channel and no writable `$HOME`. Passing it
through the environment does not work over SSH: sshd's `AcceptEnv` is `LANG LC_*` on
every distribution anybody runs, so the variable is dropped silently -- and
bash reads `$BASH_ENV` only when non-interactive anyway. Feeding `source …` in
as keystrokes does work, and races a slow `.zshrc`, echoes into the scrollback,
and lands in shell history with no portable way to remove it.

Nothing needs hiding, and that is the point of choosing this mechanism. The
setup runs before the shell exists and never writes to the PTY's *input* side,
so there is nothing for the tty to echo. The first byte a viewer sees is the
first byte of their own prompt.

There is deliberately no `133;B`. It has to live at the end of `PS1`, and any
theme that rebuilds the prompt in a hook drops it silently every time. Every
marker here comes from a shell hook instead, so none of them depends on a
prompt string surviving somebody's dotfiles.

**Markers are advisory.** A program can print `\\e]133;D;0\\a` and move a
boundary. That is not a security problem -- the captured text is sanitised and
fenced either way, and a program could already print anything on screen -- but
nobody should later try to "validate" them.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# Everything ours is under these two. 133 is FinalTerm's de-facto convention;
# 633 is VS Code's private space, borrowed because the raw stream carries the
# *echoed* command with readline's editing escapes in it and is not
# recoverable, and there is no standard code for "here is the command line".
MARK_PROMPT = "A"  # 133;A  -- a prompt is about to be drawn
MARK_OUTPUT = "C"  # 133;C  -- output starts here
MARK_DONE = "D"  # 133;D;<exit>
MARK_COMMAND = "E"  # 633;E;<escaped command>
MARK_CWD = "P"  # 633;P;Cwd=<escaped path>
MARK_READY = "LEMBAS"  # 633;LEMBAS;<shell>;1

# A marker longer than this is not one of ours. `cat` of a binary file produces
# stray ESC ] regularly, and without a bound one of them would swallow the rest
# of the session into a buffer that never emptied.
MAX_MARKER_BYTES = 8 * 1024

_ESC = 0x1B
_BEL = 0x07


# --- The snippets ------------------------------------------------------------
# `__lembas_esc` exists because an OSC payload may contain no BEL and no ESC
# (either would end it ambiguously) and no ';' (which would split the fields).
# Everything else rides through. It is also what lets `base.clean_output`'s
# existing OSC pattern swallow a whole marker in one bite.

BASH_RC = r"""
# LLeMbas shell integration.
#
# --rcfile replaces bash's normal startup files rather than adding to them, so
# bash's login sequence is reproduced here, in bash's own order, and nothing
# anybody has in a dotfile is skipped.
if [ -r /etc/profile ]; then . /etc/profile; fi
for __lembas_rc in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
  if [ -r "$__lembas_rc" ]; then . "$__lembas_rc"; break; fi
done
unset __lembas_rc
if [ -r "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi

__lembas_esc() {
  local s=${1//\\/\\\\}
  s=${s//;/\\x3b}; s=${s//$'\n'/\\x0a}; s=${s//$'\r'/\\x0d}
  s=${s//$'\e'/\\x1b}; s=${s//$'\a'/\\x07}
  builtin printf '%s' "$s"
}

__lembas_emit() {
  if [ -n "$__lembas_running" ]; then
    builtin printf '\e]133;D;%s\a' "${__lembas_last:-0}"
    __lembas_running=
    __lembas_have=
  fi
  builtin printf '\e]633;P;Cwd=%s\a' "$(__lembas_esc "$PWD")"
  builtin printf '\e]133;A\a'
  __lembas_armed=1
}

# $BASH_COMMAND is the current *simple* command, so `a | b` would give "a".
# `history 1` is the whole line as typed, which is what somebody would
# recognise; it falls back when history is off.
__lembas_line() {
  local h
  h=$(HISTTIMEFORMAT='' builtin history 1 2>/dev/null) || {
    builtin printf '%s' "$BASH_COMMAND"; return; }
  if [[ $h =~ ^[[:space:]]*[0-9]+[[:space:]]+(.*)$ ]]; then
    builtin printf '%s' "${BASH_REMATCH[1]}"
  else
    builtin printf '%s' "$BASH_COMMAND"
  fi
}

# The exit status is captured *in the DEBUG trap*, not in PROMPT_COMMAND.
#
# This is the one genuinely subtle thing in the file. DEBUG fires before every
# simple command -- including each command inside PROMPT_COMMAND -- so anything
# reading $? from there has already had it overwritten by whatever ran a moment
# earlier, and by this trap's own command substitution. The first DEBUG firing
# after the user's command is the last place the real status exists, so it is
# taken there and held until the prompt emits it.
#
# `__lembas_have` is what stops the later firings (the ones inside
# PROMPT_COMMAND) overwriting it, and `return $__s` puts $? back so nothing
# downstream sees a status this trap invented.
__lembas_debug() {
  local __s=$?
  if [ -n "$__lembas_running" ] && [ -z "$__lembas_have" ]; then
    __lembas_last=$__s
    __lembas_have=1
  fi
  if [ -n "$__lembas_armed" ]; then
    __lembas_armed=
    builtin printf '\e]633;E;%s\a' "$(__lembas_esc "$(__lembas_line)")"
    builtin printf '\e]133;C\a'
    __lembas_running=1
  fi
  return $__s
}

trap '__lembas_debug' DEBUG

# Appended, so anything already there still runs and runs first.
if [ -n "${BASH_VERSINFO[0]}" ] && [ "${BASH_VERSINFO[0]}" -ge 5 ] \
   && [ "${PROMPT_COMMAND@a}" = "a" ]; then
  PROMPT_COMMAND=("${PROMPT_COMMAND[@]}" __lembas_emit)
else
  PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND; }__lembas_emit"
fi

builtin printf '\e]633;LEMBAS;bash;1\a'

# Self-deleting: bash has read the whole file by the time this runs, and one
# left in /tmp that a later shell might source is worse than any benefit.
rm -rf -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null
"""

# zsh re-reads $ZDOTDIR before *each* startup file, so every shim points it back
# at the user's directory, sources their file, and takes it again -- otherwise
# zsh finds the user's .zshrc and ours never loads.
#
# `LEMBAS_USER_ZDOTDIR` is passed in on the exec line and must not be guessed
# here. By the time .zshenv runs, `$ZDOTDIR` is already *our* directory, so a
# `${ZDOTDIR:-$HOME}` fallback in this file captures the wrong path and the
# shim ends up sourcing itself -- which looks like it works, right up to the
# point somebody notices none of their own configuration is loaded.
_ZSH_SHIM = r"""
: ${LEMBAS_USER_ZDOTDIR:=$HOME}
ZDOTDIR=$LEMBAS_USER_ZDOTDIR
[[ -r $ZDOTDIR/%(file)s ]] && source $ZDOTDIR/%(file)s
LEMBAS_USER_ZDOTDIR=$ZDOTDIR
ZDOTDIR=$LEMBAS_DIR
"""

ZSH_ENV = _ZSH_SHIM % {"file": ".zshenv"}
ZSH_PROFILE = _ZSH_SHIM % {"file": ".zprofile"}

ZSH_RC = (
    _ZSH_SHIM % {"file": ".zshrc"}
    + r"""
__lembas_esc() {
  local s=${1//\\/\\\\}
  s=${s//;/\\x3b}; s=${s//$'\n'/\\x0a}; s=${s//$'\r'/\\x0d}
  s=${s//$'\e'/\\x1b}; s=${s//$'\a'/\\x07}
  builtin print -rn -- $s
}

__lembas_precmd() {
  local __s=$?
  if [[ -n $__lembas_running ]]; then
    builtin printf '\e]133;D;%s\a' $__s
    __lembas_running=
  fi
  builtin printf '\e]633;P;Cwd=%s\a' "$(__lembas_esc $PWD)"
  builtin printf '\e]133;A\a'
}

# $1 is the line as typed, before alias and glob expansion -- what somebody
# would recognise. $2 and $3 are progressively more expanded and less useful.
__lembas_preexec() {
  builtin printf '\e]633;E;%s\a' "$(__lembas_esc $1)"
  builtin printf '\e]133;C\a'
  __lembas_running=1
}

# Prepended, not appended: whichever precmd runs first is the only one that
# sees the real $?, and a theme's hook will have clobbered it by the time a
# later one runs.
precmd_functions=(__lembas_precmd $precmd_functions)
preexec_functions=(__lembas_preexec $preexec_functions)

builtin printf '\e]633;LEMBAS;zsh;1\a'
"""
)

ZSH_LOGIN = (
    _ZSH_SHIM % {"file": ".zlogin"}
    + r"""
# The last file zsh reads, so this is where ZDOTDIR goes back for good.
ZDOTDIR=$LEMBAS_USER_ZDOTDIR
[[ -n $LEMBAS_DIR ]] && rm -rf -- $LEMBAS_DIR
unset LEMBAS_DIR LEMBAS_USER_ZDOTDIR
"""
)


def quote(value: str) -> str:
    """A single-quoted shell word, with embedded quotes escaped."""
    return "'" + value.replace("'", "'\\''") + "'"


def command_for(project_dir: str, *, integrate: bool = True) -> str | None:
    """What the PTY runs.

    Returns None for the account's plain login shell in the one case that has
    always returned it -- no project directory and no integration -- so the
    existing behaviour is byte for byte what it was.

    `$SHELL -c` is what this is put through, so it is POSIX sh and branches
    on the shell's own name. Anything that is not bash or zsh falls out of the
    `case` into exactly the line that was here before, because a terminal that
    works without markers is worth more than markers that break a terminal.
    Every step is `2>/dev/null`, so a full `/tmp` or a read-only home costs the
    markers and nothing else.
    """
    cd = f"cd {quote(project_dir)} 2>/dev/null; " if project_dir else ""
    if not integrate:
        if not project_dir:
            return None
        return f"{cd}exec ${{SHELL:-/bin/sh}} -l"

    return (
        f"{cd}umask 077; "
        # mkdir -m 700 fails on a path that already exists, which is what makes
        # the fallback safe when mktemp is missing.
        '__L=$(mktemp -d 2>/dev/null) || { '
        '__L=${TMPDIR:-/tmp}/.lembas-$$-$RANDOM; mkdir -m 700 "$__L"; }; '
        "case ${SHELL##*/} in "
        f'  bash) printf %s {quote(BASH_RC)} > "$__L/rc" 2>/dev/null && '
        '        exec bash --rcfile "$__L/rc" -i ;; '
        f'  zsh)  printf %s {quote(ZSH_ENV)} > "$__L/.zshenv" 2>/dev/null && '
        f'        printf %s {quote(ZSH_PROFILE)} > "$__L/.zprofile" 2>/dev/null && '
        f'        printf %s {quote(ZSH_RC)} > "$__L/.zshrc" 2>/dev/null && '
        f'        printf %s {quote(ZSH_LOGIN)} > "$__L/.zlogin" 2>/dev/null && '
        # The user's own ZDOTDIR is captured *here*, before it is replaced.
        '        LEMBAS_DIR="$__L" LEMBAS_USER_ZDOTDIR="${ZDOTDIR:-$HOME}" '
        '        ZDOTDIR="$__L" exec zsh -l ;; '
        "esac; "
        # Everything that did not exec lands here: an unknown shell, a failed
        # mkdir, a full /tmp. You still get a shell.
        'rm -rf -- "$__L" 2>/dev/null; exec ${SHELL:-/bin/sh} -l'
    )


# --- Reading them back -------------------------------------------------------
_UNESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})")


def unescape(value: str) -> str:
    """Undo `__lembas_esc`."""
    return _UNESCAPE.sub(lambda m: chr(int(m.group(1), 16)), value).replace("\\\\", "\\")


class Marks:
    """Pulls the markers out of a stream that arrives in any pieces.

    Deliberately not a regex over the scrollback. The pump is handed 64 KB at a
    time on no particular boundary, so the ESC and the `]` land in different
    frames often enough to matter -- which is the same reason nothing here
    decodes the bytes. This holds at most one partial marker and nothing else.
    """

    def __init__(
        self,
        on_mark: Callable[[str, str], None],
        on_text: Callable[[bytes], None] | None = None,
    ) -> None:
        self._on_mark = on_mark
        self._on_text = on_text
        self._buffer = bytearray()
        self._in_marker = False
        self._plain = bytearray()

    def feed(self, data: bytes) -> None:
        """Split the stream into markers and everything else.

        Both come back *in order* and as they are found, not after the whole
        chunk has been scanned. That matters: a shell frequently writes the
        command marker, the output and the finished marker in one 64KB read, so
        anything that scanned first and absorbed afterwards would find the
        capture already closed and keep nothing.
        """
        for byte in data:
            if not self._in_marker:
                # A lone ESC is held: the ']' may be in the next frame.
                if byte == _ESC:
                    self._flush()
                    self._buffer = bytearray([byte])
                    self._in_marker = True
                else:
                    self._plain.append(byte)
                continue

            if len(self._buffer) == 1:
                if byte != 0x5D:  # ']' -- some other escape sequence
                    # Not ours, so it is output like anything else. Emitted
                    # rather than dropped: `clean_output` strips it later, and
                    # swallowing it here would silently eat a colour change.
                    self._in_marker = False
                    self._plain += self._buffer
                    self._plain.append(byte)
                    self._buffer.clear()
                    continue
                self._buffer.append(byte)
                continue

            # Two legal terminators, and shells in the wild use both: BEL, and
            # ESC \ (ST). The ESC has to be *appended* rather than treated as
            # the start of something new, or the ST branch below can never fire.
            if byte == _BEL:
                self._finish()
                continue

            self._buffer.append(byte)
            if self._buffer.endswith(b"\x1b\\"):
                del self._buffer[-2:]
                self._finish()
                continue
            # An `ESC ]` inside a marker that was never terminated starts a new
            # one. Without this a truncated marker would eat the next.
            if self._buffer.endswith(b"\x1b]"):
                self._buffer = bytearray(b"\x1b]")
                continue
            if len(self._buffer) > MAX_MARKER_BYTES:
                # Not one of ours. Output is worth more than a marker.
                self._in_marker = False
                self._plain += self._buffer
                self._buffer.clear()

        self._flush()

    def _flush(self) -> None:
        if not self._plain:
            return
        if self._on_text is not None:
            self._on_text(bytes(self._plain))
        self._plain.clear()

    def _finish(self) -> None:
        payload = bytes(self._buffer[2:]).decode("utf-8", "replace")
        self._in_marker = False
        self._buffer.clear()

        code, _, rest = payload.partition(";")
        if code not in ("133", "633") or not rest:
            return
        kind, _, value = rest.partition(";")
        self._on_mark(kind, value)


__all__ = [
    "BASH_RC",
    "MARK_COMMAND",
    "MARK_CWD",
    "MARK_DONE",
    "MARK_OUTPUT",
    "MARK_PROMPT",
    "MARK_READY",
    "MAX_MARKER_BYTES",
    "Marks",
    "ZSH_ENV",
    "ZSH_LOGIN",
    "ZSH_PROFILE",
    "ZSH_RC",
    "command_for",
    "quote",
    "unescape",
]
