<!-- Adapted from Hermes Agent agent/prompt_builder.py (the skills index) (MIT, © Nous Research).
     {{skills}} is the list; {{manage}} is the line about fixing and saving skills, or nothing. -->
## Skills
Before replying, scan the skills below. If one matches or is even partly relevant to the task, load it with skill_view(name) and follow it. Err on the side of loading: skills hold the specific commands, pitfalls and conventions that make the difference, and they record how the user wants that kind of work done — load one even for a task you already know how to do.
{{manage}}

<available_skills>
{{skills}}
</available_skills>

Only go on without loading a skill if none is relevant.
