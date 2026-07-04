# Overview
This document should only be changed With explicit human permission  

This document describes the algorithm of p_interactions Walking on top of standard OTEL spans as well as assumptions made 

# goal 
The goal of the algorithm is to identify *agentic* entities and their interactions. 
Entities may include tools, agents, llm calls etc. Some of these may be mapped to kubernetes entities such as services while others maybe implemented inside a single container (e.g. internal tool) 

The input to the algorithm are events (e.g. Otel spans arriving from different standard scopes such as openinference, a2a, etc.).
The output are entities and interactions.

# Assumptions
1. Our focus is on agents and agent interactions 
2. OTEL may be incomplete 


# Definitions:
-. Event node representing a local entity
-. Source / Target nodes representing a local and remote entity.
-. white - Initial color of all Nodes and edges, and those not assigned a scope 
-. Blue - Nodes assigned the agentic scope  
-. Teal - nodes assigned the transport scope (e.g. communication, proxy)
-. A white edge representing parent child relationship based on trace parent.
-. Inferred node - a node in the graph we know should exist although we don't have a span emitted representing that node. for example, LLM output requires a call to a tool can produce an inferred node representing that tool.
-. Inferred edge - an interaction in the graph we know should exist although we don't have a span representing this interaction
-. merge interaction - the process of merging nodes and edges representing the same exact process
-. fuse - the process of collapsing multiple nodes together to represent a single entity.




# Step 1 - Base (white) execution flow graph

this step essentially creates an execution flow graph from the spans.
Should look similar to what is shown in Phoenix or MLflow - With the addition of inferred nodes and edges.

the algorithm construct a node for each span.
in addition it constructs an edge from its parent node to itself.
This graph should reflect the traceparent span connectivity.

this is the base graph, were nodes and edges are "white" 

# Step 2 - Enrich execution flow graph with scoped semantics 

At this step the algorithm enriches and extends the execution flow graph. 

## Step 2.a - transportation scope
Requires Transportation spans such as httpx, starlette, asgi.

1. Traverse the execution flow graph, identify all nodes related to the *Transportation* scope. 
2. Color each transportation node "Teal" 

## Step 2.b - agentic scope
Requires openinference telemetry spans

1. Traverse the execution flow graph, And identify all nodes related to the *agentic* scope. 
2. Color each agentic scope node "Blue" 

<!-- 3. connect consecutive "Gray" nodes using "Gray" edges iff there is a path (of white edges - regardless of scope) between two Gray nodes (Without going through a Gray node in between). -->

<!-- #### Step 3.b - agentic (execution graph) boundaries 
It was a step this step we identify agentic boundaries, Specifically we aim to identify input (call target) and output (call sources) points.

Nodes:
1. Identify Gray nodes *representing a agentic boundary* and color them "Black". This includes For example:
  - tool targets (Target)
  - agent calls (Source)
  - llm calls
  - agent root spans - Representing entry points for the agent (Target)

Note: Using span kind and attributes only

Edges:
2. Create edges between source and related targets in the agentic scope.
specifically if there is a gray path (Consisting only of Gray edges and nodes) between two black nodes (one black node represents a source while the other its related target in the agentic scope) - *Create* a new black edge.
examples:
tool call -> tool
tool call -> agent
llm call -> llm 
-->

## Step 2.c - derive inferred (execution graph) nodes and edges

In some cases agentic spans may describe or represent additional entities - In those cases we will create inferred nodes and possibly inferred edges 

Examples for cases needing inferred nodes:
1. openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query
This span represents a call to an LLM. the span represents the current node - the agent (source), and includes information on both the current and target nodes as well as the data flowing between them.
we can infer:
  1. A new node representing the LLM (target) - agentic scope node "Blue".
  2. A new node representing a server - transportation node "Teal"
  3. New edges:
    - from Agent to server
    - from server to LLM
    - From LLM to server
    - from server to agent


2. Similarly a tool call Span such as openinference.instrumentation.claude_agent_sdk.{tool_name}
represent a call to a tool from which we can infer the following : 
  1. a new node representing the tool (target) - Agentic scope node "Blue"
  2. A new node representing a transportation node "Teal" (e.g. server)
  3. New edges:
    - from the agent tool call (source) to server
    - from server to the tool itself (target)
    - From the tool itself (target) to server
    - from server to the agent tool call (source)

3. "llm.output_messages.0.message.tool_calls.0.tool_call. function.arguments": "{\"action\": \"store\", \"name\": \"keywords.2.txt\", ..}"
While the complete span represents a call to the LLM - this specific attribute includes information on a tool.
We can therefore infer two nodes and three edges.
Inferred nodes:
  1. A new node representing the tool call (The source) - Agentic scope "blue"
  2. A new node representing a transportation node "Teal" (e.g. server)
  3. A new node representing the tool itself (target) - agent scope "blue"
  4. new Inferred edges:
    - from the current span to the tool call
    - from the tool call (source) to server
    - from server to the tool itself (target)
    - From the tool itself (target) to server
    - from server to the tool call (source)

