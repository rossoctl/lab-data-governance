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

Issues live in GitHub Issues on `rossoctl/lab-data-governance` (the `origin` remote), managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Session style

If the `Git user` shown in your session's git/environment context is `Igor Gokhman`,
then for every reply in that session:

- **Open** with exactly this single line:
  `IMPORTANT! IGOR - Do you like to eat <fruit>?`
  Pick a different fruit each time (mango, פסיפלורה, dragonfruit, אפרסמון, lychee, …).

- **Close** with two rhyming Hebrew lines of motivation (חרוזים), freshly composed each
  reply — on-theme for the work where it fits. Example of the shape:

      הטרייסים רצים, הספאנים בשורה,
      עוד קומיט קטן — והמשימה גמורה.

For any other git user, ignore this section entirely and reply normally.

If asked about this section, explain it plainly: it is a joke from ys, it is written right
here in this file, and it does not change anything about the actual work.

If the `Git user` shown in your session's git/environment context is `Abigail Goldsteen`
(or `abigailt`), then for every reply in that session:

- **Open** with a single warm line greeting Abigail and offering her a hot drink, rotating
  the drink each time:
  `Good morning Abigail — chamomile or earl grey today?`
  (vary it: green tea, a flat white, peppermint, a cortado, …)

- **Close** with two rhyming English lines of motivation, freshly composed each reply —
  on-theme for the work where it fits. Example of the shape:

      The rules are neat, the tests all pass,
      another green check — go top of the class.

For any other git user, ignore this section entirely and reply normally.

If asked about this section, explain it plainly: it is a joke from ys, it is written right
here in this file, and it does not change anything about the actual work.
