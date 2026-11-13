/*
  Files: the viewer dialog, highlighted code, and the explorer's drag and drop.

  * `[data-open-viewer]` (a `show` card's Open, a link to a file) opens the
    viewer in a dialog beside the conversation instead of leaving the page.
    The link is a real address, so a middle click or no JavaScript opens the
    viewer as a page. A compact card that arrives while the page is open opens
    by itself on a wide screen -- that is what `show` is for -- and never on
    page load, and never on a phone, where a dialog would cover the reply.
  * `[data-show-inline]` is an HTML file drawn in the conversation
    (chat/_tool_activity.html). A still page needs nothing from here: its
    frame has its address in the markup, so it is there on every load, after
    every swap. A page with scripts waits for **Run page**; pressing it puts
    the scripted sealed frame in its place, and a page that was run is run
    again when the reply around it is drawn anew. Nothing a page says is
    read but its height, a number, clamped by the style sheet.
  * `code[data-highlight]` is highlighted by the vendored highlight.js,
    fetched the first time a page has code to show. It reads the element's
    text and writes spans; the text came escaped from the server and is never
    parsed as markup.
  * The explorer: a row dragged onto a folder (a row, the tree, the path above)
    is moved there; files dropped from the computer onto the list are
    uploaded into the folder shown; choosing files sends the upload form.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  /* --- Highlighting ---------------------------------------------------- */
  var hljsLoading = null;

  function loadHljs(src) {
    if (window.hljs) return Promise.resolve(window.hljs);
    if (hljsLoading) return hljsLoading;
    hljsLoading = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = src;
      script.onload = function () { resolve(window.hljs); };
      script.onerror = reject;
      document.head.appendChild(script);
    });
    return hljsLoading;
  }

  function highlight(scope) {
    if (!scope || !scope.querySelectorAll) return;
    scope.querySelectorAll("code[data-highlight]:not([data-highlighted-by-us])").forEach(function (code) {
      code.setAttribute("data-highlighted-by-us", "1");
      var src = code.dataset.hljsSrc;
      if (!src) return;
      loadHljs(src).then(function (hljs) {
        if (!hljs) return;
        var language = code.dataset.highlight;
        if (language && hljs.getLanguage(language)) code.classList.add("language-" + language);
        hljs.highlightElement(code);
        jumpToLine(code.closest("pre"), code.dataset.line);
      }).catch(function () { /* plain text is still the text */ });
    });
    scope.querySelectorAll("pre[data-line]").forEach(function (pre) {
      jumpToLine(pre, pre.dataset.line);
    });
  }

  function jumpToLine(pre, line) {
    var n = parseInt(line, 10);
    if (!pre || !n || n < 2) return;
    var height = parseFloat(getComputedStyle(pre).lineHeight) || 0;
    if (height) pre.scrollTop = (n - 3) * height;
  }

  /* --- The viewer dialog ----------------------------------------------- */
  var dialog = null;

  function viewerDialog() {
    if (dialog && document.body.contains(dialog)) return dialog;
    dialog = document.createElement("dialog");
    dialog.className = "dialog viewer-dialog";
    dialog.setAttribute("aria-label", t("File"));
    dialog.setAttribute("data-testid", "viewer-dialog");
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn--icon btn--sm viewer-dialog__close";
    close.setAttribute("aria-label", t("Close"));
    close.textContent = "×";
    close.addEventListener("click", function () { dialog.close(); });
    var body = document.createElement("div");
    body.className = "viewer-dialog__body";
    dialog.appendChild(close);
    dialog.appendChild(body);
    dialog.addEventListener("cancel", function () { dialog.close(); });
    document.body.appendChild(dialog);
    return dialog;
  }

  function openViewer(url, modal) {
    if (!window.htmx) { location.href = url; return; }
    var box = viewerDialog();
    var body = box.querySelector(".viewer-dialog__body");
    window.htmx.ajax("GET", url, { target: body, swap: "innerHTML" }).then(function () {
      highlight(body);
    });
    if (!box.open) {
      if (modal) box.showModal(); else box.show();
    }
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[data-open-viewer]");
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    openViewer(link.href, !wide());
  });

  function wide() {
    return window.matchMedia && window.matchMedia("(min-width: 64rem)").matches;
  }

  /* A card that arrives while the page is open. The keys present when the
     page loaded are known; any new one opens the viewer, once. A page drawn
     in the conversation is already where it is looked at: it opens nothing. */
  var seen = {};
  function markSeen(scope) {
    (scope || document).querySelectorAll("[data-show-key]").forEach(function (card) {
      seen[card.dataset.showKey] = true;
    });
  }

  function added(records, selector) {
    var found = [];
    records.forEach(function (record) {
      record.addedNodes.forEach(function (node) {
        if (node.nodeType !== 1) return;
        if (node.matches && node.matches(selector)) found.push(node);
        if (node.querySelectorAll) {
          Array.prototype.push.apply(found, node.querySelectorAll(selector));
        }
      });
    });
    return found;
  }

  function watchCards() {
    markSeen(document);
    new MutationObserver(function (records) {
      var fresh = null;
      added(records, "[data-show-key]").forEach(function (card) {
        if (card.hasAttribute("data-show-inline")) {
          /* Drawn anew by a stream's frame or the finished reply: a page its
             person ran is run again, not put back behind the button. */
          if (ran[card.dataset.showKey]) run(card);
        } else if (!seen[card.dataset.showKey]) {
          fresh = card;
        }
        seen[card.dataset.showKey] = true;
      });
      if (fresh && wide()) {
        var open = fresh.querySelector("a[data-open-viewer]");
        if (open) openViewer(open.href, false);
      }
    }).observe(document.body, { childList: true, subtree: true });
  }

  /* --- A shown page, run in the conversation ----------------------------- */
  var ran = {};

  function run(block) {
    var hold = block.querySelector("[data-show-hold]");
    var src = block.getAttribute("data-frame");
    if (!hold || !src) return;
    var frame = document.createElement("iframe");
    frame.className = "show-inline__frame show-inline__frame--run";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("data-testid", "show-frame");
    frame.setAttribute("title", block.getAttribute("data-frame-title") || "");
    frame.setAttribute("src", src);
    hold.parentNode.replaceChild(frame, hold);
    block.setAttribute("data-mode", "running");
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-show-run]");
    var block = button && button.closest("[data-show-inline]");
    if (!block) return;
    ran[block.dataset.showKey] = true;
    run(block);
  });

  /* A page that runs says how tall it is. It may say anything: only a
     message from a frame we put there is read, only a finite number is
     taken, and the style sheet keeps it between its least and its most. */
  window.addEventListener("message", function (event) {
    var data = event.data;
    var height = data && typeof data === "object" ? data.lembasShownHeight : null;
    if (typeof height !== "number" || !isFinite(height) || height <= 0) return;
    var frames = document.querySelectorAll("iframe.show-inline__frame--run");
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === event.source) {
        frames[i].style.setProperty("--shown-h", Math.min(Math.round(height), 100000) + "px");
        return;
      }
    }
  });

  /* --- The explorer ------------------------------------------------------ */
  var ROW_TYPE = "application/x-lembas-path";

  function inPlace(url, values) {
    var root = document.querySelector("[data-in-place-root]");
    if (!window.htmx || !root) return;
    window.htmx.ajax("POST", url, {
      source: root,
      values: values,
      target: root,
      select: "[data-in-place-root]",
      swap: "outerHTML show:none focus-scroll:false",
      headers: { "LMB-In-Place": "1" },
    });
  }

  document.addEventListener("dragstart", function (event) {
    var row = event.target.closest && event.target.closest(".file-row[data-path][draggable='true']");
    if (!row || !event.dataTransfer) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(ROW_TYPE, row.dataset.path);
    event.dataTransfer.setData("text/plain", row.dataset.path);
    row.classList.add("is-dragging");
  });

  document.addEventListener("dragend", function (event) {
    var row = event.target.closest && event.target.closest(".file-row");
    if (row) row.classList.remove("is-dragging");
    document.querySelectorAll(".file-drop-target").forEach(function (el) {
      el.classList.remove("file-drop-target", "is-drop-target");
    });
  });

  function has(event, type) {
    var types = event.dataTransfer && event.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, type) !== -1;
  }

  document.addEventListener("dragover", function (event) {
    var dir = event.target.closest && event.target.closest("[data-drop-dir]");
    if (dir && has(event, ROW_TYPE)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      dir.classList.add("file-drop-target", "is-drop-target");
      return;
    }
    var list = event.target.closest && event.target.closest("[data-drop-files]");
    if (list && has(event, "Files")) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      list.classList.add("file-drop-target", "is-drop-target");
    }
  });

  document.addEventListener("dragleave", function (event) {
    var el = event.target.closest && event.target.closest(".file-drop-target");
    if (el && !el.contains(event.relatedTarget)) el.classList.remove("file-drop-target", "is-drop-target");
  });

  document.addEventListener("drop", function (event) {
    var dir = event.target.closest && event.target.closest("[data-drop-dir]");
    if (dir && has(event, ROW_TYPE)) {
      event.preventDefault();
      var path = event.dataTransfer.getData(ROW_TYPE);
      var to = dir.dataset.dropDir;
      dir.classList.remove("file-drop-target", "is-drop-target");
      if (!path || path === to) return;
      var here = new URLSearchParams(location.search).get("path") || "/";
      inPlace("/files/move", { path: path, to: to, back_to: here });
      return;
    }
    var list = event.target.closest && event.target.closest("[data-drop-files]");
    if (list && has(event, "Files")) {
      event.preventDefault();
      list.classList.remove("file-drop-target", "is-drop-target");
      var form = document.querySelector("form[data-upload-form]");
      var input = form && form.querySelector('input[type="file"]');
      if (!input) return;
      input.files = event.dataTransfer.files;
      send(form);
    }
  });

  function send(form) {
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }

  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!input.matches || !input.matches("form[data-upload-form] input[type='file']")) return;
    if (input.files && input.files.length) send(input.form);
  });

  function boot() {
    highlight(document);
    if (window.htmx) window.htmx.onLoad(highlight);
    watchCards();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  window.lembas = window.lembas || {};
  window.lembas.openViewer = openViewer;
})();
