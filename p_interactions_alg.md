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


## Step 2.c - infer (execution graph)

In some cases agentic spans may describe or represent additional entities - In those cases we will create inferred nodes and possibly inferred edges 

Examples for cases needing inferred nodes:
### 1. openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query
This span represents a call to an LLM. the span represents the current node - the agent (source), and includes information on both the current and target nodes as well as the data flowing between them.
we can infer:
  1. A new node representing the LLM (target) - agentic scope node "Blue".
  2. A new node representing a server - transportation node "Teal"
  3. New edges:
    - from Agent to server
    - from server to LLM
    - From LLM to server
    - from server to agent


### 2. Similarly a tool call Span such as openinference.instrumentation.claude_agent_sdk.{tool_name}
represent a call to a tool from which we can infer the following : 
  1. a new node representing the tool (target) - Agentic scope node "Blue"
  2. A new node representing a transportation node "Teal" (e.g. server)
  3. New edges:
    - from the agent tool call (source) to server
    - from server to the tool itself (target)
    - From the tool itself (target) to server
    - from server to the agent tool call (source)

### 3. "llm.output_messages.0.message.tool_calls.0.tool_call. function.arguments": "{\"action\": \"store\", \"name\": \"keywords.2.txt\", ..}"
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

### 4. Inferred agent node. 
this case happens (for anthropic) When the framework emits only bare leaf LLM spans — no run/agent/wrapper span. The goal here is to infer an agent node by observing that all LLM spans share a single shared parent, in the transportation scope. Example:
POST /                
 ├─ messages.create   
 └─ messages.create   
Inferred nodes:
  1. A new node representing the agent 
  2. new inferred edges:
    -. from the transportation (POST) span/node to the agent node
    -. from the agent node to each one of the LLM spans ()
  3. Disconnect the edges but maintain reference from the newly created edges to the original ones 
  
  

### Notes & timing

Additional cases may exist which need to be implemented such as tools inferred from input attributes.

Inferred edges ordering/timing:
When inferring new edges (interactions) make sure to adjust the order based on the execution order. examples: 
- outgoing edges (calls) are before incoming edges (responses)
- When tools are derived from LLM spans:
    - The edge between the LLM and the tool call is before the edge between the tool call and the tool itself
    - the edge between the tool and the tool itself is before the edge between the tool and the tool call (reverse edge)
    - Tool interactions derived from input attributes should happen before interactions with the LLM
    - tool interactions derived from output attributes should happen after interactions with the LLM



## Step 2.d - merge (execution graph)

this step identifies identical interactions - cases where a single interaction is represented more than once in the execution graph.
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

example II: assume we have a tool called retrieving information from a database (Single invocation of the database) followed by multiple interactions with an LLM. in such a case the tool input will appear in all the following LLM spans and may result in multiple inferred database tool calls.
since all those inferred nodes represent a single call to the database - They should be merged.

example III: Assume we have an agent with a call site (e.g. a tool call) whose callee was inferred (source Blue -> inferred Teal server -> inferred Blue callee). Assume that the *same* call-site span is also the root of an observed transport chain (Teal) that runs through transport nodes until it reaches observed agentic (Blue) nodes.
In that case the inferred server/callee and the observed transport chain/agentic node are the *same* interaction: the call made was actually served by the observed downstream agent.
When merging, the inferred server and callee nodes are collapsed into the observed transport chain and agentic (Blue) nodes. The result is a single interaction from the call site to the observed downstream agent (e.g. agent -> agent). For this case, a shared root node is a sufficient signal on its own.

The process of Merging is a set of heuristics - asserting the same exact processing is observed - and can be based on:
- proximity in the trace
- Same tool name 
- same exact execution time
- Same input argument and output result 
- whether the node or edge are inferred or observed in conjunction with the source spans 
- nodes from the same scope
- same root node

Timing notes:
when merging edges account for the timing of each of the edges and maintain the time of the appropriate Span. for example, After a tool call its input may be repeated several times in following spans. in this case the timing of this is interaction should be after the span creating the tool call.

When collapsing the inferred and observed nodes, the result should maintain the observed timestamps. For example, The response from example III should be anchored in the observed response nodes.



# Step 3 - entity graph 
In this step we create the entity graph, based on the execution graph,representing agentic entities and interactions
The agentic entity graph is going to be used for two things
1. Identify the entities
2. Identify the interactions 

The entity graph is going to be constructed in as follows:

## Step 3.a - Creating the Entity graph nodes


1. structurally:
  Consider the Blue and Teal nodes in the execution flow graph.

  First we are going to create subgraphs of execution graph nodes by simply dropping teal nodes. 
  Each subgraph can contain inferred nodes, observed nodes or both - But these can only be Blue or White. 

  Next, for all nodes in each sub graph represented by connected Blue and White nodes create a group.
  (Groups without blue can be ignored)

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

## Step 3.b - Creating the Entity graph edges

1. Structurally:
  - edges internal to a group are ignored
  - Each path connecting entity nodes should be represented by a single interaction connecting these entity nodes. In other words: each Teal transport chain between two Blue components becomes a single interaction (a bidirectional pair: call edge and respose edge)
  - A path may exist where only a single entity node is observed at either the start or the end of the path. In other words, There is a teal transport chain between a blue component without a blue component on the other side.
  In such a case create a single interaction (a bidirectional pair: call edge and respose edge) between the blue component and a terminal entity node.
  
 

2. Timing (absolute timestamps):
The goal in this step is to assign each interaction (call, response) an absolute started_at / ended_at. These are taken from the interaction's anchor span.

Anchor on the *observed* endpoint's span. If both endpoints are inferred, anchor on the observed span that derived them (the originating agentic span). 


## Step 3.c - infer (entity graph) 


## Step 3.d - merge (entity graph)

In this we aim to merge terminal entity nodes with entity nodes.
 
1. consider  two entities A and B, e.g. agents.
   We can consider two patterns:
   - Request / result — A calls B and control returns to A:
       A ──▶ B ──▶ A ──▶ ... 
   - Handoff — A passes control to B and does not get it back:
       A ──▶ B ──▶ ...   (B may call A, but as a new call, not a return)
       
   Consider the following observed edges:
     A ──▶ B ──▶ T (Terminal)
   Iff all conditions hold:
      - A's call-site span is an ancestor of T's chain (request/response)
      - A, B and T are adjacent (No nodes in between)
   merge the terminal node with node A (While keeping the interactions distinct)




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

