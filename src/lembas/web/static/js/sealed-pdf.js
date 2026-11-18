/*
  The PDF viewer, inside the sealed frame (api/tree.py `sealed_viewer`).

  This script is written into a document that is an opaque origin with no
  network: it cannot reach the application, its cookie or anything else. The
  page around the frame hands it three things in one message -- the library
  (pdf.js, as text), the file's bytes and a few words -- and it draws pages on
  canvases. A PDF is somebody else's file and pdf.js is a large parser, so
  both run here and never on the application's own origin. pdf.js is told not
  to build functions from the file (`isEvalSupported: false`) and has no
  scripting, forms or network of its own here.

  No text of the file is ever put into markup: only canvases and textContent.
*/
(function () {
  "use strict";

  var root = document.getElementById("view");
  var FIRST = 20;      // pages drawn at once, then asked for
  var MAX_SIDE = 4096; // a canvas no browser refuses
  var done = false;

  function note(text) {
    var p = document.createElement("p");
    p.className = "note";
    p.textContent = text;
    root.replaceChildren(p);
  }

  function theme(colours) {
    Object.keys(colours || {}).forEach(function (name) {
      if (/^[a-z]+$/.test(name) && /^[#(),.%\s\w-]{1,40}$/.test(colours[name])) {
        document.documentElement.style.setProperty("--" + name, colours[name]);
      }
    });
  }

  function module(text) {
    return URL.createObjectURL(new Blob([text], { type: "text/javascript" }));
  }

  async function draw(doc, number, holder) {
    var page = await doc.getPage(number);
    var base = page.getViewport({ scale: 1 });
    var width = Math.max(200, root.clientWidth);
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    var scale = Math.min((width * ratio) / base.width, MAX_SIDE / Math.max(base.width, base.height));
    var viewport = page.getViewport({ scale: scale });
    var canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    canvas.className = "page";
    canvas.setAttribute("data-page", String(number));
    holder.appendChild(canvas);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: viewport }).promise;
    page.cleanup();
  }

  async function show(message) {
    theme(message.theme);
    var words = message.words || {};
    var pdfjs = await import(module(message.libs.main));
    pdfjs.GlobalWorkerOptions.workerSrc = module(message.libs.worker);
    var doc = await pdfjs.getDocument({
      data: new Uint8Array(message.bytes),
      isEvalSupported: false,
      enableXfa: false,
      disableRange: true,
      disableStream: true,
      disableAutoFetch: true,
      useSystemFonts: true,
    }).promise;

    // A frame not on show yet has no width to draw a page for.
    while (root.clientWidth < 50) await new Promise(function (go) { setTimeout(go, 200); });

    var holder = document.createElement("div");
    holder.className = "pages";
    root.replaceChildren(holder);
    document.documentElement.setAttribute("data-pages", String(doc.numPages));
    var next = 1;

    async function more() {
      var until = Math.min(doc.numPages, next + FIRST - 1);
      for (; next <= until; next++) await draw(doc, next, holder);
      var old = root.querySelector("button");
      if (old) old.remove();
      if (next <= doc.numPages) {
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = (words.more || "More pages") + " (" + (next - 1) + " / " + doc.numPages + ")";
        button.addEventListener("click", function () {
          button.disabled = true;
          more().catch(function () { note(words.failed || ""); });
        });
        root.appendChild(button);
      }
      document.documentElement.setAttribute("data-drawn", String(next - 1));
    }
    await more();
  }

  window.addEventListener("message", function (event) {
    var message = event.data;
    if (done || event.source !== window.parent || !message || message.lembasView !== "pdf") return;
    done = true;
    show(message).catch(function () {
      note((message.words && message.words.failed) || "");
      document.documentElement.setAttribute("data-failed", "1");
    });
  });
  /* Said until answered: the page around may not be listening yet. */
  var asked = 0;
  (function ready() {
    if (done || asked++ > 40) return;
    window.parent.postMessage({ lembasViewerReady: "pdf" }, "*");
    setTimeout(ready, 500);
  })();
})();
