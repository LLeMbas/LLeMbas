/*
  The file editor: CodeMirror 6 over the page's textarea (files/edit.html).

  The modules come from the vendored, hash-pinned copies the page's import
  map names; nothing is fetched from anywhere else. If they cannot load, the
  textarea is still there and the form still saves: CodeMirror is an
  improvement on the editor, not the editor.

  Saving (the button, or Ctrl/Cmd+S) posts the form without leaving the page
  and keeps the hash the server answers with, so the next save is checked
  against the file as it now is. A file that changed meanwhile (409) is not
  overwritten: the editor says so and offers to save anyway.
*/
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";

/* The words, in the reader's language: see `lembasT` in base.html. */
var t = window.lembasT || function (text) { return text; };

async function language(name) {
  try {
    switch (name) {
      case "markdown": return (await import("@codemirror/lang-markdown")).markdown();
      case "python": return (await import("@codemirror/lang-python")).python();
      case "javascript": case "jsx": return (await import("@codemirror/lang-javascript")).javascript({ jsx: name === "jsx" });
      case "typescript": case "tsx": return (await import("@codemirror/lang-javascript")).javascript({ typescript: true, jsx: name === "tsx" });
      case "html": return (await import("@codemirror/lang-html")).html();
      case "css": case "scss": return (await import("@codemirror/lang-css")).css();
      case "json": return (await import("@codemirror/lang-json")).json();
      default: return [];
    }
  } catch (error) {
    return [];
  }
}

/* Colours from the theme (tokens.css), never from CodeMirror's own. */
var look = EditorView.theme({
  "&": { color: "var(--ink)", backgroundColor: "var(--code-bg)", fontSize: "var(--text-sm)" },
  ".cm-content": { fontFamily: "var(--font-mono)", caretColor: "var(--accent)" },
  ".cm-gutters": { backgroundColor: "var(--code-bg)", color: "var(--ink-faint)", border: "none" },
  ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "var(--surface-hover)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--selection)" },
  "&.cm-focused": { outline: "none" },
  ".cm-cursor": { borderLeftColor: "var(--accent)" },
});
var colours = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword, tags.modifier], color: "var(--accent)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--leaf)" },
  { tag: [tags.comment], color: "var(--ink-faint)", fontStyle: "italic" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--warning)" },
  { tag: [tags.function(tags.variableName), tags.definition(tags.variableName)], color: "var(--ink)", fontWeight: "600" },
  { tag: [tags.heading], color: "var(--accent)", fontWeight: "700" },
  { tag: [tags.link, tags.url], color: "var(--accent)", textDecoration: "underline" },
  { tag: [tags.tagName, tags.typeName, tags.className], color: "var(--accent-hover)" },
  { tag: [tags.invalid], color: "var(--danger)" },
]);

async function start(form) {
  var area = form.querySelector("textarea[name='content']");
  var state = form.querySelector("[data-editor-state]");
  var base = form.querySelector("input[name='base']");
  if (!area) return;

  var say = function (text) { if (state) state.textContent = text; };
  var dirty = false;

  async function save(force) {
    area.value = view.state.doc.toString();
    var body = new FormData(form);
    if (force) body.set("force", "1");
    say(t("Saving…"));
    var response;
    try {
      response = await fetch(form.action, { method: "POST", body: body, headers: { Accept: "application/json" } });
    } catch (error) {
      say(t("Not saved: the server could not be reached."));
      return;
    }
    var answer = {};
    try { answer = await response.json(); } catch (error) { answer = {}; }
    if (response.ok) {
      if (base && answer.sha) base.value = answer.sha;
      dirty = false;
      say(t("Saved."));
      return;
    }
    if (response.status === 409 && window.lembas && window.lembas.confirm) {
      say(answer.detail || t("That did not work."));
      var again = await window.lembas.confirm({
        title: t("Changed meanwhile"),
        message: answer.detail || "",
        confirmLabel: t("Save anyway"),
      });
      if (again) save(true);
      return;
    }
    say(answer.detail || t("That did not work."));
  }

  var view = new EditorView({
    parent: area.parentElement,
    state: EditorState.create({
      doc: area.value,
      extensions: [
        basicSetup,
        look,
        syntaxHighlighting(colours),
        EditorView.lineWrapping,
        keymap.of([{ key: "Mod-s", preventDefault: true, run: function () { save(false); return true; } }]),
        EditorView.updateListener.of(function (update) {
          if (update.docChanged && !dirty) { dirty = true; say(t("Not saved yet")); }
        }),
        await language(area.dataset.language || ""),
      ],
    }),
  });
  area.hidden = true;
  area.parentElement.insertBefore(view.dom, area);
  view.dom.classList.add("editor__cm");
  view.dom.setAttribute("data-testid", "editor-cm");

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    save(false);
  });
  window.addEventListener("beforeunload", function (event) {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
}

document.querySelectorAll("form[data-editor]").forEach(function (form) { start(form); });
