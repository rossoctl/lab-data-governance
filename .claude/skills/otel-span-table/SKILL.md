---
name: otel-span-table
description: Generate a Markdown reference table of all OpenTelemetry spans emitted by a Python SDK at a given GitHub repo (optionally pinned to a tag, branch, or commit SHA).
argument-hint: "<github-repo-url> [<version>]"
---

# otel-span-table

Given a GitHub URL to a Python SDK repository (optionally pinned to a specific version), produce a Markdown reference table of all OpenTelemetry spans it emits.

## Steps

1. Parse the args:
   - First whitespace-separated token: the GitHub repo URL.
   - Second token (optional): a version specifier — a git tag (`v1.5.1`), a branch name (`main`, `release/0.2`), or a commit SHA. If absent, default to the repo's default branch (typically `main`).
   - Resolve the URL to a `ref`: tag → matching tag, branch → branch tip, SHA → that SHA. All subsequent fetches MUST be pinned to this ref so the table is reproducible.
2. Fetch the repo's source tree (at `ref`) to locate `telemetry.py` (or equivalent tracing utilities).
3. Read `telemetry.py` to understand the span-naming convention and decorator signatures (`trace_function`, `trace_class`, or equivalent).
4. Recursively find every file that imports or applies those decorators.
5. For each instrumented class/function, extract:
   - **Span name** — derive from the decorator arguments or the auto-naming rule (`{module}.{ClassName}.{method}`)
   - **SpanKind** — SERVER / CLIENT / INTERNAL / PRODUCER / CONSUMER
   - **Attributes** — list any `attributes=` dict keys or `attribute_extractor` fields; write `none` if absent
   - **Source file** — repo-relative path
   - **Description** — one sentence describing what the event represents
6. Write a Markdown file with:
   - A short header explaining tracer name, **version / ref the table was generated from** (record the resolved git tag, branch, or commit SHA verbatim), kill-switch env var, and span-naming pattern
   - One `## Section` per logical layer (e.g. server request handlers, event queues, client transports)
   - Within each section, a table with columns: `Span name` · `Kind` · `Attributes` · `Source` · `Description`
   - A summary count table at the bottom

## Usage

```
/otel-span-table <github-repo-url> [<version>]
```

- `<github-repo-url>` — required; the starting point for all fetches.
- `<version>` — optional; a git tag (`v1.5.1`), branch (`main`), or commit SHA. Defaults to the repo's default branch.

Examples:

```
/otel-span-table https://github.com/Arize-ai/openinference v1.5.1
/otel-span-table https://github.com/example/sdk main
/otel-span-table https://github.com/example/sdk a1b2c3d
/otel-span-table https://github.com/example/sdk
```

Output the finished Markdown to a file named `<repo_name>_telemetry_spans.md` (or `<repo_name>_<ref>_telemetry_spans.md` when a non-default ref was requested) in the current working directory, then `git add` and report the file path.
