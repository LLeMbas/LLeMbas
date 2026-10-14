/*
  The terminal panel.

  Loaded only on a chat that can actually open a shell -- see the head and
  scripts blocks in chat/index.html -- because xterm is nearly three times the
  size of everything else vendored here. The Terminal object itself is built on
  the first *open* rather than on load, so even here nothing is parsed for
  somebody who never presses the button.

  Three things about xterm that are easy to get wrong, and cost an afternoon
  each:

  * `fit()` measures `offsetWidth`, which is 0 inside a `[hidden]` ancestor, so
    fitting while closed silently does nothing and leaves an 80-column terminal
    in a 34rem panel. Everything below is arranged so a fit only ever happens
    after the panel is visible.
  * A window `resize` event does not fire when the sidebar is toggled or a panel
    opens beside this one, which is by far the commonest way the panel changes
    size. Hence the ResizeObserver.
  * xterm does not read CSS variables. The theme is built from the computed
    style at open time and rebuilt when the theme changes, or switching to
    `shire` leaves a black rectangle in a light interface.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var panel = null;
  var term = null;
  var fit = null;
  var socket = null;
  var screen = null;
  var messageEl = null;
  var observer = null;
  var closedOnPurpose = false;
  /* "off" | "copy" | "send". Three states rather than a boolean, because the
     old one did the wrong one of them: it appended into the composer, on top of
     whatever was being typed there. A select rather than a cycling button --
     a button cannot say which of three states it is in. */
  var autoMode = "off";
  /* A linked device's shell marks its own commands -- OSC 133 (A prompt,
     B command, C output, D;exit) and OSC 7 (where it is) -- and the ready frame
     says so. Then this panel reads them itself: nothing is parsed on the
     server, which never sees the command line a device's shell ran. `osc` is
     false for every other shell, and everything below it stays as it was. */
  var osc = false;
  var pendingMark = null;
  var finished = [];
  var visiting = -1;
  var MAX_MARKED = 200;

  function say(text, isError) {
    if (!messageEl) return;
    messageEl.textContent = text;
    messageEl.classList.toggle("terminal__message--error", !!isError);
  }

  /* --- Theme -------------------------------------------------------------- */
  /* The sixteen ANSI slots, in the order xterm names them. A shell chooses
     these itself -- `ls --color`, a git diff, htop's meters -- so without them
     the panel rendered a foreign palette inside the application, and switching
     to Shire left dark-theme colours on parchment. */
  var ANSI = [
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightMagenta", "brightCyan", "brightWhite"
  ];

  function readTheme() {
    var style = getComputedStyle(document.documentElement);
    function token(name, fallback) {
      return (style.getPropertyValue(name) || "").trim() || fallback;
    }
    var theme = {
      background: token("--code-bg", "#07090B"),
      foreground: token("--ink", "#E2E8EC"),
      cursor: token("--accent", "#8FD8E8"),
      cursorAccent: token("--code-bg", "#07090B"),
      selectionBackground: token("--accent-soft", "rgba(143, 179, 204, 0.3)")
    };
    /* camelCase to --kebab-case: brightBlack -> --ansi-bright-black. A slot
       with no token is left off the object entirely rather than set to
       undefined, which xterm treats as a colour and renders as black. */
    ANSI.forEach(function (name) {
      var value = token(
        "--ansi-" + name.replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); }),
        ""
      );
      if (value) theme[name] = value;
    });
    return theme;
  }

  /* --- Sizing ------------------------------------------------------------- */
  function visible() {
    return panel && !panel.hasAttribute("hidden") && panel.offsetWidth > 0;
  }

  function refit() {
    if (!term || !fit || !visible()) return;
    try {
      fit.fit();
    } catch (error) {
      return;
    }
    send({ t: "resize", cols: term.cols, rows: term.rows });
  }

  /* --- The socket --------------------------------------------------------- */
  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
    }
  }

  function connect() {
    if (socket) return;
    /* No target yet. The same guard `repointTerminal` has, and it belongs here
       too: with no connection chosen `dataset.url` is "", so the URL below
       becomes `ws://host?cols=80&rows=24` -- a handshake against the app root,
       which fails into onerror and blames the proxy for something the reader
       simply has not chosen yet. */
    if (!panel.dataset.url) {
      say(t("Choose a connection above, and this panel will open a shell on it."));
      return;
    }
    closedOnPurpose = false;

    var base = location.protocol === "https:" ? "wss://" : "ws://";
    var url =
      base + location.host + panel.dataset.url +
      "?cols=" + (term.cols || 80) + "&rows=" + (term.rows || 24);

    say(t("Connecting…"));
    /* Held in a local as well as in `socket`, because every handler below has
       to know *which* connection it belongs to.

       `close()` queues its event rather than firing it, and both reconnect
       paths -- the `behind` frame and `repointTerminal` -- close the old
       socket and immediately call this function. So the old socket's `close`
       arrives *after* a new one has been assigned, and a handler that touched
       the module-level `socket` was reaching past its own connection into the
       live one. It set it to null: output kept arriving, because `onmessage`
       is bound to the object, while `send`, `term.onData` and the paste
       handler all gate on `socket && readyState === OPEN` -- so every
       keystroke was dropped and no resize was ever sent again, on a panel that
       looked perfectly healthy. And `closedOnPurpose` had already been cleared
       for the new connection, so it said "Disconnected. Close and reopen to
       reconnect." about a shell that had just reconnected. */
    var opened = new WebSocket(url);
    socket = opened;
    opened.binaryType = "arraybuffer";

    opened.onmessage = function (event) {
      if (socket !== opened) return;
      if (typeof event.data === "string") return control(event.data);
      /* Written straight through as bytes. xterm's decoder is stateful across
         calls, so a multi-byte character split across two frames still lands
         correctly -- which is exactly why the server never decodes either. */
      term.write(new Uint8Array(event.data));
    };

    opened.onclose = function () {
      if (socket !== opened) return;
      socket = null;
      if (!closedOnPurpose) say(t("Disconnected. Close and reopen to reconnect."));
    };

    opened.onerror = function () {
      if (socket !== opened) return;
      /* A failed handshake gives the page nothing: no status, no reason. So
         this is a guess, and it names the likeliest cause rather than
         pretending to know. */
      say(t("Could not connect. If this instance is behind a proxy, it may not be passing WebSocket upgrades through."), true);
    };
  }

  function control(raw) {
    var payload;
    try {
      payload = JSON.parse(raw);
    } catch (error) {
      return;
    }

    if (payload.t === "ready") {
      say(payload.shared
        ? t("Connected. This shell is also open in another tab, and they share a size.")
        : t("Connected."));
      if (payload.dir) {
        var where = panel.querySelector("[data-terminal-where]");
        if (where) where.textContent = payload.dir;
      }
      osc = !!payload.osc;
      showMarkControls();
      showLast("");
      applyIntegration();
      /* The server may have opened the shell at a size chosen by whoever got
         here first, so ask for ours now that there is something to ask. */
      refit();
      term.focus();
      return;
    }

    if (payload.t === "behind") {
      /* This window stopped reading and was disconnected so the others kept
         up. Reconnecting costs nothing: the scrollback is the state. */
      say(payload.message || t("Reconnecting…"));
      closedOnPurpose = true;
      if (socket) socket.close();
      socket = null;
      term.reset();
      forgetMarks();
      connect();
      return;
    }

    if (payload.t === "closed" || payload.t === "error") {
      say(payload.message || t("This terminal closed."), payload.t === "error");
      closedOnPurpose = true;
      /* Deliberately no reconnect. A new shell has lost the working directory,
         the environment and the half-typed command, and quietly substituting
         one is worse than saying the connection went. */
    }
  }

  /* --- Building it -------------------------------------------------------- */
  function build() {
    if (term) return true;
    if (typeof Terminal === "undefined" || typeof FitAddon === "undefined") {
      say(t("The terminal could not be loaded."), true);
      return false;
    }

    var style = getComputedStyle(document.documentElement);
    term = new Terminal({
      allowProposedApi: true,
      convertEol: false,
      cursorBlink: true,
      fontFamily: style.getPropertyValue("--font-mono").trim() || "monospace",
      /* xterm wants a number, so the token has to be a plain pixel value and
         is parsed back out here. Reading it rather than repeating 13 is what
         keeps it adjustable in one place with everything else. */
      fontSize: parseFloat(style.getPropertyValue("--terminal-font-size")) || 13,
      scrollback: 5000,
      theme: readTheme()
    });
    /* The module namespace is the UMD global, so the class is a property of
       it. `new FitAddon()` is the mistake that reads correctly. */
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(screen);
    /* Registered always, acting only when the device said its shell marks
       (`osc`): a handler that returns false leaves the sequence to xterm, which
       swallows an OSC it does not know -- exactly what happened before. */
    term.parser.registerOscHandler(133, onCommandMark);
    term.parser.registerOscHandler(7, onWhere);

    term.onData(function (data) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(new TextEncoder().encode(data));
      }
    });

    /* Ctrl+C is interrupt here, which is correct and will still surprise
       somebody. Copy and paste are the shifted pair, as in every terminal. */
    term.attachCustomKeyEventHandler(function (event) {
      if (!event.ctrlKey || !event.shiftKey || event.type !== "keydown") return true;
      /* The previous and the next command, with the device's marks. */
      if (osc && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
        visit(event.key === "ArrowUp" ? -1 : 1);
        return false;
      }
      var key = event.key.toLowerCase();
      if (key === "c") {
        var selection = term.getSelection();
        if (selection) navigator.clipboard.writeText(selection);
        return false;
      }
      if (key === "v") {
        navigator.clipboard.readText().then(function (text) {
          if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(new TextEncoder().encode(text));
          }
        });
        return false;
      }
      return true;
    });

    /* The panel changes size when the sidebar is toggled or the window is
       resized, and only the second of those fires a `resize` event. */
    if (window.ResizeObserver) {
      observer = new ResizeObserver(function () {
        refit();
      });
      observer.observe(panel);
    }
    return true;
  }

  function open() {
    if (!build()) return;
    /* Next frame: the panel has just had `hidden` removed and has no measured
       width yet, so fitting now would be the silent no-op this file exists to
       avoid. */
    requestAnimationFrame(function () {
      refit();
      connect();
      term.focus();
    });
  }

  /* --- A device shell's own marks ----------------------------------
     Markers, not line numbers: xterm moves a marker as the scrollback trims,
     and disposes it when its line goes, so a command scrolled out of the
     buffer is simply no longer there to visit. */
  function onCommandMark(data) {
    if (!osc || !term) return false;
    var parts = String(data).split(";");
    var kind = parts[0];
    if (kind === "A") {
      pendingMark = { prompt: term.registerMarker(0) };
    } else if (kind === "B") {
      pendingMark = pendingMark || {};
      pendingMark.input = term.registerMarker(0);
      pendingMark.inputX = term.buffer.active.cursorX;
    } else if (kind === "C") {
      pendingMark = pendingMark || {};
      pendingMark.output = term.registerMarker(0);
      pendingMark.command = commandText(pendingMark);
    } else if (kind === "D") {
      /* A D with no command before it is the first prompt's, and says nothing. */
      if (pendingMark && pendingMark.output) {
        pendingMark.exit = parseInt(parts[1], 10) || 0;
        pendingMark.end = term.registerMarker(0);
        commandDone(pendingMark);
      }
      pendingMark = null;
    }
    return true;
  }

  function onWhere(data) {
    if (!osc) return false;
    var text = String(data);
    var path = text.replace(/^file:\/\/[^/]*/, "");
    try { path = decodeURIComponent(path); } catch (error) { /* as it came */ }
    var where = panel && panel.querySelector("[data-terminal-where]");
    // textContent: a path off somebody's machine, never markup.
    if (where && path) where.textContent = path;
    return true;
  }

  /* The lines between two markers, wrapped lines joined back into one. */
  function linesBetween(from, to, firstX) {
    var buffer = term.buffer.active;
    var out = [];
    for (var y = from; y < to; y++) {
      var line = buffer.getLine(y);
      if (!line) continue;
      var text = line.translateToString(true, y === from ? (firstX || 0) : 0);
      if (line.isWrapped && out.length) out[out.length - 1] += text;
      else out.push(text);
    }
    while (out.length && !out[out.length - 1].trim()) out.pop();
    return out.join("\n");
  }

  function commandText(mark) {
    if (!mark.input || !mark.output || mark.input.line < 0) return "";
    return linesBetween(mark.input.line, Math.max(mark.output.line, mark.input.line + 1),
      mark.inputX).trim();
  }

  function outputOf(mark) {
    if (!mark.output || !mark.end || mark.output.line < 0 || mark.end.line < 0) return "";
    return linesBetween(mark.output.line, mark.end.line, 0);
  }

  function commandDone(mark) {
    var anchor = mark.prompt || mark.input || mark.output;
    if (anchor && anchor.line >= 0 && term.registerDecoration) {
      /* Its exit status beside the prompt it ran from: a class, coloured by
         the theme's tokens in app.css, so a theme change recolours it. */
      var decoration = term.registerDecoration({ marker: anchor, x: 0, width: 1 });
      if (decoration) {
        decoration.onRender(function (element) {
          element.classList.add("terminal__mark");
          element.classList.toggle("terminal__mark--fail", mark.exit !== 0);
          element.title = "exit " + mark.exit;
        });
        mark.decoration = decoration;
      }
    }
    finished.push(mark);
    if (finished.length > MAX_MARKED) {
      var old = finished.shift();
      if (old.decoration) old.decoration.dispose();
    }
    visiting = -1;
    showLast((mark.command || "…") + (mark.exit ? " · exit " + mark.exit : ""));
    if (autoMode !== "off") {
      var text = blockOf(mark);
      if (!text) return;
      if (autoMode === "copy") intoComposer(text, true);
      else sendStraightToChat(text);
    }
  }

  function blockOf(mark) {
    if (!mark) return "";
    var where = panel && panel.querySelector("[data-terminal-where]");
    var head = (where && where.textContent ? where.textContent + " " : "") + "$ " +
      (mark.command || "");
    return "```\n" + head + "\n" + outputOf(mark) + "\n```\n" +
      (mark.exit ? "exit " + mark.exit + "\n" : "");
  }

  function lastFinished() {
    for (var i = finished.length - 1; i >= 0; i--) {
      if (finished[i].output && finished[i].output.line >= 0) return finished[i];
    }
    return null;
  }

  function visit(step) {
    var live = finished.filter(function (mark) {
      var anchor = mark.prompt || mark.input || mark.output;
      return anchor && anchor.line >= 0;
    });
    if (!live.length) return;
    visiting = visiting < 0 ? live.length : visiting;
    visiting = Math.max(0, Math.min(live.length - 1, visiting + step));
    var anchor = live[visiting].prompt || live[visiting].input || live[visiting].output;
    term.scrollToLine(anchor.line);
  }

  function forgetMarks() {
    finished.forEach(function (mark) { if (mark.decoration) mark.decoration.dispose(); });
    finished = [];
    pendingMark = null;
    visiting = -1;
  }

  function showMarkControls() {
    if (!panel) return;
    panel.querySelectorAll("[data-terminal-prev], [data-terminal-next]").forEach(function (button) {
      button.hidden = !osc;
    });
  }

  /* --- Handing a command to the chat --------------------------------------
     Three buttons over one idea: the last command, its output, and where it
     ran. With the device's marks (`osc`) the block is read from this panel's
     own buffer between them; without, it is the screen as it appeared, and
     says so. */

  function applyIntegration() {
    if (!panel) return;
    var auto = panel.querySelector("[data-terminal-auto]");
    if (!auto) return;
    var usable = osc;
    auto.disabled = !usable;
    auto.title = usable
      ? t("What to do with each command you run")
      : t("This shell did not load the LLeMbas command markers, so there is no way to tell where the output of one command ends.");
    if (!usable && autoMode !== "off") setAuto("off");
  }

  /* The last command's one-line summary beside the buttons; "" clears it. */
  function showLast(summary) {
    var slot = panel && panel.querySelector("[data-terminal-last]");
    if (!slot) return;
    // textContent, always: this is a command line off somebody's machine.
    slot.textContent = summary || "";
  }

  var AUTO_SAID = {
    off: t("Commands are no longer attached automatically."),
    copy: t("Every command you run will be put into the message box."),
    send: t("Every command you run will be sent as a message on its own.")
  };

  function setAuto(mode) {
    autoMode = AUTO_SAID[mode] ? mode : "off";
    var select = panel && panel.querySelector("[data-terminal-auto]");
    if (select && select.value !== autoMode) select.value = autoMode;
    say(AUTO_SAID[autoMode]);
  }

  /* Sent, not typed. The composer is left entirely alone -- somebody may be
     half-way through a sentence in it, and overwriting that is the complaint
     this replaces. The thread receives whatever the server decides the message
     is: a streaming pair, or a single queued bubble if a reply is already being
     written. Nothing here needs to know which. */
  function sendStraightToChat(text) {
    var url = panel.dataset.url.replace(/\/terminal\/ws$/, "/messages");
    if (!window.htmx) return;
    window.htmx.ajax("POST", url, {
      target: "#thread",
      swap: "beforeend",
      values: { content: text }
    });
  }

  /* A selection always wins, in every state. People rely on it, and it is the
     only way to send part of something. */
  function selected() {
    var text = term ? term.getSelection() : "";
    return text && text.trim() ? text : "";
  }

  function scraped() {
    var lines = [];
    var buffer = term.buffer.active;
    var last = buffer.baseY + buffer.cursorY;
    for (var y = Math.max(0, last - 40); y <= last; y++) {
      var line = buffer.getLine(y);
      if (line) lines.push(line.translateToString(true));
    }
    return lines.join("\n").replace(/\n+$/, "");
  }

  /* The marked block, or falls back to the screen. (The automatic path reads
     a finished mark directly, in `commandDone`.) */
  function capture() {
    var chosen = selected();
    if (chosen) return Promise.resolve("```\n" + chosen + "\n```\n");

    /* The device's marks: the last command and its output, read from
       this panel's own buffer between its markers. */
    if (osc) {
      var mark = lastFinished();
      if (!mark) {
        say(panel.dataset.sayNothingYet || "Nothing has been run in this shell yet.");
        return Promise.resolve("");
      }
      return Promise.resolve(blockOf(mark));
    }

    var text = scraped();
    if (!text.trim()) {
      say(t("Nothing to send: select some output first."));
      return Promise.resolve("");
    }
    /* Said plainly rather than dressed up. Without markers this is the last
       forty rows as they appeared, wraps and all, and pretending otherwise
       would put a precise-looking block in front of a model that is not. */
    say(t("Copied the last of the screen, as it appeared. This shell does not mark where commands begin."));
    return Promise.resolve("```\n" + text + "\n```\n");
  }

  function intoComposer(text, quiet) {
    if (!text) return;
    var input = document.querySelector("[data-composer-input]");
    if (!input) return;
    input.value = input.value ? input.value.replace(/\s*$/, "\n\n") + text : text;
    if (window.lembas && window.lembas.autosize) window.lembas.autosize(input);
    if (window.lembas && window.lembas.paintComposer) window.lembas.paintComposer();
    /* Auto-send never steals focus: it fires while somebody is typing in the
       terminal, and yanking the caret out of a shell mid-command is the sort
       of thing that gets a feature switched off for good. */
    if (!quiet) input.focus();
  }

  function sendToChat() {
    if (!term) return;
    capture().then(function (text) {
      if (!text) return;
      /* Into the composer, never sent. What a machine printed is exactly the
         sort of text somebody should read before a model does, and the box is
         where that happens. */
      intoComposer(text, false);
    });
  }

  function copyToClipboard() {
    if (!term) return;
    capture().then(function (text) {
      if (!text) return;
      if (window.lembas && window.lembas.copyText) {
        window.lembas.copyText(text);
        say(t("Copied."));
      }
    });
  }

  /* --- Wiring ------------------------------------------------------------- */
  function start() {
    panel = document.querySelector("[data-terminal]");
    if (!panel) return;
    screen = panel.querySelector("[data-terminal-screen]");
    messageEl = panel.querySelector("[data-terminal-message]");

    panel.addEventListener("lembas:toggle", function (event) {
      if (event.detail && event.detail.open) open();
      /* Closing leaves the Terminal object and the socket alone. `write()` is
         internally queued, so disposing mid-output drops it, and keeping the
         object is what makes reopening instant. The session on the far side
         outlives this panel by design. */
    });

    panel.addEventListener("click", function (event) {
      if (event.target.closest("[data-terminal-send]")) {
        event.preventDefault();
        return sendToChat();
      }
      if (event.target.closest("[data-terminal-copy]")) {
        event.preventDefault();
        return copyToClipboard();
      }
      if (event.target.closest("[data-terminal-prev]")) {
        event.preventDefault();
        return visit(-1);
      }
      if (event.target.closest("[data-terminal-next]")) {
        event.preventDefault();
        return visit(1);
      }
    });

    panel.addEventListener("change", function (event) {
      var select = event.target.closest("[data-terminal-auto]");
      if (select) setAuto(select.value);
    });

    /* xterm holds colours as values, not as variables, so a theme change has
       to be pushed into it. */
    document.addEventListener("lembas:theme", function () {
      if (term) term.options.theme = readTheme();
    });

    /* The panel now belongs to a different shell: the new-chat screen changed
       connection or directory, so `dataset.url` has been rewritten and the
       socket is pointed at the wrong machine. Closing is deliberate -- hence
       the flag, which is what stops "Disconnected" being reported for something
       nobody lost -- and the screen is cleared because this really is a
       different shell, unlike a reconnect to the same one.

       Reconnecting is left to the panel being opened, so a target changed while
       the terminal is shut costs nothing. */
    window.lembas = window.lembas || {};
    window.lembas.repointTerminal = function () {
      if (socket) {
        closedOnPurpose = true;
        socket.close();
        socket = null;
      }
      if (term) term.reset();
      forgetMarks();
      osc = false;
      showMarkControls();
      if (panel && !panel.hidden && panel.dataset.url) connect();
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
