/*
  Typing affordances in the composer: `@` to attach something by name, and
  (from the commands section below) `/` to run something instead of sending.

  Both are the same shape -- a token at the caret opens a menu, the menu
  filters as you type, and choosing replaces the token -- so they share one
  menu and one keyboard handler rather than fighting over the composer.

  Three things here are not obvious.

  The keydown listener is registered with `capture: true`. app.js already has a
  document-level Enter handler that submits the form, and listeners on the same
  element in the same phase fire in registration order -- app.js loads first,
  so a bubble-phase listener here would never get to say "that Enter chose a
  menu item, it did not send the message".

  Nothing is inserted into the composer as HTML. Every name in the menu came
  off somebody's filesystem or out of their library.

  A chip is a chip. Choosing a file posts to a route that returns the same
  attachment chip an upload returns, so the composer learns nothing new and the
  remove button, the hidden file_ids input and `claim()` on send all work
  already.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var menu = null;
  var list = null;
  var footer = null;
  var open = false;
  var kind = "";
  var pending = null;
  var active = -1;

  /* commands.js loads first and defines these. Guarded anyway so that a page
     which does not carry it -- or one where it failed to parse -- still gets
     `@`, and a message beginning with a slash is simply sent. */
  function commands() {
    return window.lembasCommands || {
      list: function () { return document.createElement("div"); },
      find: function () { return null; },
      run: function () {}
    };
  }

  function commandList(query) { return commands().list(query); }
  function commandIn(value) { return commands().find(value); }
  function runCommand(name, rest) { commands().run(name, rest); }

  /* --- Where we are ------------------------------------------------------- */
  function composer() {
    return document.querySelector("[data-composer-input]");
  }

  function context() {
    var box = document.querySelector(".composer");
    var picker = document.querySelector('select[name="ssh_profile_id"]');
    var dir = document.querySelector("[data-dir-value]");
    return {
      chatId: (box && box.dataset.chatId) || "",
      /* A chat under way carries its connection on the composer; a new one is
         still choosing it, so the select and the hidden field are the truth. */
      profileId: picker ? picker.value : (box && box.dataset.profileId) || "",
      projectDir: dir ? dir.value : (box && box.dataset.projectDir) || "",
      /* The model the composer is writing to. Only the new-chat screen needs
         it -- a chat that exists answers "which data group" by itself -- but
         there it decides which library items may be offered at all. */
      modelId: (function () {
        var field = document.querySelector('.composer input[name="model_id"], [data-picker-input]');
        return field ? field.value : "";
      })()
    };
  }

  /*
    The token being typed, or null.

    `@` is claimed anywhere it follows whitespace, so `see @src/main.py` works
    mid-sentence, but not inside an email address -- `a@b` is not a mention and
    treating it as one would open a menu every time somebody typed one.
  */
  function tokenAt(input) {
    var value = input.value;
    var caret = input.selectionStart;
    if (caret !== input.selectionEnd) return null;

    var before = value.slice(0, caret);
    var at = before.lastIndexOf("@");
    if (at !== -1 && (at === 0 || /\s/.test(before[at - 1]))) {
      var query = before.slice(at + 1);
      if (!/\s/.test(query)) return { kind: "@", query: query, start: at, end: caret };
    }
    /* A slash command is only ever the first thing in the box. Anywhere else a
       slash is a path, a date or a fraction. */
    if (before[0] === "/" && before[1] !== "/") {
      var word = before.slice(1);
      if (!/\s/.test(word) && caret === value.length) {
        return { kind: "/", query: word, start: 0, end: caret };
      }
    }
    return null;
  }

  /* --- The menu ----------------------------------------------------------- */
  function build() {
    if (menu) return;
    var host = document.querySelector(".composer__inner");
    if (!host) return;
    menu = document.createElement("div");
    menu.className = "composer-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    list = document.createElement("div");
    menu.appendChild(list);
    /* What the keys do, in the one place somebody is looking when they need to
       know. Cheaper than a help sheet nobody opens. */
    footer = document.createElement("div");
    footer.className = "composer-menu__keys";
    menu.appendChild(footer);
    host.insertBefore(menu, host.firstChild);

    menu.addEventListener("mousedown", function (event) {
      /* Before the composer loses focus, or the caret position the choice is
         about to be written at is already gone. */
      event.preventDefault();
    });
    menu.addEventListener("click", function (event) {
      var option = event.target.closest("[data-mention-token], [data-command]");
      if (option) choose(option);
    });
  }

  function show() {
    build();
    if (!menu) return;
    if (footer) {
      // textContent: nothing here is markup, and one day somebody will want to
      // put a filename in it.
      footer.textContent = options().length
        ? (kind === "/"
            ? t("↑↓ choose · Tab complete · Enter run · Esc dismiss")
            : t("↑↓ choose · Enter attach · Esc dismiss"))
        : t("Esc dismiss");
    }
    menu.hidden = false;
    open = true;
  }

  function hide() {
    if (!menu) return;
    menu.hidden = true;
    open = false;
    kind = "";
    active = -1;
  }

  function options() {
    return menu ? Array.prototype.slice.call(
      menu.querySelectorAll("[data-mention-token], [data-command]")
    ) : [];
  }

  function highlight(index) {
    var all = options();
    if (!all.length) return;
    active = (index + all.length) % all.length;
    all.forEach(function (option, position) {
      option.classList.toggle("is-selected", position === active);
    });
    if (all[active].scrollIntoView) all[active].scrollIntoView({ block: "nearest" });
  }

  /* --- Filling it --------------------------------------------------------- */
  /* The linked device a chat runs on, or "": the chat's own, or the
     one the new-chat screen has selected. Its files come from it. */
  function deviceOf(where) {
    var box = document.querySelector(".composer");
    if (box && box.dataset.deviceToken) return box.dataset.deviceToken;
    return where.profileId.indexOf("device:") === 0 ? where.profileId.slice(7) : "";
  }

  function loadMentions(query) {
    var where = context();
    var url =
      "/api/files/mention-picker?q=" + encodeURIComponent(query) +
      "&chat_id=" + encodeURIComponent(where.chatId) +
      "&profile_id=" + encodeURIComponent(where.profileId) +
      "&project_dir=" + encodeURIComponent(where.projectDir) +
      "&model_id=" + encodeURIComponent(where.modelId);

    function text(response) { return response.ok ? response.text() : ""; }
    var asked = [fetch(url, { credentials: "same-origin" }).then(text)];
    /* A device chat's files are the device's, asked of it beside the
       library, and listed first: in an agent chat they are what `@` is for. A
       device that cannot answer gives back nothing, and the rest still shows. */
    var device = deviceOf(where);
    if (device) {
      asked.unshift(
        fetch(
          "/api/devices/" + encodeURIComponent(device) + "/files?q=" +
            encodeURIComponent(query) + "&chat_id=" + encodeURIComponent(where.chatId) +
            "&cwd=" + encodeURIComponent(where.projectDir),
          { credentials: "same-origin" }
        ).then(text).catch(function () { return ""; })
      );
    }

    Promise.all(asked)
      .then(function (parts) {
        /* The reply is a round trip late: the caret may have moved off the
           mention, or onto a slash, by the time it lands. */
        if (kind !== "@" || !list) return;
        var html = parts.join("");
        /* The library's "Nothing matches" is not true when the device found
           something, so it is left out then. */
        if (parts.length > 1 && parts[0].indexOf("picker__option") !== -1 &&
            parts[1].indexOf("picker__option") === -1) {
          html = parts[0];
        }
        list.innerHTML = html;
        show();
        highlight(0);
      })
      .catch(function () { hide(); });
  }

  function refresh() {
    var input = composer();
    if (!input) return;
    /* Built here, not lazily inside `show()`. It used to be, and everything
       below writes to `list` *before* calling `show()` -- so the first slash or
       at-sign ever typed threw on a null `list` and took the whole handler with
       it. The menu never appeared, in any browser, for the entire life of the
       feature. Anything that touches `list` builds first. */
    build();
    if (!list) return;

    var token = tokenAt(input);
    if (!token) return hide();

    kind = token.kind;
    if (token.kind === "/") {
      /* The device's commands, fetched again in the background: its
         skills and custom commands can change while the page is open. */
      if (commands().refresh) commands().refresh();
      list.innerHTML = "";
      list.appendChild(commandList(token.query));
      show();
      highlight(0);
      return;
    }

    clearTimeout(pending);
    pending = setTimeout(function () { loadMentions(token.query); }, 150);
  }

  /* --- Choosing ----------------------------------------------------------- */
  function replaceToken(text) {
    var input = composer();
    var token = tokenAt(input);
    if (!input || !token) return;
    var head = input.value.slice(0, token.start);
    var tail = input.value.slice(token.end);
    var written = (token.kind === "@" ? "@" : "/") + text + " ";
    input.value = head + written + tail;
    var caret = head.length + written.length;
    input.setSelectionRange(caret, caret);
    if (window.lembas && window.lembas.autosize) window.lembas.autosize(input);
    input.focus();
  }

  /*
    `complete` is Tab, `!complete` is Enter, and the difference matters for a
    command that takes an argument: Tab writes `/mode ` and leaves the caret
    after it so the value can be typed, Enter runs what is there. Picking with
    the mouse runs, because there is nowhere else for a click to mean.
  */
  function choose(option, complete) {
    if (option.dataset.command) {
      /* A device's command that takes something after it is
         completed on Enter too: it is sent as a message, so there is no
         second chance to add the argument once it has gone. */
      if (complete || option.hasAttribute("data-command-input")) {
        replaceToken(option.dataset.command);
        hide();
        /* Straight back, because `/mode ` is now a token with a space in it and
           the menu would otherwise stay open over the argument being typed. */
        return;
      }
      var input = composer();
      hide();
      /* Cleared before running. A command is the whole message, never part of
         one, so leaving `/help` sitting in the box after running it means the
         next Enter runs it again. */
      if (input) {
        input.value = "";
        if (window.lembas && window.lembas.autosize) window.lembas.autosize(input);
      }
      runCommand(option.dataset.command, "");
      return;
    }

    var where = context();
    /* The reference stays in the sentence being written *and* the contents
       come along as a chip. The first is what makes "change the thing in
       @main.py" read as a sentence; the second is what stops a small model
       having to spend a round fetching it. */
    // A device's path is read by the CLI's own `@` expander, where a
    // space ends the mention and `#` starts a line range: both go escaped.
    var token = option.dataset.mentionToken;
    if (option.hasAttribute("data-mention-device")) token = token.replace(/([\\ #])/g, "\\$1");
    replaceToken(token);
    hide();
    /* A device's file is only the reference: the device reads it when
       the prompt arrives, as its own terminal would. Nothing is copied here. */
    if (option.hasAttribute("data-mention-device")) return;

    var body = new FormData();
    body.append("chat_id", where.chatId);
    body.append("model_id", where.modelId);
    if (option.dataset.mentionFile) {
      body.append("profile_id", where.profileId);
      body.append("path", option.dataset.mentionFile);
      attach("/api/files/from-project", body);
    } else if (option.dataset.mentionKnowledge) {
      body.append("document_id", option.dataset.mentionKnowledge);
      attach("/api/files/from-knowledge", body);
    } else if (option.dataset.mentionNote) {
      body.append("note_id", option.dataset.mentionNote);
      attach("/api/files/from-note", body);
    } else if (option.dataset.mentionSkill) {
      body.append("skill_id", option.dataset.mentionSkill);
      attach("/api/files/from-skill", body);
    } else if (option.dataset.mentionAttachment) {
      body.append("attachment_id", option.dataset.mentionAttachment);
      attach("/api/files/from-attachment", body);
    } else if (option.dataset.mentionUrl) {
      body.append("url", option.dataset.mentionUrl);
      attach("/api/files/link", body);
    } else if (option.dataset.mentionBase) {
      /* Not an attachment: nothing is copied, and what changes is what this
         chat is allowed to search. It goes on the chat, so the route is the
         chat's own. */
      body.append("base_id", option.dataset.mentionBase);
      attach("/api/chats/" + where.chatId + "/bases", body);
    }
  }

  function attach(url, body) {
    var target = document.getElementById("attachments");
    if (!target) return;
    fetch(url, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        target.insertAdjacentHTML("beforeend", html);
        // The chip's remove button is htmx-driven and inert until announced.
        if (window.htmx) window.htmx.process(target.lastElementChild);
      })
      .catch(function () {
        if (window.lembas) window.lembas.notify(t("Could not attach that."), { kind: "error" });
      });
  }

  /* --- Marking the tokens as they are typed --------------------------------
     Backgrounds only. The mirror's text is transparent and exists purely to
     put a rectangle in the right place; the visible glyphs are still the
     textarea's own. Everything here is set as textContent or built with
     createElement -- what is in the box is the one string a person controls
     exactly, and it must never become markup. */
  function mirrorEl() {
    return document.querySelector("[data-composer-mirror]");
  }

  /* The same rule the server applies to a sent message: `@` at the start or
     after whitespace, so an email address is not a file reference. */
  var MENTION = /(^|\s)(@[^\s@]+)/g;

  function paint() {
    var mirror = mirrorEl();
    var input = composer();
    if (!mirror || !input) return;

    var value = input.value;
    mirror.replaceChildren();

    /* A command is marked only when it resolves. `/thoughts on this` is not a
       command and must not look like one before it is sent -- that ambiguity
       is the whole reason `//` exists. */
    var rest = value;
    var command = commandIn(value);
    if (command) {
      var head = "/" + command.name;
      mirror.appendChild(token(head, "tok-command"));
      rest = value.slice(head.length);
    }

    var last = 0;
    var match;
    MENTION.lastIndex = 0;
    while ((match = MENTION.exec(rest)) !== null) {
      mirror.appendChild(document.createTextNode(rest.slice(last, match.index) + match[1]));
      mirror.appendChild(token(match[2], "tok-mention"));
      last = match.index + match[0].length;
    }
    // A trailing newline collapses in a div; the space keeps the last line.
    mirror.appendChild(document.createTextNode(rest.slice(last) + "\n"));
    mirror.scrollTop = input.scrollTop;
  }

  function token(text, cls) {
    var span = document.createElement("span");
    span.className = cls;
    span.textContent = text;
    return span;
  }

  /* --- Keys --------------------------------------------------------------- */
  document.addEventListener(
    "keydown",
    function (event) {
      var input = event.target.closest && event.target.closest("[data-composer-input]");
      if (!input) return;

      if (open) {
        if (event.key === "Escape") {
          event.stopPropagation();
          event.preventDefault();
          return hide();
        }
        if (event.key === "ArrowDown") {
          event.preventDefault();
          return highlight(active + 1);
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          return highlight(active - 1);
        }
        if (event.key === "Enter" || event.key === "Tab") {
          var all = options();
          if (all.length && active >= 0) {
            /* Capture phase, so app.js's Enter-to-send never sees this one.
               Without stopping it the message would be sent *and* the menu
               item chosen. */
            event.preventDefault();
            event.stopPropagation();
            choose(all[active], event.key === "Tab");
            return;
          }
        }
      }

      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        /* Not a menu key: a command typed in full and submitted. Handled here
           rather than on submit so the form is never posted at all -- a
           command is not a message and must not become one if it is unknown. */
        var command = commandIn(input.value);
        if (command) {
          event.preventDefault();
          event.stopPropagation();
          input.value = "";
          if (window.lembas && window.lembas.autosize) window.lembas.autosize(input);
          runCommand(command.name, command.rest);
        }
      }
    },
    true
  );

  document.addEventListener("input", function (event) {
    if (!event.target.closest("[data-composer-input]")) return;
    refresh();
    paint();
  });

  /* The textarea scrolls once it hits data-max-height, and the mirror has to
     go with it or the rectangles drift off the words. */
  document.addEventListener(
    "scroll",
    function (event) {
      var input = event.target;
      if (!input.closest || !input.closest("[data-composer-input]")) return;
      var mirror = mirrorEl();
      if (mirror) mirror.scrollTop = input.scrollTop;
    },
    true
  );

  /* Sending, resetting, dictating and pasting all change the value without an
     `input` event that reaches here, so the mirror is repainted after any htmx
     swap and once at load. */
  document.addEventListener("DOMContentLoaded", paint);
  document.addEventListener("htmx:afterSwap", paint);
  document.addEventListener("htmx:afterSettle", paint);

  /* And after the *request*, a frame later, which is the one that matters on
     send. htmx fires afterSwap and afterSettle before afterRequest, and the
     composer empties itself from `hx-on::after-request` -- so every repaint
     above ran while the box still held the message, and the highlight stayed
     behind over an empty field until the next keystroke repainted it.

     A frame later for two reasons: `form.reset()` fires its `reset` event
     *before* the fields are actually cleared, and reading the value in the same
     turn would paint the text that is about to disappear. */
  function repaintSoon() {
    if (window.requestAnimationFrame) window.requestAnimationFrame(paint);
    else setTimeout(paint, 0);
  }
  document.addEventListener("htmx:afterRequest", repaintSoon);
  document.addEventListener("reset", repaintSoon);

  document.addEventListener("click", function (event) {
    if (!event.target.closest(".composer-menu") &&
        !event.target.closest("[data-composer-input]")) hide();
  });

  /* There was a `[data-mention-open]` handler here, for a button in the scope
     menu that typed an `@` on your behalf. Both are gone: the `@` key does it,
     and a menu you open in order to insert one character is a longer way round
     than the character. Nothing else in the file knew about it -- the token is
     recognised on `input`, wherever the `@` came from. */

  window.lembas = window.lembas || {};
  window.lembas.closeComposerMenu = hide;
  /* Anything that writes into the composer from outside -- dictation, the
     terminal's Send, a slash command that clears it -- calls this so the
     rectangles keep up. */
  window.lembas.paintComposer = paint;
})();
