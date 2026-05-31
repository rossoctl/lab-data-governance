## Overview
This document describes the algorithm of p_interactions as well as assumptions made 

### The algorithm From 10,000 feet 
The goal of the algorithm is to identify agentic entities and their interactions. 
Entities may include tools, agents, llm calls etc. Some of these may be mapped to kubernetes entities such as services while others maybe implemented inside a single container (e.g. internal tool) 

The input to the algorithm are events (e.g. Otel spans).
The output are entities and interactions.

### Observations
when monitoring protocol events, We should expect to see - Assuming all events are received - A send event from one entity and a matching receiving event from another entity.
Importantly, we expect to see these two events to be *consecutive* in the trace (Otherwise we should be able to identify and flag the semantics of the event in between)

We may be receiving events for multiple sources such as a2a and httpx, and - assuming tracesparent is on - those events will be interleaved. For example:
a2a tool call -> http send -> ... -> http recieve -> a2a call recieve. Importantly both a2a call and http send events represent the same entity While both receive events represent another entity. 

In case a component does not produce any events our trace will be broken. We will only have the one side of the interaction. The graph will be split.
Another case may be where only partial sources of events are emmiting events in a given component. For example a receiving side may not have a2a events resulting in:
a2a tool call -> http send -> ... -> http recieve | 
Note that in such a case traceparent will not be forwarded resulting in two traces and a split graph

Lastly note that all events between receive and call essentially belong to the same entity.

### Details

The algorithm handles each scope separately constructing multiple graphs (can be viewed as layered).
For each scope, it builds a graph, identifies nodes and edges and proceeds to merge and enrich nodes (based on event semantics)
Next, The algorithm attempts to merge different graphs across the layers into a single graph. Graph mergers can be performed based on event semantics 

#### Step 1.a
For each scope (A scope is the source type of the event - e.g. otel.httpx, oi.openai_agents)
- Identify all events related to a scope (in order)
- Build a graph Where We have 
    - Two types of nodes:
        1. Event node representing a local entity
        2. Source / Target nodes representing a local and remote entity.
    - Two types of edges:
        1. A Gray edge representing the order of events
        2. A Black edge representing a call across entities/ components/Containers
  
- Building process: For each event, based on the event semantics 
    - Non call event:
        1. create an event node representing this event 
        2. Add a Gray edge between the previous node and this node 
    - Call Event: 
        1. create two Entity nodes One represents the local entity and the other the remote entity 
        2. Add a Gray between the previous node in the local node.
        3. add a black edge between the local node and the remote node 

    - Receive Event: 
        1. Create two Entity nodes One represents the local entity and the other the remote entity (The caller, e.g. the client)
        2. Add a Gray between the previous node in th trace and the local node.
        3. Add a black edge fron the remote node to the local node.

note that if we have a client server call we will generate 4 nodes and two edges. We will generate two (Entity) nodes and a single (Black) edge on the caller side and the same at the receiver side. 

#### Step 1.b

we now have a graph of nodes and edges. Our goal is to merge nodes representing the same entity and edges

Addressing non call events nodes:
All Consecutive Non call or receive Nodes (Connected with Gray edges) can be merged into a single node.

Addressing duplicate call/recieve nodes: 
In the simplest case we should see the following:
-> [call site] send => [call site, remote] recieve -> [recive site, remoe] call => [recieve site] recieve

-> denotes Gray edge\
=> denotes Black edge

In such a case we should be able to merge the the call nodes, the receive nodes, and black edges (While omitting Gray edges). In addition using the attributes we may be able to enrich both call and receive nodes (For for example: call site remote URL maybe used to enrich the receive node entity name)

Note: We should identify events between the receive and call and handle them specifically. I don't expect many of those events.


#### Step 2


- We are now going to merge nodes across layered graphs to match entities:


#### Decisions
- On an event describing an edge - two nodes will be created: local and remote


