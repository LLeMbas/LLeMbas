<!-- Source: OpenCode command/template/initialize.txt (MIT, © 2025 opencode), shortened, and made to
     ask about git the way blocks/agents-missing.md does (LLeMbas CLI's /init). -->
Create or update `AGENTS.md` at the project root, {{root}}/AGENTS.md: the file every later session here starts from. Every line should answer "would an agent likely get this wrong without being told?" — if not, leave it out.

{{focus}}

Investigate first, highest value first: README, the manifests and lockfiles, build, test, lint and type-check configuration, CI workflows, and any instruction files already there (AGENTS.md, CLAUDE.md, .cursorrules, .github/copilot-instructions.md). Read a few representative source files only if the structure is still unclear. Where the documentation and the scripts disagree, trust the scripts.

Write down what is hard-earned:
- the exact commands to build, run and test — and how to run one test;
- the order checks must run in, where it matters;
- where the real entry points are, and what the main directories are for;
- generated code, migrations, special environments, anything that is not the default for the language;
- conventions this project keeps that differ from the usual ones.

Leave out generic advice, long file trees, and anything you could not verify. Short sections and bullets; a simple project gets a short file.

If the file is already there, improve it in place: keep what is still true, remove what is stale. If it says nothing about how git is used here, ask the user once, with ask_user, and write their answer under a "## Git" heading. Ask nothing the repository already answers.
