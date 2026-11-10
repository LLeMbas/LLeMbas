# Phone actions

Spec 3.6.0. `phone_action` (`tools/phone_action.json`) lets a chat's model do something on the
person's phone — set an alarm, start a route, draft a message — when the person is writing from the
LLeMbas app on that phone. The model proposes; **the app shows the person a card and does nothing
until they confirm it there**; the app carries it out with the phone's own apps and answers what
happened, which is the call's result. The texts and numbers are `loop.json` `phone`.

It is the server's tool alone (`scope: llembas`, `sessions: chat`). The CLI never offers it: a
terminal has no phone behind it.

## 1. Who offers it

- **The capability.** The app declares `phone_actions` (`loop.json` `phone.capability`) with each
  message it sends. A message sent from anywhere else — the web, Desktop, another app — declares
  nothing.
- **`requires: phone`.** The tool is offered in a chat once a message has been sent to it with the
  capability, and stays offered for the rest of the chat (the tool list is the session's,
  `context.md` §10): a chat started on the phone and continued on the web keeps the tool, and a web
  chat continued on the phone gains it from that message on (the list changes once, so the prompt
  is made again once).
- **Unavailable.** A call answers the tool's `unavailable` text, word for word, when the message
  being answered was not sent with the capability (there is no phone to confirm on), and in a chat
  nobody attends (a schedule's, a subagent's). Nothing reaches any phone then.
- **Confirm: always.** Every call is put to the person, in every mode; nothing remembers an answer.
  The card is the phone's own: the server never asks a second time on another screen. Its risk is
  `interact`: no mode refuses it, Plan mode included, because nothing on disk changes and the
  person decides on the phone.

## 2. A call

`action` is one of `phone.actions`; `summary` is the line the phone shows. Each action reads only
its `phone.fields`; anything else in the call is dropped before the phone sees it. The call is
checked before anything is sent, in this order, and the first problem is the call's result:

1. `action` not one of the actions: `texts.unknown_action` (`{{actions}}` the list, joined with
   `, `).
2. The action's `phone.required`, each entry in turn: a field is present when it is not null, not
   an empty string (after trimming) and not an empty list or object; a list inside the list is
   present when any one of its fields is. Every entry that is missing, in order, goes in one
   `texts.missing`: a field by its name, a group as its names joined with ` or `, the entries
   joined with ` and ` (`add_stop needs stop and destination.`).
3. The fields present, in `phone.check` order (only those the action reads): one that is not what
   it should be is `texts.bad` with `{{field}}` its name and `{{what}}` from `phone.what`:
   - `time`: `HH:MM`, hours 00–23, minutes 00–59.
   - `days`: a list, each one of `mon` … `sun`.
   - `seconds`: an integer (never a boolean) from 1 to 86400.
   - `start`, `end`: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` (not checked as a calendar date).
   - `to`, `cc`: a list of strings, each non-empty after trimming.
   - `number`: at least three digits, and nothing but digits, spaces and `+ ( ) - . / # *`.
   - `destination`, `stop`: an object with numeric `lat` and `lon` together (each in its range),
     or a non-empty `query`, or a non-empty `name`. `stops`: a list of such places.
   - `mode`, `near`, `command`, `panel`: one of the schema's values.
   - `url`: begins `http://` or `https://` (any case) and has something after it.
   - `on`: a boolean.
   - `values`: an object of strings.

A call that passes is the **action** the phone receives: `action`, `summary` (trimmed; the action's
name when empty) and the action's fields that are present, as the model gave them, strings trimmed.

## 3. The answer

The server waits up to `phone.wait_seconds` for the phone's answer: `status` (`done`, `declined`
or `failed`) and an optional `message`, at most `phone.message_max_chars` characters (cut there).
The call's result is the status's text — `texts.done`, `texts.declined`, `texts.failed` — and,
when there is a message, a blank line and `texts.says` with it. A status the phone does not know is
`failed`. No answer in time is `texts.no_answer`, with no message; the card goes away.

What the person pressed is theirs: **declined** means they said no, and the model is told not to
try again unless asked. **Failed** is the phone's: an app missing, a permission refused, a map app
that cannot do what was asked.

## 4. Navigation

Map apps differ, and the phone says when one cannot: the model is not told which app the person
uses.

- `navigate` starts a route to `destination` through `stops`, in order; `mode` is how the person
  travels (`drive` when not said). `replace_route` replaces a route already running without the
  phone asking first.
- `add_stop`: **no map app adds a stop to a running route**, so the phone plans again — from where
  the person is, to `stop`, then to `destination`. The model gives `destination` as the person told
  it when the route began: the tool's description tells it to keep it.
- `search_nearby` looks for `query` around the person (`near: me`) or along the running route
  (`near: route`).
- `stop_navigation` ends the route where the map app allows it; one that does not answers
  `failed` with why.

## 5. The other actions

- `alarm` and `timer` are the phone's clock app; `days` repeats an alarm.
- `calendar_event` opens the calendar's new-event screen filled in; the person saves it.
- `sms_draft` and `email_draft` are **drafts**: the messaging or mail app opens with them, and the
  person sends. `dial` opens the dialer with the number; the person places the call.
- `open_app` names an app as the person does; `open_url` opens an address in the browser.
- `media` controls what is playing; `torch` switches the torch.
- `settings_panel` opens the phone's quick panel or page for a setting; the person changes it.
- `automation_intent` runs an automation the person made in an automation app (Tasker when `app`
  is not said), by its name, with `values` as its inputs.
