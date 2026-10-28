/*
  Save in place.

  A settings or administration form used to post, be redirected back to its
  page, and reload it: the page jumped to the top, an open section shut, and
  the person had to find the field they had just saved. Now every plain POST
  form inside a `[data-in-place-root]` is sent by htmx instead, with
  `LMB-In-Place: 1`, and the server answers with the page's new state
  (web/in_place.py) -- the root is swapped where it stands and nothing else
  moves.

  Opting out: a form with its own hx-* request, a `target` (it opens a
  window or the top frame on purpose), a GET method, or `data-in-place="off"`
  (it downloads a file, or signs out) is left exactly as it was. With
  JavaScript off nothing here runs and every form posts as it always did.

  What the page said once -- an alert rendered from `?saved=` or `?error=`,
  marked `data-flash` -- becomes a toast instead of a box at the top of a page
  the person is not looking at. When the page has no such box the words come
  from the `lembas:in-place` trigger the server sends along.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var ROOT = "[data-in-place-root]";
  var VERBS = ["hx-get", "hx-post", "hx-put", "hx-patch", "hx-delete"];

  function eligible(form) {
    if (!form || form.tagName !== "FORM") return false;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return false;
    if (form.hasAttribute("target")) return false;
    if (form.dataset.inPlace === "off") return false;
    for (var i = 0; i < VERBS.length; i++) {
      if (form.hasAttribute(VERBS[i])) return false;
    }
    return !!form.closest(ROOT);
  }

  function prepare(scope) {
    if (!window.htmx || !scope || !scope.querySelectorAll) return;
    var forms = scope.matches && scope.matches("form") ? [scope] : [];
    forms = forms.concat(Array.prototype.slice.call(scope.querySelectorAll("form")));
    forms.forEach(function (form) {
      if (form.dataset.inPlaceReady || !eligible(form)) return;
      form.dataset.inPlaceReady = "1";
      form.setAttribute("hx-post", form.getAttribute("action") || (location.pathname + location.search));
      form.setAttribute("hx-target", "closest " + ROOT);
      form.setAttribute("hx-select", ROOT);
      form.setAttribute("hx-swap", "outerHTML show:none focus-scroll:false");
      form.setAttribute("hx-headers", JSON.stringify({ "LMB-In-Place": "1" }));
      window.htmx.process(form);
    });
  }

  function isInPlace(detail) {
    var headers = detail && detail.requestConfig && detail.requestConfig.headers;
    return !!(headers && headers["LMB-In-Place"]);
  }

  /* What the swap would otherwise lose: the scroll position of whatever scrolls
     the root (a shorter page in between would clamp it), and which sections
     were open. Taken just before, put back just after. */
  var kept = null;

  function scroller(node) {
    for (var el = node && node.parentElement; el; el = el.parentElement) {
      var style = getComputedStyle(el);
      if (/(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight) return el;
    }
    return document.scrollingElement || document.documentElement;
  }

  function remember(root) {
    var box = scroller(root);
    var open = [];
    root.querySelectorAll("details[id]").forEach(function (d) {
      open.push([d.id, d.open]);
    });
    kept = { box: box, top: box.scrollTop, open: open };
  }

  function restore() {
    if (!kept) return;
    var state = kept;
    kept = null;
    state.open.forEach(function (pair) {
      var d = document.getElementById(pair[0]);
      if (d && d.tagName === "DETAILS") d.open = pair[1];
    });
    state.box.scrollTop = state.top;
  }

  function sayFlashes(root, said) {
    var spoke = false;
    if (root) {
      root.querySelectorAll("[data-flash]").forEach(function (box) {
        var text = (box.textContent || "").replace(/\s+/g, " ").trim();
        if (text && window.lembas && window.lembas.notify) {
          window.lembas.notify(text, { kind: box.dataset.flash || "success" });
          spoke = true;
        }
        box.remove();
      });
    }
    if (spoke || !said || !window.lembas || !window.lembas.notify) return;
    /* A bare code ("1", "language") is not a sentence; a sentence is shown. */
    function sentence(value) { return value && /\s/.test(value) ? value : ""; }
    if (said.error != null) {
      window.lembas.notify(sentence(said.error) || t("That did not work."), { kind: "error" });
    } else if (said.saved != null || said.message != null) {
      window.lembas.notify(sentence(said.saved) || sentence(said.message) || t("Saved."),
                           { kind: "success" });
    }
  }

  var pendingSaid = null;

  document.addEventListener("lembas:in-place", function (event) {
    pendingSaid = event.detail || {};
  });

  document.addEventListener("htmx:beforeRequest", function (event) {
    if (!isInPlace(event.detail)) return;
    var root = event.detail.elt.closest(ROOT);
    if (root) remember(root);
    pendingSaid = null;
  });

  document.addEventListener("htmx:beforeSwap", function (event) {
    if (!isInPlace(event.detail)) return;
    var xhr = event.detail.xhr;
    var body = xhr.responseText || "";
    var hasRoot = body.indexOf("data-in-place-root") !== -1;
    if (xhr.status >= 400) {
      if (hasRoot) {
        /* A page re-rendered with its error: show it, as the browser would. */
        event.detail.shouldSwap = true;
        event.detail.isError = false;
        return;
      }
      var said = "";
      try { said = JSON.parse(body).detail || ""; } catch (e) { said = ""; }
      if (typeof said !== "string") said = "";
      if (window.lembas && window.lembas.notify) {
        window.lembas.notify(said || t("That did not work."), { kind: "error" });
      }
      event.detail.shouldSwap = false;
      kept = null;
      return;
    }
    if (xhr.getResponseHeader("HX-Redirect")) return;
    if (!hasRoot) {
      /* Never swap the root for nothing. A different page entirely is shown
         whole, as a reload would have shown it. */
      event.detail.shouldSwap = false;
      kept = null;
      if (!body.trim()) return;
      document.open();
      document.write(body);
      document.close();
    }
  });

  document.addEventListener("htmx:afterSettle", function (event) {
    if (!isInPlace(event.detail)) return;
    restore();
    var root = document.querySelector(ROOT);
    sayFlashes(root, pendingSaid);
    pendingSaid = null;
  });

  function boot() {
    prepare(document.body);
    if (window.htmx) window.htmx.onLoad(prepare);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
