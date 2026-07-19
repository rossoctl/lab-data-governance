Only humans should edit this file

travel_agent_II - e8f7f7c4d7b35e5aa4fbdaaae2a90f75 - 
	-. user asks asks agent to book trip to Japan
	-. agent calls LLM
	-. (step 1) agent calls search destinations (tool)
	-. agent calls LLM
 	-. (step 2) agent calls get weather (tool)
	-. agent calls LLM
 	-. (step 3) agent calls get flights (tool)
	-. agent calls LLM
 	-. (step 4) agent calls delegate to research agent (tool)
		-. Research agent (tool) Calls to research agent
		-. Research agent calls LLM
	-. agent calls LLM
 	-. (step 5) agent calls Booking agent (tool)
		-. Booking agent (tool) Calls to booking agent
		-. Booking agent calls LLM
			-. Booking agent calls Create Booking tool // error - But this is the trace, this call is not a sibling of the LLM call instead it's a child..
		-. Booking agent calls LLM
	-. agent calls LLM (LLM returns: request is approved)
 	-. (step 6) agent calls Booking agent (tool)
		-. Booking agent calls LLM
			-. Booking agent Calls to Create booking tool // error as above
				-. Create Booking Tool agent calls payment agent // error as above
					-. Payment Agent calls LLM
					-. payment agent calls Get Payment Info tool
					-. Payment Agent calls LLM
					-. payment agent calls Charge card tool
					-. Payment Agent calls LLM
		-. Booking agent calls LLM
	-. agent calls LLM
Summary 
	agents:
		Travel agent 
		Research agent <
		Booking agent 	
		payment agent  	 
	Tools:
		Search Destinations 
		get weather
		Get flights
		Delegate to research agent tool
		Delegate to booking agent tool
		Create booking tool
		Get payment info
		charge card
	llm
		Claude Haiko
