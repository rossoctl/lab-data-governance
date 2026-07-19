This file should be edited by humans only


8ae1f64d4bb51b750168c6ef1e11a2d8 - Travel agent III (Old version)

Very simple trace,
	agent calls LLM
	agent calls tool search destination
	agent calls LLM
	agent calls tool get weather
	agent calls LLM
	agent calls tool get flights
	agent calls LLM
	agent calls tool get flights (Again)
	agent calls LLM


	entities:
		agent:travel-advisor
		llm:claude-haiku-4-5-20251001
		tool:get_flights
		tool:get_weather
		Self distinction tool:search_destinations
