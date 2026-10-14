# Changelog

What changed in each release of the LLeMbas server. The release notes are also
at <https://llembas.eu/changelog.html>.

## Unreleased

### Changed

- A new agent chat now starts in the directory you last worked in on the chosen
  device, rather than in the device's first allowed directory. The directory
  button opens a short list of the directories you have already used there,
  newest first, with "Browse…" at the bottom for the device's directory
  browser. On a device you have not used yet, no directory is chosen: the
  button goes straight to the browser, and the message is not sent until you
  pick one. Starting a chat inside a folder that names a directory still uses
  the folder's.

## 1.0.1 — 2026-10-14

A clean-up release: code nothing reached any more is gone. Nothing changes in
use, and no stored data is touched.

### Removed

- Code left over from when an agent chat could run over SSH, which nothing
  reached any more: the executor interface and its request, result and listing
  types, the reply limits in the agent policy, and the terminal's SSH reader,
  re-keying and per-connection closing. Agent chats and their terminals run on
  linked devices, as before.
- Agent settings nothing read: the loopback switch, the command timeouts and
  output caps, the step, time and token budgets, the default allow and deny
  lists, the project listing and instructions budgets, the unfinished-plan
  nudge, background jobs and the terminal integration switch. Values already
  stored are ignored.
- The project-file half of the `@` picker, which was always empty, and the
  unused "open a file" dialog behind the canvas.
- Unused helpers across the services and models, and comments naming modules
  that no longer exist. The hint under "Most shells per person" now says what
  the setting holds open: a shell on the linked device.

## 1.0.0 — 2026-10-09

The first public release. A self-hosted web UI for language models: chats in
folders and a searchable library, tools and web search, agent chats on devices
linked through the LLeMbas CLI, accounts with credits and usage, and an
OpenAI-compatible API — packaged to run with Docker or as a system service.