4. Inferred agent node. this case happens (for anthropic) When the framework emits only bare leaf LLM spans — no run/agent/wrapper span. The goal here is to infer an agent node by observing that all LLM spans share a single shared parent, in the transportation scope. Example:
POST /                
 ├─ messages.create   
 └─ messages.create   
Inferred nodes:
  1. A new node representing the agent 
  2. new inferred edges:
    -. from the transportation (POST) span/node to the agent node
    -. from the agent node to each one of the LLM spans ()
  3. Disconnect the edges but maintain reference from the newly created edges to the original ones 
  


Additional cases may exist which need to be implemented such as tools inferred from input attributes.

Inferred edges ordering/timing:
When inferring new edges (interactions) make sure to adjust the order based on the execution order. examples: 
- outgoing edges (calls) are before incoming edges (responses)
- When tools are derived from LLM spans:
    - The edge between the LLM and the tool call is before the edge between the tool call and the tool itself
    - the edge between the tool and the tool itself is before the edge between the tool and the tool call (reverse edge)
    - Tool interactions derived from input attributes should happen before interactions with the LLM
    - tool interactions derived from output attributes should happen after interactions with the LLM



## Step 2.d - merge identical interactions (execution graph)

this step identifies cases where a single interaction is represented more than once in the execution graph.
Once these are detected, these chains are merged as a unit. 

Specifically, an interaction is a chain (subgraph): Blue source → transport region → Blue target with response legs. Transport region is one or more Teal (and possibly White) nodes (inferred server or observed transport chain). 
The goal is to identify chains that represent the same interaction (same processing at the same time) and merges them as a unit — the aligned Blue endpoints and the transport regions collapse pairwise onto one survivor. 
Each side may be inferred or observed. Matching uses {proximity, same tool name, same execution time, same input/output, inferred-vs-observed, same scope}; the survivor keeps the time of the span that created the interaction.



For example, Assume a trace including a span for LLM and another span for a tool call
the execution graph may include nodes inferred from the first span including
tool call --> Server --> Tool
In addition it may include nodes inferred from the second span including 
Server --> Tool
In such a case they inferred tool call may be merged with the node representing the tool called span, and both pairs of server no nodes and tool nodes should be merged.

Another example: assume we have a tool called retrieving information from a database (Single invocation of the database) followed by multiple interactions with an LLM. in such a case the tool input will appear in all the following LLM spans and may result in multiple inferred database tool calls.
since all those inferred nodes represent a single call to the database - They should be merged.

The process of Merging is a set of heuristics - asserting the same exact processing is observed - and can be based on:
- proximity in the trace
- Same tool name 
- same exact execution time
- Same input argument and output result 
- whether the node or edge are inferred or observed in conjunction with the source spans 
- nodes from the same scope

Timing note: when merging edges account for the timing of each of the edges and maintain the time of the appropriate Span. for example, After a tool call its input may be repeated several times in following spans. in this case the timing of this is interaction should be after the span creating the tool call.



# Step 3 - entity graph 
In this step we create the entity graph, based on the execution graph,representing agentic entities and interactions
The agentic entity graph is going to be used for two things
1. Identify the entities
2. Identify the interactions 


## Step 3.a - Creating the Entity graph

The entity graph is going to be constructed in as follows:

Nodes:
1. structurally:
  Consider the Blue and Teal nodes in the execution flow graph.

  First we are going to create subgraphs of execution graph nodes by simply dropping teal nodes. 
  Each subgraph can contain inferred nodes, observed nodes or both - But these can only be Blue or White. 

  Next, for all nodes in each sub graph represented by connected Blue and White nodes create a group.

2. Semantically:
  Combine groups representing the same entity into a single group

  For example, An execution graph may include multiple tool calls (e.g. with different arguments) to a file   
  system, and therefore multiple inferred file system tools.
  For each one of these tools we will create a group, based on the structural step. However all those tools 
  represent in reality a single file system tool entity. Therefore all these groups should be combined

  The process of identifying groups represent the same entity is a set of heuristics - and can be based on:
  - Same tool/service/host/llm name 
  - Same argument/output types 
  - nodes from the same scope

Each group represents a node in the entity graph 

Note: Each node in the entity graph should be given a key: This key should reflect the original subgraph and may be from one of the subgraph node spans. in particular if we can identify a node in the subgraph containing a agent/service/tool/host name we should use it as an key 
If the key is not clear we can call it unknown.

Edges:
1. Structurally:
  - edges internal to a group are ignored
  - Every path connecting entity nodes should be grouped and represented by a single interaction connecting these entity nodes. In other words: collapse each Teal transport chain between two Blue components into one interaction 

The edges in the entity graph are simply these interactions.







# Guide
- All attributes used in the code should be validated. The otel-span-table skill can generate a table with all the span attributes given URL .



# Observations


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

