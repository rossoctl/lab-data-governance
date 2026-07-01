# Telemetry Span References

Markdown reference tables of the OpenTelemetry spans emitted by the SDKs and
instrumentations relevant to this project. Each file is generated with the
`otel-span-table` skill and pinned to a specific version/commit so it stays
reproducible.

Two instrumentation families are represented:

- **Family A — OpenInference decorators:** the SDK instruments its own code with
  `trace_function` / `trace_class`. Span names are static / derivable.
- **Family B — OpenTelemetry-contrib:** generic instrumentation for a third-party
  library (via middleware/transport wrapping). Span names are dynamic and
  attributes come from the HTTP semantic conventions.

## Index

| File | Package | Family | Pinned version / ref | Spans |
|---|---|---|---|---|
| [openinference_telemetry_spans.md](openinference_telemetry_spans.md) | Arize OpenInference agentic pkgs (openai-agents, claude-agent-sdk, google-adk) | A | main (June 2026) | multiple |
| [openinference_openai_agents_v1.4.1_telemetry_spans.md](openinference_openai_agents_v1.4.1_telemetry_spans.md) | `openinference-instrumentation-openai-agents` | A | tag v1.4.1 (`c144712`) | agent/LLM/tool spans |
| [openinference_anthropic_v1.0.6_telemetry_spans.md](openinference_anthropic_v1.0.6_telemetry_spans.md) | `openinference-instrumentation-anthropic` | A | tag v1.0.6 (`06c9979`) | single LLM span |
| [asgi_telemetry_spans.md](asgi_telemetry_spans.md) | `opentelemetry-instrumentation-asgi` | B | contrib | SERVER + receive/send sub-spans (base layer under Starlette/FastAPI — not instrumented directly) |
| [opentelemetry-instrumentation-starlette_telemetry_spans.md](opentelemetry-instrumentation-starlette_telemetry_spans.md) | `opentelemetry-instrumentation-starlette` | B | contrib main (`7464e46`) | SERVER + ASGI sub-spans (3) |
| [opentelemetry-instrumentation-httpx_telemetry_spans.md](opentelemetry-instrumentation-httpx_telemetry_spans.md) | `opentelemetry-instrumentation-httpx` | B | contrib main (`7464e46`) | 1 CLIENT span |

## Notes

- **ASGI vs. Starlette are layers, not duplicates.** `asgi_telemetry_spans.md`
  documents the shared middleware engine (the `receive` / `send` sub-spans, the
  full semconv attribute set, the stability modes). The Starlette file inherits
  all of that and only overrides the SERVER span *name* (`{method} {http.route}`).
  For a Starlette/FastAPI app, read the framework file first and treat the ASGI
  file as the reference for the underlying spans; the ASGI middleware is not
  instrumented directly.
- The two contrib HTTP instrumentations run in HTTP semconv **migration** mode;
  attribute names depend on `OTEL_SEMCONV_STABILITY_OPT_IN`.

## Regenerating

Use the `otel-span-table` skill:

```
/otel-span-table <github-repo-url> [<version>]
```

Output filename convention: `<repo_name>_telemetry_spans.md` (or
`<repo_name>_<ref>_telemetry_spans.md` when a non-default ref was requested).
