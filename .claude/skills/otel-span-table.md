# otel-span-table

Given a GitHub URL to a Python SDK repository, produce a Markdown reference table of all OpenTelemetry spans it emits.

## Steps

1. Fetch the repo's source tree to locate `telemetry.py` (or equivalent tracing utilities).
2. Read `telemetry.py` to understand the span-naming convention and decorator signatures (`trace_function`, `trace_class`, or equivalent).
3. Recursively find every file that imports or applies those decorators.
4. For each instrumented class/function, extract:
   - **Span name** — derive from the decorator arguments or the auto-naming rule (`{module}.{ClassName}.{method}`)
   - **SpanKind** — SERVER / CLIENT / INTERNAL / PRODUCER / CONSUMER
   - **Attributes** — list any `attributes=` dict keys or `attribute_extractor` fields; write `none` if absent
   - **Source file** — repo-relative path
   - **Description** — one sentence describing what the event represents
5. Write a Markdown file with:
   - A short header explaining tracer name, version, kill-switch env var, and span-naming pattern
   - One `## Section` per logical layer (e.g. server request handlers, event queues, client transports)
   - Within each section, a table with columns: `Span name` · `Kind` · `Attributes` · `Source` · `Description`
   - A summary count table at the bottom

## Usage

```
/otel-span-table <github-repo-url>
```

The args value is the GitHub URL passed by the user. Use it as the starting point for all fetches.

Output the finished Markdown to a file named `<repo_name>_telemetry_spans.md` in the current working directory, then `git add` and report the file path.
