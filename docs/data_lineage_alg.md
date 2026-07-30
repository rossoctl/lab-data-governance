# Overview
This document should only be changed With explicit human permission  

Data lineage is the end-to-end trace of how upstream data (sources) flow through entities/processes possibly transformed to produce downstream data (outputs), showing the source → transformation → output relationships over time.


The two main questions:
    Where did the data Originate? - data source 
	data movement and transformations? - What processes, Entities, data stores did the data go through and which transformations (masking, redaction, summarization) were applied...  

# Definitions:

Intra-Trace lineage - Computing the lineage within a trace
Inter-Trace Lineage - computing the lineage across traces (- information flows Through a shared persistent storage)


We have three types of components:
1. Data Sources - The origin - Information flowing out should be tagged and/or classified 
2. Entities with session/transient memory and/or persistent store (e.g. Agent) 
    transient/session memory and persistent storage behave the same during the execution trace.
	The difference is the assumption that session/transient memory does not survive between two executions - Which will impact inter trace lineage/.
3. data target

# Entity Taxonomy

we are going to assume a table will exists (after analysis) that identifies all the entities in the system and sets relevant properties including persistent storage, source and target
Reading from the table is deferred

Until then, and to support cases where entities have not been analyzed and don't appear in the table - we will use the following defaults:

| entity | source | target | location (internal or external) |
| --- | --- | --- | --- |
| LLM | ✗ | ✗ | external |
| tool | ✓ | ✓ | external |
| agent | ✗ | ✗ | internal |

# Design Elements	

## transformation 
transformation is a finite enumeration, It can include: Anonymization, Summarization etc.
to do with a human: work on this list
	

