/*
  The `python` tool's runner, on a chat page (api/pyodide.py, services/pages.py).

  Loaded only on a chat whose instance has Python switched on and Pyodide
  vendored. The page says it can run Python (announce, and again every 20
  seconds), takes the chat's calls by long poll, runs each in a Web Worker --
  Pyodide, CPython in WebAssembly, sandboxed by the browser -- and answers with
  what it printed, its last value, an error, and the files it was asked to
  save. A call that runs past its time is ended by ending the worker.

  Nothing here reaches anything but this chat's own routes; the code in the
  worker has the files it was handed and nothing else of this application.
*/
(function () {
  "use strict";
  var root = document.querySelector("[data-python-runner]");
  if (!root) return;
  var chat = root.dataset.chatId;
  var workerSrc = root.dataset.workerSrc;
  var indexUrl = root.dataset.pyodideIndex;
  if (!chat || !workerSrc || !indexUrl) return;

  var page = Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(12)), function (b) {
    return ("0" + b.toString(16)).slice(-2);
  }).join("");
  var base = "/chat/" + encodeURIComponent(chat) + "/page/";
  var worker = null;
  var stopped = false;

  function post(path, body) {
    return fetch(base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
  }

  function announce() {
    if (stopped) return;
    post("announce", { page_id: page }).catch(function () {});
  }

  function freshWorker() {
    if (worker) worker.terminate();
    worker = new Worker(workerSrc);
    worker.postMessage({ type: "init", indexURL: indexUrl });
    return worker;
  }

  function run(request) {
    var limits = request.limits || {};
    var seconds = Math.max(1, Number(limits.seconds) || 30);
    return new Promise(function (resolve) {
      var w = worker || freshWorker();
      var done = false;
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        w.terminate();
        worker = null;
        resolve({ stdout: "", result: null, error: "Stopped after " + seconds + " seconds.", files: [] });
      }, seconds * 1000 + 5000);
      w.onmessage = function (event) {
        var data = event.data || {};
        if (data.type !== "result" || data.call_id !== request.call_id || done) return;
        done = true;
        clearTimeout(timer);
        var out = String(data.stdout || "");
        var cap = Number(limits.output_chars) || 20000;
        resolve({ stdout: out.slice(0, cap), result: data.result, error: data.error || "", files: data.files || [] });
      };
      w.postMessage({ type: "run", request: request });
    });
  }

  function loop() {
    if (stopped) return;
    fetch(base + "take?page=" + page, { credentials: "same-origin" })
      .then(function (response) {
        if (response.status === 200) return response.json();
        if (response.status === 204) return null;
        throw new Error("take " + response.status);
      })
      .then(function (request) {
        if (!request) return null;
        return run(request).then(function (result) {
          return post("answer", { page_id: page, call_id: request.call_id, result: result });
        });
      })
      .then(function () { loop(); })
      .catch(function () { setTimeout(loop, 5000); });
  }

  announce();
  setInterval(announce, 20000);
  loop();

  window.addEventListener("pagehide", function () {
    stopped = true;
    try {
      navigator.sendBeacon(base + "withdraw", new Blob([JSON.stringify({ page_id: page })],
        { type: "application/json" }));
    } catch (error) { /* the server forgets a silent page after a minute anyway */ }
  });
})();
