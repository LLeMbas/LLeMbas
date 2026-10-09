/*
  The canvas panel's small amount of behaviour.

  Almost all of it is htmx: the tabs post, the save posts, the panel swaps.
  Three things need JavaScript, and only three.

  1. Tab inserts a tab character instead of leaving the field. Without it the
     one editor affordance whose absence is genuinely maddening is missing.
  2. The editor tracks whether it has been changed, so the tab shows a dot and
     so leaving the page with unsaved work warns.
  3. Opening the panel scrolls the active tab into view, since the strip
     scrolls sideways and the tab in front may be off the end of it.

  What is *not* here is any guard against a swap taking the editor away. A
  model opening a file sends the tab strip and nothing else, and does not move
  the active tab -- both settled on the server, where they cannot be lost to a
  race.

  Nothing here ever assigns innerHTML from a fetch: every swap is htmx's, and
  the content is a file off somebody else's disk.
*/
(function () {
  "use strict";

  var panel = null;
  /* Whether the editor has been changed since it was last rendered. Module
     state rather than a data attribute, because the element it describes is
     replaced by every swap and an attribute would go with it. */
  var dirty = false;

  function markDirty(on) {
    dirty = !!on;
    var dot = panel && panel.querySelector(".canvas__tab.is-active [data-canvas-dirty]");
    if (dot) dot.hidden = !dirty;
  }

  /* --- Typing ------------------------------------------------------------- */
  function onInput(event) {
    if (event.target && event.target.matches("[data-canvas-editor]")) markDirty(true);
  }

  function onKeydown(event) {
    var box = event.target;
    if (!box || !box.matches || !box.matches("[data-canvas-editor]")) return;
    if (event.key !== "Tab" || event.ctrlKey || event.altKey || event.metaKey) return;
    /* Shift+Tab still leaves the field, which is the only way out of it for
       somebody using the keyboard. */
    if (event.shiftKey) return;

    event.preventDefault();
    var start = box.selectionStart;
    var end = box.selectionEnd;
    box.value = box.value.slice(0, start) + "\t" + box.value.slice(end);
    box.selectionStart = box.selectionEnd = start + 1;
    markDirty(true);
  }

  /* --- Swaps -------------------------------------------------------------- */
  /* There is deliberately nothing here guarding the editor against a swap.
     A model opening a file sends the tab *strip* and nothing else -- the body
     is never pushed -- and `canvas.open_tab` does not move the active tab for
     a model, so the file in front and the field being typed in both stay put.
     That is settled on the server, where it cannot be lost to a race. */
  function onAfterSwap(event) {
    if (!panel || !event.target || !panel.contains(event.target)) return;
    /* The server has just rendered what is stored, so nothing is unsaved until
       somebody types again. */
    markDirty(false);
    showActiveTab();
  }

  function showActiveTab() {
    var active = panel && panel.querySelector(".canvas__tab.is-active");
    if (active && active.scrollIntoView) {
      active.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  /* --- Leaving with unsaved work ------------------------------------------ */
  function onBeforeUnload(event) {
    if (!dirty) return;
    event.preventDefault();
    /* The browser shows its own wording; returning a string is what makes older
       ones show anything at all. */
    event.returnValue = "";
    return "";
  }

  /* --- Wiring ------------------------------------------------------------- */
  function start() {
    panel = document.querySelector("[data-canvas]");
    if (!panel) return;

    panel.addEventListener("input", onInput);
    panel.addEventListener("keydown", onKeydown);
    panel.addEventListener("lembas:toggle", function (event) {
      if (event.detail && event.detail.open) showActiveTab();
    });

    /* On document, not on the panel: htmx fires these on the element being
       swapped, and by the time afterSwap runs the old node is gone. */
    document.body.addEventListener("htmx:afterSwap", onAfterSwap);
    window.addEventListener("beforeunload", onBeforeUnload);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
