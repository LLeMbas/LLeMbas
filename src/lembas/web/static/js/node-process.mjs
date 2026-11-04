/*
  The `process` that esm.sh's builds import as "/node/process.mjs" (its Node
  polyfill). @lezer/lr reads `process.env.LOG` to decide whether to log its
  parse; nothing else here asks. Without this the absolute path is a 404 on
  this server, @lezer/lr fails to load, and with it every editor language
  that parses: the editor then opens with no highlighting at all.
  Mapped by the import map in files/edit.html.
*/
export default { env: {} };
