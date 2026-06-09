## Overview
This document should only be changed With explicit human permission  

This document describes the algorithm of p_interactions Walking on top of standard OTEL spans as well as assumptions made 

### The algorithm From 10,000 feet 
The goal of the algorithm is to identify *agentic* entities and their interactions. 
Entities may include tools, agents, llm calls etc. Some of these may be mapped to kubernetes entities such as services while others maybe implemented inside a single container (e.g. internal tool) 

The input to the algorithm are events (e.g. Otel spans arriving from different standard scopes such as openinference, a2a, etc.).
The output are entities and interactions.


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

### Details

The algorithm begins by creating a common graph for spans. next it handles each scope separately accounting for its semantics. Since we are looking for agentic semantics - the a2a and openinference scope are most important. other scopes are used for enrichment.

#### Step 1 - Base graph

the algorithm construct a node for each span.
in addition it constructs an edge from its parent node to itself.
This graph should reflect the traceparent span connectivity.

this is the base graph, were nodes and edges are "white" 


#### Step 2 - Agentic scope

The algorithm begins by handling the agentic scope, Specifically open inference spans.

based on this scope we will build the agentic graph.

All other scopes will be used to update and enrich the graph.
deferred to later steps.




#### Definitions:
1. Event node representing a local entity
2. Source / Target nodes representing a local and remote entity.
3. A white edge representing parent child relationship based on trace parent.
4. A Gray edge representing the order of events - The (grand-)parent child considerin agentic scoped events.
5. A Black edge representing a source/target across agentic entities/ components/ Containers


Some agentic scope spans represent a source (client) or a target (server) (or both) of agentic protocols
for example: 
TODO: chhange! - micha
- A source and target span: openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query - A span covers the agentic conversation including input and output.



#### Step 2.a - Enrich agentic nodes
in this step we enrich the base graph with agentic semantics:

Requires: openinference_telemetry_spans.md 

1. Traverse the base graph, And identify all nodes related to the agentic scope. 
2. Mark each agentic scope node as "Gray" 
3. connect consecutive "Gray" nodes with "Gray" edges. Essentially if there is a path (of white edges) between two Gray nodes (Without going through a Gray node in between) Create a Gray edge.
4. Identify Gray nodes *representing a abentic boundary* and color them "Black". A boundary is node source *calling* an agent a tool an LLM or another service, or a *target* of such a call
5. If there is a Gray edge between Black nodes - Color the edge black.

#### Step 2.b - handling Special cases 
In some cases agentic spans may represent both the source and the target. For example:
openinference.instrumentation.claude_agent_sdk.ClaudeAgentSDK.query
This span represents both the source and the target as it includes both the information outgoing and ingoing.

For these cases we will modify the graph.
When such a "black" node is encountered We will create an additional black node pointing To the same span.
The original black node will represent The source entity while the new black node will represent the target entity
Two black edges will need to be added from the source entity to the target entity and back.

#### Step 2.c - Handling missing spans / call targets
in some cases spans may be missing from the execution flow - This may be due to a Bug, incorrect Otel instrumentation or just missing instrumentation.

we can detect some of those cases. specifically an agentic source without a target or vice versa. Whenever we detect a black node without black edges we basically miss some instrumentation.

For every black node without black edges we should create a new black node an unobserved, synthetic peer - pointing to the same span - representing the target or source. Then we should add black edge from the source to the target and from the target to the source as appropriate.


#### Step 3 - agentic entity Graph

In this step we are going to create a new graph representing agentic entities and interactions 


The agentic entity graph is going to be used for two things
1. Identify the entities
2. Identify the interactions 

#### Step 3.a - Creating the graph
Consider the Gray and black nodes and edges in the base graph.
The black edges represent connections between entities 

First we are going to create subgraphs by simply ignoring the black edges.  
Next, each sub graph represented by connected Gray and black nodes will become a new node in the entity graph
The nodes (entities) the in the new graph are connected with new edges matching the black edges 

#### Step 3.b - Merging of identical unobserved peers
note: this step must be performed on separate subgraphs

In the previous step an unobserved peer was created as a target  node when an explicit node and Edge were missing.

In this step we compare the unobserved nodes and identify similar ones based on the source span attributes. For example: tool name.
Next we Merge all identical unobserved nodes Keeping the same attributes. 

Important: We want to preserve all interactions - for this reason all edges are kept - the edge source or target -  the original-unobserved peer node is replaced with an observed peer merged node. 

#### Step 3.c - Naming nodes

Goal: Each node in the entity graph should be given a key. 

This key should reflect the original subgraph and may be from one of the subgraph node spans. in particular if we can identify a node in the subgraph containing the host name we should use it as an key 
If the key is not clear we can call it unknown.



### Step 4
Deffered

- And verify and handle synthetic peers Generated from A span even though the subgraph exists
- Align names across different executions

#### Guide
- All attributes used in the code should be validated. The otel-span-table can generate a table with all the span attributes given URL .
- development and implementation of each scope should be separate




