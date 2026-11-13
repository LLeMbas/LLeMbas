# Permission rules: which one decides

Spec 3.8.0. The rules are data (`defaults.json`, the person's own config, a project's, and those
made while a session runs); the modes say what happens to a call no rule settles (`modes.json`);
the hardline is under all of it (`hardline.json`). This says **which rule decides a call when
several match** — in both implementations, for a rule a person typed into a file exactly as for
one a menu wrote. Cases: `conformance/rules.json`.

## 1. A rule, and where it was made

A rule is `{ permission, pattern, action }`: the permission key (a tool's access; `*` is every
key), a pattern over what the call asks for (a command line, a path, an address; `*` is
anything), and `allow`, `ask` or `deny`. Every rule has a **layer**, by where it was made:

| Layer | Is | Read |
|---|---|---|
| `default` | the rules every session starts from (`defaults.json`) | first |
| `user` | the person's own configuration — theirs alone, on their machine or their account | then |
| `project` | a project's configuration (a trusted project's `.agent/config.yaml`; the server: none) | then |
| `made` | made during the session: an **"always" answer** to an approval card, or a rule **set in a menu** | last |

Within a layer, rules are read in the order they are written.

## 2. The rule that decides

For a call's permission and what it asks for, among the rules that match both:

1. **The last one read is the candidate** — later layers after earlier ones, later lines after
   earlier ones.

A candidate from the `project` or `made` layer then gives way to whichever of these is stricter
than it (`deny` over `ask` over `allow`) — the strictest of them decides:

2. **A narrower rule.** A rule is *narrower* than another when everything its permission and
   pattern take in, the other's take in too, and they are not the same (`read *.env` under
   `read *`; `read *` under `* *`). Any matching rule of the `default`, `user` or `project` layer
   that is narrower than the candidate counts. So `read: allow` in a project leaves
   `read *.env: ask` asking, and `bash: allow` leaves `bash "git push *": ask`: a later, broader
   rule changes what a broader rule decided, never what somebody wrote for the narrower case. To
   allow the narrow thing, name it.
3. **The person's own answer.** What the `user` layer alone says of the call — its last matching
   rule, of any breadth. A project, an "always" answer and a menu cannot loosen what the person's
   own configuration says.
4. **A written deny, for an "always" answer.** When the candidate was made by an "always" answer:
   the last matching rule of the `default`, `user` or `project` layer, if it denies.

A candidate from the `default` or `user` layer decides as it is: the person's own file is read
last-match-wins, as it always was. Where no rule matches, the call is asked about or allowed by
its class and the mode.

Several patterns in one call (a command line of several commands, several paths) are each
decided this way, and the strictest answer is the call's.

## 3. Making a rule

When a person makes a rule — an "always" answer, or one set in a menu:

- **Choosing the action already in force changes nothing**: no rule is added and nothing is
  written. (The action in force is what §2 gives for the new rule's own permission and pattern.)
- **A rule that could never take effect is refused**, naming the rule in its way: a `user` rule,
  or a written `deny` of any layer, that is stricter and takes in *all* of the new rule (the new
  rule is the same as it, or narrower). It is changed where it was written.
- Otherwise the rule is **made**: in force at once, and — where the implementation saves it (a
  trusted project's configuration) — saved as a `project` rule. Because §2 treats `made` and
  `project` alike, the session it was made in and the next start from the saved file decide
  every call the same way.
- The person is told what it did: where it holds, whether it was saved, and **which stricter
  rules still apply** — every rule of the `default`, `user` or `project` layer that is narrower
  than the new rule and stricter than it.
