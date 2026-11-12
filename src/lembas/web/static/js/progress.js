/*
  Progress (harness spec 3.4.0, progress.md): the line a reply's phase is drawn
  with, "{{text}} · {{elapsed}}", the same words every client uses.

  The server sends the object (`data-progress` on a `.progress-line`, in the
  stream's `progress` frame, and on a tree row of the activity panel) with
  the line already rendered; this keeps the elapsed clock moving once a second
  and counts `retrying` down. The words come from the `lembas-progress` island
  (base.html): the spec's tables in the reader's language.
*/
(function () {
  "use strict";

  var tables = null;
  function shape() {
    if (tables) return tables;
    try {
      tables = JSON.parse(document.getElementById("lembas-progress").textContent) || null;
    } catch (e) { tables = null; }
    return tables;
  }

  function fill(text, values) {
    return String(text).replace(/\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g, function (all, name) {
      return Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : all;
    });
  }

  function elapsed(ms) {
    var total = Math.max(0, Math.floor(ms / 1000));
    if (total < 60) return total + "s";
    var minutes = Math.floor(total / 60), rest = total % 60;
    if (minutes < 60) return minutes + "m " + (rest < 10 ? "0" : "") + rest + "s";
    var hours = Math.floor(minutes / 60), mins = minutes % 60;
    return hours + "h " + (mins < 10 ? "0" : "") + mins + "m";
  }

  function text(p, now, s) {
    var T = s.texts;
    switch (p.phase) {
      case "queued":
        return p.ahead != null ? fill(T["queued.ahead"], { ahead: p.ahead }) : T.queued;
      case "waiting_for_model":
        return fill(T.waiting_for_model, { model: p.model || "", reason: s.reason[p.reason] || p.reason || "" });
      case "loading_model":
        return fill(T.loading_model, { model: p.model || "" });
      case "reading_prompt":
        if (p.done == null || !p.total) return T["reading_prompt.bare"];
        return fill(T.reading_prompt, { done: p.done, total: p.total, percent: Math.floor(p.done * 100 / p.total) });
      case "writing":
        if (p.rate == null || !isFinite(p.rate)) return T["writing.bare"];
        return fill(T.writing, { rate: (Math.floor(p.rate * 10 + 0.5) / 10).toFixed(1) });
      case "running_tool":
        return p.what ? fill(T.running_tool, { tool: p.tool || "", what: p.what })
                      : fill(T["running_tool.bare"], { tool: p.tool || "" });
      case "waiting_for_person": {
        var W = s.what, what;
        if (p.what === "approval") what = p.tool ? fill(W.approval, { tool: p.tool }) : W["approval.bare"];
        else what = W[p.what] || p.what || "";
        return fill(T.waiting_for_person, { what: what });
      }
      case "waiting_for_subagents":
        return fill(T.waiting_for_subagents, { running: p.running || 0 });
      case "retrying":
        return fill(T.retrying, { in: Math.max(0, (p.in || 0) - Math.floor((now - p.since) / 1000)), reason: p.reason || "" });
      case "stopped":
        return p.reason ? fill(T["stopped.reason"], { reason: s.stopped_reason[p.reason] || p.reason }) : T.stopped;
      case "failed":
        return fill(T.failed, { reason: p.reason || "" });
      default:
        return T[p.phase] || p.phase;
    }
  }

  function line(p, now, s) {
    var said = text(p, now, s);
    if (s.no_elapsed.indexOf(p.phase) >= 0) return said;
    return fill(s.line, { text: said, elapsed: elapsed(now - p.since) });
  }

  function tick() {
    var s = shape();
    if (!s) return;
    var now = Date.now();
    document.querySelectorAll(".progress-line[data-progress]").forEach(function (el) {
      var p;
      try { p = JSON.parse(el.getAttribute("data-progress")); } catch (e) { return; }
      if (!p || !p.phase) return;
      var drawn = el.hasAttribute("data-progress-short") ? text(p, now, s) : line(p, now, s);
      if (el.textContent !== drawn) el.textContent = drawn;
    });
  }

  window.lembasProgress = { line: function (p) { var s = shape(); return s ? line(p, Date.now(), s) : ""; } };
  setInterval(tick, 1000);
  /* A line that has just arrived is drawn at once rather than at the next
     second. Only for a swap that brought one: a reply being written swaps ten
     times a second, and all but its `progress` frame land somewhere else. */
  document.addEventListener("htmx:afterSwap", function (event) {
    var el = event.target;
    if (!el || !el.querySelector) return;
    if (el.querySelector(".progress-line[data-progress]") ||
        (el.matches && el.matches(".progress-line[data-progress]"))) tick();
  });
})();
