# Workflow

## Starting work on a new issue

When asked to work on a GitHub issue, always:

1. Switch to `main`.
2. Pull the latest (`git pull --ff-only`).
3. Create the feature branch from that fresh `main`.

Never branch off another in-progress feature branch unless the user explicitly asks for stacked work. If the working tree is currently on a feature branch and the user asks for a new issue, the implicit instruction is "start from updated `main`," not "stack on top of where I am."

## Committing

Always commit with `git commit -s` (Developer Certificate of Origin sign-off). This repo requires the `Signed-off-by` trailer on every commit.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `kagenti/lab-data-governance` (the `origin` remote), managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
