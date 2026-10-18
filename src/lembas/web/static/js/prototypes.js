/*
  Prototypes: mounting the frame a model's HTML runs in.

  The server renders a prototype fence as a placeholder -- `.proto`, carrying
  the kind, the title and a digest of the block -- and never as an <iframe>,
  because the sanitiser every reply passes through does not let one through and
  must not learn to. This builds the frame from those attributes and nothing
  else, and the address it points at serves the page under a sealing policy
  (services/prototypes.py). The `sandbox` attribute set here is the second lock
  on the same door.

  Five jobs:

  1. Mount: a frame inside an inline prototype, an Open button on a page one,
     and the tools on both. Driven by a MutationObserver, because placeholders
     arrive in stream frames, `done` frames, history pages and rewinds, and a
     hook in each of those is four places to forget one.
  2. Size: an inline frame is as tall as its page says it is. The page is an
     opaque origin and cannot be measured from here, so the boot script the
     server puts in front of it posts its height, and this clamps it.
  3. Theme: the reader's colours, sent to each page as `--lembas-*` variables
     when it says it is ready and again whenever the theme changes.
  4. The panel: one frame, pointed at whichever page was opened, with a desktop
     and a phone width.
  5. Open a finished reply's page in the panel by itself -- once, on a screen
     wide enough to show it beside the conversation.

  Never innerHTML from anywhere. The only markup inserted is a <template> the
  page itself rendered; everything from the placeholder goes in as textContent
  or as a validated piece of a URL.
*/
(function () {
  "use strict";

  /* Ids are this application's own (32 hex), and the digest is 16 hex -- but a
     model can write a placeholder by hand, so both are checked rather than
     trusted before they become part of an address. */
  var ID = /^[A-Za-z0-9_-]{1,64}$/;
  var DIGEST = /^[0-9a-f]{16}$/;
  var KINDS = { chat: true, page: true };

  /* What a page is told about the reader's theme. Colours, the three type
     stacks and the radii -- enough to look like it belongs, not a copy of the
     whole token set. */
  var THEME = [
    "bg", "bg-sunken", "surface", "surface-raised", "ink", "ink-muted", "ink-faint",
    "border", "border-strong", "accent", "accent-ink", "accent-soft", "leaf",
    "danger", "warning", "success", "code-bg",
    "font-body", "font-ui", "font-mono", "radius", "radius-sm", "radius-lg"
  ];

  var VIEW_KEY = "lembas-prototype-view";
  var WIDE = "(min-width: 64.0625rem)";
  var NARROW = "(max-width: 48rem)";

  /* --- Strings, from the template the page rendered in the reader's language */
  function ui() { return document.getElementById("proto-ui"); }

  function text(name) {
    var template = ui();
    var node = template && template.content.querySelector('[data-text="' + name + '"]');
    return node ? node.textContent : "";
  }

  function cloneTools() {
    var template = ui();
    var tools = template && template.content.querySelector(".proto__tools");
    return tools ? tools.cloneNode(true) : null;
  }

  function cloneIcon() {
    var template = ui();
    var icon = template && template.content.querySelector("[data-proto-icon]");
    return icon ? icon.cloneNode(true) : null;
  }

  /* --- Addresses --------------------------------------------------------- */
  function scheme() {
    var value = getComputedStyle(document.documentElement).colorScheme || "";
    return value.indexOf("dark") !== -1 ? "dark" : "light";
  }

  function addressOf(card) {
    var article = card.closest("[data-chat][data-message]");
    if (!article) return null;
    var chat = article.getAttribute("data-chat");
    var message = article.getAttribute("data-message");
    var digest = card.getAttribute("data-proto-hash");
    if (!ID.test(chat || "") || !ID.test(message || "") || !DIGEST.test(digest || "")) {
      return null;
    }
    return "/api/chats/" + encodeURIComponent(chat) + "/messages/" +
      encodeURIComponent(message) + "/prototypes/" + digest;
  }

  /* The height each inline page last reported, by address. The steps container
     is swapped whole every time a round closes, which remounts every frame in
     it from scratch: without this each one came back at the starting height
     and jumped to its real one when the page had booted and said so again --
     and the transcript jumped with it, once per frame per round. A frame
     mounted on an address already measured is reserved its height at once. */
  var heights = {};

  function frame(address, title, className) {
    var element = document.createElement("iframe");
    element.className = className;
    element.setAttribute("data-proto-frame", "");
    /* No allow-same-origin, ever: with it, a page served from this origin is
       this origin. The server's policy says the same thing again. */
    element.setAttribute("sandbox", "allow-scripts allow-forms");
    element.setAttribute("referrerpolicy", "no-referrer");
    element.setAttribute("title", title);
    element.setAttribute("data-address", address);
    if (heights[address]) element.style.setProperty("--proto-h", heights[address] + "px");
    element.src = address + "?scheme=" + scheme();
    return element;
  }

  /* The transcript follows a frame that grew, through the one scroll owner in
     app.js, so the growth and the follow are one layout rather than a jump and
     a correction. */
  function follow() {
    if (window.lembas && window.lembas.scrollThread) window.lembas.scrollThread(false);
  }

  /* --- Mounting ---------------------------------------------------------- */
  function titleOf(card) {
    return card.getAttribute("data-proto-title") || text("untitled");
  }

  function status(card, message) {
    var line = card.querySelector(".proto__status");
    if (!line) {
      line = document.createElement("p");
      line.className = "proto__status";
      var head = card.querySelector(".proto__head");
      if (head && head.nextSibling) card.insertBefore(line, head.nextSibling);
      else card.appendChild(line);
    }
    line.textContent = message;
  }

  function mountOne(card) {
    if (card.hasAttribute("data-proto-mounted")) return;
    card.setAttribute("data-proto-mounted", "");

    var kind = card.getAttribute("data-proto");
    if (!KINDS[kind]) return;

    var head = card.querySelector(".proto__head");
    var title = card.querySelector(".proto__title");
    if (title && !title.textContent) title.textContent = text("untitled");
    var icon = cloneIcon();
    if (head && icon) head.insertBefore(icon, head.firstChild);

    if (card.classList.contains("proto--writing")) {
      var lines = card.getAttribute("data-proto-lines") || "0";
      status(card, text("writing").replace("{lines}", lines));
      return;
    }

    /* The step still being written is re-rendered a dozen times a second, and a
       frame in it would reload on every one. It is shown once that step
       closes, which is moments later. */
    if (card.closest(".msg__body--live")) {
      status(card, text("waiting"));
      return;
    }

    var address = addressOf(card);
    if (!address) return;      /* a report, a note: the source is all there is */

    var tools = cloneTools();
    if (tools && head) {
      var tab = tools.querySelector("[data-proto-tab]");
      if (tab) tab.href = address + "?scheme=" + scheme();
      /* Inline ones open into the panel with the expand button; a page one has
         the labelled Open. Each card keeps the one that suits it. */
      var drop = kind === "page" ? "[data-proto-expand]" : "[data-proto-open]";
      var unwanted = tools.querySelector(drop);
      if (unwanted) unwanted.remove();
      head.appendChild(tools);
    }

    if (kind === "chat") {
      var stage = card.querySelector(".proto__stage");
      if (stage) stage.appendChild(frame(address, titleOf(card), "proto__frame"));
    } else {
      status(card, text("page"));
    }

    revealToggles();
  }

  function mount(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && root.matches(".proto[data-proto]")) mountOne(root);
    root.querySelectorAll(".proto[data-proto]").forEach(mountOne);
  }

  /* The topbar button and the window-switch entry exist from the start but
     stay hidden until there is something for them to show. */
  function revealToggles() {
    if (!document.getElementById("prototype")) return;
    document.querySelectorAll("[data-proto-toggle][hidden]").forEach(function (button) {
      button.hidden = false;
    });
  }

  /* --- The panel --------------------------------------------------------- */
  function panel() { return document.getElementById("prototype"); }

  function openInPanel(card) {
    var address = addressOf(card);
    if (!address) return;
    var host = panel();
    if (!host) {
      /* A page with no panel -- the Messages view. A tab is the next best
         place, and the address is sealed wherever it is opened. */
      window.open(address + "?scheme=" + scheme(), "_blank", "noopener");
      return;
    }

    var stage = host.querySelector("[data-proto-stage]");
    var empty = host.querySelector("[data-proto-empty]");
    var old = stage && stage.querySelector("[data-proto-frame]");
    var title = titleOf(card);

    if (old && old.getAttribute("data-address") === address) {
      /* Opening the one already in front shows the panel; it does not restart
         a page somebody has been clicking through. */
    } else if (stage) {
      if (old) old.remove();
      var element = frame(address, title, "proto-panel__frame");
      element.setAttribute("data-address", address);
      stage.appendChild(element);
    }
    if (empty) empty.hidden = true;

    var label = host.querySelector("[data-proto-panel-title]");
    if (label) label.textContent = title;
    var tab = host.querySelector("[data-proto-tab]");
    if (tab) { tab.href = address + "?scheme=" + scheme(); tab.hidden = false; }
    var download = host.querySelector("[data-proto-download]");
    if (download) { download.href = address + "/download"; download.hidden = false; }
    var reload = host.querySelector("[data-proto-reload]");
    if (reload) reload.hidden = false;

    revealToggles();
    if (window.lembas && window.lembas.setPanel) {
      window.lembas.setPanel("#prototype", true, "side");
    } else {
      host.hidden = false;
    }
    fit();
  }

  function reload() {
    var host = panel();
    var current = host && host.querySelector("[data-proto-frame]");
    /* Re-assigning the address runs the page again from the top; a game that
       has ended or a form somebody filled in starts over. */
    if (current) current.src = current.src;
  }

  /* --- Fitting a viewport into the panel ---------------------------------
     The page is laid out at a desktop's width or a phone's, and the picture is
     scaled to fit; the CSS does the scaling from `--proto-scale`, and this
     works out the number whenever the stage changes size. */
  function lengthPx(value, fallback) {
    var text = (value || "").trim();
    var number = parseFloat(text);
    if (!isFinite(number)) return fallback;
    if (/rem$/.test(text)) {
      return number * (parseFloat(getComputedStyle(document.documentElement).fontSize) || 16);
    }
    return number;
  }

  function fit() {
    var host = panel();
    var stage = host && host.querySelector("[data-proto-stage]");
    if (!stage || host.hidden) return;
    var style = getComputedStyle(stage);
    var width = stage.clientWidth;
    var height = stage.clientHeight;
    if (!width || !height) return;

    var scale;
    var seen;
    if (stage.getAttribute("data-view") === "phone") {
      var phoneWidth = lengthPx(style.getPropertyValue("--proto-phone-width"), 390);
      var phoneHeight = lengthPx(style.getPropertyValue("--proto-phone-height"), 844);
      var room = 2 * lengthPx(style.getPropertyValue("--sp-4"), 16);
      var edge = 2 * lengthPx(style.getPropertyValue("--border-w-thick"), 2);
      scale = Math.min(1, (width - room) / (phoneWidth + edge), (height - room) / (phoneHeight + edge));
      seen = [phoneWidth, phoneHeight];
    } else {
      var desktop = lengthPx(style.getPropertyValue("--proto-desktop-width"), 1280);
      scale = Math.min(1, width / desktop);
      seen = [scale < 1 ? desktop : width, height / scale];
    }
    scale = Math.max(0.1, scale);
    stage.style.setProperty("--proto-scale", String(scale));

    var badge = stage.querySelector("[data-proto-scale]");
    if (badge) {
      var shrunk = scale < 0.995 && !!stage.querySelector("[data-proto-frame]");
      badge.hidden = !shrunk;
      badge.textContent = Math.round(seen[0]) + " \u00d7 " + Math.round(seen[1]) +
        " \u00b7 " + Math.round(scale * 100) + "%";
    }
  }

  function setView(view) {
    var host = panel();
    var stage = host && host.querySelector("[data-proto-stage]");
    if (!stage) return;
    stage.setAttribute("data-view", view === "phone" ? "phone" : "web");
    host.querySelectorAll("[data-proto-view]").forEach(function (input) {
      input.checked = input.value === stage.getAttribute("data-view");
    });
    try { localStorage.setItem(VIEW_KEY, stage.getAttribute("data-view")); } catch (e) { /* private window */ }
    fit();
  }

  /* Somebody's last choice on this device, or -- before they have made one --
     the view that suits the screen they are holding: a desktop shrunk to a
     quarter of its size is a thumbnail on a phone, not a preview. */
  function restoreView() {
    var view = "";
    try { view = localStorage.getItem(VIEW_KEY) || ""; } catch (e) { /* private window */ }
    if (!view) view = window.matchMedia(NARROW).matches ? "phone" : "web";
    var stage = panel() && panel().querySelector("[data-proto-stage]");
    if (stage) stage.setAttribute("data-view", view === "phone" ? "phone" : "web");
    if (panel()) {
      panel().querySelectorAll("[data-proto-view]").forEach(function (input) {
        input.checked = input.value === (view === "phone" ? "phone" : "web");
      });
    }
  }

  /* A reply that has just finished with a page in it opens that page --
     the last one, if it wrote several, since that is the version it settled
     on. Only where the panel sits beside the conversation: on a narrower
     screen it would take the reply's own words off the screen the moment
     they arrived, and the card is one tap away. Once per reply: the flag is
     consumed here, so re-rendering the bubble later does not open it again. */
  function openFinished(root) {
    var articles = [];
    if (root.matches && root.matches("[data-just-finished]")) articles.push(root);
    if (root.querySelectorAll) {
      root.querySelectorAll("[data-just-finished]").forEach(function (a) { articles.push(a); });
    }
    articles.forEach(function (article) {
      article.removeAttribute("data-just-finished");
      if (!panel() || !window.matchMedia(WIDE).matches) return;
      var pages = article.querySelectorAll('.proto[data-proto="page"][data-proto-hash]');
      if (pages.length) openInPanel(pages[pages.length - 1]);
    });
  }

  /* --- Messages from the frames ------------------------------------------ */
  function frameFor(source) {
    var frames = document.querySelectorAll("iframe[data-proto-frame]");
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === source) return frames[i];
    }
    return null;
  }

  function themeTokens() {
    var style = getComputedStyle(document.documentElement);
    var tokens = {};
    THEME.forEach(function (name) {
      var value = style.getPropertyValue("--" + name).trim();
      if (value) tokens[name] = value;
    });
    return tokens;
  }

  function sendTheme(target) {
    try {
      target.contentWindow.postMessage(
        { lembasPrototype: "theme", tokens: themeTokens(), scheme: scheme() }, "*"
      );
    } catch (e) { /* the frame went away mid-call */ }
  }

  function onMessage(event) {
    var data = event.data;
    if (!data || typeof data !== "object" || typeof data.lembasPrototype !== "string") return;
    var source = frameFor(event.source);
    if (!source) return;

    if (data.lembasPrototype === "ready") {
      sendTheme(source);
    } else if (data.lembasPrototype === "size" && source.classList.contains("proto__frame")) {
      var height = Number(data.height);
      if (!isFinite(height) || height < 0) return;
      /* The CSS clamps it between the two tokens; this only keeps an absurd
         number from being written at all. */
      var px = Math.min(Math.round(height), 100000);
      var address = source.getAttribute("data-address");
      if (address) heights[address] = px;
      if (source.style.getPropertyValue("--proto-h") === px + "px") return;
      source.style.setProperty("--proto-h", px + "px");
      follow();
    }
  }

  /* --- Wiring ------------------------------------------------------------ */
  function cardOf(target) { return target.closest(".proto[data-proto]"); }

  function onClick(event) {
    var open = event.target.closest("[data-proto-open], [data-proto-expand]");
    if (open) {
      var card = cardOf(open);
      if (card) {
        event.preventDefault();
        openInPanel(card);
      }
      return;
    }
    if (event.target.closest("[data-proto-reload]")) {
      event.preventDefault();
      reload();
    }
  }

  function start() {
    mount(document);
    openFinished(document);
    restoreView();

    new MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          mount(node);
          openFinished(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });

    window.addEventListener("message", onMessage);

    var host = panel();
    var stage = host && host.querySelector("[data-proto-stage]");
    if (stage && window.ResizeObserver) new ResizeObserver(fit).observe(stage);
    if (host) {
      host.addEventListener("lembas:toggle", function (event) {
        if (event.detail && event.detail.open) fit();
      });
    }
    document.addEventListener("click", onClick);
    document.addEventListener("change", function (event) {
      if (event.target.matches && event.target.matches("[data-proto-view]")) {
        setView(event.target.value);
      }
    });
    /* After the attribute has changed and the new tokens apply. */
    document.addEventListener("lembas:theme", function () {
      window.requestAnimationFrame(function () {
        document.querySelectorAll("iframe[data-proto-frame]").forEach(sendTheme);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
