/*
  Client-side behaviour.

  Everything here is progressive: the application is server-rendered and works
  without this file, apart from the streaming reply, which is htmx's SSE
  extension rather than anything hand-written below.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var THEME_KEY = "lembas-theme";

  /* --- Theme -------------------------------------------------------------
     Stored locally so the choice applies instantly and survives being signed
     out, and mirrored to the server so it follows the user to another device.
     The server call is best-effort: a failure must not undo the local switch.

     The list used to be a literal pair here, and in four other places. It comes
     from `data-themes` on <html> now -- "id:base" pairs, space separated --
     because an administrator can define one, and a hard-coded pair would refuse
     it silently: applyTheme would return, the button would do nothing, and
     nothing anywhere would say why. */
  function themes() {
    var raw = document.documentElement.dataset.themes || "moria:moria shire:shire";
    var map = {};
    raw.split(/\s+/).forEach(function (entry) {
      var parts = entry.split(":");
      if (parts[0]) map[parts[0]] = parts[1] || "moria";
    });
    return map;
  }

  function themeNames() {
    return Object.keys(themes());
  }

  function currentTheme() {
    return document.documentElement.dataset.theme || themeNames()[0];
  }

  function applyTheme(name) {
    var known = themes();
    if (!Object.prototype.hasOwnProperty.call(known, name)) return;
    document.documentElement.dataset.theme = name;
    /* Both attributes, always. `data-base` is what makes a custom theme inherit
       its built-in palette -- tokens.css matches it as well as `data-theme` --
       so setting only the first leaves a custom light theme's four colours on
       Moria's near-black surfaces. */
    document.documentElement.dataset.base = known[name];
    try {
      localStorage.setItem(THEME_KEY, name);
    } catch (e) { /* private mode */ }

    /* Installed, the browser's own chrome is the application's chrome, so it
       has to follow the theme too. Read from the stylesheet rather than
       repeating the hex here: tokens.css is the one place colours live. */
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    var bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--bg").trim();
    if (bg) {
      metas.forEach(function (meta) {
        /* There are two of them, scoped by `prefers-color-scheme`, so that a
           light instance is not painted dark before this file has run. Once it
           has, the reader's *chosen* theme is the answer and the system's
           preference is not -- somebody on the parchment theme inside a dark
           desktop wants parchment. Dropping the `media` attribute is what makes
           the choice win; leaving it would let the unchosen one apply. */
        meta.removeAttribute("media");
        meta.setAttribute("content", bg);
      });
    }

    /* The toggle names where it is going, not where it is. With more than two
       themes "the next one" is the honest description, because naming it would
       mean carrying every label into the browser for a label nobody reads
       twice. */
    document.querySelectorAll("[data-theme-toggle]").forEach(function (el) {
      el.setAttribute("aria-label", known[name] === "moria" ? t("Switch to the light theme")
                                                            : t("Switch to the dark theme"));
    });

    /* For anything holding colours as values rather than reading them from a
       variable. The terminal is the only such thing: xterm copies its palette
       at construction, so switching to Shire would otherwise leave a black
       rectangle in a light interface. */
    document.dispatchEvent(new CustomEvent("lembas:theme", { detail: { theme: name } }));

    if (document.body.dataset.authenticated === "true") {
      fetch("/api/preferences/theme", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: name })
      }).catch(function () { /* preference is already applied locally */ });
    }
  }

  /* Round the list rather than between two names. With only the built-in pair
     this is exactly what it always did; with a third defined it reaches it,
     which a hard-coded flip never could. */
  function toggleTheme() {
    var names = themeNames();
    var at = names.indexOf(currentTheme());
    applyTheme(names[(at + 1) % names.length] || names[0]);
  }

  /* --- Textarea autosize -------------------------------------------------
     Grows the composer with its content up to a cap, after which it scrolls. */
  function autosize(el) {
    if (!el) return;
    var max = parseInt(el.dataset.maxHeight || "320", 10);
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, max) + "px";
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  }

  /* --- Copy --------------------------------------------------------------
     Falls back to a hidden textarea because navigator.clipboard is unavailable
     on pages served over plain http, which self-hosted installs often are. */
  function copyText(text, trigger) {
    function done() {
      if (!trigger) return;
      var original = trigger.getAttribute("aria-label");
      trigger.classList.add("is-copied");
      trigger.setAttribute("aria-label", t("Copied"));
      setTimeout(function () {
        trigger.classList.remove("is-copied");
        if (original) trigger.setAttribute("aria-label", original);
      }, 1400);
    }

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(function () {});
      return;
    }
    var scratch = document.createElement("textarea");
    scratch.value = text;
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.opacity = "0";
    document.body.appendChild(scratch);
    scratch.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* nothing to do */ }
    document.body.removeChild(scratch);
  }

  /* --- Copying a code block ---------------------------------------
     Every .code-block gets a copy button in its label bar, cloned from the
     <template id="code-copy"> in base.html. A block written without a language
     has no label bar, so it gets an empty one: the button lives in the same
     place on every block. Idempotent, so it can run on anything that arrives
     -- see the observer in the DOMContentLoaded handler. */
  function addCodeCopy(root) {
    var template = document.getElementById("code-copy");
    if (!template || !root || !root.querySelectorAll) return;
    var blocks = Array.prototype.slice.call(root.querySelectorAll(".code-block"));
    if (root.matches && root.matches(".code-block")) blocks.push(root);
    blocks.forEach(function (block) {
      if (block.querySelector(".code-block__copy")) return;
      var label = block.querySelector(".code-block__label");
      if (!label) {
        label = document.createElement("div");
        label.className = "code-block__label code-block__label--bare";
        block.insertBefore(label, block.firstChild);
      }
      label.appendChild(template.content.firstElementChild.cloneNode(true));
    });
  }

  /* --- Thread scrolling --------------------------------------------------
     Only auto-scrolls when the reader is already near the bottom, so scrolling
     up to re-read something is not yanked away by an incoming token. */
  var STICK_THRESHOLD = 120;

  function isNearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD;
  }

  /* Whether the view is following the reply. Distance to the bottom used to be
     the whole test, and it very nearly is -- but somebody near the bottom who
     opens a tool block is *reading*, and the next frame eighty milliseconds
     later dragged them straight back down again. At twelve frames a second that
     reads as a block that will not open at all.

     So opening one turns following off, and returning to the bottom turns it
     back on. `stick` is the gate; `isNearBottom` is what maintains it. */
  var stick = true;
  /* Where scrollThread last put it, so a scroll event can be told apart from a
     scroll the reader did. Without this the programmatic scroll re-arms `stick`
     on its own and nothing ever unsticks. */
  var placed = -1;

  /* One scroll owner, one scroll a frame.

     Every swap and every stream frame asks for the bottom; this grants it once,
     in a `requestAnimationFrame`, after everything else that was going to touch
     the transcript in this turn has touched it. Two things depend on that
     order. The MutationObservers in this file and in prototypes.js add a label
     bar to every code block and a status line to every prototype card, and
     they run as microtasks -- so when the scroll happened in the swap listener
     itself, the bottom it measured was short by exactly what they were about to
     insert, on every frame. And a shrink -- a round closing, incomplete Markdown
     changing shape as the next token lands, a block the reader shuts -- clamps
     `scrollTop` when layout runs, which fires a scroll event *upward* that the
     listener below would read as the reader leaving. Here the clamp and the
     follow happen in one layout, so the one scroll event the browser fires
     lands exactly on `placed` and is ignored as ours.

     `.thread-scroll` therefore has no `scroll-behavior: smooth`: an animated
     assignment reads back its *start* position, so `placed` was never where the
     scroll ended, and the animation's own events went through the up/near-bottom
     logic eight times a frame. And `overflow-anchor: none`, so Chrome's scroll
     anchoring does not add a second opinion about where the reader is. */
  var scrollFrame = 0;

  function scrollThread(force) {
    if (force) stick = true;
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(function () {
      scrollFrame = 0;
      var thread = document.getElementById("thread-scroll");
      if (!thread || !stick) return;
      thread.scrollTop = thread.scrollHeight;
      placed = thread.scrollTop;
    });
  }

  /* Any scroll *up* by the reader stops following at once; only
     coming back down to the bottom starts it again. The threshold alone used to
     decide both, so somebody who had moved up less than STICK_THRESHOLD to read
     a line was still "following" -- and the next swap pulled them back down,
     which is the page "dragging me down". A scroll that ends where
     `scrollThread` put it is ours, whatever happened to the height on the way
     there -- see the note above the function. */
  var lastTop = -1;
  document.addEventListener(
    "scroll",
    function (event) {
      var thread = event.target;
      if (!thread || thread.id !== "thread-scroll") return;
      var top = thread.scrollTop;
      var up = lastTop >= 0 && top < lastTop - 1;
      lastTop = top;
      if (top === placed) return;
      if (up) stick = false;
      else if (isNearBottom(thread)) stick = true;
    },
    true
  );

  /* `toggle` does not bubble, so this has to be registered in the CAPTURE phase.
     Without the third argument the listener is never called and the whole thing
     is silently dead in every browser -- the same shape of failure as a trigger
     bound where the event does not go. There is a test on it.

     Only the reader's own toggle counts. steps.js opens blocks too -- putting
     back what was open before a swap replaced it -- and a programmatic open
     fires exactly the same `toggle`, trusted and all, so its opens are marked
     `data-restored` and the mark is consumed here. Without that, once somebody
     had opened a tool block the next round to close would open its copy and
     stop following the reply for good. A block closing shrinks the page: the
     scroll is asked for so the clamp and the follow are one event. */
  document.addEventListener(
    "toggle",
    function (event) {
      var el = event.target;
      if (!event.isTrusted || !el || el.tagName !== "DETAILS") return;
      if (!(el.closest && el.closest("#thread-scroll"))) return;
      if (el.hasAttribute("data-restored")) {
        el.removeAttribute("data-restored");
        return;
      }
      if (el.open) stick = false;
      else scrollThread(false);
    },
    true
  );

  /* --- Attachments -------------------------------------------------------
     Files are uploaded one at a time as soon as they are chosen, dropped or
     pasted, rather than all at once when the message is sent. The chip (or the
     rejection) then appears immediately, and a large file cannot make the send
     button appear to hang. */
  function uploadFiles(fileList) {
    var input = document.getElementById("file-input");
    var target = document.getElementById("attachments");
    if (!input || !target || !fileList || !fileList.length) return;

    var url = input.dataset.uploadUrl;

    Array.prototype.forEach.call(fileList, function (file) {
      var body = new FormData();
      body.append("file", file, file.name);

      fetch(url, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (response) { return response.text(); })
        .then(function (html) {
          target.insertAdjacentHTML("beforeend", html);
          // The chip's remove button is htmx-driven, so the new markup has to
          // be announced or its attributes are inert.
          if (window.htmx) window.htmx.process(target.lastElementChild);
        })
        .catch(function () {
          target.insertAdjacentHTML(
            "beforeend",
            '<div class="attach-chip attach-chip--error">' +
              '<span class="attach-chip__body">' +
              '<span class="attach-chip__name"></span>' +
              '<span class="attach-chip__warning">' + t("Upload failed.") + '</span></span></div>'
          );
          // Set as text, never as HTML: the filename comes from the user.
          target.lastElementChild.querySelector(".attach-chip__name").textContent =
            file.name;
        });
    });
  }

  /* --- Attaching something that is not a file ----------------------------
     The composer's menu offers four things; two of them are the file picker
     with a different filter, and two need a round trip. Both of those post a
     form and get a chip back, exactly like an upload, so the composer does not
     have to know where a chip came from. */
  function chipTarget() {
    return document.getElementById("attachments");
  }

  /* The composer's model, which on the new-chat screen decides which data
     group the library picker may offer. See data_groups.for_composer. */
  function modelId() {
    var field = document.querySelector('.composer input[name="model_id"], [data-picker-input]');
    return field ? field.value : "";
  }

  function chatId() {
    var input = document.getElementById("file-input");
    var url = (input && input.dataset.uploadUrl) || "";
    var match = url.match(/chat_id=([^&]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function postForChip(url, body) {
    var target = chipTarget();
    if (!target) return Promise.resolve();
    return fetch(url, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        target.insertAdjacentHTML("beforeend", html);
        if (window.htmx) window.htmx.process(target.lastElementChild);
      });
  }

  function attachLink() {
    if (!window.lembas || !window.lembas.prompt) return;
    window.lembas.prompt({
      title: t("Attach a web page"),
      message: t("The page is fetched now and its text attached, so it will not change between writing this and sending it."),
      placeholder: "https://example.com/article",
      confirmLabel: t("Fetch"),
    }).then(function (url) {
      if (!url || !url.trim()) return;
      var body = new FormData();
      body.append("url", url.trim());
      body.append("chat_id", chatId());
      return postForChip("/api/files/link", body);
    });
  }

  /* The knowledge dialog re-queries the server as you type rather than
     filtering in the browser: the library is searched with FTS, which is what
     makes it work at five hundred documents instead of five. */
  function attachKnowledge() {
    var dialog = document.createElement("dialog");
    dialog.className = "dialog dialog--wide";
    dialog.innerHTML =
      '<div class="dialog__form">' +
      '<h2 class="dialog__title">' + t("Attach from your library") + '</h2>' +
      '<input class="input" type="search" placeholder="' + t("Search your documents…") + '" ' +
      'aria-label="' + t("Search your documents") + '">' +
      '<div class="dialog__results"></div>' +
      '<div class="dialog__actions"><button class="btn" type="button">' + t("Close") + '</button></div>' +
      "</div>";
    document.body.appendChild(dialog);

    var search = dialog.querySelector("input");
    var results = dialog.querySelector(".dialog__results");
    var close = dialog.querySelector("button");

    function load(query) {
      fetch("/api/files/knowledge-picker?q=" + encodeURIComponent(query || "") +
        "&chat_id=" + encodeURIComponent(chatId()) +
        "&model_id=" + encodeURIComponent(modelId()), {
        credentials: "same-origin",
      })
        .then(function (response) { return response.text(); })
        .then(function (html) { results.innerHTML = html; })
        .catch(function () { results.textContent = t("Could not load your library."); });
    }

    var pending = null;
    search.addEventListener("input", function () {
      clearTimeout(pending);
      pending = setTimeout(function () { load(search.value); }, 200);
    });

    results.addEventListener("click", function (event) {
      var option = event.target.closest("[data-attach-knowledge]");
      if (!option) return;
      var body = new FormData();
      body.append("document_id", option.dataset.attachKnowledge);
      body.append("chat_id", chatId());
      body.append("model_id", modelId());
      postForChip("/api/files/from-knowledge", body);
      finish();
    });

    function finish() {
      dialog.close();
      setTimeout(function () { dialog.remove(); }, 200);
    }

    close.addEventListener("click", finish);
    dialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      finish();
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) finish();
    });

    dialog.showModal();
    load("");
    search.focus();
  }

  /* --- Choosing a directory on the far side --------------------------------
     Shaped like attachKnowledge above, with one difference: a click walks
     deeper rather than finishing, and finishing is its own button. The path
     that gets submitted is the directory you are *standing in*, not the last
     row you pressed, so choosing the directory you are already looking at
     needs no click at all. */
  function chooseDirectory(profileId, current, onPick) {
    /* A linked device is asked rather than browsed: `device:<token>`
       goes to the device's own listing, which offers its trusted places and
       says where it would refuse to work. */
    var device = true;
    var listing = "/api/devices/" + encodeURIComponent(String(profileId).replace(/^device:/, "")) + "/browse?path=";
    var dialog = document.createElement("dialog");
    dialog.className = "dialog dialog--wide";
    dialog.innerHTML =
      '<div class="dialog__form">' +
      '<h2 class="dialog__title">' + t("Project directory") + '</h2>' +
      '<p class="dialog__note">' + (device
        ? t("Where this chat works on the device: its trusted places and anything in them. The device has the last word.")
        : t("Where this chat starts, and what a relative path is measured from. You can walk anywhere the account can reach.")) + "</p>" +
      '<div class="dialog__results"></div>' +
      '<input class="input input--mono" type="text" spellcheck="false" ' +
      'aria-label="' + t("Path") + '" placeholder="/project">' +
      '<div class="dialog__actions">' +
      '<button class="btn" type="button" data-dir-cancel>' + t("Cancel") + '</button>' +
      '<button class="btn btn--primary" type="button" data-dir-use>' + t("Use this directory") + '</button>' +
      "</div></div>";
    document.body.appendChild(dialog);

    var results = dialog.querySelector(".dialog__results");
    var typed = dialog.querySelector("input");
    var here = current || "";

    function load(path) {
      results.setAttribute("aria-busy", "true");
      fetch(listing + encodeURIComponent(path || ""), { credentials: "same-origin" })
        .then(function (response) { return response.text(); })
        .then(function (html) {
          results.innerHTML = html;
          var box = results.querySelector("#dir-results");
          /* The server decides where we ended up -- it resolved the empty
             path to the profile's own default -- so the typed field follows
             it rather than the other way round. */
          if (box) { here = box.dataset.here || path || ""; typed.value = here; }
          results.removeAttribute("aria-busy");
        })
        .catch(function () {
          results.textContent = t("Could not reach that machine.");
          results.removeAttribute("aria-busy");
        });
    }

    results.addEventListener("click", function (event) {
      var row = event.target.closest("[data-dir-open]");
      if (!row) return;
      load(row.dataset.dirOpen);
    });

    /* Typing a path you already know beats clicking to it, so the field is a
       first-class way in and not only a display of where you are. */
    typed.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      load(typed.value.trim());
    });

    function finish(chosen) {
      if (chosen !== undefined) onPick(chosen);
      dialog.close();
      setTimeout(function () { dialog.remove(); }, 200);
    }

    dialog.querySelector("[data-dir-use]").addEventListener("click", function () {
      finish(typed.value.trim() || here);
    });
    dialog.querySelector("[data-dir-cancel]").addEventListener("click", function () {
      finish();
    });
    dialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      finish();
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) finish();
    });

    dialog.showModal();
    load(current || "");
  }

  document.addEventListener("click", function (event) {
    var choice = event.target.closest("[data-attach]");
    if (!choice) return;
    event.preventDefault();

    var kind = choice.dataset.attach;
    if (kind === "file") document.getElementById("file-input").click();
    else if (kind === "image") document.getElementById("image-input").click();
    else if (kind === "link") attachLink();
    else if (kind === "knowledge") attachKnowledge();
  });

  function setupDropzone() {
    var zone = document.querySelector("[data-dropzone]");
    if (!zone) return;

    /* dragenter/dragleave fire for every child element the pointer crosses, so
       a plain toggle flickers. Counting entries and exits is the standard fix. */
    var depth = 0;

    function hasFiles(event) {
      return event.dataTransfer && Array.prototype.indexOf.call(
        event.dataTransfer.types || [], "Files"
      ) !== -1;
    }

    zone.addEventListener("dragenter", function (event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth += 1;
      zone.classList.add("is-dropping");
    });

    zone.addEventListener("dragover", function (event) {
      if (hasFiles(event)) event.preventDefault();
    });

    zone.addEventListener("dragleave", function () {
      depth = Math.max(0, depth - 1);
      if (depth === 0) zone.classList.remove("is-dropping");
    });

    zone.addEventListener("drop", function (event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      zone.classList.remove("is-dropping");
      uploadFiles(event.dataTransfer.files);
    });

    /* Pasting a screenshot straight into the composer. Only files are taken;
       pasted text must still behave as text. */
    document.addEventListener("paste", function (event) {
      var composer = event.target.closest("[data-composer-input]");
      if (!composer || !event.clipboardData) return;
      var files = Array.prototype.filter.call(
        event.clipboardData.files || [], function (f) { return f && f.size; }
      );
      if (!files.length) return;
      event.preventDefault();
      uploadFiles(files);
    });
  }

  /* --- Installing as an app ----------------------------------------------
     Chromium fires beforeinstallprompt when it decides the app is installable
     and lets the page defer the prompt. The event is the only handle on it, so
     it is kept; there is no way to ask later whether one is available.

     Nothing appears unless the browser offers it. Firefox and desktop Safari
     never fire the event, and there is no useful button to show in their
     place -- an "Install" that does nothing is worse than none. */
  var installPrompt = null;

  function revealInstall(show) {
    document.querySelectorAll("[data-install-app]").forEach(function (el) {
      el.hidden = !show;
    });
  }

  function promptInstall() {
    if (!installPrompt) return;
    installPrompt.prompt();
    installPrompt.userChoice.then(function () {
      // A prompt is single-use, accepted or dismissed.
      installPrompt = null;
      revealInstall(false);
    });
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    installPrompt = event;
    revealInstall(true);
    describeInstall();
  });

  window.addEventListener("appinstalled", function () {
    installPrompt = null;
    revealInstall(false);
    describeInstall();
  });

  /* Why there is no Install button, in a sentence.

     Every reason looks identical from the outside -- the button is simply not
     there -- and the hint beside it used to say "only offered over HTTPS or on
     localhost", which is true of one of the four cases and useless for the other
     three. The commonest on a home network is the one it did not mention: a
     certificate signed by your own CA, which the phone does not trust, so the
     page is not a secure context and the worker is refused. That is
     indistinguishable, without this, from a browser that cannot install at all.

     `textContent`, never innerHTML: `detail` is a browser's error message, and
     while a browser is not a hostile source it is not ours to trust either. */
  function installExplanation() {
    var worker = window.lembasWorker || {};
    if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) {
      return t("Already installed — you are using the installed app now.");
    }
    if (installPrompt) return "";
    if (worker.state === "insecure") {
      return t("This page is not a secure context, so the browser will not install it. That means plain http, or https with a certificate this device does not trust — a private or self-signed certificate has to be installed on the device before any browser will treat the site as secure.");
    }
    if (worker.state === "failed") {
      return t("The service worker could not be registered, so the browser will not offer an install. The usual cause is a certificate this device does not trust. The browser said: %(reason)s",
               { reason: worker.reason + (worker.detail ? " \u2014 " + worker.detail : "") });
    }
    if (worker.state === "unsupported") {
      return t("This browser does not support installing. On iOS, use Share → Add to Home Screen.");
    }
    if (worker.state === "ready") {
      return t("Everything this end is ready and your browser has not offered an install. Some never do — Firefox and desktop Safari — and Chrome will not offer one twice for the same app.");
    }
    return "";
  }

  function describeInstall() {
    var text = installExplanation();
    document.querySelectorAll("[data-install-status]").forEach(function (el) {
      el.textContent = text;
      el.hidden = !text;
    });
  }

  document.addEventListener("lembas:worker", describeInstall);

  /* --- Panels ------------------------------------------------------------- */
  /* A panel can be opened or closed by more than one control -- the button in
     the topbar and the panel's own Close -- and it can now also be closed by
     something nobody clicked, because two panels sharing the right-hand side
     of the screen must not both be open. So the state is applied to the panel
     and then *every* toggle pointing at it is brought in line. Setting
     aria-expanded on the clicked button alone left the other one lying. */
  function syncToggles(selector, open) {
    var toggles = document.querySelectorAll('[data-toggle="' + selector + '"]');
    for (var i = 0; i < toggles.length; i++) {
      toggles[i].setAttribute("aria-expanded", open ? "true" : "false");
      toggles[i].classList.toggle("is-active", open);
    }
  }

  /* --- The sidebar -------------------------------------------------------
     Its own pair of functions rather than a branch inside `setPanel`, because
     it is the one panel whose *default* depends on the width of the window:
     open beside the conversation on a desktop, closed over it on a phone. The
     `hidden` attribute the other three use is a single value for both, which
     is how the drawer came to be open on every phone with its own toggle
     underneath it.

     `data-sidebar` on <html> has a third state -- absent -- meaning "follow
     the width", and absent is what the server renders, because the server
     cannot know the width. Everything downstream is unchanged: `syncToggles`
     still writes `aria-expanded` on every control pointing here, and the panel
     still gets `lembas:toggle`. */
  var NARROW = "(max-width: 48rem)";

  function sidebarOpen() {
    var state = document.documentElement.dataset.sidebar;
    if (state === "open") return true;
    if (state === "closed") return false;
    return !window.matchMedia(NARROW).matches;
  }

  function setSidebar(open) {
    var panel = document.querySelector("#sidebar");
    document.documentElement.dataset.sidebar = open ? "open" : "closed";
    syncToggles("#sidebar", open);

    /* Nothing behind an open drawer may be reached by the keyboard -- but only
       while it *is* a drawer. Cleared whenever the query stops matching, and
       cleared unconditionally when it closes: an `inert` left behind on a
       window somebody widened is a page that has stopped responding, which is
       a far worse bug than the one it is here to fix. */
    var main = document.querySelector(".shell > .main");
    if (main) main.toggleAttribute("inert", open && window.matchMedia(NARROW).matches);

    if (panel) {
      panel.dispatchEvent(
        new CustomEvent("lembas:toggle", { bubbles: true, detail: { open: open } })
      );
    }
  }

  function setPanel(selector, open, group) {
    if (selector === "#sidebar") return setSidebar(open);

    var panel = document.querySelector(selector);
    if (!panel) return;

    /* One at a time down the right-hand side. Not only a narrow-screen
       concern: a 1280px window with the sidebar, the inspector and the
       terminal all open leaves the conversation about seventy pixels wide. */
    if (open && group) {
      var others = document.querySelectorAll('[data-toggle-group="' + group + '"]');
      for (var i = 0; i < others.length; i++) {
        var other = others[i].dataset.toggle;
        if (other && other !== selector) setPanel(other, false);
      }
    }

    panel.toggleAttribute("hidden", !open);
    syncToggles(selector, open);
    syncWindows();
    /* What a panel needs to know it is visible. The terminal listens for this:
       xterm cannot measure itself inside a hidden element, so it has to be
       told rather than left to discover. */
    panel.dispatchEvent(
      new CustomEvent("lembas:toggle", { bubbles: true, detail: { open: open } })
    );
  }

  /* --- The window switch ---------------------------------------------------
     On a phone a side panel is the whole screen, and `.winswitch` is how you
     move between it and the conversation. Which window is in front is written
     to `data-window` on <html> -- the panel's selector, or empty for the
     conversation -- and the CSS shows the switch only below the phone
     breakpoint and only while that is not empty. Kept in step from here,
     because every way a panel opens or closes ends in `setPanel`. */
  function sideSelectors() {
    var seen = {};
    var found = [];
    document.querySelectorAll('[data-toggle-group="side"]').forEach(function (toggle) {
      var selector = toggle.dataset.toggle;
      if (selector && !seen[selector]) { seen[selector] = true; found.push(selector); }
    });
    return found;
  }

  function syncWindows() {
    var front = "";
    sideSelectors().forEach(function (selector) {
      var panel = document.querySelector(selector);
      if (panel && !panel.hasAttribute("hidden")) front = selector;
    });
    document.documentElement.dataset.window = front;
    document.querySelectorAll("[data-window]").forEach(function (button) {
      if (button === document.documentElement) return;
      button.setAttribute("aria-pressed", button.dataset.window === front ? "true" : "false");
    });
  }

  /* Pressing the window you are in does nothing -- it is a switch, not a
     toggle. The conversation is "every side panel closed". */
  function showWindow(selector) {
    if (selector) {
      setPanel(selector, true, "side");
      return;
    }
    sideSelectors().forEach(function (other) { setPanel(other, false); });
  }

  /* --- Dragging a panel wider ---------------------------------------------
     Generic rather than terminal-specific: the inspector and the sidebar want
     the same handle, and a second copy of this is how two panels end up
     resizing differently.

     The width lands on a CSS variable on <html> rather than on the panel, so
     the ≤64rem overlay rule -- which clamps it with min() -- keeps working
     without knowing anything about dragging. Persisted the way the theme is:
     localStorage for this tab, best-effort POST for the next device. */
  var RESIZE_KEY = "lembas-panel-widths";

  function storedWidths() {
    try {
      return JSON.parse(localStorage.getItem(RESIZE_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function applyWidths() {
    var widths = storedWidths();
    Object.keys(widths).forEach(function (name) {
      document.documentElement.style.setProperty(name, widths[name] + "px");
    });
  }

  function rememberWidth(name, pixels) {
    var widths = storedWidths();
    widths[name] = Math.round(pixels);
    try {
      localStorage.setItem(RESIZE_KEY, JSON.stringify(widths));
    } catch (e) { /* private mode */ }
    if (document.body.dataset.authenticated === "true") {
      fetch("/api/preferences/layout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(widths)
      }).catch(function () { /* already applied locally */ });
    }
  }

  function setupResize() {
    document.addEventListener("pointerdown", function (event) {
      var handle = event.target.closest("[data-resize]");
      if (!handle || event.button !== 0) return;
      var panel = handle.closest("[data-resize-target]") || handle.parentElement;
      if (!panel) return;

      var name = handle.dataset.resize;
      var min = parseFloat(handle.dataset.resizeMin || "320");
      var startX = event.clientX;
      var startWidth = panel.getBoundingClientRect().width;
      var frame = null;
      var pending = startWidth;

      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      document.body.classList.add("is-resizing");

      function move(moveEvent) {
        /* The handle is on the panel's *left* edge and the panel is on the
           right of the shell, so dragging left makes it wider. */
        var max = Math.max(min, window.innerWidth - 360);
        pending = Math.min(Math.max(startWidth - (moveEvent.clientX - startX), min), max);
        /* Coalesced to a frame: the ResizeObserver on the panel calls xterm's
           fit() and sends a resize frame up the socket, and doing that once
           per pointermove is a frame per pixel of drag. */
        if (frame) return;
        frame = requestAnimationFrame(function () {
          frame = null;
          document.documentElement.style.setProperty(name, pending + "px");
        });
      }

      function stop() {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
        document.body.classList.remove("is-resizing");
        if (frame) cancelAnimationFrame(frame);
        document.documentElement.style.setProperty(name, pending + "px");
        rememberWidth(name, pending);
      }

      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });

    /* A keyboard has to be able to do this too, or the panel is only resizable
       with a mouse and the handle is a focus trap that does nothing. */
    document.addEventListener("keydown", function (event) {
      var handle = event.target.closest("[data-resize]");
      if (!handle) return;
      var step = event.key === "ArrowLeft" ? 32 : event.key === "ArrowRight" ? -32 : 0;
      if (!step) return;
      event.preventDefault();
      var panel = handle.closest("[data-resize-target]") || handle.parentElement;
      var name = handle.dataset.resize;
      var min = parseFloat(handle.dataset.resizeMin || "320");
      var max = Math.max(min, window.innerWidth - 360);
      var width = Math.min(Math.max(panel.getBoundingClientRect().width + step, min), max);
      document.documentElement.style.setProperty(name, width + "px");
      rememberWidth(name, width);
    });
  }

  window.lembas = {
    setPanel: setPanel,
    showWindow: showWindow,
    applyTheme: applyTheme,
    toggleTheme: toggleTheme,
    copyText: copyText,
    scrollThread: scrollThread,
    autosize: autosize,
    uploadFiles: uploadFiles,
    chooseDirectory: chooseDirectory,
    promptInstall: promptInstall
  };

  /* --- Wiring ------------------------------------------------------------ */
  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-theme-toggle]");
    if (toggle) {
      event.preventDefault();
      toggleTheme();
      return;
    }

    /* The code itself, not the highlighting: textContent of the <pre> is the
       source as the model wrote it, without the label or the button. */
    var codeCopy = event.target.closest(".code-block__copy");
    if (codeCopy) {
      event.preventDefault();
      var pre = codeCopy.closest(".code-block").querySelector("pre");
      if (pre) copyText(pre.textContent.replace(/\n$/, ""), codeCopy);
      return;
    }

    var copy = event.target.closest("[data-copy]");
    if (copy) {
      event.preventDefault();
      var source = document.getElementById(copy.dataset.copy);
      if (source) copyText(source.textContent.trim(), copy);
      return;
    }

    /* A suggestion card sends its prompt. One click, one reply -- filling the
       box and waiting for Enter makes the card a form to review rather than a
       thing to press. The built-in prompts are written to work sent cold: each
       asks for what it needs, so the answer is a question back rather than a
       guess at material nobody has given yet. */
    var suggestion = event.target.closest("[data-suggestion]");
    if (suggestion) {
      event.preventDefault();
      var input = document.querySelector("[data-composer-input]");
      if (!input) return;
      input.value = suggestion.dataset.suggestion;
      autosize(input);
      if (window.lembas.paintComposer) window.lembas.paintComposer();
      var form = input.closest("form");
      /* requestSubmit, not submit(): it fires the submit event, which is what
         htmx is listening for. Same call the Enter key makes. */
      if (form) form.requestSubmit();
      return;
    }

    var windowButton = event.target.closest("button[data-window]");
    if (windowButton) {
      event.preventDefault();
      showWindow(windowButton.dataset.window);
      return;
    }

    /* Show/hide a panel by selector, so templates do not each carry their own
       inline toggle script. */
    var toggle = event.target.closest("[data-toggle]");
    if (toggle) {
      event.preventDefault();
      if (toggle.dataset.toggle === "#sidebar") {
        setSidebar(!sidebarOpen());
        return;
      }
      var panel = document.querySelector(toggle.dataset.toggle);
      if (!panel) return;
      setPanel(toggle.dataset.toggle, panel.hasAttribute("hidden"), toggle.dataset.toggleGroup);
    }
  });

  document.addEventListener("input", function (event) {
    if (event.target.matches("[data-autosize]")) autosize(event.target);
  });

  /* A "select all" box driving every checkbox inside a container. Scoped to a
     selector rather than the whole page, so a list can carry more than one. */
  document.addEventListener("change", function (event) {
    var master = event.target.closest("[data-select-all]");
    if (!master) return;
    var scope = document.querySelector(master.dataset.selectAll);
    if (!scope) return;
    scope.querySelectorAll('input[type="checkbox"]').forEach(function (box) {
      box.checked = master.checked;
    });
  });

  /* Enter sends, Shift+Enter inserts a newline -- the convention every chat
     application uses. Left alone on touch devices, where there is no easy
     Shift and Enter should mean "new line". */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.shiftKey) return;
    /* The Enter that commits an IME composition is not the Enter that sends.
       Typing Japanese or Chinese, every accepted candidate would otherwise
       post the half-written message. */
    if (event.isComposing || event.keyCode === 229) return;
    var composer = event.target.closest("[data-composer-input]");
    if (!composer) return;
    if (window.matchMedia("(pointer: coarse)").matches) return;
    event.preventDefault();
    var form = composer.closest("form");
    if (form && composer.value.trim()) form.requestSubmit();
  });

  /* Every sidebar section folds: `[ PINNED MODELS ]`, `[ CHATS ]`,
     each folder, each device on the Agents side, Archived. Each heading is a
     button carrying `data-section` (its key), `aria-expanded` and
     `aria-controls` (its body) -- `section_toggle` in _macros.html -- so this
     is one delegated handler for all of them, and the chevron inside the
     button is pressed with it. One mechanism, not one per kind of section (a
     `<details>`, Alpine, nothing), and the chevron inside the button, so the
     arrow always does what the name does.

     Applied at once, then remembered on the account
     (`POST /api/preferences/section`), because the server renders the tree --
     out of band on the switch, a device's filter, a session arriving from a
     terminal -- and draws each section from what it stored, with no flash.

     `folded` is this tab's own record of what it changed. A swap that was
     rendered before the post landed would otherwise reopen a section just
     shut; `reapply` puts this tab's word back on whatever a swap brings in.
     Delegated on the document, because those swaps replace the buttons. */
  var folded = {};

  function setSection(button, open) {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    var body = document.getElementById(button.getAttribute("aria-controls") || "");
    if (body) body.hidden = !open;
  }

  function reapply(root) {
    (root || document).querySelectorAll("[data-section]").forEach(function (button) {
      var key = button.dataset.section;
      if (Object.prototype.hasOwnProperty.call(folded, key)) setSection(button, folded[key]);
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-section]");
    if (!button) return;
    var open = button.getAttribute("aria-expanded") !== "true";
    var key = button.dataset.section;
    folded[key] = open;
    setSection(button, open);
    if (document.body.dataset.authenticated !== "true") return;
    var body = new FormData();
    body.append("key", key);
    body.append("state", open ? "1" : "0");
    fetch("/api/preferences/section", {
      method: "POST", body: body, credentials: "same-origin"
    }).catch(function () { /* already applied on screen */ });
  });

  document.addEventListener("htmx:afterSettle", function () { reapply(document); });
  document.addEventListener("htmx:oobAfterSwap", function () { reapply(document); });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-autosize]").forEach(autosize);
    scrollThread(true);
    applyTheme(currentTheme());
    setupDropzone();
    setupResize();

    /* The toggle used to render `aria-expanded="true"` in the template, which
       is a claim nobody checked and which was false on every phone. The
       stylesheet decides whether the drawer is showing; this is the one place
       that can ask it and say so. */
    syncToggles("#sidebar", sidebarOpen());

    /* The worker may already have answered before this runs, in which case the
       event has been and gone -- so the state is read here as well as listened
       for. Either path, never both mattering. */
    describeInstall();

    /* Code blocks: the ones in the page now, and every one that arrives later
       -- a finished reply is swapped in by the SSE extension, an older page of
       the thread by htmx -- so one observer rather than a hook per path. Cheap:
       it only looks inside added element nodes. */
    addCodeCopy(document);
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) addCodeCopy(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  });

  /* A drawer that is dismissed by tapping beside it should be dismissed by
     Escape too -- and only while it *is* a drawer, or Escape would collapse the
     sidebar on a desktop, where nobody asked it to. */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (!window.matchMedia(NARROW).matches || !sidebarOpen()) return;
    if (document.querySelector("dialog[open]")) return;
    setSidebar(false);
  });

  /* Widening the window past the breakpoint must not leave `inert` on the page
     behind a drawer that is no longer a drawer. Recomputed rather than cleared,
     so narrowing it again while the drawer is open puts the guard back. */
  window.matchMedia(NARROW).addEventListener("change", function () {
    var main = document.querySelector(".shell > .main");
    if (main) {
      main.toggleAttribute(
        "inert", sidebarOpen() && window.matchMedia(NARROW).matches
      );
    }
    syncToggles("#sidebar", sidebarOpen());
  });

  /* Before first paint rather than on DOMContentLoaded, so a panel that was
     dragged wider does not open at its default and jump. */
  applyWidths();

  /* --- Saying that something is happening --------------------------------
     A count, not a flag: several requests overlap constantly here -- the
     unread poll every ten seconds, the transcript tail, whatever somebody just
     clicked -- and a flag means the first of them to finish switches the bar
     off while the others are still running.

     The poll and the tail are excluded. They are the two requests nobody
     started and nobody is waiting for, and a bar that sweeps every ten seconds
     on an idle page is not information, it is a tic. */
  var pending = 0;

  function quiet(event) {
    var el = event.detail && event.detail.elt;
    if (!el || !el.getAttribute) return false;
    var url = (event.detail.pathInfo && event.detail.pathInfo.requestPath) || "";
    return url.indexOf("/unread") !== -1 || url.indexOf("/tail") !== -1;
  }

  function showProgress(on) {
    var bar = document.querySelector("[data-progress]");
    if (bar) bar.classList.toggle("is-busy", on);
  }

  document.body.addEventListener("htmx:beforeRequest", function (event) {
    if (quiet(event)) return;
    pending += 1;
    showProgress(true);
  });

  ["htmx:afterRequest", "htmx:sendError", "htmx:timeout", "htmx:abort"].forEach(
    function (name) {
      document.body.addEventListener(name, function (event) {
        if (quiet(event)) return;
        pending = Math.max(0, pending - 1);
        if (!pending) showProgress(false);
      });
    }
  );

  /* --- A release that arrived while you were reading ----------------------
     The worker no longer takes over open pages on its own -- see sw.js -- so
     something has to say that one is waiting, and the reader decides. A toast
     rather than a reload: an application with a reply streaming into it must
     not be navigated out from under somebody.

     🚨 "A worker is waiting" is not the same as "this page is out of date",
     and the toast used to treat them as one. After a release it offered a
     reload on every page, including one just fetched with Ctrl+Shift+R, and
     reloading could not make it stop. A page is always fetched from the network
     and every asset it names carries `?v=<release>`, so a page loaded after
     the update IS the update, whichever worker happens to control it. And the
     worker that controls it is nearly always the previous one: a reload
     creates the new page before the old one goes away, so the old worker
     never runs out of pages and the new one never stops waiting.

     So the question is asked of the page. The worker's release is in its own
     script URL (`/sw.js?v=`), and the page's is `window.lembasRelease` from
     base.html. When the two match there is nothing newer to reload into, and
     the worker is left to take over once the old tabs are closed. */
  var PAGE_RELEASE = window.lembasRelease || "";

  function releaseOf(worker) {
    try {
      return new URL(worker.scriptURL).searchParams.get("v") || "";
    } catch (error) {
      return "";
    }
  }

  /* Unknown on either side counts as newer: better an extra offer than a
     release nobody is told about. */
  function isNewerThanThisPage(worker) {
    var release = releaseOf(worker);
    return !PAGE_RELEASE || !release || release !== PAGE_RELEASE;
  }

  function offerReload(worker) {
    window.lembas.notify(
      t("A new version is ready. Reload to use it."),
      { kind: "info", action: { label: t("Reload"), run: function () {
        worker.postMessage({ type: "SKIP_WAITING" });
      } } }
    );
  }

  function watchForUpdate(registration) {
    function offer(worker) {
      if (!worker || !navigator.serviceWorker.controller) return;
      worker.addEventListener("statechange", function () {
        if (worker.state !== "installed") return;
        if (isNewerThanThisPage(worker)) offerReload(worker);
      });
    }
    if (registration.waiting && navigator.serviceWorker.controller &&
        isNewerThanThisPage(registration.waiting)) {
      offerReload(registration.waiting);
    }
    registration.addEventListener("updatefound", function () {
      offer(registration.installing);
    });
  }

  /* The new worker calling skipWaiting() is what fires this, and reloading is
     the right answer to it -- the page is now being served by a worker whose
     cache it did not start from.

     Three guards, and the second is the one that is easy to miss. A flag,
     because `controllerchange` can fire more than once. And `hadController`,
     because on a *first* visit there is no worker at all: the one that
     installs then calls `clients.claim()`, which fires this event for the
     first time -- so without it, the very first page anybody loads reloads
     itself in front of them for no reason they could possibly work out.

     The third is the same question as the toast's. Somebody pressing Reload in
     one tab activates the worker for all of them, and a tab that was already
     rendered by that release has nothing to gain from a reload -- and may have
     a reply streaming into it. */
  var reloading = false;
  if ("serviceWorker" in navigator) {
    var hadController = !!navigator.serviceWorker.controller;
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (reloading || !hadController) return;
      var controller = navigator.serviceWorker.controller;
      if (controller && !isNewerThanThisPage(controller)) return;
      reloading = true;
      window.location.reload();
    });
    navigator.serviceWorker.ready.then(watchForUpdate).catch(function () {});
  }

  /* After any htmx swap: re-measure a composer that was swapped, and follow
     new content -- only content that arrived in the transcript. A poll swapping
     the sidebar's unread dots, or the composer re-rendering, is not a reason to
     move the transcript, and doing it on every swap is what pulled a reader
     down every few seconds.

     The autosize is scoped to what arrived, by selector, and not every textarea
     on the page: measuring one sets its height twice and forces a layout, and
     doing that for the composer on every stream frame that swapped something
     else entirely was a reflow of the whole shell twelve times a second. */
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var target = (event.detail && event.detail.target) || event.target;
    if (target && target.querySelectorAll) {
      if (target.matches && target.matches("[data-autosize]")) autosize(target);
      target.querySelectorAll("[data-autosize]").forEach(autosize);
    }
    var thread = document.getElementById("thread-scroll");
    if (thread && target && (target === thread || thread.contains(target) || target.id === "thread")) {
      scrollThread(false);
    }
    /* The helper panel is opened by what lands in it, not by a toggle: the
       jobs strip's Follow button fetches a helper's transcript into
       `#jobpanel-body`, and the panel has to be shown for that swap to mean
       anything. The same `setPanel` as a toggle, in the `side` group, so it
       takes the slot the inspector and the canvas share. A poll of the
       transcript inside the panel swaps `#jobpanel-turns`, not the body, so it
       re-opens nothing. In this one listener rather than a second: every path
       a swap takes is read here. */
    if (target && target.id === "jobpanel-body") setPanel("#jobpanel", true, "side");
  });

  /* --- The transcript tail -----------------------------------------------
     A reply can begin without a request from this page: a background job
     finishing wakes the chat server-side. There is no chat-level channel to
     hear about it on -- the only stream is per-message, and it is opened by a
     bubble this page has not got. So `#thread-tail` polls, and this is where it
     is told what the page already holds.

     The cursor is read from the DOM rather than from a variable rendered into
     the page, because the DOM is the honest answer to that question. Every path
     that appends a bubble moves it -- the composer's own POST, the `done`
     frame's out-of-band swaps, the last poll -- and a variable would have to be
     updated by each of them, correctly, forever.

     `htmx:configRequest` and not `hx-vals="js:…"`: two of the three things here
     are *cancellations*, which `hx-vals` cannot express, and splitting the read
     from the cancellations would put one decision in two files. (It is also the
     only string htmx would ever be handed to evaluate in this project, and it
     would die silently under a CSP.) */
  document.body.addEventListener("htmx:configRequest", function (event) {
    var elt = (event.detail && event.detail.elt) || event.target;
    if (!elt || elt.id !== "thread-tail") return;

    var thread = document.getElementById("thread");
    if (!thread) return event.preventDefault();

    /* Quiet while this page is following a reply. That reply delivers its own
       bubbles through the `done` frame, which is the only channel that can get
       the *order* right -- and it is the one window in which the transcript's
       order moves underneath us, since `_inject` restamps the placeholder to
       sort after a prompt taken into it. Asking during it is how a page appends
       a bubble it already has.

       Exact rather than approximate: a page holding an incomplete assistant
       bubble always carries this attribute, which is the state machine
       `_message.html` documents. */
    if (thread.querySelector("[sse-connect]")) return event.preventDefault();

    /* Deliberately not `article.msg:last-of-type`. That is per-parent, and
       `querySelector` returns the first match in document order -- so on a
       compacted chat it answers with the last article inside
       `<details class="compacted">` rather than the newest message. The last of
       everything matching is what "the last bubble this page holds" means. */
    var articles = thread.querySelectorAll("article.msg");
    var last = articles.length ? articles[articles.length - 1] : null;
    if (!last || last.id.indexOf("msg-") !== 0) return event.preventDefault();

    event.detail.parameters.after = last.id.slice(4);
  });

  /* Whatever the reason -- the composer's POST committing between this request
     going out and its answer coming back, a `done` frame landing first -- if any
     bubble in the answer is already on the page then the page has moved on since
     the question was asked, and swapping would duplicate it. A duplicate here is
     not cosmetic: it would carry a second `sse-connect` for one message.

     This is the race `hx-sync` cannot reach, since the two requests come from
     different elements. The whole answer is dropped rather than filtered: the
     next poll is five seconds away and recomputes its cursor from a DOM that has
     settled, which is a correct page one tick late instead of a wrong one now. */
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    var elt = (event.detail && event.detail.elt) || event.target;
    if (!elt || elt.id !== "thread-tail" || !event.detail) return;

    var seen = /\bid="(msg-[^"]+)"/g;
    var body = event.detail.serverResponse || "";
    var match;
    while ((match = seen.exec(body)) !== null) {
      if (document.getElementById(match[1])) {
        event.detail.shouldSwap = false;
        return;
      }
    }
  });

  /* Tokens arriving over SSE are appended outside the normal swap cycle.

     Narrowed to frames that land in the thread: `canvas` and `plan` arrive on
     this event too and land in a panel. The rest all land in the bubble, and
     each is a request for the same single scroll a frame -- `scrollThread`
     coalesces them, so a pass of several frames moves the transcript once. */
  document.body.addEventListener("htmx:sseMessage", function (event) {
    var target = event.target;
    if (target && target.closest && !target.closest("#thread-scroll")) return;
    scrollThread(false);
  });

  /* A temporary chat that lives "until you leave it" says so on the way out.
     `pagehide` rather than `beforeunload`: it fires on mobile when a tab is
     put away, and does not stop the page entering the back/forward cache. A
     reload fires it too, which is why the server waits a minute and the page
     that comes back cancels it. sendBeacon carries the session cookie, and a
     failed one costs nothing: the sweep's floor deletes the chat anyway. */
  var leave = document.querySelector("[data-temporary-leave]");
  if (leave && navigator.sendBeacon) {
    window.addEventListener("pagehide", function () {
      navigator.sendBeacon(leave.getAttribute("data-temporary-leave"));
    });
  }
})();
