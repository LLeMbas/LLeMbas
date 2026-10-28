/*
  The worker the `python` tool's code runs in (python.js). Pyodide comes from
  this server's vendored copy; the code gets the files it was handed in
  Pyodide's own in-memory file system, and nothing is read back but the paths
  it was asked to save.
*/
/* global importScripts, loadPyodide */
"use strict";
var ready = null;

function b64ToBytes(text) {
  var raw = atob(text || "");
  var out = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function bytesToB64(bytes) {
  var chunks = [];
  for (var i = 0; i < bytes.length; i += 0x8000) {
    chunks.push(String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)));
  }
  return btoa(chunks.join(""));
}

function ensureDir(py, path) {
  var parts = path.split("/").slice(1, -1);
  var at = "";
  parts.forEach(function (part) {
    at += "/" + part;
    try { py.FS.mkdir(at); } catch (error) { /* already there */ }
  });
}

self.onmessage = function (event) {
  var data = event.data || {};
  if (data.type === "init") {
    importScripts(data.indexURL + "pyodide.js");
    ready = loadPyodide({ indexURL: data.indexURL });
    return;
  }
  if (data.type !== "run" || !ready) return;
  var request = data.request || {};
  ready.then(function (py) {
    var out = [];
    py.setStdout({ batched: function (line) { out.push(line); } });
    py.setStderr({ batched: function (line) { out.push(line); } });
    (request.files || []).forEach(function (file) {
      ensureDir(py, file.path);
      py.FS.writeFile(file.path, b64ToBytes(file.content_b64));
    });
    var result = null;
    var error = "";
    return py.runPythonAsync(String(request.code || "")).then(function (value) {
      if (value !== undefined && value !== null) {
        result = String(value && value.toString ? value.toString() : value);
        if (value && value.destroy) value.destroy();
      }
    }).catch(function (problem) {
      error = String(problem && problem.message || problem);
    }).then(function () {
      var files = [];
      (request.save || []).forEach(function (path) {
        try {
          files.push({ path: path, content_b64: bytesToB64(py.FS.readFile(path)) });
        } catch (problem) { /* not written: nothing to save */ }
      });
      self.postMessage({ type: "result", call_id: request.call_id, stdout: out.join("\n"),
                         result: result, error: error, files: files });
    });
  }).catch(function (problem) {
    self.postMessage({ type: "result", call_id: request.call_id, stdout: "", result: null,
                       error: "Python could not start: " + String(problem && problem.message || problem),
                       files: [] });
  });
};
