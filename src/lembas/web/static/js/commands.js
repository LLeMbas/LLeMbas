/*
  Slash commands, and the keyboard shortcuts that do the same jobs.

  Both live here, in one table, so `/help` cannot describe a shortcut that no
  longer exists. Every entry does something that was already possible by
  clicking -- none of this is new server behaviour except `/usage`, which is a
  question the interface could not previously answer at all.

  The rule that matters: a message that merely *starts* with a slash must still
  send. `//` escapes, an unrecognised command is left alone and posted as text,
  and only an exact match against this table is intercepted. Silently eating
  somebody's message is a far worse failure than an unknown command.

  Shortcuts are Alt-based rather than Ctrl+Shift: the browser owns
  Ctrl+Shift+T, N and W and will not give them up. They are matched on
  `event.code`, which is the physical key, so a Dvorak or a Slovak layout gets
  the same shortcuts rather than whichever letters happen to sit there.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  function el(selector) { return document.querySelector(selector); }
  function chat() {
    var box = el(".composer");
    return (box && box.dataset.chatId) || "";
  }
  function isAgent() { return !!el('[name="agent_mode"]'); }
  function post(url, options) {
    return fetch(url, Object.assign({ method: "POST", credentials: "same-origin" }, options || {}));
  }
  function note(message, kind) {
    if (window.lembas && window.lembas.notify) {
      window.lembas.notify(message, { kind: kind || "info" });
    }
  }

  /* --- Shortcuts ---------------------------------------------------------- */
  var SHORTCUTS = [
    { keys: "Ctrl/⌘ + K", what: t("Open the command menu") },
    { keys: "Ctrl/⌘ + Enter", what: t("Send, from anywhere on the page") },
    { keys: "Alt + M", what: t("Dictate") },
    { keys: "Alt + R", what: t("Read the last reply aloud") },
    { keys: "Alt + 1 … 4", what: "Manual, Edit, Auto, Plan" },
    { keys: "Alt + E", what: t("Canvas") },
    { keys: "Alt + T", what: t("Terminal") },
    { keys: "Alt + I", what: t("Inspector") },
    { keys: "Alt + B", what: t("Sidebar") },
    { keys: "Alt + N", what: t("New chat") },
    { keys: t("↑ in an empty box"), what: t("Edit your last message") },
    { keys: "Esc", what: t("Close what is open, or stop the reply") },
    { keys: "Ctrl + Shift + C / V", what: t("Copy and paste inside the terminal") }
  ];

  /* --- The table ---------------------------------------------------------- */
  var COMMANDS = [
    {
      name: "help",
      summary: t("Commands and keyboard shortcuts"),
      run: function () { helpSheet(); }
    },
    {
      name: "usage",
      summary: t("Tokens and context used by this chat"),
      when: function () { return !!chat(); },
      run: function () {
        fetch("/api/chats/" + chat() + "/usage", { credentials: "same-origin" })
          .then(function (r) { return r.text(); })
          .then(function (html) { sheet(t("Usage"), html); })
          .catch(function () { note(t("Could not read this chat's usage."), "error"); });
      }
    },
    {
      name: "compact",
      summary: t("Summarise the earlier turns so they stop costing context"),
      when: function () { return !!chat() && !!el("#thread .msg"); },
      run: compact
    },
    {
      name: "effort",
      summary: t("How hard a reasoning model should think"),
      argument: "low | medium | high | off",
      /* Offered wherever there is a model, not only where the control is.
         `available()` filters `find()` and `run()` as well as the menu, so a
         command hidden here is not merely unlisted -- typing it in full stops
         being a command and gets sent as a message. Better to answer. */
      when: function () { return !!el('[name="model_id"]'); },
      run: function (rest) { setEffort(rest); }
    },
    {
      name: "mode",
      summary: t("Approval mode: manual, edit, auto or plan"),
      argument: "manual | edit | auto | plan",
      when: isAgent,
      run: function (rest) {
        var select = el('[name="agent_mode"]');
        var wanted = (rest || "").trim().toLowerCase();
        if (!wanted) return note(t("Modes: manual, edit, auto, plan."));
        var found = Array.prototype.find.call(select.options, function (option) {
          return option.value === wanted;
        });
        if (!found) return note(t("“%(mode)s” is not a mode.", { mode: wanted }), "error");
        select.value = wanted;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        note(t("Mode set to %(mode)s.", { mode: found.textContent.trim() }));
      }
    },
    {
      name: "title",
      summary: t("Rename this chat"),
      argument: t("the new title"),
      when: function () { return !!chat(); },
      run: function (rest) {
        var wanted = (rest || "").trim();
        if (!wanted) return note(t("Give it a title: /title Something."));
        var body = new FormData();
        body.append("title", wanted);
        fetch("/api/chats/" + chat(), { method: "PATCH", body: body, credentials: "same-origin" })
          .then(function () {
            /* Both places the title appears. The heading alone left the sidebar
               row showing the old name until the next reload, which reads as a
               rename that half worked -- and is the reason the route now hands
               back the out-of-band pair for every other caller. This one is a
               bare fetch rather than htmx, so it sets them itself.

               textContent, never innerHTML: this is text somebody typed. */
            [el("#chat-title"), el("#chat-link-label-" + chat())].forEach(function (node) {
              if (node) node.textContent = wanted;
            });
            note(t("Renamed."));
          });
      }
    },
    {
      name: "terminal",
      summary: t("Show or hide the terminal"),
      when: function () { return !!el("#terminal"); },
      run: function () { toggle("#terminal", "side"); }
    },
    {
      name: "inspector",
      summary: t("Show or hide the request inspector"),
      when: function () { return !!el("#inspector"); },
      run: function () { toggle("#inspector", "side"); }
    },
    { name: "sidebar", summary: t("Show or hide the sidebar"), run: function () { toggle("#sidebar"); } },
    {
      name: "theme",
      summary: t("Switch theme"),
      /* Read from the document rather than written here, for the reason
         app.js's own list is: an administrator can define a theme, and a
         literal pair would leave `/theme dusk` silently toggling instead. */
      argument: function () {
        return (document.documentElement.dataset.themes || "moria:moria shire:shire")
          .split(/\s+/).map(function (entry) { return entry.split(":")[0]; }).join(" | ");
      },
      run: function (rest) {
        var wanted = (rest || "").trim().toLowerCase();
        var known = (document.documentElement.dataset.themes || "").split(/\s+/)
          .map(function (entry) { return entry.split(":")[0]; });
        if (wanted && known.indexOf(wanted) !== -1) window.lembas.applyTheme(wanted);
        else window.lembas.toggleTheme();
      }
    },
    {
      name: "image",
      summary: t("Draw a picture"),
      argument: t("what to draw"),
      /* Offered wherever there is a chat, not only where image generation is
         switched on. `available()` filters `run()` as well as the menu, so a
         command hidden here stops being a command and gets *sent as a message*
         -- and "/image a red bicycle" arriving as prose is worse than being
         told the feature is off. The server answers either way. */
      when: function () { return !!chat(); },
      run: function (rest) {
        var wanted = (rest || "").trim();
        if (!wanted) return note(t("Say what to draw: /image a red bicycle in the rain."));
        var thread = el("#thread");
        if (!thread) return;

        var body = new FormData();
        body.append("content", wanted);
        /* The whole of what this command is. The turn goes through the ordinary
           path -- same route, same bubbles, same stream -- and carries one extra
           field that makes the first round call the image tool instead of
           deciding whether to. The words still say what is wanted, so an
           endpoint that ignores tool_choice steers on those alone. */
        body.append("force_tool", "image_generate");

        fetch("/api/chats/" + chat() + "/messages", {
          method: "POST",
          body: body,
          credentials: "same-origin"
        })
          .then(function (r) { return r.text(); })
          .then(function (html) {
            thread.insertAdjacentHTML("beforeend", html);
            /* Without this the assistant bubble's sse-connect is inert markup
               and the reply never starts -- the same reason /compact processes
               the thread it swapped in. */
            if (window.htmx) window.htmx.process(thread);
            if (window.lembas.scrollThread) window.lembas.scrollThread(true);
          })
          .catch(function () { note(t("Could not start the image."), "error"); });
      }
    },
    { name: "new", summary: t("Start a new chat"), run: function () { window.location = "/chat"; } },
    {
      name: "temp",
      summary: t("Start a temporary chat that saves nothing"),
      run: function () {
        /* On the new-chat screen the model, folder and kind already chosen are
           in the query string. Add the flag to them rather than starting over,
           or the chat is made on the default model. */
        var query = new URLSearchParams(
          window.location.pathname === "/chat" ? window.location.search : ""
        );
        query.set("temporary", "1");
        window.location = "/chat?" + query.toString();
      }
    },
    {
      name: "continue",
      summary: t("Carry on from where the reply stopped"),
      run: function () {
        var button = el("#thread .msg--assistant.is-newest [data-continue]");
        if (button) button.click();
        else note(t("There is no reply to continue yet."));
      }
    },
    {
      name: "stop",
      summary: t("Stop the reply being written"),
      run: function () {
        var button = el('[data-composer-action="stop"]');
        if (button) button.click();
        else note(t("Nothing is being written."));
      }
    },
    { name: "devices", summary: t("Your devices: the LLeMbas CLI signed in as you"), run: go("/settings?tab=devices") },
    { name: "scheduled", summary: t("Your scheduled tasks"), run: go("/settings?tab=scheduled") }
  ];

  /* `/devices` and `/scheduled` are tabs of Settings, and on a desktop
     Settings opens as a window over the chat (ui.js) -- which is the whole
     reason to type a command here rather than click away. `openSettings` says
     false for anything it will not take (not Settings, a phone), and that is
     a navigation as before. */
  function go(url) {
    return function () {
      var lembas = window.lembas;
      if (lembas && lembas.openSettings && lembas.openSettings(url)) return;
      window.location = url;
    };
  }

  /* --- Reading the project directory again --------------------------------
     The listing is cached for five minutes and only ever built when a reply
     starts, so anything done in the terminal panel -- a checkout, a build --
     is invisible to it until then. This is the "look again now". */
  function efforts() {
    var select = el("[data-effort]");
    if (!select) return [];
    return Array.prototype.map
      .call(select.options, function (option) { return option.value; })
      .filter(function (value) { return value !== "off"; });
  }

  function setEffort(rest) {
    var select = el("[data-effort]");
    if (!select) {
      return note(
        t("This model is not marked as a reasoning model, so effort would do nothing. An administrator can mark it on the model's page."),
        "error"
      );
    }
    var available = efforts();
    var listed = available.join(", ");
    var wanted = (rest || "").trim().toLowerCase();
    if (!wanted) {
      return note(
        available.indexOf(select.value) === -1
          ? t("No effort is being sent. Try %(levels)s.", { levels: listed })
          : t("Effort is %(level)s. /effort %(levels)s, or off.", { level: select.value, levels: listed })
      );
    }
    /* "off" is the option's real value, not an empty string: the new-chat form
       cannot tell an absent field from an empty one, so the picker sends a
       sentinel and this has to match it. "default" and "none" still work,
       because somebody's fingers will type them. */
    if (wanted === "default" || wanted === "none") wanted = "off";
    else if (wanted !== "off" && available.indexOf(wanted) === -1) {
      return note(
        t("“%(level)s” is not an effort this model takes. Try %(levels)s or off.", { level: wanted, levels: listed }),
        "error"
      );
    }
    select.value = wanted;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    note(
      wanted === "off"
        ? t("Effort cleared; nothing is sent.")
        : t("Effort set to %(level)s.", { level: wanted })
    );
  }

  /* --- Compacting --------------------------------------------------------
     One implementation, reached from the command and from the overflow menu
     alike. The menu used to do its own hx-post, which meant two code paths and
     a spinner on neither -- so after confirming, the menu closed and nothing
     visible happened for however long the summarising model took.

     The `Generation.status` channel that says "Summarising earlier messages…"
     during *automatic* compaction cannot be borrowed: it lives inside the
     streaming bubble, and this endpoint refuses to run while any message is
     unfinished, so there is no bubble to put it in. */
  var compacting = false;

  function compact() {
    if (compacting) return note(t("Already summarising."));
    var thread = el("#thread");
    if (!thread) return;

    window.lembas.confirm(
      t("Summarise everything before the last reply? The messages stay in the transcript; they just stop being sent to the model."),
      { title: t("Compact this chat"), label: t("Compact") }
    ).then(function (yes) {
      if (!yes) return;

      compacting = true;
      var working = document.createElement("div");
      working.className = "thread__working";
      // The same words the automatic path uses, so the two do not look like
      // different features.
      working.innerHTML =
        '<span class="dots"><i></i><i></i><i></i></span>' +
        "<span>" + t("Summarising the earlier messages…") + "</span>";
      thread.appendChild(working);
      if (window.lembas.scrollThread) window.lembas.scrollThread(true);

      function done() {
        compacting = false;
        working.remove();
      }

      post("/api/chats/" + chat() + "/compact")
        .then(function (r) { return r.ok ? r.text() : Promise.reject(r); })
        .then(function (html) {
          compacting = false;
          // The response replaces the thread wholesale, placeholder included.
          thread.innerHTML = html;
          if (window.htmx) window.htmx.process(thread);
          if (window.lembas.scrollThread) window.lembas.scrollThread(true);
        })
        .catch(function (r) {
          done();
          /* The endpoint's four 409s are written for a person to read -- "Wait
             for the current reply to finish, then compact." -- and until now
             reached nobody at all. */
          if (r && r.json) {
            r.json()
              .then(function (body) { note(body.detail || t("Could not compact."), "error"); })
              .catch(function () { note(t("Could not compact this chat."), "error"); });
          } else {
            note(t("Could not compact this chat."), "error");
          }
        });
    });
  }

  function toggle(selector, group) {
    var panel = el(selector);
    if (panel && window.lembas.setPanel) {
      window.lembas.setPanel(selector, panel.hasAttribute("hidden"), group);
    }
  }

  function available() {
    return COMMANDS.filter(function (command) { return !command.when || command.when(); });
  }

  /* --- A linked device's own commands ------------------------------
     A device chat's session offers its custom commands, its skills and the
     built-ins that work without its terminal. They are listed after these,
     marked as the device's, and running one sends `/name args` as the message
     -- the device expands it as its terminal would. A name this table already
     has stays this table's: `/compact` here already compacts the device's
     session, and two entries for one name would be one that never runs.

     Fetched, not rendered: the set changes as skills are added on the device,
     and on the new-chat screen the device and the directory are still being
     chosen. Nothing is cached in the page beyond the last answer. */
  var deviceCommands = [];
  var deviceAsked = "";
  var deviceAskedAt = 0;

  function deviceSource() {
    var box = el(".composer");
    var token = (box && box.dataset.deviceToken) || "";
    if (!token) {
      var picker = el('select[name="ssh_profile_id"]');
      var value = picker ? picker.value : "";
      token = value.indexOf("device:") === 0 ? value.slice(7) : "";
    }
    if (!token) return "";
    var dir = el("[data-dir-value]");
    return "/api/devices/" + encodeURIComponent(token) + "/commands?chat_id=" +
      encodeURIComponent(chat()) + "&cwd=" +
      encodeURIComponent(dir ? dir.value : (box && box.dataset.projectDir) || "");
  }

  function refreshDevice(force) {
    var url = deviceSource();
    if (!url) {
      deviceCommands = [];
      deviceAsked = "";
      return;
    }
    /* At most every few seconds for the same chat and directory: the menu asks
       on every slash typed. */
    if (!force && url === deviceAsked && Date.now() - deviceAskedAt < 5000) return;
    deviceAsked = url;
    deviceAskedAt = Date.now();
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : { commands: [] }; })
      .then(function (body) {
        if (url !== deviceAsked) return;
        deviceCommands = (body.commands || []).map(function (command) {
          return {
            name: command.name,
            summary: command.description || "",
            argument: command.hint || "",
            device: true
          };
        });
      })
      .catch(function () { deviceCommands = []; });
  }

  function fromDevice() {
    var local = available().map(function (command) { return command.name; });
    return deviceCommands.filter(function (command) {
      return local.indexOf(command.name) === -1;
    });
  }

  /* Sent as the message it is: the composer's own submit, so it is queued
     behind a reply being written exactly as typed text would be. */
  function sendToDevice(name, rest) {
    var input = el("[data-composer-input]");
    if (!input || !input.form) return;
    input.value = "/" + name + (rest ? " " + rest : "");
    if (window.lembas && window.lembas.autosize) window.lembas.autosize(input);
    input.form.requestSubmit();
  }

  /* --- What the composer calls -------------------------------------------- */
  function list(query) {
    var needle = (query || "").toLowerCase();
    var matches = available().filter(function (command) {
      return command.name.indexOf(needle) === 0;
    });
    var device = fromDevice().filter(function (command) {
      return command.name.toLowerCase().indexOf(needle) === 0;
    });

    var wrap = document.createElement("div");
    if (!matches.length && !device.length) {
      var empty = document.createElement("p");
      empty.className = "muted text-sm";
      empty.style.padding = "var(--sp-3)";
      empty.textContent = t("No command called “%(name)s”. It will be sent as a message.", { name: query });
      wrap.appendChild(empty);
      return wrap;
    }

    if (matches.length) wrap.appendChild(group(t("Commands"), matches));
    if (device.length) {
      var box = el(".composer");
      wrap.appendChild(group((box && box.dataset.deviceLabel) || t("On the device"), device));
    }
    return wrap;
  }

  function group(title, commands) {
    var holder = document.createElement("div");
    var heading = document.createElement("p");
    heading.className = "picker__group";
    heading.textContent = title;
    holder.appendChild(heading);

    var items = document.createElement("ul");
    items.className = "picker__list";
    commands.forEach(function (command) {
      var row = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "picker__option";
      button.dataset.command = command.name;
      /* The device's: sent as a message, and completed rather than sent when
         it takes something after its name. */
      if (command.device && argumentOf(command)) button.setAttribute("data-command-input", "");

      var body = document.createElement("span");
      body.className = "picker__option-body";
      var name = document.createElement("span");
      name.className = "picker__option-name";
      var hint = argumentOf(command);
      name.textContent = "/" + command.name + (hint ? " " + hint : "");
      var summary = document.createElement("span");
      summary.className = "picker__option-note";
      summary.textContent = command.summary;
      body.appendChild(name);
      body.appendChild(summary);
      button.appendChild(body);
      row.appendChild(button);
      items.appendChild(row);
    });
    holder.appendChild(items);
    return holder;
  }

  /*
    A command, or null -- and null is the important half.

    Anything not matching exactly is left for the composer to send as an
    ordinary message, and `//` strips one slash on the way. A chat application
    that swallows a message because it began with a slash has done something
    much worse than failing to recognise a command.
  */
  function find(value) {
    if (value[0] !== "/" || value[1] === "/") return null;
    var match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/.exec(value.trim());
    if (!match) return null;
    var found = available().find(function (command) { return command.name === match[1]; });
    return found ? { name: found.name, rest: match[2] || "" } : null;
  }

  function run(name, rest) {
    var found = available().find(function (command) { return command.name === name; });
    if (found) return found.run(rest || "");
    /* A device's command chosen from the menu. One typed in full never
       arrives here: `find` knows only this table, so it is sent as written. */
    var mine = fromDevice().find(function (command) { return command.name === name; });
    if (mine) sendToDevice(mine.name, rest || "");
  }

  /* --- Sheets ------------------------------------------------------------- */
  function sheet(title, html) {
    var dialog = document.createElement("dialog");
    dialog.className = "dialog dialog--wide";
    var form = document.createElement("div");
    form.className = "dialog__form";
    var heading = document.createElement("h2");
    heading.className = "dialog__title";
    heading.textContent = title;
    var body = document.createElement("div");
    // Server-rendered and already escaped there; nothing user-typed reaches
    // this path as markup.
    body.innerHTML = html;
    var actions = document.createElement("div");
    actions.className = "dialog__actions";
    var close = document.createElement("button");
    close.className = "btn";
    close.type = "button";
    close.textContent = t("Close");
    actions.appendChild(close);
    form.appendChild(heading);
    form.appendChild(body);
    form.appendChild(actions);
    dialog.appendChild(form);
    document.body.appendChild(dialog);

    function finish() {
      dialog.close();
      setTimeout(function () { dialog.remove(); }, 200);
    }
    close.addEventListener("click", finish);
    dialog.addEventListener("cancel", function (event) { event.preventDefault(); finish(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) finish(); });
    dialog.showModal();
  }

  /* A command's argument hint, which is usually a literal and is sometimes
     worked out from the page -- `/theme` lists the themes that exist, and those
     are an administrator's to define. Resolved in the two places that render
     it, so a command may be either without either caring. */
  function argumentOf(command) {
    var value = command.argument;
    return typeof value === "function" ? value() : (value || "");
  }

  function helpSheet() {
    var rows = available().map(function (command) {
      return (
        "<tr><td class='mono'>/" + command.name +
        (argumentOf(command) ? " " + escapeText(argumentOf(command)) : "") +
        "</td><td>" + escapeText(command.summary) + "</td></tr>"
      );
    });
    var keys = SHORTCUTS.map(function (shortcut) {
      return (
        "<tr><td class='mono'>" + escapeText(shortcut.keys) + "</td><td>" +
        escapeText(shortcut.what) + "</td></tr>"
      );
    });
    sheet(
      t("Commands and shortcuts"),
      "<table class='sheet'><tbody>" + rows.join("") + "</tbody></table>" +
      "<h3 class='section-title'>" + t("Keyboard") + "</h3>" +
      "<table class='sheet'><tbody>" + keys.join("") + "</tbody></table>" +
      "<p class='muted text-sm'>" + t("A message that starts with a slash but is not a command is sent as written. Type <span class='mono'>//</span> to start one with a literal slash.") + "</p>"
    );
  }

  function escapeText(value) {
    var holder = document.createElement("span");
    holder.textContent = value;
    return holder.innerHTML;
  }

  /* --- Keyboard ----------------------------------------------------------- */
  var MODES = ["manual", "edit", "auto", "plan"];

  document.addEventListener("keydown", function (event) {
    if (event.isComposing) return;

    /* Never inside the terminal: every keystroke there belongs to the shell,
       and a shortcut that steals one is a shortcut that breaks vim. */
    if (event.target.closest && event.target.closest("#terminal")) return;

    if ((event.ctrlKey || event.metaKey) && event.code === "KeyK") {
      event.preventDefault();
      var input = document.querySelector("[data-composer-input]");
      if (!input) return;
      input.focus();
      input.value = "/";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }

    /* Send, from anywhere on the page.

       Enter already sends, but only with the caret inside the box (app.js), and
       deliberately not at all on a touch device. This covers both: after
       clicking a message to copy it, after using the model picker, after
       answering an approval card, or with a hardware keyboard on a tablet.

       Never Stop. Send and Stop are the same element, so Ctrl+Enter meaning
       "abandon the reply" would be a trap -- and Esc already stops. */
    if ((event.ctrlKey || event.metaKey) &&
        (event.code === "Enter" || event.code === "NumpadEnter")) {
      var action = el("[data-composer-action]");
      var box = el("[data-composer-input]");
      if (action && action.dataset.composerAction === "send" && box && box.value.trim()) {
        event.preventDefault();
        action.click();
      }
      return;
    }

    if (!event.altKey || event.ctrlKey || event.metaKey) return;

    /* Dictation and read-aloud both work by clicking the button that already
       does the job, so audio.js keeps its one delegated click listener and
       there is no second copy of the recording state machine. Alt+M rather than
       Alt+D: Alt+D is the address bar in Chrome and Firefox. */
    if (event.code === "KeyM") {
      var mic = el("[data-mic]");
      if (mic) {
        event.preventDefault();
        mic.click();
      }
      return;
    }
    if (event.code === "KeyR") {
      var speakers = document.querySelectorAll("#thread .msg--assistant [data-speak]");
      if (speakers.length) {
        event.preventDefault();
        /* A second press stops it: audio.js already toggles a message that is
           speaking, so this costs nothing and is the obvious second press. */
        speakers[speakers.length - 1].click();
      }
      return;
    }

    if (event.code === "KeyT" && el("#terminal")) {
      event.preventDefault();
      return toggle("#terminal", "side");
    }
    if (event.code === "KeyI" && el("#inspector")) {
      event.preventDefault();
      return toggle("#inspector", "side");
    }
    if (event.code === "KeyB") {
      event.preventDefault();
      return toggle("#sidebar");
    }
    /* The sidebar's +, so the shortcut opens what the + would: a chat
       on the Chats side, an agent chat on the first linked device on the
       Agents side. */
    if (event.code === "KeyN") {
      event.preventDefault();
      var plus = el("[data-new-chat]");
      window.location = (plus && plus.getAttribute("href")) || "/chat";
      return;
    }
    var digit = ["Digit1", "Digit2", "Digit3", "Digit4"].indexOf(event.code);
    if (digit !== -1 && isAgent()) {
      event.preventDefault();
      run("mode", MODES[digit]);
    }
  });

  /* Up-arrow in an empty box edits your last turn, the way a shell recalls the
     last command. Only when the box is empty, so it never eats a cursor key
     somebody was using to move around what they had written. */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "ArrowUp" || event.shiftKey || event.altKey) return;
    var input = event.target.closest && event.target.closest("[data-composer-input]");
    if (!input || input.value !== "") return;
    var edits = document.querySelectorAll("#thread .msg--user [data-edit-message]");
    if (!edits.length) return;
    event.preventDefault();
    edits[edits.length - 1].click();
  });

  /* The device's commands as soon as the page knows its device, and again
     whenever the new-chat screen points somewhere else. */
  function primeDevice() { refreshDevice(true); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", primeDevice);
  } else {
    primeDevice();
  }
  document.addEventListener("change", function (event) {
    if (event.target.closest && event.target.closest('select[name="ssh_profile_id"], [data-dir-value]')) {
      primeDevice();
    }
  });
  document.addEventListener("lembas:agent-target", primeDevice);

  window.lembasCommands = {
    list: list, find: find, run: run, help: helpSheet,
    refresh: function () { refreshDevice(false); }
  };
})();