## Lineage metadata
Lineage metadata includes:
1. Sources - the list of Data sources
2. Transformations - a map between data source and a set of transformations (order doesn't matter)
3. Entities - the set of entities - through which entities the data passed through
		Note: this is unordered. In case an order is needed - it will need to be derived from the trace using an API.
		Note: The set may include also sources - As a source may also be an entity where data passed 

## Semantic matching

The purpose of semantic matching is to analyze two payloads (e.g., Input and output) to identify if there is identical content or semantics. This is a basic block on top which lineage can be implemented

match(payload_a, payload_b) → {matched: bool, transformation: enum, ...evidence}.
1. matched 
	- True - indicating the payloads relates to the input (and we believe there is lineage)
	- False - Indicating there is no relationship (and we believe there is no lineage)
2. In case matched is true - it should return the transformation performed 
	- null - If no transform was performed or none was identified
	- summarization - if the summary was performed and semantics is still intact
	- anonymization - if elements were removed Or anonymized removing the ability to relate information to a specific person

A default implementation of match - Returning to always without an analysis: 
simple_match(payload_a, payload_b)  → { true, null, null }
this allows for improvements over time, no need to change in run time

## Lineage processing operations:
At the first stage lineage processing will be performed intra trace 
inter trace lineage is postponed.

traversing a trace interactions from the beginning will allow us to compute the lineage 
following add a few basic operations which we will connect later

in case payload(s) are missing - truncates the trace at the first absent payload and mark it partial

1. The starting point of a payload, and it's lineage (e.g. read)
	- init_lineage( entity: string ) -> metadata
	  in this case The metadata is trivial:
		1. Sources is assigned the entity 
		2. transformations, set a key - entity, with an empty set of transformations as value
		3. entities - a new empty set


2. on process, we may address multiple cases - we may have a single or multiple payloads 
	Following is a generic approach considering multiple sources as well as the payloads:
		- merge_lineage( payload_a: string, metadata_a: object
				 		 payload_b: string, metadata_b: object
						 ...
				 		 output_payload: string,
				 		 entity_name: string,
						 is_entity_source: bool ) -> output Metadata
			a. The idea here is to call the matching function with every source payload and output payload (e.g. payload_a,output_payload; payload_b,output_payload, ..)
			The result of these calls will inform the construction of output metadata
			b. if is_entity_source == true, It should be considered as an additional source

			Construction of output metadata:
			- Match returns false for a specific (payload, output_payload) - it indicates there is no lineage. And thereforet the payloads metadata can be ignored.

			1. In case Match returns false for *all* payloads - There is no lineage.
				This may be the case if we anonymize payloads. or if based on an IDs we read record:
				1. call init lineage, the entity is the source 
						<!-- (ignoring is_entity_source, intuition: If is_entity_source = false - There should be no meaningful output	otherwise, It's a new starting point ) -->
			
			2. In case Match returns true for *one or more* payloads (and possibly provides transformation). assume payloads are A,B,.. 
				1. Sources are assigend the union of the sources:  A ∪ B ∪ ...
				   if is_entity_source == true:  A ∪ B ∪ ... ∪ entity.
				2. merge the keys from the metadata maps (A,B,.. ) into a new map, merge the      	  
				   transformation sets in case a key appears twice
				   Also, add entity, with empty transformation set, to the transformation sets
				3. the sets of entities are merged, and extended with the entity.


		Examples:

			Example 1 - assume there are two payloads and match returns 
				false for payload_a,output payload (no payload_a --> output payload lineage). 
				true for payload_b, output payload and summarize as transformation 
							(there is lineage, Summarization was performed on payload_b).
				entity is target (is_entity_source = false)
			  in such a case the output metadata Will include:
				1. Sources assigned b sources only
				2. copy Each entry (key, value) from metadata_b, add summarization to each set and to the output metadata map
				3. the set of entities copied from metadata_b
				Note: no need to add entity to transformation or entities- its not a source


			Example 2 - assume there are three payloads as input and match returns
				false for payload A,output_payload
				true for payload B,output_payload and anonymize
				true for payload C,output_payload and summarize
				entity is source (is_entity_source = true)
			  in such a case the output metadata Will include:
				1. The data sources of the output metadata from both metadata B and C (union)
				   as well as the entity.
				2. For each data source from B create copy the Transformation set, add anonymization, and store in the new map. Next For each data source from C create copy the Transformation set, add Summarization, and store in the the map - either by adding a new data source key or merging the transformation set with the existing one. Also, add entity, with empty transformation set, to the transformation sets.
				3. Merge the entities from metadata B and C and extend with entity_name
			
			Example 3 - assume there are two payloads as input and match returns
				false for payload A,output_payload
				false for payload B,output_payload
				// entity is source (false or true)
			  in such a case
			  	call init lineage, the entity is the source
				
				


## trace interaction lineage
Given these basic lineage operations we can map interactions In a trace to those operations 
This is necessary to compute the lineage Of the payloads in the trace

consider the following examples (arrows identify interactions and payloads, numbers represent the interaction sequence order and nodes represent entities)

1.	Assume The following interaction: 
		user -1-> LLM
	Use init_lineage to compute the lineage of #1

2.	Assume Two interactions: 
		-1-> Tool -2->
	use merge_lineage To compute the lineage of #2

3.	Assume The following interactions
		-1-> Agent
		     Agent -2-> LLM
		     Agent <-3- LLM
		     Agent -4-> LLM
		     Agent <-5- LLM
		<-6- Agent
	Use merge_lineage (1, 2) -> #2
	Use merge_lineage (2, 3) -> #3
	Use merge lineage (1, 3, 4) -> #4 (Since the agent has memory)
	Use merge_lineage (4, 5) -> #5
	Use merge lineage (1, 3, 5, 6) -> #6 (Since the agent has memory)
		
	*Assume agent has transient/session memory, tools and LLM do not.*

in summary:
lineage[i] = for each interaction i in seq order:
  if input payload has no lineage:    					
  	 init_lineage(entity) 	// E.g. the caller entity is outside the trace / a source.
  else:
	payloads = payloads i's entity received earlier (requests handed + responses returned)
										 // since we assume transient memory
  	merge_lineage(payloads, entity, is_entity_source)    



## Lineage result

1. step I - Intra-Trace lineage - 
		Assumptions: 
			A payload exists on each interaction, 
			Identification of entities with storage
		Output:
			lineage metadata for each ineraction leg

2. step II - Inter-Trace lineage - 
		Assumptions:
			One trace writes to a shared persistent storage while the other reads 
			A payload or metadata exists On each interaction
		Output:
			Same as above			
 
	Step two is currently deferred


	"outputs":
	an API - Given execution flow interactions, we can easily compute the trace lineage - And provide lineage metadata for which interaction/payload in the trace 

	Tables - Addressing the What are the data sources (without the need to recompute everything)
	- Map from interaction/payload -> lineage metadata 

	Defferred:
	- Map payloads (hash) -> persisting entity (e.g file sysytem tool)
	

## deferred issues
- how to handle interactions with no payloads (not captured, not arrived, missing or genuinely empty)
- How to handle entity persistency - And, handle it as keyed or blob
