Write all the decisions that have been made so far to `data-governance/docs/decisions/HISTORY.md` in a form of:
  ```
  - D<decision number>:
    Questions:
      - Question: <the original question>
        Options:
          A: <option A>
          B: <option B>
          ...
        Recommendation: <recommended option>
        Explanation: <recommendation explanation>
      - Question: <a follow-up question (optional)>
        Options:
          A: <option A>
          B: <option B>
          ...
        Recommendation: <recommended option>
        Explanation: <recommendation explanation>
      - ...

    Answer: <my original answer>
    Decision: <a decision made based on the answer - your interpretation>
    Notes: <additional notes>
  ```
Append every future decision made to the file in the described form as we go.


Read decision history in `data-governance/docs/decisions/HISTORY.md` and produce a concise decision list, writting it in `data-governance/docs/decisions/LIST.md` in the following format:
```
- D<decision number>:
  Decision: <a decision text>
  Notes: <optional additional notes>
```

/grill-me-with-docs Here are my thoughts on the prototype:
  1. Interactions and entities creation should be based on a streaming of spans by seq.
  2. Ideally, each span should only belong to one interaction and only if it adds value to that interation or entities it connects. By value I mean additional information.
  3. Let's assume, that this process can add entities and interations then update them when required. This means that there can be incomplete interactions (e.g. lacking source or destination entity, other information such as payload and related spans in `interaction_spans`)
  4. Let's also assume, that this process does not need to merge two incomplete interactions, unless we explicitely encounter this situation.
  5. An entity can be created when we see a span with service name that does not match any existing entity name. Each entity should have a nullable `project_name` property extracted from span's `openinference.project.name` if available. This property defines a prefix, which should
  be removed from the service name it present making the `entity.name` = `span.service_name` - `openinference.project.name`. Let's call it a canonical service name. It can be calculated for evary span.
  6. An interaction can be created when a span Sn arrives and it has a parent span Sp so that Sp's canonical service name is not equal to Sn's canonical service name. Both spans should be linked to the newly created interaction. They both should be marked as anchor spans.
  7. `interaction_spans` should have additional boolean fields: `is_connector_only` and `adds_info` - to designate connector only and information adding spans.
  8. Anchor spans are not connector only spans, as they add information to an interaction about entities it connects



@agent-github-issue-tdd-implementer We are working on `data-governance` project. Proceed with issue #5, but only if it has no blockers.

/goal Proceed with the first open github issue assined to `too-far-away`, but only if such an issue exists, labeled `ready-for-agent` and has no blockers. After it has been implemented use a subagent to review the created PR and add should-fix findings as a comment to the PR. Make the required fixes and repeat the review-fix process until no should-fix findings are left. Then merge the PR.
