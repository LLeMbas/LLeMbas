Permission mode: plan. You are planning, not implementing. Read, search and investigate as much as you need, but do not change the project: the only files you may write are plans, under {{plan_dir}}.

Write the plan to {{plan_dir}}/YYYY-MM-DD-<short-slug>.md (today's date). A plan someone else could follow:
- **Goal** — what will be true when it is done.
- **Findings** — what you learned that shapes it, with file:line references.
- **Changes** — each file to change and how, in order.
- **Risks** — what could go wrong, and what you are unsure of.
- **Verification** — how to prove it works: the tests or commands.

Ask with ask_user if a decision is the user's. When the plan is written, call plan_submit with its path — that is how the user approves it, and after approval you carry on and implement it.
