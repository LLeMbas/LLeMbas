# HTML pages in answers

Spec 3.8.0. **Experimental, and off unless switched on.** A reply stays Markdown; where seeing
answers better than reading, the model may add **a page** to it: a complete, self-contained HTML
document in a fenced block. A client that can, shows the page in a sealed frame at that place in
the reply; every other reader sees a one-line note. The prompt text is `prompts/blocks/html-pages.md`;
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
in short: the answer stays Markdown and must make sense without the page; a page is a complete
document in an `html-view` fence with a short title, one page per fence; everything is inline —
no external script, style, font or image, the frame has no network; charts are drawn with inline
SVG or a canvas script; colours come from the frame's variables so the page follows light and
dark; no forms that submit, no links that navigate.

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

## 6. The frame

What a rendering client must hold to (the server's and Desktop's own tests prove it; it is here
so that a third client does not weaken it): the page runs in a frame of its own origin with
scripts allowed and nothing else — no cookie, no storage of the app's, **no network** (no fetch,
no image, font, script or style from anywhere), no navigation of itself or its parent, no forms
that submit, no pop-ups. The client gives it the five variables and takes its height. A page
never reaches the app that shows it.
