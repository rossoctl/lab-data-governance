# General principles

- Keep explanations short and concise.
- When debugging, always find the root cause — don't patch over symptoms.
- Maintain a single source of truth; never duplicate code.

# Workflow

## Starting work on a new issue

When asked to work on a GitHub issue, always:

1. Switch to `main`.
2. Pull the latest (`git pull --ff-only`).
3. Create the feature branch from that fresh `main`.

Never branch off another in-progress feature branch unless the user explicitly asks for stacked work. If the working tree is currently on a feature branch and the user asks for a new issue, the implicit instruction is "start from updated `main`," not "stack on top of where I am."

