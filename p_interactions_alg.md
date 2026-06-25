## Overview
This document should only be changed With explicit human permission  

This document describes the algorithm of p_interactions Walking on top of standard OTEL spans as well as assumptions made 

## goal 
The goal of the algorithm is to identify *agentic* entities and their interactions. 
Entities may include tools, agents, llm calls etc. Some of these may be mapped to kubernetes entities such as services while others maybe implemented inside a single container (e.g. internal tool) 

The input to the algorithm are events (e.g. Otel spans arriving from different standard scopes such as openinference, a2a, etc.).
The output are entities and interactions.

## Assumptions
1. Our focus is on agents and agent interactions 
2. OTEL may be incomplete 


## Definitions:
1. Event node representing a local entity
2. Source / Target nodes representing a local and remote entity.
3. A white edge representing parent child relationship based on trace parent.
4. A Gray edge representing the order of events - The (grand-)parent child relationship in agentic scoped events.
5. agentic boundary - A boundary is (node) source *calling* an agent, a tool, an LLM or another service, or a *target* of such a call
6. A Black edge representing a source/target across agentic entities/ components/ Containers
7. Inferred node - a node in the graph we know should exist although we don't have a span emitted representing that node. for example, LLM output requires a call to a tool can produce an inferred node representing that tool.
8. Inferred edge - an interaction in the graph we know should exist although we don't have a span representing this interaction
9. merge - the process of merging nodes and edges representing the same exact entity and interaction 
8. fuse - the process of collapsing multiple nodes together to represent a single entity.




### Step 1 - Base (white) execution flow graph

this step essentially creates an execution flow graph from the spans.
Should look similar to what is shown in Phoenix or MLflow - With the addition of inferred nodes and edges.

the algorithm construct a node for each span.
in addition it constructs an edge from its parent node to itself.
This graph should reflect the traceparent span connectivity.

this is the base graph, were nodes and edges are "white" 


### Step 2 - Extend execution flow graph - Scope specific

At this step the algorithm enriches and extends the execution flow graph. 

#### Step 2.a - open inference scope - derive inferred (execution graph) nodes and edges

The algorithm begins by handling the open inference scope:

Requires: Open inference telemetry (openinference_telemetry_spans.md, openinference_openai_agents_v1.4.1_telemetry_spans.md, openinference_anthropic_v1.0.6_telemetry_spans.md)

In some cases agentic spans may describe or represent additional entities - In those cases we will create inferred nodes And possibly inferred edges 

Examples for cases needing inferred nodes:
1. openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query
This span represents a call to an LLM. the span represents the current node - the agent (source), and includes information on both the current and target nodes as well as the data flowing between them.
we can infer:
  1. A new node representing the LLM (target). 
  2. A new edge between the agent and the LLM target
  3. A new edge between the LLM target and the agent

2. Similarly a tool call Span such as openinference.instrumentation.claude_agent_sdk.{tool_name}
represent a call to a tool from which we can infer the following : 
  1. a new node representing the tool (target)
  2. a new edge between the tool call (source) and the tool itself (target)
  3. a new edge between the tool right (target) and the agent tool call (source)

3. "llm.output_messages.0.message.tool_calls.0.tool_call. function.arguments": "{\"action\": \"store\", \"name\": \"keywords.2.txt\", ..}"
While the complete span represents a call to the LLM - this specific attribute includes information on a tool.
We can therefore infer two nodes and three edges.
Inferred nodes:
  1. A new node representing the tool call (The source)
  2. A new node representing the tool itself (target)
Inferred edges:
1. A new edge between the current span and the tool call 
2. A new edge between the tool call and the tool itself (The target)
3. A new edge between the tool itself and the tool call (Reverse edge)

Additional cases may exist which need to be implemented such as tools inferred from input attributes.

