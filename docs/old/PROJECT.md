We want to work on a new open source project in the `data-governance` directory. It will add data governance support to the `kagenty` platform (can be found in the `platform` directory).

The data governance should provide a user with the following information on agents and tools deployed on kagenti platform:
1. Data flow
2. Data lineage
3. Data classification
4. Data related risks, explanations and suggestions
5. Detections of data policy violations, explanations and enforcement suggestions.

Additional considerations:
1. Minimal changes to kagenty platform itself, e.g. adding OTEL exporter to sent telemetry to our ingestion module.
2. Loosely coupled with kagenty platform
3. Data classification should be built on the existing `UDC` project.
4. Please use existing open source components where available. Do not invent things that has been already implemented.
5. Use kagenti platform capabilities to pull information about installed agents and tools in different name spaces.
6. Data ingestion should store the raw events, but only those that can be beneficial for later analytics.
7. Data ingestion should perform a kind of data normalization, e.g. if events are coming from different agent frameworks we need to represents them in a similar manner.
8. Analytic modules should be able to work with raw traces as well as with a higher level APIs over ingested data.
9. UI
9.1. Raw events view
9.2. System topology view - users, agents, tools, tool targets (sites, databases, etc.)
9.3. Execution flow in a form of sequence diagram
9.4. Allow analytics to display their own UI.
9.5. Some analytics may want to produce an additional information which can be shown as an overlay on the topology graph.

Architectural thoughts (feel free to change and improve):
1. Currently open telemetry is the source of telemetry data and should be one of possible providers for data ingestion pipeline.
2. Kagenty platform's OTEL collector can be configured to send traces directly to our ingestion module.
3. We need to store the ingested information and also allow storing analysis results somewhere. Would postgresql be an acceptable solution?
4. Different analytics should be implemented as plug-ins.
