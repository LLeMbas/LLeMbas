/*
  The tick tree (access/_rows.html, api/access_ui.py).

  Ticking a row gives its path a level of its own and enables the level;
  unticking it takes the rule away. Every row's note is worked out again from
  the ticks on the page, with the resolver's own rule (security/access.py): a
  path has the level of its nearest ticked ancestor-or-self; an unticked path
  with a tick below it and none above is a passage -- not listed, not read,
  and what is ticked inside is reached by its full path only.

  A folder's toggle shows or hides the rows below it; a branch the server did
  not draw is fetched the first time (hx-get on the toggle).
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var LABELS = {
    none: function () { return t("No access"); },
    ro: function () { return t("Read only"); },
    stage: function () { return t("Read and write"); },
    commit: function () { return t("Read, write and commit"); },
  };

  function within(path, top) {
    if (top === "/") return true;
    return path === top || path.indexOf(top + "/") === 0;
  }

  function rows(form) {
    return Array.prototype.slice.call(form.querySelectorAll("[data-tick-row]"));
  }

  function rules(form) {
    var out = {};
    rows(form).forEach(function (row) {
      var tick = row.querySelector("[data-tick]");
      var level = row.querySelector("[data-tick-level]");
      if (tick && tick.checked && level) out[row.dataset.path] = level.value;
    });
    return out;
  }

  function nearest(map, path) {
    var best = null;
    Object.keys(map).forEach(function (p) {
      if (within(path, p) && (best === null || p.length > best.length)) best = p;
    });
    return best;
  }

  function refresh(form) {
    var map = rules(form);
    rows(form).forEach(function (row) {
      var path = row.dataset.path;
      var tick = row.querySelector("[data-tick]");
      var level = row.querySelector("[data-tick-level]");
      var note = row.querySelector("[data-tick-note]");
      var ticked = !!(tick && tick.checked);
      if (level) level.disabled = !ticked;
      row.classList.toggle("is-ticked", ticked);
      var from = ticked ? path : nearest(map, path);
      var passage = !ticked && from === null && Object.keys(map).some(function (p) {
        return p !== path && within(p, path);
      });
      row.classList.toggle("is-passage", passage);
      if (!note) return;
      if (ticked) note.textContent = "";
      else if (passage) note.textContent = t("Passage: not listed or readable; what is ticked inside is reached by its full path.");
      else if (from !== null) note.textContent = t("As above: %(level)s", { level: (LABELS[map[from]] || LABELS.none)() });
      else note.textContent = t("No access");
    });
  }

  document.addEventListener("change", function (event) {
    var form = event.target.closest && event.target.closest("form[data-ticks]");
    if (form) refresh(form);
  });

  /* Showing and hiding a folder's rows: everything after it that is deeper. */
  document.addEventListener("click", function (event) {
    var toggle = event.target.closest && event.target.closest("[data-tick-toggle]");
    if (!toggle) return;
    var row = toggle.closest("[data-tick-row]");
    var open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (toggle.hasAttribute("hx-get") && open && !toggle.dataset.loaded) {
      toggle.dataset.loaded = "1";
      return; /* htmx fetches the branch */
    }
    var depth = parseInt(row.dataset.depth, 10) || 0;
    for (var next = row.nextElementSibling; next && next.hasAttribute("data-tick-row"); next = next.nextElementSibling) {
      if ((parseInt(next.dataset.depth, 10) || 0) <= depth) break;
      next.hidden = !open;
    }
  });

  document.addEventListener("htmx:afterSettle", function (event) {
    var form = event.target.closest && event.target.closest("form[data-ticks]");
    if (form) refresh(form);
  });
})();
