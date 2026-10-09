/*
  Keeping an opened block open while the reply is still being written.

  The steps container is swapped with innerHTML every time a round closes, and
  the `done` frame replaces the whole bubble at the end. Both tear down every
  <details> inside and build new ones, so anything the reader had expanded shut
  itself again -- which, at up to twelve frames a second, made a tool block
  impossible to open at all rather than merely annoying to.

  The fix is to write down which are open before the swap and put them back
  after. It works because the ids are stable: `tool-{message}-{step}-{n}` and
  `think-{message}-{step}` are built from the mark index, the marks are
  append-only, and the finished bubble emits the same ids the live one did. See
  services/steps.py.

  Not `hx-preserve`: that keeps a node and its contents, and these nodes have to
  update. Not a CSS checkbox either -- the input would live inside the container
  being replaced, so it would be destroyed with everything else.
*/
(function () {
  "use strict";

  /* Keyed by container id, so two replies streaming into one page cannot read
     each other's state. */
  var open = {};

  function remember(box) {
    var ids = [];
    box.querySelectorAll("details[id][open]").forEach(function (details) {
      ids.push(details.id);
    });
    open[box.id || "steps"] = ids;
  }

  function restore(box) {
    (open[box.id || "steps"] || []).forEach(function (id) {
      /* CSS.escape, never concatenation into a selector: an id is built from a
         message id and two integers today, but a selector assembled by hand is
         how that stops being true safely. */
      var found = box.querySelector("details[id='" + cssEscape(id) + "']");
      if (found) found.open = true;
    });
  }

  function cssEscape(value) {
    return window.CSS && window.CSS.escape ? window.CSS.escape(value) : value;
  }

  function container(target) {
    if (!target || !target.closest) return null;
    return target.closest("[data-steps]");
  }

  /* Cancellable: the SSE extension reads what listeners return to decide
     whether to swap at all. So nothing here may cancel the event -- doing so
     would freeze the transcript with no error anywhere. There is a test on it,
     which matches the source, comments included. */
  document.body.addEventListener("htmx:sseBeforeMessage", function (event) {
    var box = container(event.target);
    if (box) remember(box);
  });

  document.body.addEventListener("htmx:sseMessage", function (event) {
    var box = container(event.target);
    if (box) restore(box);
  });

  /* The `done` frame swaps the whole <article> with outerHTML, so the element
     that comes back is a different one carrying the same id -- the finished
     bubble's container repeats both the id and the marker for exactly this
     reason. Before the swap the old box is still reachable under the target;
     after it, the safe move is to look again across the page, because what
     `afterSwap` hands back for an outerHTML swap is not reliably the new node.
     Restoring every container is harmless: what is remembered is keyed on the
     container's own id. */
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    var box = event.target && event.target.querySelector
      ? event.target.querySelector("[data-steps]")
      : null;
    if (box) remember(box);
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    document.querySelectorAll("[data-steps]").forEach(restore);
  });

  /* The reader opening or closing a block is written down the moment it
     happens. Before, the list was only taken just before a stream
     frame -- so once a reply had finished, the next unrelated swap anywhere on
     the page (the unread poll, every few seconds) put back whatever was open at
     the last frame, and a block somebody had since closed opened itself again:
     "I open a thinking block and cannot close it". `toggle` does not bubble:
     capture phase. Restoring sets `open`, which toggles too, and records the
     same thing again -- harmless. */
  document.addEventListener(
    "toggle",
    function (event) {
      var el = event.target;
      if (!el || el.tagName !== "DETAILS" || !el.id) return;
      var box = container(el);
      if (box) remember(box);
    },
    true
  );
})();
