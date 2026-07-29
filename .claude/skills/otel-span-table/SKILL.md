---
name: otel-span-table
description: Generate a Markdown reference table of all OpenTelemetry spans emitted by a Python SDK at a given GitHub repo (optionally pinned to a tag, branch, or commit SHA). Handles both OpenInference decorator-instrumented SDKs and generic OpenTelemetry-contrib instrumentations (e.g. httpx, starlette).
argument-hint: "<github-repo-url> [<version>]"
---

# otel-span-table

Given a GitHub URL to a Python SDK repository (optionally pinned to a specific version), produce a Markdown reference table of all OpenTelemetry spans it emits.

Two families of instrumentation are supported; **detect which one applies (Step 2) and follow the matching extraction path (Step 3/4)**:

- **A. OpenInference decorator style** — the SDK wraps its own classes/functions with `trace_function` / `trace_class` decorators (Arize OpenInference: openai_agents, google_adk, anthropic, claude_agent_sdk, strands, …). Span names are static and derivable from the decorator/auto-naming rule.
- **B. OpenTelemetry-contrib style** — a generic instrumentation for a third-party library (from `opentelemetry-python-contrib`: `opentelemetry-instrumentation-httpx`, `-starlette`, `-fastapi`, `-requests`, …). There is no `telemetry.py` and no `trace_*` decorators; the package wraps middleware / transports / client methods and emits spans whose **names are dynamic** (e.g. `POST /route`) and whose **attributes come from the OpenTelemetry semantic conventions**, not a literal `attributes=` dict in the source.

## Steps

1. Parse the args:
   - First whitespace-separated token: the GitHub repo URL.
   - Second token (optional): a version specifier — a git tag (`v1.5.1`), a branch name (`main`, `release/0.2`), or a commit SHA. If absent, default to the repo's default branch (typically `main`).
   - Resolve the URL to a `ref`: tag → matching tag, branch → branch tip, SHA → that SHA. All subsequent fetches MUST be pinned to this ref so the table is reproducible.
2. **Detect the instrumentation family.** Fetch the repo's source tree (at `ref`) and look for the OpenInference signature first: a `telemetry.py` (or equivalent) defining `trace_function` / `trace_class` decorators. 
   - If found → **family A**, follow Step 3A/4A.
   - If absent, and the package is an `opentelemetry-instrumentation-*` contrib package (or otherwise instruments a third-party library via middleware/transport wrapping) → **family B**, follow Step 3B/4B.
   - If genuinely ambiguous, state which signals you saw and pick the closer match; note the choice in the output header.

### Family A — OpenInference decorators
3A. Read `telemetry.py` to understand the span-naming convention and decorator signatures (`trace_function`, `trace_class`, or equivalent).
4A. Recursively find every file that imports or applies those decorators. For each instrumented class/function, extract:
   - **Span name** — derive from the decorator arguments or the auto-naming rule (`{module}.{ClassName}.{method}`)
   - **SpanKind** — SERVER / CLIENT / INTERNAL / PRODUCER / CONSUMER
   - **Attributes** — list any `attributes=` dict keys or `attribute_extractor` fields; write `none` if absent
   - **Source file** — repo-relative path
   - **Description** — one sentence describing what the event represents

### Family B — OpenTelemetry-contrib
3B. Locate the instrumentation entry points — typically `__init__.py` / `package.py` under `src/opentelemetry/instrumentation/<lib>/`. Identify where spans are started: `tracer.start_span` / `start_as_current_span` calls, ASGI/WSGI middleware, or wrapped client/transport methods. Note the `_instruments` / package name and the instrumented library + version range.
4B. For each span the instrumentation can emit, extract:
   - **Span name** — this is usually **dynamic**, computed at request time. Record the *rule*, not a literal, e.g. `"{http.request.method} {http.route}"` (server) or `"{http.request.method}"` (client), per the HTTP semantic conventions (`{method} {target}`; `{target}` = `http.route` for SERVER, `url.template` for CLIENT; falls back to bare `{method}`). Cite the semconv rule rather than inventing a static name.
   - **SpanKind** — SERVER for inbound (ASGI/WSGI middleware, e.g. starlette/fastapi), CLIENT for outbound (httpx/requests). PRODUCER/CONSUMER for messaging instrumentations.
   - **Attributes** — these come from the **semantic conventions**, not an `attributes=` dict. List the attribute keys the package sets, grouped as the semconv spec does (Required / Conditionally Required / Recommended / Opt-In) where known. For HTTP that is the [HTTP spans semconv](https://opentelemetry.io/docs/specs/semconv/http/http-spans/): SERVER — `http.request.method`, `url.path`, `url.scheme`, `http.route`, `http.response.status_code`, `server.address`, `server.port`, `client.address`, `network.protocol.version`, `user_agent.original`, `error.type`; CLIENT — `http.request.method`, `server.address`, `server.port`, `url.full`, `http.response.status_code`, `network.protocol.version`, `error.type`. Verify against the package's actual code (attribute names drift with semconv versions and the `OTEL_SEMCONV_STABILITY_OPT_IN` migration — old names like `http.method`/`http.url`/`http.status_code` vs new `http.request.method`/`url.full`/`http.response.status_code`); record which convention version the code targets. Prefer the [semconv HTTP registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/http/) for exact current keys.
   - **Source file** — repo-relative path.
   - **Description** — one sentence describing what the span represents (e.g. "an inbound HTTP request handled by the Starlette app").

5. Write a Markdown file with:
   - A short header explaining tracer/instrumentation name, **version / ref the table was generated from** (record the resolved git tag, branch, or commit SHA verbatim), the instrumentation **family (A or B)**, kill-switch / disable mechanism (env var or `uninstrument()`), the span-naming pattern, and — for family B — the semantic-convention version the attributes follow.
   - One `## Section` per logical layer (family A: e.g. server request handlers, event queues, client transports; family B: e.g. server middleware, client transport, plus any sub-spans like `http send` / `http receive`).
   - Within each section, a table with columns: `Span name` · `Kind` · `Attributes` · `Source` · `Description`. For family B, put the naming *rule* in the `Span name` cell (e.g. `{method} {http.route}`) rather than a concrete example.
   - A summary count table at the bottom.

## Usage

```
/otel-span-table <github-repo-url> [<version>]
```

- `<github-repo-url>` — required; the starting point for all fetches.
- `<version>` — optional; a git tag (`v1.5.1`), branch (`main`), or commit SHA. Defaults to the repo's default branch.

Examples:

```
# Family A — OpenInference decorator-instrumented SDKs
/otel-span-table https://github.com/Arize-ai/openinference v1.5.1
/otel-span-table https://github.com/example/sdk main
/otel-span-table https://github.com/example/sdk a1b2c3d
/otel-span-table https://github.com/example/sdk

# Family B — OpenTelemetry-contrib instrumentations (dynamic names + semconv attributes)
/otel-span-table https://github.com/open-telemetry/opentelemetry-python-contrib main
```

For a family-B package that lives in a monorepo subdirectory (e.g. `opentelemetry-python-contrib`), point at the specific instrumentation dir when narrowing scope, e.g. `.../tree/main/instrumentation/opentelemetry-instrumentation-httpx` or `.../opentelemetry-instrumentation-starlette`.

Output the finished Markdown to a file named `<repo_name>_telemetry_spans.md` (or `<repo_name>_<ref>_telemetry_spans.md` when a non-default ref was requested) in the current working directory, then `git add` and report the file path.
