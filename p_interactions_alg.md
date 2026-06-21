## Overview
This document should only be changed With explicit human permission  

This document describes the algorithm of p_interactions Walking on top of standard OTEL spans as well as assumptions made 

### goal 
The goal of the algorithm is to identify *agentic* entities and their interactions. 
Entities may include tools, agents, llm calls etc. Some of these may be mapped to kubernetes entities such as services while others maybe implemented inside a single container (e.g. internal tool) 

The input to the algorithm are events (e.g. Otel spans arriving from different standard scopes such as openinference, a2a, etc.).
The output are entities and interactions.

### Assumptions
1. Our focus is on agents and agent interactions 
2. OTEL may be incomplete 

### Details

The algorithm begins by creating a common graph for spans. next it handles each scope separately accounting for its semantics. Since we are looking for agentic semantics - the a2a and openinference scope are most important. other scopes are used for enrichment.
Along the stages of graph construction we may create inferred nodes. these nodes will be merged using heuristics. 




#### Definitions:
1. Event node representing a local entity
2. Source / Target nodes representing a local and remote entity.
3. A white edge representing parent child relationship based on trace parent.
4. A Gray edge representing the order of events - The (grand-)parent child relationship in agentic scoped events.
5. agentic boundary - A boundary is (node) source *calling* an agent, a tool, an LLM or another service, or a *target* of such a call
6. A Black edge representing a source/target across agentic entities/ components/ Containers
7. Inferred node - a node in the graph we know should exist although we don't have a span emitted representing that node. for example, LLM output requires a call to a tool can produce an inferred node representing that tool.
8. merge - the process of collapsing inferred nodes with real nodes - this process can be based on heuristics.


Some agentic scope spans represent a source (client) or a target (server) (or both) of agentic protocols
for example: 
openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query - A span covers the agentic conversation including input and output.


#### Step 1 - Base (white) execution flow graph

this step essentially creates an execution flow graph from the spans.
Should look similar to what is shown in Phoenix or MLflow

the algorithm construct a node for each span.
in addition it constructs an edge from its parent node to itself.
This graph should reflect the traceparent span connectivity.

this is the base graph, were nodes and edges are "white" 


#### Step 2 - Agentic (gray) scope

The algorithm begins by handling the agentic scope, Specifically open inference spans.

based on this scope we will build the agentic graph.

All other scopes will be used to update and enrich the graph.
deferred to later steps.



#### Step 2.a - Enrich agentic (Execution graph) nodes
in this step we enrich the base graph with agentic semantics:

Requires: Open inference telemetry (openinference_telemetry_spans.md, openinference_openai_agents_v1.4.1_telemetry_spans.md, openinference_anthropic_v1.0.6_telemetry_spans.md)

1. Traverse the base graph, And identify all nodes related to the agentic scope. 
2. Mark each agentic scope node as "Gray" 
3. connect consecutive "Gray" nodes using "Gray" edges iff there is a path (of white edges) between two Gray nodes (Without going through a Gray node in between).



#### Step 2.b - Inferred (execution graph) nodes 
In some cases agentic spans may describe or represent additional entities - In those cases we will create inferred nodes

Examples for cases needing inferred nodes:
1. openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query
This span represents a call to an LLM. While the span represents the current node - the agent (source), and since the spans includes information on both the current and target nodes as well as the data flowing between them.
we can infer:
1. A new node representing the LLM (target). 
2. An edge between the agent and the LLM target
3. An edge between the LLM target and the agent

2. Similarly a tool call Span such as openinference.instrumentation.claude_agent_sdk.{tool_name}
represent a call to a tool from which we can infer the following : 
1. a new node representing the tool (target)
2. an edge between the tool call (source) and the tool itself (target)
3. an edge between the tool right (target) and the agent tool call (source)

3. "llm.output_messages.0.message.tool_calls.0.tool_call. function.arguments": "{\"action\": \"store\", \"name\": \"keywords.2.txt\", ..}"
Similarly The complete span represents a call to the LLM - however this specific attribute includes information on a tool.
We can therefore infer two nodes: The first represents the tool call (The source) while the second represents the tool itself (target)
In this case we will also infer several edges:
1. between the current span and the tool call 
2. Between the tool call and the tool itself (The target)
3. Between the tool itself and the tool call (Reverse edge)