Inferred edges ordering/timing:
When inferring new edges (interactions) Make sure to adjust the order based on the execution order. examples: 
- outgoing edges (calls) are before incoming edges (responses)
- When tools are derived from LLM spans:
    - The edge between the LLM and the tool call is before the edge between the tool call and the tool itself
    - the edge between the tool and the tool itself is before the edge between the tool and the tool call (reverse edge)
    - Tool interactions derived from input attributes should happen before interactions with the LLM
    - tool interactions derived from output attributes should happen after interactions with the LLM

#### Step 2.b - Other scopes
deferred to later steps.



### Step 3 - Enrich execution flow graph with agentic semantics 

#### Step 3.a Color agentic nodes
1. Traverse the execution flow graph, And identify all nodes related to the agentic scope. 
2. Mark each agentic scope node as "Gray" 
3. connect consecutive "Gray" nodes using "Gray" edges iff there is a path (of white edges) between two Gray nodes (Without going through a Gray node in between).

#### Step 3.b - agentic (execution graph) boundaries 
In this step we identify agentic boundaries 

1. Identify Gray nodes *representing a abentic boundary* and color them "Black".
2. If there is a Gray edge between Black nodes Representing the same entity type (Agent, LLM, tool) - and one black node is a source while the other is *its* target - Color the edge black.
examples:
tool call --> tool
llm call --> llm


### Step 4 - execution graph node / edge merge 
this step identifies nodes and/or edges In the execution graph representing the same entity or interaction and merges those. The process accounts for merging inferred/inferred, inferred/observed as well as observed/observed nodes or edges.

The process of Merging is a set of heuristics identifying nodes/edges representing the same entity or edge (interaction).
the process starts with merging nodes. Next the process continues with merging edges.

This can be based on:
1. similarity of node attributes - e.g. identical tool names could hint the nodes represent the same tool (entity) and should be merged. in such a case the edges should be maintained (The source or target node should be updated) (Note that the edges could be merged)
2. Similarity of edge attributes - two edges with similar arguments, same source and same target. E.g. a tool call with the same arguments press can represent a single interaction hinting the two edges should be merged 

Additional hints can be derived from:
- proximity in the trace
- same/similar time 
- whether the node or edge are inferred or observed in conjunction with the source spans 

Timing note: when merging edges account for the timing of each of the edges And maintain the time of the Original Span. for example, After a tool call its input may be repeated several times. in this case the timing of this is interaction should be after the span creating the tool call.

### Step 5 - agentic entity Graph (fuse)
based on this scope we will build the agentic graph. 

In this step we are going to create a new graph representing agentic entities and interactions
In this step multiple nodes Representing the same entity in the execution flow graph are fused 


The agentic entity graph is going to be used for two things
1. Identify the entities
2. Identify the interactions 

#### Step 5.a - Creating the Entity graph
Consider the Gray and black nodes and edges in the execution flow graph.
The black edges represent connections between entities 

First we are going to create subgraphs of execution graph nodes by simply ignoring the black edges. 
The subgraph can contain inferred nodes, observed nodes Or both. 

Next, each sub graph represented by connected Gray and black nodes will become a new node in the entity graph - Effectively fusing all nodes from the Execution flow subgraph into a single entity node.

Note that the black edges (both observed and inferred) should be maintained, meaning, the nodes (entities) the in the new graph are connected with new edges matchin the original black edges 


#### Step 5.b - Naming nodes

Goal: Each node in the entity graph should be given a key. 

This key should reflect the original subgraph and may be from one of the subgraph node spans. in particular if we can identify a node in the subgraph containing the host name we should use it as an key 
If the key is not clear we can call it unknown.




## Guide
- All attributes used in the code should be validated. The otel-span-table skill can generate a table with all the span attributes given URL .
- development and implementation of each scope should be separate




## Observations


Some agentic scope spans represent a source (client) or a target (server) (or both) of agentic protocols
for example: 
openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query - A span covers the agentic conversation including input and output.


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

