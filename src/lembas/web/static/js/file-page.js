/*
  A file's page (files/view.html): Edit and Preview, and Run.

  * Preview shows the file as the viewer draws it -- the server renders it
    (Markdown through services/markdown.py, HTML only in the sealed frame), so
    nothing the editor holds is ever put into this page as markup. After a
    save the preview is fetched again; before one it says it shows the saved
    file.
  * Run (a `.py` file, when Python is switched on here) runs the editor's text
    in Pyodide, in a Web Worker in this browser -- the `python` tool's own
    worker (python-worker.js), which sees only what it is handed: here, the
    code and nothing else. What it printed, its last value or its error are
    shown under the file as text. Stop ends the worker. `data-run-now` (the
    explorer's Run button) runs it once on arrival.
*/
(function () {
  "use strict";
  var t = window.lembasT || function (text) { return text; };

  var page = document.querySelector("[data-file-page]");
  if (!page) return;

  /* --- The editor's text, whichever editor is there ----------------------- */
  function source() {
    var form = page.querySelector("form[data-editor]");
    if (form && typeof form.lembasText === "function") return form.lembasText();
    var area = form && form.querySelector("textarea[name='content']");
    if (area) return area.value;
    var code = page.querySelector(".viewer__code code");
    return code ? code.textContent : "";
  }

  /* --- Edit / Preview -------------------------------------------------------- */
  var preview = page.querySelector("[data-file-preview]");
  var editor = page.querySelector("form[data-editor]");
  var stale = false;

  document.addEventListener("lembas:file-saved", function () { stale = true; });

  function refreshPreview() {
    if (!preview || !stale) return Promise.resolve();
    stale = false;
    var url = "/files/view?path=" + encodeURIComponent(page.dataset.path) + "&fragment=1";
    return fetch(url, { headers: { "HX-Request": "true" }, credentials: "same-origin" })
      .then(function (response) { return response.ok ? response.text() : ""; })
      .then(function (html) {
        if (!html) return;
        var doc = new DOMParser().parseFromString(html, "text/html");
        var body = doc.querySelector(".viewer__body");
        var here = preview.querySelector(".viewer__body");
        if (body && here) here.replaceWith(document.importNode(body, true));
      })
      .catch(function () { /* the last preview stays */ });
  }

  page.querySelectorAll("input[data-file-mode]").forEach(function (input) {
    input.addEventListener("change", function () {
      if (!input.checked || !preview || !editor) return;
      var showing = input.value === "preview";
      if (showing) {
        refreshPreview().then(function () {
          editor.hidden = true;
          preview.hidden = false;
        });
      } else {
        preview.hidden = true;
        editor.hidden = false;
      }
    });
  });

  /* --- Run ------------------------------------------------------------------- */
  var workerSrc = page.dataset.runWorker;
  var indexUrl = page.dataset.runIndex;
  var panel = page.querySelector("[data-run-panel]");
  var button = page.querySelector("[data-file-run]");
  if (!workerSrc || !indexUrl || !panel || !button) return;

  var out = panel.querySelector("[data-run-out]");
  var state = panel.querySelector("[data-run-state]");
  var stop = panel.querySelector("[data-run-stop]");
  var close = panel.querySelector("[data-run-close]");
  var worker = null;
  var running = null;

  function fresh() {
    if (worker) worker.terminate();
    worker = new Worker(workerSrc);
    worker.postMessage({ type: "init", indexURL: indexUrl });
    return worker;
  }

  function finish(text, error, said) {
    running = null;
    button.disabled = false;
    stop.hidden = true;
    out.textContent = text;
    if (error) {
      var line = document.createElement("span");
      line.className = "fx-run__error";
      line.textContent = (text ? "\n" : "") + error;
      out.appendChild(line);
    }
    state.textContent = said;
  }

  function run() {
    if (running) return;
    panel.hidden = false;
    out.textContent = "";
    state.textContent = worker ? t("Running…") : t("Starting Python… (the first run downloads it)");
    button.disabled = true;
    stop.hidden = false;
    var id = "run-" + Date.now();
    var started = Date.now();
    var w = worker || fresh();
    running = id;
    w.onmessage = function (event) {
      var data = event.data || {};
      if (data.type !== "result" || data.call_id !== id || running !== id) return;
      var text = String(data.stdout || "");
      if (data.result != null && data.result !== "") text += (text ? "\n" : "") + String(data.result);
      var seconds = ((Date.now() - started) / 1000).toFixed(1);
      finish(text, data.error || "", data.error ? t("Failed after %(s)s s", { s: seconds })
                                               : t("Finished in %(s)s s", { s: seconds }));
    };
    w.onerror = function (event) {
      if (running !== id) return;
      finish("", String(event.message || t("Python could not start.")), t("Failed"));
      worker = null;
    };
    w.postMessage({ type: "run", request: { call_id: id, code: source(), files: [], save: [] } });
  }

  button.addEventListener("click", run);
  stop.addEventListener("click", function () {
    if (!running) return;
    if (worker) worker.terminate();
    worker = null;
    finish(out.textContent, "", t("Stopped"));
  });
  close.addEventListener("click", function () { panel.hidden = true; });

  if (page.dataset.runNow) {
    /* Once: a reload of this address should not run it again. */
    try {
      var url = new URL(location.href);
      url.searchParams.delete("run");
      history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    } catch (error) { /* the address keeps it; harmless */ }
    run();
  }
})();