Additional cases may exist which need to be implemented such as tools inferred from input attributes.

Inferred interaction ordering:
When inferring new edges (interactions) Make sure to adjust the order.
- outgoing edges (calls) are before incoming edges (responses)
- When tools are derived from LLM spans:
    - The edge between the LLM and the tool call is before the edge between the two call and the tool itself
    - the edge between the tool and the tool itself is before the edge between the tool and the tool call (reverse edge)
    - Tool interactions derived from input attributes should happen before interactions with the LLM
    - tool interactions derived from output attributes should happen after interactions with the LLM


#### Step 2.c Intra trace merging
In this step we apply heuristics to merge pairs of nodes 
(inferred/inferred and inferred/observed) - representing the same entity - and both reside in the same trace.
Merging of nodes will also entail merging of edges as well as merging of attributes 

the process of merging Can be viewed as a set of heuristics identifying nodes representing the same entity 
This can be based on:
1 proximity in the trace - It is reasonable to assume that an observed node will be close by to the inferred node. it can be a sibling an ancestor etc.
2 similarity of attributes - e.g. identical tool names identical  values

#### Step 2.d - agentic (execution graph) boundaries 
In this step we identify agentic boundaries 

1. Identify Gray nodes *representing a abentic boundary* and color them "Black".
2. If there is a Gray edge between Black nodes Representing the same entity type (Agent, LLM, tool) - and one black node is a source while the other is *its* target - Color the edge black.
examples:
tool call --> tool
llm call --> llm






#### Step 3 - agentic entity Graph

In this step we are going to create a new graph representing agentic entities and interactions
In this step multiple nodes Representing the same entity in the execution flow graph are combined 


The agentic entity graph is going to be used for two things
1. Identify the entities
2. Identify the interactions 

#### Step 3.a - Creating the Entity graph
Consider the Gray and black nodes and edges in the execution flow graph.
The black edges represent connections between entities 

First we are going to create subgraphs of execution graph nodes by simply ignoring the black edges. 

Next, each sub graph represented by connected Gray and black nodes will become a new node in the entity graph - Effectively combining all nodes from the Execution flow subgraph into a single entity node.
The subgraph could contain inferred nodes, observed nodes Or both. 

Note that the edges should be maintained: The nodes (entities) the in the new graph are connected with new edges matching the black edges 

Next Combine multiple entity graph nodes representing the same entity.
Those entity graph nodes should be combined based on an identifying attribute (such as tool name).
Again, The edges should be maintained

#### Step 3.b - Naming nodes

Goal: Each node in the entity graph should be given a key. 

This key should reflect the original subgraph and may be from one of the subgraph node spans. in particular if we can identify a node in the subgraph containing the host name we should use it as an key 
If the key is not clear we can call it unknown.



### Step 4 - system graph
Deferred 

#### Inter trace merging
In this step we apply heuristics to merge pairs of nodes where one node is inferred and the other is observed while they reside in different traces (this may happen for example where when Traceparent is not properly bust) 

Deferred 

- Align names across different executions


#### Guide
- All attributes used in the code should be validated. The otel-span-table can generate a table with all the span attributes given URL .
- development and implementation of each scope should be separate




### Observations
when monitoring protocol events, We should expect to see - Assuming all events are received - A send event from one entity and a matching receiving event from another entity.
Importantly, we expect to see these two events to be *consecutive* in the trace (Otherwise we should be able to identify and flag the semantics of the event in between)

Note there may be cases such as Google ADK - LLM Spans were a single span represents both the send and receive.

We may be receiving events for multiple sources such as a2a and httpx, and - assuming tracesparent is on - those events will be interleaved. For example:
a2a tool call -> http send -> ... -> http recieve -> a2a call recieve. Importantly both a2a call and http send events represent the same entity While both receive events represent another entity. 

In case a component does not produce any events our trace will be broken. We will only have the one side of the interaction. The graph will be split.
Another case may be where only partial sources of events are emmiting events in a given component. For example a receiving side may not have a2a events resulting in:
a2a tool call -> http send -> ... -> http recieve | 
Note that in such a case traceparent will not be forwarded resulting in two traces and a split graph

Lastly note that all events between receive and send essentially belong to the same entity.

