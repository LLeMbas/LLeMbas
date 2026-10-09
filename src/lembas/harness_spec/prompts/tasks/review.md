<!-- Source: OpenCode session/prompt/gpt.txt, "If the user asks for a review" (MIT, © 2025 opencode),
     made into a brief. -->
Review the change below with a code-review mindset. Your job is to find what is wrong, not to summarise what was done.

- Findings first, ordered by severity: bugs, then risks and behavioural regressions, then missing tests. For each: file:line, what is wrong, and a concrete way it fails.
- Read the code around the change where you need it — the diff alone is not the whole story.
- Then any open questions or assumptions.
- Only after that, and briefly, what the change does.
- If you find nothing, say so plainly, and name any residual risk or gap in testing.

You are reviewing, not fixing: change nothing.

{{scope}}

```diff
{{diff}}
```
