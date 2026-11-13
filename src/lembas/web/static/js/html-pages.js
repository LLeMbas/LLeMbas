/*
  Pages in answers (harness spec 3.8.0, pages.md).

  The Markdown renderer leaves a mount where a page is: a title, an empty
  frame, the source behind a disclosure (`services/markdown.py`). This gives
  the frame its address -- `<base>/<digest>`, the base being the nearest
  `data-pages-base` (a reply's, a report's) -- with the theme's colours in the
  query, so the page can follow light and dark through its `--page-*`
  variables without a line of script inside it.

  Nothing here reads anything a page says. The frame is sealed (an empty
  `sandbox`, a policy of its own, no script: `security/sealed.py`), so there
  is no message to listen for and no height to be told: a page has a fixed
  height and scrolls inside it, and "Open larger" shows it again in a dialog.
  Everything is built with createElement and textContent.
*/
(function () {
  "use strict";

  /* Which of the application's colours each of the frame's variables is. */
  var t = window.lembasT || function (text) { return text; };
  var COLOURS = { bg: "--surface", ink: "--ink", muted: "--ink-muted", accent: "--accent", line: "--border" };

  function theme() {
    var style = getComputedStyle(document.documentElement);
    var parts = [];
    Object.keys(COLOURS).forEach(function (name) {
      var value = style.getPropertyValue(COLOURS[name]).trim();
      if (value) parts.push(name + "=" + encodeURIComponent(value));
    });
    return parts.join("&");
  }

  function address(mount) {
    var holder = mount.closest("[data-pages-base]");
    var digest = mount.getAttribute("data-digest") || "";
    if (!holder || !/^[0-9a-f]{32}$/.test(digest)) return "";
    return holder.getAttribute("data-pages-base") + "/" + digest + "?" + theme();
  }

  function fill(mount) {
    var frame = mount.querySelector("iframe");
    var src = address(mount);
    if (!frame || !src) {
      /* Nowhere to load it from (a preview, a quoted reply): the note and the
         source stand for it. */
      mount.classList.add("html-page--plain");
      mount.setAttribute("data-ready", "plain");
      return;
    }
    if (frame.getAttribute("src") !== src) frame.setAttribute("src", src);
    mount.setAttribute("data-ready", "frame");
  }

  var queued = false;
  function scan(all) {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () {
      queued = false;
      var selector = all === true ? "[data-html-page]" : "[data-html-page]:not([data-ready])";
      Array.prototype.forEach.call(document.querySelectorAll(selector), fill);
    });
  }

  function larger(mount) {
    var src = address(mount);
    if (!src || typeof HTMLDialogElement === "undefined") return;
    var title = mount.querySelector(".html-page__title");
    var dialog = document.createElement("dialog");
    dialog.className = "dialog html-page-dialog";
    var bar = document.createElement("div");
    bar.className = "html-page__bar";
    var name = document.createElement("span");
    name.className = "html-page__title";
    name.textContent = title ? title.textContent : "";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn--sm";
    close.textContent = t("Close");
    close.addEventListener("click", function () { dialog.close(); });
    bar.appendChild(name);
    bar.appendChild(close);
    var frame = document.createElement("iframe");
    frame.className = "html-page__frame html-page__frame--large";
    frame.setAttribute("sandbox", "");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("title", name.textContent);
    frame.setAttribute("src", src);
    dialog.appendChild(bar);
    dialog.appendChild(frame);
    dialog.addEventListener("close", function () { dialog.remove(); });
    document.body.appendChild(dialog);
    dialog.showModal();
    close.focus();
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-html-page-larger]") : null;
    if (!button) return;
    var mount = button.closest("[data-html-page]");
    if (mount) larger(mount);
  });

  /* A reply arrives in pieces and is swapped whole at its end; a chat is
     opened in place. Whatever added the mount, it is found on the next frame. */
  new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      if (records[i].addedNodes.length) { scan(); return; }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  /* The theme changed: the pages follow, by being asked for again. */
  new MutationObserver(function () { scan(true); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-base", "style"] });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
})();
