# HTML pages in answers

Spec 3.8.0. **Experimental, and off unless switched on.** A reply stays Markdown; where seeing
answers better than reading, the model may add **a page** to it: a complete, self-contained HTML
document in a fenced block — **static: HTML, CSS and inline SVG, no script** (§6). A client that
can, shows the page in a sealed frame at that place in the reply; every other reader sees a
one-line note. The prompt text is `prompts/blocks/html-pages.md`;
the names and texts `loop.json` `pages`; the model flag `models/schema.json` `html_pages`; the
cases `conformance/pages.json`. (It takes the place of the 1.x *prototypes*, which 2.0 removed:
one kind of block instead of two, no panel, no network.)

## 1. The fence

A page is a fenced code block whose info string begins with the word **`html-view`**
(`pages.fence`), followed by a short title:

````
```html-view Monthly sales
<!doctype html>
<html>…</html>
```
````

- **Opening line**: up to three spaces, then three or more backticks — or three or more tildes —
  then `html-view`, then optionally white space and the title, to the end of the line. The word
  is exact (`html`, `html-views`, `HTML-VIEW` are ordinary code blocks).
- **Closing line**: up to three spaces, the same character at least as many times as the
  opening, and nothing else but white space.
- **One page per fence**: everything between the two lines is the page, as written.
- **An unclosed fence is not a page.** While a reply streams, a client shows a placeholder for a
  page still being written; a reply that ends inside the fence has no page there — it is the
  code block it is.
- **Backticks inside a page** (a template string, a code sample) do not end it unless a line is
  a closing fence: a page that has a line of three backticks is written in a fence of four, as
  any Markdown block is.
- **The title**: the rest of the opening line, white space collapsed, cut to `pages.title_max`
  characters; when the line gives none, the page's own `<title>`; when it has none either,
  `pages.untitled`.

## 2. What the model is told

`prompts/blocks/html-pages.md`, as bullets of the system prompt when pages are offered (§4) —
in short: the answer stays Markdown and must make sense without the page; **what the person
asks to see in the answer is a page, not an HTML file opened with `show`** (3.8.1: without
this a model wrote a file and showed it, and the person got a card to open instead of the thing
they asked for); **what has to run is not a page** — the model says a page cannot run anything
and, where it has `write` and `show`, writes an HTML file and shows that, which a client runs
in its sealed viewer; a page is a complete
document in an `html-view` fence with a short title, one page per fence; it is **static** — HTML,
CSS and inline SVG, nothing that runs and nothing loaded from anywhere; a chart is inline SVG the
model writes out, shapes and numbers it worked out itself; tables and layout are CSS; colours
come from the frame's variables so the page follows light and dark.

**The frame's variables**, set on the page's root by the client that shows it, always used with
a fallback (`pages.variables`):

| Variable | Is |
|---|---|
| `--page-bg` | the background the page sits on |
| `--page-ink` | text |
| `--page-muted` | secondary text, axis labels |
| `--page-accent` | the one accent colour: a series, a highlight |
| `--page-line` | rules, borders, grid lines |

## 3. A reader that cannot show it

- **A client that does not render pages** (a terminal, a phone, a notification) shows in the
  page's place the note `pages.note` — *This answer has a page: {{title}}* — and may add where it
  can be seen (`pages.where`). It never shows the page's source as if it were the answer.
- **Anything that reads a reply as text** — search, a title, read-aloud, compaction, a summary,
  a preview, the plain-text form of a report — sees each page as the one line
  **`[page: {{title}}]`** (`pages.projection`) in its place, on a line of its own. The **text
  projection** of a reply is the reply with every page replaced so; everything else is unchanged.
- The model's own earlier replies go back to it as it wrote them (the prefix stays still,
  `context.md` §10); the projection is for readers, not for the conversation.

## 4. When it is offered

All of these, or the block is not in the prompt and a page fence is an ordinary code block:

1. **the instance's switch** is on (off by default: the feature is experimental);
2. **the model's flag** `html_pages` is true (`models/schema.json`; false by default — a model
   that cannot write a working page is simply not asked to);
3. **the chat's switch** is on. A scheduled run has no chat to switch: for a run, the instance
   and the model decide — so a report may carry a chart.

And never, whatever the switches say:

- to **a subagent** (its reply is read by an agent, not shown);
- in **the phone assistant** (spoken, and shown on a small screen);
- in **an agent chat** — a session on a device, served by the CLI, the terminal's included: the
  CLI is never offered it and never offers it. (A terminal still *reads* replies that have
  pages — a report, a chat seen over the link — and shows the note of §3.)

## 5. Switched on or off during a chat

The system prompt of a chat is not rebuilt (`context.md` §10). When the chat's switch changes
after the prompt was made, the model is told by a line of the session note on the next user
message: `context.note.pages_on` — with the block's text as `{{pages_text}}` — or
`context.note.pages_off`. The instance's switch and the model's flag are settled when the prompt
is made; a change of those reaches a chat the same way, as the chat's switch going off or
becoming available.

## 6. A page is static

**Everywhere, in every client: HTML, CSS and inline SVG — nothing that runs, nothing that
reaches anywhere.** A frame with scripts on cannot be sealed by policy alone: a script can still
send data out (a WebRTC datagram to a server it chooses), and even without script a document can
make the browser look a name up (`<link rel="dns-prefetch">`, `preconnect`, `prefetch`). So the
rule is about the document, not only the frame around it (`loop.json` `pages.static`;
`conformance/pages.json`, `show: static`):

- **No script of any kind**: no `<script>`, no event-handler attribute (`on…`), no
  `javascript:` address.
- **Only the listed elements** (`pages.static.elements`: text, lists, tables, figures, `style`,
  and the SVG shapes) — so no `<link>`, `<meta>`, `<base>`, no frames (`iframe`, `frame`,
  `embed`, `object`), no media (`audio`, `video`, `source`, `track`, `canvas`), no forms
  (`form`, `input`, `button`, `select`, `textarea`), no `<template>`, `<slot>`, `<foreignObject>`.
- **Only the listed attributes** (`pages.static.attributes`): presentation and structure.
- **No address but two**: `#fragment` (a link or a reference within the page) and an inline
  `data:image/png|jpeg|gif|webp;base64,…` image. Nothing else in `href`, `src`, `xlink:href` or
  any other attribute — no `http:`, `https:`, `//host`, relative paths, `data:` of another kind.
- **CSS that fetches nothing**: no `@import`, and no `url(…)` other than `url(#fragment)` or
  such an inline image — in a `<style>` element and in a `style` attribute alike.

A chart is therefore **inline SVG the model writes out**: bars, lines and pie slices as SVG
shapes, with the numbers it computed; a table is a table, styled with CSS.

**What a client that shows pages must do**, all of it:

1. **Rebuild the document from the allow-list** before showing it: parse what the model wrote,
   keep the listed elements and attributes and the two kinds of address, drop everything else
   (the element with its content for `script`, `style` that fetches, and the forbidden
   elements; the attribute alone for a forbidden attribute). What is shown is the rebuilt
   document, never the model's text as it came.
2. Show it in a **frame of its own origin with scripts off** — nothing allowed to the frame:
   no script, no forms, no pop-ups, no navigation of itself or its parent, no cookie or storage
   of the app's, no network.
3. Give it the five variables, a **fixed height with its own scrolling** (a static document
   cannot report its height), and an **Open larger** control that shows the same rebuilt
   document larger, in place, under the same rules.

**A client that cannot guarantee all of this shows the note of §3 instead** — a terminal, a
phone, and any client whose frame cannot be held to it. A page never reaches the app that shows
it, and never reaches anywhere else.
