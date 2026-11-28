window.LINES = [
 {
  "n": 1,
  "beat": "title",
  "text": "DAM runs AI agents for the long haul.",
  "start": 0.4,
  "end": 3.3,
  "hold": 0.0
 },
 {
  "n": 2,
  "beat": "arch",
  "text": "This is how DAM works at the moment.",
  "start": 3.9,
  "end": 6.325,
  "hold": 0.0
 },
 {
  "n": 3,
  "beat": "arch",
  "text": "Each agent works in its own secure micro-VM, and everything it does goes through a gateway that holds its credentials and enforces its owner's network rules.",
  "start": 6.625,
  "end": 17.075,
  "hold": 1.0
 },
 {
  "n": 4,
  "beat": "keycloak",
  "text": "Our demo has two people, who sign in to DAM through its Keycloak.",
  "start": 18.675,
  "end": 22.9,
  "hold": 0.0
 },
 {
  "n": 5,
  "beat": "keycloak",
  "text": "Alice Mayer is a developer.",
  "start": 23.2,
  "end": 25.45,
  "hold": 1.0
 },
 {
  "n": 6,
  "beat": "keycloak",
  "text": "Bob Segal is a tester.",
  "start": 26.75,
  "end": 28.75,
  "hold": 1.0
 },
 {
  "n": 7,
  "beat": "cast",
  "text": "They share two agents. Alice owns them, and Bob is an editor on both.",
  "start": 30.35,
  "end": 35.075,
  "hold": 0.0
 },
 {
  "n": 8,
  "beat": "cast",
  "text": "The first, issue-workbench, is where they work on issues together.",
  "start": 35.375,
  "end": 39.575,
  "hold": 0.0
 },
 {
  "n": 9,
  "beat": "desc-w",
  "text": "Its description reads: Helps the team work on issues in the team's demo repositories. It reads and summarizes issues, posts comments, and hands an issue back and forth between the developer and the tester with its labels, ready for testing or needs a fix. It never closes issues.",
  "start": 40.175,
  "end": 58.65,
  "hold": 0.0
 },
 {
  "n": 10,
  "beat": "cast",
  "text": "So Alice fixes an issue and hands it to Bob for testing, and Bob sends it back if it still fails.",
  "start": 59.25,
  "end": 65.6,
  "hold": 0.0
 },
 {
  "n": 11,
  "beat": "cast",
  "text": "The second, qa-signoff, is for the final review.",
  "start": 65.9,
  "end": 69.45,
  "hold": 0.0
 },
 {
  "n": 12,
  "beat": "desc-q",
  "text": "Its description reads: Final QA review. It reads an issue and its history, posts the sign-off comment, and closes the issue as completed, or as not planned. It never relabels issues and never changes what an issue says.",
  "start": 70.05,
  "end": 84.95,
  "hold": 0.0
 },
 {
  "n": 13,
  "beat": "cast",
  "text": "So once a fix is verified, the tester signs it off and closes the issue there.",
  "start": 85.55,
  "end": 90.65,
  "hold": 0.5
 },
 {
  "n": 14,
  "beat": "login",
  "text": "Alice and Bob each sign in to DAM. From here on, we follow them side by side.",
  "start": 91.75,
  "end": 97.125,
  "hold": 1.0
 },
 {
  "n": 15,
  "beat": "intro",
  "text": "In DAM, each agent shows its description, and Bob, added to its people as an editor.",
  "start": 98.725,
  "end": 104.625,
  "hold": 1.0
 },
 {
  "n": 16,
  "beat": "act1",
  "text": "Act one: DAM as it is, with no Sentry.",
  "start": 106.225,
  "end": 109.325,
  "hold": 0.0
 },
 {
  "n": 17,
  "beat": "a1-handover",
  "text": "Alice asks the workbench, using the GitHub tool, on issue one: comment that the fix is in PR twelve, ready for testing, and label the issue ready for testing.",
  "start": 109.925,
  "end": 120.575,
  "hold": 0.1
 },
 {
  "n": 18,
  "beat": "a1-handover",
  "text": "Both calls go through, as they should.",
  "start": 120.975,
  "end": 123.5,
  "hold": 1.5
 },
 {
  "n": 19,
  "beat": "a1-sendback",
  "text": "Bob tests it, and it still fails on Safari seventeen. So Bob asks the workbench to comment just that, and to label the issue needs a fix.",
  "start": 125.6,
  "end": 134.975,
  "hold": 0.4
 },
 {
  "n": 20,
  "beat": "a1-sendback",
  "text": "That works too: this back and forth is what the workbench is for.",
  "start": 135.675,
  "end": 139.825,
  "hold": 1.5
 },
 {
  "n": 21,
  "beat": "a1-closehere",
  "text": "Alice pushes another fix, and this time it works. So Bob asks the workbench to close the issue as completed, because it works now.",
  "start": 141.925,
  "end": 150.475,
  "hold": 0.0
 },
 {
  "n": 22,
  "beat": "a1-closehere",
  "text": "It closes, but it is marked yellow: Bob used the wrong agent. The workbench is for the back and forth between developer and tester, and its description says it never closes issues. The close belongs in qa-signoff.",
  "start": 150.775,
  "end": 164.75,
  "hold": 1.5
 },
 {
  "n": 23,
  "beat": "reopen",
  "text": "For the sake of the demo, we reopen the issue after each close, so we can try each way of closing it.",
  "start": 166.85,
  "end": 173.075,
  "hold": 0.0
 },
 {
  "n": 24,
  "beat": "a1-close",
  "text": "Next, Alice asks qa-signoff to close the issue as completed.",
  "start": 173.675,
  "end": 178.275,
  "hold": 2.4
 },
 {
  "n": 25,
  "beat": "a1-close",
  "text": "It closes, and it is yellow again. Alice is the developer, and proper procedure has the tester verify a fix and close the issue, not the developer who made it.",
  "start": 180.975,
  "end": 191.25,
  "hold": 1.5
 },
 {
  "n": 26,
  "beat": "a1-signoff",
  "text": "Then Bob asks qa-signoff, on issue one, to comment: sign-off, verified; Alice resolved it; for any questions, reach Alice at her email. And then to close the issue.",
  "start": 193.35,
  "end": 204.475,
  "hold": 2.9
 },
 {
  "n": 27,
  "beat": "a1-signoff",
  "text": "It is posted to a public GitHub issue, personal data and all. Yellow: this data should not leave the company.",
  "start": 207.675,
  "end": 215.0,
  "hold": 1.5
 },
 {
  "n": 28,
  "beat": "act1end",
  "text": "Every request went through. There are no restrictions on agent activity, either by intent or by security considerations.",
  "start": 217.1,
  "end": 225.225,
  "hold": 0.5
 },
 {
  "n": 29,
  "beat": "plug",
  "text": "This is where Sentry comes in.",
  "start": 226.325,
  "end": 228.625,
  "hold": 0.0
 },
 {
  "n": 30,
  "beat": "plug",
  "text": "We've added an MCP policy enforcement point to DAM's gateway: every MCP tool call an agent makes is checked there, before its credential is attached.",
  "start": 228.925,
  "end": 239.3,
  "hold": 0.0
 },
 {
  "n": 31,
  "beat": "plug",
  "text": "Sentry's capabilities cover both the agent's intent and the company's policy, for access control and for data governance.",
  "start": 239.6,
  "end": 247.7,
  "hold": 0.0
 },
 {
  "n": 32,
  "beat": "plug",
  "text": "We first turn Sentry on without any company policy in place, and see the effect.",
  "start": 248.0,
  "end": 253.175,
  "hold": 0.0
 },
 {
  "n": 33,
  "beat": "plug",
  "text": "Alice turns Sentry on for both agents. Each gateway restarts, with the policy check in place.",
  "start": 253.475,
  "end": 259.8,
  "hold": 0.0
 },
 {
  "n": 34,
  "beat": "onboard",
  "text": "Sentry reads each agent's description and decides its operations, down to what a single tool does.",
  "start": 260.4,
  "end": 266.975,
  "hold": 0.0
 },
 {
  "n": 35,
  "beat": "onboard",
  "text": "The workbench may comment and relabel, but not close. qa-signoff may comment and close, but not relabel.",
  "start": 267.275,
  "end": 274.55,
  "hold": 0.0
 },
 {
  "n": 36,
  "beat": "onboard",
  "text": "Sentry writes each agent's rules as Rego, which OPA evaluates on every call, before the call leaves the gateway.",
  "start": 274.85,
  "end": 282.825,
  "hold": 1.5
 },
 {
  "n": 37,
  "beat": "act2",
  "text": "Act two: Sentry, from each agent's description, starting again from the open issue.",
  "start": 284.925,
  "end": 290.55,
  "hold": 0.0
 },
 {
  "n": 38,
  "beat": "a2-handover",
  "text": "Alice asks for the same hand-over: comment that the fix is in PR twelve, and label the issue ready for testing.",
  "start": 291.15,
  "end": 298.625,
  "hold": 3.3
 },
 {
  "n": 39,
  "beat": "a2-handover",
  "text": "Sentry checks both calls against the workbench's description, and allows them.",
  "start": 302.225,
  "end": 307.1,
  "hold": 1.5
 },
 {
  "n": 40,
  "beat": "a2-sendback",
  "text": "Bob sends the issue back the same way: it still fails on Safari seventeen, so the label is needs a fix.",
  "start": 309.2,
  "end": 316.425,
  "hold": 2.6
 },
 {
  "n": 41,
  "beat": "a2-sendback",
  "text": "That is allowed too.",
  "start": 319.325,
  "end": 321.125,
  "hold": 1.5
 },
 {
  "n": 42,
  "beat": "a2-closehere",
  "text": "Again, after another fix, Bob asks the workbench to close the issue as completed, because it works now.",
  "start": 323.225,
  "end": 330.125,
  "hold": 0.8
 },
 {
  "n": 43,
  "beat": "a2-closehere",
  "text": "This time, it is denied. The reason quotes the agent's own description: it never closes issues.",
  "start": 331.225,
  "end": 337.8,
  "hold": 2.0
 },
 {
  "n": 44,
  "beat": "a2-relabel",
  "text": "Bob then tries to send it back through qa-signoff instead, setting the label back to needs a fix.",
  "start": 340.4,
  "end": 346.675,
  "hold": 1.3
 },
 {
  "n": 45,
  "beat": "a2-relabel",
  "text": "This is denied as well: qa-signoff never relabels issues.",
  "start": 348.275,
  "end": 352.775,
  "hold": 2.0
 },
 {
  "n": 46,
  "beat": "a2-close",
  "text": "Then Alice asks qa-signoff to close the issue as completed.",
  "start": 355.375,
  "end": 359.7,
  "hold": 2.7
 },
 {
  "n": 47,
  "beat": "a2-close",
  "text": "This is allowed, but it is still yellow. Proper procedure has the tester verify a fix and close the issue, not the developer. That is company procedure, and it has to be enforced on the agent.",
  "start": 362.7,
  "end": 374.65,
  "hold": 1.5
 },
 {
  "n": 48,
  "beat": "a2-signoff",
  "text": "With the issue reopened, Bob posts the same sign-off: verified; Alice resolved it; for any questions, reach Alice at her email. And then qa-signoff should close the issue.",
  "start": 376.75,
  "end": 388.025,
  "hold": 2.8
 },
 {
  "n": 49,
  "beat": "a2-signoff",
  "text": "This is allowed too, and still yellow. The agent is only concerned with its own job; data governance and company policy have to be enforced on the agent.",
  "start": 391.125,
  "end": 401.225,
  "hold": 1.5
 },
 {
  "n": 50,
  "beat": "act2end",
  "text": "Now let's address access control and data governance in a company policy that applies to all agents in the system.",
  "start": 403.325,
  "end": 410.975,
  "hold": 0.5
 },
 {
  "n": 51,
  "beat": "policy",
  "text": "An operator writes the company security policy once, in plain language, on DAM's operator page.",
  "start": 412.075,
  "end": 418.975,
  "hold": 0.0
 },
 {
  "n": 52,
  "beat": "policy-text",
  "text": "Developers and testers may read issues, comment on them and label them. Only testers close an issue, and the QA sign-off agent is used by testers only. Personal data and restricted data must not be sent to an untrusted external destination.",
  "start": 419.575,
  "end": 436.475,
  "hold": 0.0
 },
 {
  "n": 53,
  "beat": "split",
  "text": "Sentry takes in the security policy and builds the corresponding rules: access control rules, for developers and testers, and data governance rules, for personal data.",
  "start": 437.075,
  "end": 448.05,
  "hold": 0.0
 },
 {
  "n": 54,
  "beat": "split",
  "text": "On the right, the same rules as OPA evaluates them, in Rego. The roles come from Keycloak, and no agent or gateway restarts.",
  "start": 448.35,
  "end": 457.475,
  "hold": 2.0
 },
 {
  "n": 55,
  "beat": "act3",
  "text": "Act three: Sentry, with the company security policy.",
  "start": 460.075,
  "end": 463.95,
  "hold": 0.0
 },
 {
  "n": 56,
  "beat": "a3-handover",
  "text": "Alice hands the issue over to Bob again: the fix is in PR twelve, ready for testing.",
  "start": 464.55,
  "end": 470.4,
  "hold": 5.0
 },
 {
  "n": 57,
  "beat": "a3-handover",
  "text": "That still works.",
  "start": 475.7,
  "end": 477.4,
  "hold": 1.0
 },
 {
  "n": 58,
  "beat": "a3-sendback",
  "text": "And Bob sends it back: it still fails on Safari seventeen, so the label is needs a fix.",
  "start": 479.0,
  "end": 485.3,
  "hold": 3.5
 },
 {
  "n": 59,
  "beat": "a3-sendback",
  "text": "That works too, as it should.",
  "start": 489.1,
  "end": 491.3,
  "hold": 1.0
 },
 {
  "n": 60,
  "beat": "a3-close",
  "text": "After one more fix, Alice asks qa-signoff to close the issue.",
  "start": 492.9,
  "end": 497.45,
  "hold": 2.5
 },
 {
  "n": 61,
  "beat": "a3-close",
  "text": "This time, it is denied. The user is Alice, a developer, and the company security policy states that only testers close an issue, and that the QA sign-off agent is used by testers only.",
  "start": 500.25,
  "end": 513.075,
  "hold": 2.0
 },
 {
  "n": 62,
  "beat": "a3-signoff-email",
  "text": "Bob verifies the fix and tries the same sign-off: verified; Alice resolved it; for any questions, reach Alice at her email; and then close the issue.",
  "start": 515.675,
  "end": 525.625,
  "hold": 4.1
 },
 {
  "n": 63,
  "beat": "a3-signoff-email",
  "text": "Denied: the company security policy states that personal data must not be sent to an untrusted external destination, such as GitHub.",
  "start": 530.025,
  "end": 539.575,
  "hold": 2.0
 },
 {
  "n": 64,
  "beat": "a3-signoff",
  "text": "Bob writes the sign-off without the personal data: verified on Safari seventeen and eighteen, fixed in PR twelve. And then close the issue.",
  "start": 542.175,
  "end": 551.525,
  "hold": 1.4
 },
 {
  "n": 65,
  "beat": "a3-signoff",
  "text": "Now the comment is posted, and the issue is closed as completed. Green: the right agent, the right user and role, and no personal data.",
  "start": 553.225,
  "end": 562.475,
  "hold": 1.5
 },
 {
  "n": 66,
  "beat": "summary",
  "text": "Here is every request, act by act.",
  "start": 564.575,
  "end": 567.375,
  "hold": 0.0
 },
 {
  "n": 67,
  "beat": "summary",
  "text": "With DAM as it is, every request goes through, the yellow ones included.",
  "start": 567.675,
  "end": 572.4,
  "hold": 0.0
 },
 {
  "n": 68,
  "beat": "summary",
  "text": "With Sentry and each agent's description, each agent keeps to its job.",
  "start": 572.7,
  "end": 577.45,
  "hold": 0.0
 },
 {
  "n": 69,
  "beat": "summary",
  "text": "With the company security policy, the user and role, and the data, are decided too.",
  "start": 577.75,
  "end": 583.325,
  "hold": 2.0
 },
 {
  "n": 70,
  "beat": "close",
  "text": "A call goes through only when the agent's intent and the company's policy both allow it.",
  "start": 586.925,
  "end": 592.475,
  "hold": 0.0
 },
 {
  "n": 71,
  "beat": "close",
  "text": "DAM with granular security: control every tool call, and every piece of data.",
  "start": 592.775,
  "end": 598.475,
  "hold": 0.0
 },
 {
  "n": 72,
  "beat": "close",
  "text": "Sentry, on DAM.",
  "start": 598.775,
  "end": 600.525,
  "hold": 0.0
 }
];
window.WORDS = [
{
"line": 1,
"beat": "title",
"word": "Dam",
"start": 0.7,
"end": 0.95
},
{
"line": 1,
"beat": "title",
"word": "runs",
"start": 0.95,
"end": 1.238
},
{
"line": 1,
"beat": "title",
"word": "AI",
"start": 1.238,
"end": 1.625
},
{
"line": 1,
"beat": "title",
"word": "agents",
"start": 1.625,
"end": 2.0
},
{
"line": 1,
"beat": "title",
"word": "for",
"start": 2.0,
"end": 2.112
},
{
"line": 1,
"beat": "title",
"word": "the",
"start": 2.112,
"end": 2.2
},
{
"line": 1,
"beat": "title",
"word": "long",
"start": 2.2,
"end": 2.475
},
{
"line": 1,
"beat": "title",
"word": "haul",
"start": 2.475,
"end": 3.05
},
{
"line": 2,
"beat": "arch",
"word": "This",
"start": 4.175,
"end": 4.375
},
{
"line": 2,
"beat": "arch",
"word": "is",
"start": 4.375,
"end": 4.5
},
{
"line": 2,
"beat": "arch",
"word": "how",
"start": 4.5,
"end": 4.713
},
{
"line": 2,
"beat": "arch",
"word": "Dam",
"start": 4.713,
"end": 5.05
},
{
"line": 2,
"beat": "arch",
"word": "works",
"start": 5.05,
"end": 5.287
},
{
"line": 2,
"beat": "arch",
"word": "at",
"start": 5.287,
"end": 5.362
},
{
"line": 2,
"beat": "arch",
"word": "the",
"start": 5.362,
"end": 5.45
},
{
"line": 2,
"beat": "arch",
"word": "moment",
"start": 5.45,
"end": 6.075
},
{
"line": 3,
"beat": "arch",
"word": "Each",
"start": 6.925,
"end": 7.212
},
{
"line": 3,
"beat": "arch",
"word": "agent",
"start": 7.212,
"end": 7.675
},
{
"line": 3,
"beat": "arch",
"word": "works",
"start": 7.675,
"end": 8.025
},
{
"line": 3,
"beat": "arch",
"word": "in",
"start": 8.025,
"end": 8.112
},
{
"line": 3,
"beat": "arch",
"word": "its",
"start": 8.112,
"end": 8.312
},
{
"line": 3,
"beat": "arch",
"word": "own",
"start": 8.312,
"end": 8.625
},
{
"line": 3,
"beat": "arch",
"word": "secure",
"start": 8.625,
"end": 9.05
},
{
"line": 3,
"beat": "arch",
"word": "micro",
"start": 9.05,
"end": 9.475
},
{
"line": 3,
"beat": "arch",
"word": "V-M",
"start": 9.475,
"end": 10.275
},
{
"line": 3,
"beat": "arch",
"word": "and",
"start": 10.375,
"end": 10.537
},
{
"line": 3,
"beat": "arch",
"word": "everything",
"start": 10.537,
"end": 11.025
},
{
"line": 3,
"beat": "arch",
"word": "it",
"start": 11.025,
"end": 11.15
},
{
"line": 3,
"beat": "arch",
"word": "does",
"start": 11.15,
"end": 11.55
},
{
"line": 3,
"beat": "arch",
"word": "goes",
"start": 11.55,
"end": 11.85
},
{
"line": 3,
"beat": "arch",
"word": "through",
"start": 11.85,
"end": 12.05
},
{
"line": 3,
"beat": "arch",
"word": "a",
"start": 12.05,
"end": 12.15
},
{
"line": 3,
"beat": "arch",
"word": "gateway",
"start": 12.15,
"end": 12.675
},
{
"line": 3,
"beat": "arch",
"word": "that",
"start": 12.675,
"end": 12.825
},
{
"line": 3,
"beat": "arch",
"word": "holds",
"start": 12.825,
"end": 13.112
},
{
"line": 3,
"beat": "arch",
"word": "its",
"start": 13.112,
"end": 13.25
},
{
"line": 3,
"beat": "arch",
"word": "credentials",
"start": 13.25,
"end": 14.225
},
{
"line": 3,
"beat": "arch",
"word": "and",
"start": 14.225,
"end": 14.362
},
{
"line": 3,
"beat": "arch",
"word": "enforces",
"start": 14.362,
"end": 15.0
},
{
"line": 3,
"beat": "arch",
"word": "its",
"start": 15.0,
"end": 15.212
},
{
"line": 3,
"beat": "arch",
"word": "owner's",
"start": 15.212,
"end": 15.688
},
{
"line": 3,
"beat": "arch",
"word": "network",
"start": 15.688,
"end": 16.1
},
{
"line": 3,
"beat": "arch",
"word": "rules",
"start": 16.1,
"end": 16.825
},
{
"line": 4,
"beat": "keycloak",
"word": "Our",
"start": 18.95,
"end": 19.1
},
{
"line": 4,
"beat": "keycloak",
"word": "demo",
"start": 19.1,
"end": 19.5
},
{
"line": 4,
"beat": "keycloak",
"word": "has",
"start": 19.5,
"end": 19.7
},
{
"line": 4,
"beat": "keycloak",
"word": "two",
"start": 19.7,
"end": 19.9
},
{
"line": 4,
"beat": "keycloak",
"word": "people",
"start": 19.9,
"end": 20.45
},
{
"line": 4,
"beat": "keycloak",
"word": "who",
"start": 20.55,
"end": 20.713
},
{
"line": 4,
"beat": "keycloak",
"word": "sign",
"start": 20.713,
"end": 20.963
},
{
"line": 4,
"beat": "keycloak",
"word": "in",
"start": 20.963,
"end": 21.075
},
{
"line": 4,
"beat": "keycloak",
"word": "to",
"start": 21.075,
"end": 21.2
},
{
"line": 4,
"beat": "keycloak",
"word": "Dam",
"start": 21.2,
"end": 21.5
},
{
"line": 4,
"beat": "keycloak",
"word": "through",
"start": 21.5,
"end": 21.688
},
{
"line": 4,
"beat": "keycloak",
"word": "its",
"start": 21.688,
"end": 21.85
},
{
"line": 4,
"beat": "keycloak",
"word": "Key-cloak",
"start": 21.85,
"end": 22.65
},
{
"line": 5,
"beat": "keycloak",
"word": "Alice",
"start": 23.5,
"end": 23.85
},
{
"line": 5,
"beat": "keycloak",
"word": "Mayer",
"start": 23.85,
"end": 24.225
},
{
"line": 5,
"beat": "keycloak",
"word": "is",
"start": 24.225,
"end": 24.337
},
{
"line": 5,
"beat": "keycloak",
"word": "a",
"start": 24.337,
"end": 24.387
},
{
"line": 5,
"beat": "keycloak",
"word": "developer",
"start": 24.387,
"end": 25.175
},
{
"line": 6,
"beat": "keycloak",
"word": "Bob",
"start": 27.025,
"end": 27.275
},
{
"line": 6,
"beat": "keycloak",
"word": "Segal",
"start": 27.275,
"end": 27.625
},
{
"line": 6,
"beat": "keycloak",
"word": "is",
"start": 27.625,
"end": 27.738
},
{
"line": 6,
"beat": "keycloak",
"word": "a",
"start": 27.738,
"end": 27.8
},
{
"line": 6,
"beat": "keycloak",
"word": "tester",
"start": 27.8,
"end": 28.475
},
{
"line": 7,
"beat": "cast",
"word": "They",
"start": 30.6,
"end": 30.763
},
{
"line": 7,
"beat": "cast",
"word": "share",
"start": 30.763,
"end": 31.038
},
{
"line": 7,
"beat": "cast",
"word": "two",
"start": 31.038,
"end": 31.25
},
{
"line": 7,
"beat": "cast",
"word": "agents",
"start": 31.25,
"end": 32.025
},
{
"line": 7,
"beat": "cast",
"word": "Alice",
"start": 32.188,
"end": 32.525
},
{
"line": 7,
"beat": "cast",
"word": "owns",
"start": 32.525,
"end": 32.85
},
{
"line": 7,
"beat": "cast",
"word": "them",
"start": 32.85,
"end": 33.1
},
{
"line": 7,
"beat": "cast",
"word": "and",
"start": 33.175,
"end": 33.3
},
{
"line": 7,
"beat": "cast",
"word": "Bob",
"start": 33.3,
"end": 33.587
},
{
"line": 7,
"beat": "cast",
"word": "is",
"start": 33.587,
"end": 33.688
},
{
"line": 7,
"beat": "cast",
"word": "an",
"start": 33.688,
"end": 33.775
},
{
"line": 7,
"beat": "cast",
"word": "editor",
"start": 33.775,
"end": 34.138
},
{
"line": 7,
"beat": "cast",
"word": "on",
"start": 34.138,
"end": 34.25
},
{
"line": 7,
"beat": "cast",
"word": "both",
"start": 34.25,
"end": 34.825
},
{
"line": 8,
"beat": "cast",
"word": "The",
"start": 35.625,
"end": 35.737
},
{
"line": 8,
"beat": "cast",
"word": "first",
"start": 35.737,
"end": 36.25
},
{
"line": 8,
"beat": "cast",
"word": "issue",
"start": 36.35,
"end": 36.65
},
{
"line": 8,
"beat": "cast",
"word": "workbench",
"start": 36.65,
"end": 37.35
},
{
"line": 8,
"beat": "cast",
"word": "is",
"start": 37.45,
"end": 37.562
},
{
"line": 8,
"beat": "cast",
"word": "where",
"start": 37.562,
"end": 37.688
},
{
"line": 8,
"beat": "cast",
"word": "they",
"start": 37.688,
"end": 37.825
},
{
"line": 8,
"beat": "cast",
"word": "work",
"start": 37.825,
"end": 38.038
},
{
"line": 8,
"beat": "cast",
"word": "on",
"start": 38.038,
"end": 38.175
},
{
"line": 8,
"beat": "cast",
"word": "issues",
"start": 38.175,
"end": 38.6
},
{
"line": 8,
"beat": "cast",
"word": "together",
"start": 38.6,
"end": 39.3
},
{
"line": 9,
"beat": "desc-w",
"word": "Its",
"start": 40.425,
"end": 40.65
},
{
"line": 9,
"beat": "desc-w",
"word": "description",
"start": 40.65,
"end": 41.25
},
{
"line": 9,
"beat": "desc-w",
"word": "reads",
"start": 41.25,
"end": 41.975
},
{
"line": 9,
"beat": "desc-w",
"word": "Helps",
"start": 42.088,
"end": 42.4
},
{
"line": 9,
"beat": "desc-w",
"word": "the",
"start": 42.4,
"end": 42.513
},
{
"line": 9,
"beat": "desc-w",
"word": "team",
"start": 42.513,
"end": 42.85
},
{
"line": 9,
"beat": "desc-w",
"word": "work",
"start": 42.85,
"end": 43.063
},
{
"line": 9,
"beat": "desc-w",
"word": "on",
"start": 43.063,
"end": 43.225
},
{
"line": 9,
"beat": "desc-w",
"word": "issues",
"start": 43.225,
"end": 43.7
},
{
"line": 9,
"beat": "desc-w",
"word": "in",
"start": 43.7,
"end": 43.788
},
{
"line": 9,
"beat": "desc-w",
"word": "the",
"start": 43.788,
"end": 43.888
},
{
"line": 9,
"beat": "desc-w",
"word": "team's",
"start": 43.888,
"end": 44.313
},
{
"line": 9,
"beat": "desc-w",
"word": "demo",
"start": 44.313,
"end": 44.7
},
{
"line": 9,
"beat": "desc-w",
"word": "repositories",
"start": 44.7,
"end": 45.95
},
{
"line": 9,
"beat": "desc-w",
"word": "It",
"start": 46.05,
"end": 46.263
},
{
"line": 9,
"beat": "desc-w",
"word": "reads",
"start": 46.263,
"end": 46.788
},
{
"line": 9,
"beat": "desc-w",
"word": "and",
"start": 46.788,
"end": 46.975
},
{
"line": 9,
"beat": "desc-w",
"word": "summarizes",
"start": 46.975,
"end": 47.65
},
{
"line": 9,
"beat": "desc-w",
"word": "issues",
"start": 47.65,
"end": 48.325
},
{
"line": 9,
"beat": "desc-w",
"word": "posts",
"start": 48.438,
"end": 48.85
},
{
"line": 9,
"beat": "desc-w",
"word": "comments",
"start": 48.85,
"end": 49.575
},
{
"line": 9,
"beat": "desc-w",
"word": "and",
"start": 49.65,
"end": 49.775
},
{
"line": 9,
"beat": "desc-w",
"word": "hands",
"start": 49.775,
"end": 50.062
},
{
"line": 9,
"beat": "desc-w",
"word": "an",
"start": 50.062,
"end": 50.15
},
{
"line": 9,
"beat": "desc-w",
"word": "issue",
"start": 50.15,
"end": 50.538
},
{
"line": 9,
"beat": "desc-w",
"word": "back",
"start": 50.538,
"end": 50.812
},
{
"line": 9,
"beat": "desc-w",
"word": "and",
"start": 50.812,
"end": 50.938
},
{
"line": 9,
"beat": "desc-w",
"word": "forth",
"start": 50.938,
"end": 51.3
},
{
"line": 9,
"beat": "desc-w",
"word": "between",
"start": 51.3,
"end": 51.613
},
{
"line": 9,
"beat": "desc-w",
"word": "the",
"start": 51.613,
"end": 51.7
},
{
"line": 9,
"beat": "desc-w",
"word": "developer",
"start": 51.7,
"end": 52.338
},
{
"line": 9,
"beat": "desc-w",
"word": "and",
"start": 52.338,
"end": 52.463
},
{
"line": 9,
"beat": "desc-w",
"word": "the",
"start": 52.463,
"end": 52.55
},
{
"line": 9,
"beat": "desc-w",
"word": "tester",
"start": 52.55,
"end": 52.975
},
{
"line": 9,
"beat": "desc-w",
"word": "with",
"start": 52.975,
"end": 53.113
},
{
"line": 9,
"beat": "desc-w",
"word": "its",
"start": 53.113,
"end": 53.3
},
{
"line": 9,
"beat": "desc-w",
"word": "labels",
"start": 53.3,
"end": 54.025
},
{
"line": 9,
"beat": "desc-w",
"word": "ready",
"start": 54.15,
"end": 54.4
},
{
"line": 9,
"beat": "desc-w",
"word": "for",
"start": 54.4,
"end": 54.562
},
{
"line": 9,
"beat": "desc-w",
"word": "testing",
"start": 54.562,
"end": 55.225
},
{
"line": 9,
"beat": "desc-w",
"word": "or",
"start": 55.225,
"end": 55.425
},
{
"line": 9,
"beat": "desc-w",
"word": "needs",
"start": 55.425,
"end": 55.663
},
{
"line": 9,
"beat": "desc-w",
"word": "a",
"start": 55.663,
"end": 55.763
},
{
"line": 9,
"beat": "desc-w",
"word": "fix",
"start": 55.763,
"end": 56.6
},
{
"line": 9,
"beat": "desc-w",
"word": "It",
"start": 56.725,
"end": 56.9
},
{
"line": 9,
"beat": "desc-w",
"word": "never",
"start": 56.9,
"end": 57.2
},
{
"line": 9,
"beat": "desc-w",
"word": "closes",
"start": 57.2,
"end": 57.625
},
{
"line": 9,
"beat": "desc-w",
"word": "issues",
"start": 57.625,
"end": 58.4
},
{
"line": 10,
"beat": "cast",
"word": "So",
"start": 59.525,
"end": 59.713
},
{
"line": 10,
"beat": "cast",
"word": "Alice",
"start": 59.713,
"end": 60.1
},
{
"line": 10,
"beat": "cast",
"word": "fixes",
"start": 60.1,
"end": 60.488
},
{
"line": 10,
"beat": "cast",
"word": "an",
"start": 60.488,
"end": 60.575
},
{
"line": 10,
"beat": "cast",
"word": "issue",
"start": 60.575,
"end": 61.138
},
{
"line": 10,
"beat": "cast",
"word": "and",
"start": 61.138,
"end": 61.275
},
{
"line": 10,
"beat": "cast",
"word": "hands",
"start": 61.275,
"end": 61.563
},
{
"line": 10,
"beat": "cast",
"word": "it",
"start": 61.563,
"end": 61.65
},
{
"line": 10,
"beat": "cast",
"word": "to",
"start": 61.65,
"end": 61.75
},
{
"line": 10,
"beat": "cast",
"word": "Bob",
"start": 61.75,
"end": 62.05
},
{
"line": 10,
"beat": "cast",
"word": "for",
"start": 62.05,
"end": 62.175
},
{
"line": 10,
"beat": "cast",
"word": "testing",
"start": 62.175,
"end": 62.95
},
{
"line": 10,
"beat": "cast",
"word": "and",
"start": 63.05,
"end": 63.175
},
{
"line": 10,
"beat": "cast",
"word": "Bob",
"start": 63.175,
"end": 63.488
},
{
"line": 10,
"beat": "cast",
"word": "sends",
"start": 63.488,
"end": 63.738
},
{
"line": 10,
"beat": "cast",
"word": "it",
"start": 63.738,
"end": 63.825
},
{
"line": 10,
"beat": "cast",
"word": "back",
"start": 63.825,
"end": 64.113
},
{
"line": 10,
"beat": "cast",
"word": "if",
"start": 64.113,
"end": 64.213
},
{
"line": 10,
"beat": "cast",
"word": "it",
"start": 64.213,
"end": 64.3
},
{
"line": 10,
"beat": "cast",
"word": "still",
"start": 64.3,
"end": 64.588
},
{
"line": 10,
"beat": "cast",
"word": "fails",
"start": 64.588,
"end": 65.35
},
{
"line": 11,
"beat": "cast",
"word": "The",
"start": 66.15,
"end": 66.263
},
{
"line": 11,
"beat": "cast",
"word": "second",
"start": 66.263,
"end": 66.675
},
{
"line": 11,
"beat": "cast",
"word": "Q-A",
"start": 66.75,
"end": 67.075
},
{
"line": 11,
"beat": "cast",
"word": "sign-off",
"start": 67.075,
"end": 67.7
},
{
"line": 11,
"beat": "cast",
"word": "is",
"start": 67.8,
"end": 67.95
},
{
"line": 11,
"beat": "cast",
"word": "for",
"start": 67.95,
"end": 68.062
},
{
"line": 11,
"beat": "cast",
"word": "the",
"start": 68.062,
"end": 68.163
},
{
"line": 11,
"beat": "cast",
"word": "final",
"start": 68.163,
"end": 68.525
},
{
"line": 11,
"beat": "cast",
"word": "review",
"start": 68.525,
"end": 69.175
},
{
"line": 12,
"beat": "desc-q",
"word": "Its",
"start": 70.3,
"end": 70.45
},
{
"line": 12,
"beat": "desc-q",
"word": "description",
"start": 70.45,
"end": 71.025
},
{
"line": 12,
"beat": "desc-q",
"word": "reads",
"start": 71.025,
"end": 71.725
},
{
"line": 12,
"beat": "desc-q",
"word": "Final",
"start": 71.875,
"end": 72.337
},
{
"line": 12,
"beat": "desc-q",
"word": "Q-A",
"start": 72.337,
"end": 72.75
},
{
"line": 12,
"beat": "desc-q",
"word": "review",
"start": 72.75,
"end": 73.45
},
{
"line": 12,
"beat": "desc-q",
"word": "It",
"start": 73.65,
"end": 73.825
},
{
"line": 12,
"beat": "desc-q",
"word": "reads",
"start": 73.825,
"end": 74.112
},
{
"line": 12,
"beat": "desc-q",
"word": "an",
"start": 74.112,
"end": 74.237
},
{
"line": 12,
"beat": "desc-q",
"word": "issue",
"start": 74.237,
"end": 74.737
},
{
"line": 12,
"beat": "desc-q",
"word": "and",
"start": 74.737,
"end": 74.862
},
{
"line": 12,
"beat": "desc-q",
"word": "its",
"start": 74.862,
"end": 75.0
},
{
"line": 12,
"beat": "desc-q",
"word": "history",
"start": 75.0,
"end": 75.675
},
{
"line": 12,
"beat": "desc-q",
"word": "posts",
"start": 75.787,
"end": 76.2
},
{
"line": 12,
"beat": "desc-q",
"word": "the",
"start": 76.2,
"end": 76.312
},
{
"line": 12,
"beat": "desc-q",
"word": "sign-off",
"start": 76.312,
"end": 76.775
},
{
"line": 12,
"beat": "desc-q",
"word": "comment",
"start": 76.775,
"end": 77.375
},
{
"line": 12,
"beat": "desc-q",
"word": "and",
"start": 77.45,
"end": 77.587
},
{
"line": 12,
"beat": "desc-q",
"word": "closes",
"start": 77.587,
"end": 78.037
},
{
"line": 12,
"beat": "desc-q",
"word": "the",
"start": 78.037,
"end": 78.175
},
{
"line": 12,
"beat": "desc-q",
"word": "issue",
"start": 78.175,
"end": 78.587
},
{
"line": 12,
"beat": "desc-q",
"word": "as",
"start": 78.587,
"end": 78.75
},
{
"line": 12,
"beat": "desc-q",
"word": "completed",
"start": 78.75,
"end": 79.475
},
{
"line": 12,
"beat": "desc-q",
"word": "or",
"start": 79.587,
"end": 79.775
},
{
"line": 12,
"beat": "desc-q",
"word": "as",
"start": 79.775,
"end": 79.925
},
{
"line": 12,
"beat": "desc-q",
"word": "not",
"start": 79.925,
"end": 80.15
},
{
"line": 12,
"beat": "desc-q",
"word": "planned",
"start": 80.15,
"end": 80.95
},
{
"line": 12,
"beat": "desc-q",
"word": "It",
"start": 81.075,
"end": 81.263
},
{
"line": 12,
"beat": "desc-q",
"word": "never",
"start": 81.263,
"end": 81.55
},
{
"line": 12,
"beat": "desc-q",
"word": "relabels",
"start": 81.55,
"end": 82.075
},
{
"line": 12,
"beat": "desc-q",
"word": "issues",
"start": 82.075,
"end": 82.587
},
{
"line": 12,
"beat": "desc-q",
"word": "and",
"start": 82.587,
"end": 82.725
},
{
"line": 12,
"beat": "desc-q",
"word": "never",
"start": 82.725,
"end": 83.013
},
{
"line": 12,
"beat": "desc-q",
"word": "changes",
"start": 83.013,
"end": 83.55
},
{
"line": 12,
"beat": "desc-q",
"word": "what",
"start": 83.55,
"end": 83.688
},
{
"line": 12,
"beat": "desc-q",
"word": "an",
"start": 83.688,
"end": 83.775
},
{
"line": 12,
"beat": "desc-q",
"word": "issue",
"start": 83.775,
"end": 84.075
},
{
"line": 12,
"beat": "desc-q",
"word": "says",
"start": 84.075,
"end": 84.7
},
{
"line": 13,
"beat": "cast",
"word": "So",
"start": 85.825,
"end": 86.0
},
{
"line": 13,
"beat": "cast",
"word": "once",
"start": 86.0,
"end": 86.237
},
{
"line": 13,
"beat": "cast",
"word": "a",
"start": 86.237,
"end": 86.337
},
{
"line": 13,
"beat": "cast",
"word": "fix",
"start": 86.337,
"end": 86.612
},
{
"line": 13,
"beat": "cast",
"word": "is",
"start": 86.612,
"end": 86.75
},
{
"line": 13,
"beat": "cast",
"word": "verified",
"start": 86.75,
"end": 87.55
},
{
"line": 13,
"beat": "cast",
"word": "the",
"start": 87.65,
"end": 87.75
},
{
"line": 13,
"beat": "cast",
"word": "tester",
"start": 87.75,
"end": 88.188
},
{
"line": 13,
"beat": "cast",
"word": "signs",
"start": 88.188,
"end": 88.487
},
{
"line": 13,
"beat": "cast",
"word": "it",
"start": 88.487,
"end": 88.575
},
{
"line": 13,
"beat": "cast",
"word": "off",
"start": 88.575,
"end": 88.925
},
{
"line": 13,
"beat": "cast",
"word": "and",
"start": 88.925,
"end": 89.05
},
{
"line": 13,
"beat": "cast",
"word": "closes",
"start": 89.05,
"end": 89.438
},
{
"line": 13,
"beat": "cast",
"word": "the",
"start": 89.438,
"end": 89.575
},
{
"line": 13,
"beat": "cast",
"word": "issue",
"start": 89.575,
"end": 89.85
},
{
"line": 13,
"beat": "cast",
"word": "there",
"start": 89.85,
"end": 90.4
},
{
"line": 14,
"beat": "login",
"word": "Alice",
"start": 92.05,
"end": 92.387
},
{
"line": 14,
"beat": "login",
"word": "and",
"start": 92.387,
"end": 92.5
},
{
"line": 14,
"beat": "login",
"word": "Bob",
"start": 92.5,
"end": 92.775
},
{
"line": 14,
"beat": "login",
"word": "each",
"start": 92.775,
"end": 93.012
},
{
"line": 14,
"beat": "login",
"word": "sign",
"start": 93.012,
"end": 93.3
},
{
"line": 14,
"beat": "login",
"word": "in",
"start": 93.3,
"end": 93.425
},
{
"line": 14,
"beat": "login",
"word": "to",
"start": 93.425,
"end": 93.525
},
{
"line": 14,
"beat": "login",
"word": "Dam",
"start": 93.525,
"end": 94.15
},
{
"line": 14,
"beat": "login",
"word": "From",
"start": 94.275,
"end": 94.45
},
{
"line": 14,
"beat": "login",
"word": "here",
"start": 94.45,
"end": 94.675
},
{
"line": 14,
"beat": "login",
"word": "on",
"start": 94.675,
"end": 95.025
},
{
"line": 14,
"beat": "login",
"word": "we",
"start": 95.1,
"end": 95.262
},
{
"line": 14,
"beat": "login",
"word": "follow",
"start": 95.262,
"end": 95.587
},
{
"line": 14,
"beat": "login",
"word": "them",
"start": 95.587,
"end": 95.762
},
{
"line": 14,
"beat": "login",
"word": "side",
"start": 95.762,
"end": 96.05
},
{
"line": 14,
"beat": "login",
"word": "by",
"start": 96.05,
"end": 96.237
},
{
"line": 14,
"beat": "login",
"word": "side",
"start": 96.237,
"end": 96.875
},
{
"line": 15,
"beat": "intro",
"word": "In",
"start": 99.025,
"end": 99.15
},
{
"line": 15,
"beat": "intro",
"word": "Dam",
"start": 99.15,
"end": 99.6
},
{
"line": 15,
"beat": "intro",
"word": "each",
"start": 99.7,
"end": 99.937
},
{
"line": 15,
"beat": "intro",
"word": "agent",
"start": 99.937,
"end": 100.325
},
{
"line": 15,
"beat": "intro",
"word": "shows",
"start": 100.325,
"end": 100.587
},
{
"line": 15,
"beat": "intro",
"word": "its",
"start": 100.587,
"end": 100.725
},
{
"line": 15,
"beat": "intro",
"word": "description",
"start": 100.725,
"end": 101.575
},
{
"line": 15,
"beat": "intro",
"word": "and",
"start": 101.675,
"end": 101.8
},
{
"line": 15,
"beat": "intro",
"word": "Bob",
"start": 101.8,
"end": 102.325
},
{
"line": 15,
"beat": "intro",
"word": "added",
"start": 102.462,
"end": 102.775
},
{
"line": 15,
"beat": "intro",
"word": "to",
"start": 102.775,
"end": 102.912
},
{
"line": 15,
"beat": "intro",
"word": "its",
"start": 102.912,
"end": 103.075
},
{
"line": 15,
"beat": "intro",
"word": "people",
"start": 103.075,
"end": 103.475
},
{
"line": 15,
"beat": "intro",
"word": "as",
"start": 103.475,
"end": 103.587
},
{
"line": 15,
"beat": "intro",
"word": "an",
"start": 103.587,
"end": 103.675
},
{
"line": 15,
"beat": "intro",
"word": "editor",
"start": 103.675,
"end": 104.375
},
{
"line": 16,
"beat": "act1",
"word": "Act",
"start": 106.525,
"end": 106.775
},
{
"line": 16,
"beat": "act1",
"word": "one",
"start": 106.775,
"end": 107.175
},
{
"line": 16,
"beat": "act1",
"word": "Dam",
"start": 107.275,
"end": 107.55
},
{
"line": 16,
"beat": "act1",
"word": "as",
"start": 107.55,
"end": 107.662
},
{
"line": 16,
"beat": "act1",
"word": "it",
"start": 107.662,
"end": 107.762
},
{
"line": 16,
"beat": "act1",
"word": "is",
"start": 107.762,
"end": 107.95
},
{
"line": 16,
"beat": "act1",
"word": "with",
"start": 107.987,
"end": 108.15
},
{
"line": 16,
"beat": "act1",
"word": "no",
"start": 108.15,
"end": 108.337
},
{
"line": 16,
"beat": "act1",
"word": "Sentry",
"start": 108.337,
"end": 109.05
},
{
"line": 17,
"beat": "a1-handover",
"word": "Alice",
"start": 110.225,
"end": 110.587
},
{
"line": 17,
"beat": "a1-handover",
"word": "asks",
"start": 110.587,
"end": 110.975
},
{
"line": 17,
"beat": "a1-handover",
"word": "the",
"start": 110.975,
"end": 111.075
},
{
"line": 17,
"beat": "a1-handover",
"word": "workbench",
"start": 111.075,
"end": 111.75
},
{
"line": 17,
"beat": "a1-handover",
"word": "using",
"start": 111.862,
"end": 112.187
},
{
"line": 17,
"beat": "a1-handover",
"word": "the",
"start": 112.187,
"end": 112.275
},
{
"line": 17,
"beat": "a1-handover",
"word": "GitHub",
"start": 112.275,
"end": 112.662
},
{
"line": 17,
"beat": "a1-handover",
"word": "tool",
"start": 112.662,
"end": 113.05
},
{
"line": 17,
"beat": "a1-handover",
"word": "on",
"start": 113.1,
"end": 113.25
},
{
"line": 17,
"beat": "a1-handover",
"word": "issue",
"start": 113.25,
"end": 113.55
},
{
"line": 17,
"beat": "a1-handover",
"word": "one",
"start": 113.55,
"end": 114.2
},
{
"line": 17,
"beat": "a1-handover",
"word": "comment",
"start": 114.362,
"end": 114.8
},
{
"line": 17,
"beat": "a1-handover",
"word": "that",
"start": 114.8,
"end": 114.912
},
{
"line": 17,
"beat": "a1-handover",
"word": "the",
"start": 114.912,
"end": 115.062
},
{
"line": 17,
"beat": "a1-handover",
"word": "fix",
"start": 115.062,
"end": 115.45
},
{
"line": 17,
"beat": "a1-handover",
"word": "is",
"start": 115.45,
"end": 115.625
},
{
"line": 17,
"beat": "a1-handover",
"word": "in",
"start": 115.625,
"end": 115.775
},
{
"line": 17,
"beat": "a1-handover",
"word": "P-R",
"start": 115.775,
"end": 116.15
},
{
"line": 17,
"beat": "a1-handover",
"word": "twelve",
"start": 116.15,
"end": 116.775
},
{
"line": 17,
"beat": "a1-handover",
"word": "ready",
"start": 116.875,
"end": 117.15
},
{
"line": 17,
"beat": "a1-handover",
"word": "for",
"start": 117.15,
"end": 117.275
},
{
"line": 17,
"beat": "a1-handover",
"word": "testing",
"start": 117.275,
"end": 118.025
},
{
"line": 17,
"beat": "a1-handover",
"word": "and",
"start": 118.137,
"end": 118.275
},
{
"line": 17,
"beat": "a1-handover",
"word": "label",
"start": 118.275,
"end": 118.612
},
{
"line": 17,
"beat": "a1-handover",
"word": "the",
"start": 118.612,
"end": 118.75
},
{
"line": 17,
"beat": "a1-handover",
"word": "issue",
"start": 118.75,
"end": 119.162
},
{
"line": 17,
"beat": "a1-handover",
"word": "ready",
"start": 119.162,
"end": 119.475
},
{
"line": 17,
"beat": "a1-handover",
"word": "for",
"start": 119.475,
"end": 119.6
},
{
"line": 17,
"beat": "a1-handover",
"word": "testing",
"start": 119.6,
"end": 120.35
},
{
"line": 18,
"beat": "a1-handover",
"word": "Both",
"start": 121.25,
"end": 121.5
},
{
"line": 18,
"beat": "a1-handover",
"word": "calls",
"start": 121.5,
"end": 121.8
},
{
"line": 18,
"beat": "a1-handover",
"word": "go",
"start": 121.8,
"end": 121.987
},
{
"line": 18,
"beat": "a1-handover",
"word": "through",
"start": 121.987,
"end": 122.325
},
{
"line": 18,
"beat": "a1-handover",
"word": "as",
"start": 122.4,
"end": 122.537
},
{
"line": 18,
"beat": "a1-handover",
"word": "they",
"start": 122.537,
"end": 122.687
},
{
"line": 18,
"beat": "a1-handover",
"word": "should",
"start": 122.687,
"end": 123.225
},
{
"line": 19,
"beat": "a1-sendback",
"word": "Bob",
"start": 125.875,
"end": 126.137
},
{
"line": 19,
"beat": "a1-sendback",
"word": "tests",
"start": 126.137,
"end": 126.512
},
{
"line": 19,
"beat": "a1-sendback",
"word": "it",
"start": 126.512,
"end": 126.825
},
{
"line": 19,
"beat": "a1-sendback",
"word": "and",
"start": 126.9,
"end": 127.012
},
{
"line": 19,
"beat": "a1-sendback",
"word": "it",
"start": 127.012,
"end": 127.112
},
{
"line": 19,
"beat": "a1-sendback",
"word": "still",
"start": 127.112,
"end": 127.412
},
{
"line": 19,
"beat": "a1-sendback",
"word": "fails",
"start": 127.412,
"end": 127.862
},
{
"line": 19,
"beat": "a1-sendback",
"word": "on",
"start": 127.862,
"end": 128.05
},
{
"line": 19,
"beat": "a1-sendback",
"word": "Safari",
"start": 128.05,
"end": 128.55
},
{
"line": 19,
"beat": "a1-sendback",
"word": "seventeen",
"start": 128.55,
"end": 129.6
},
{
"line": 19,
"beat": "a1-sendback",
"word": "So",
"start": 129.737,
"end": 129.925
},
{
"line": 19,
"beat": "a1-sendback",
"word": "Bob",
"start": 129.925,
"end": 130.237
},
{
"line": 19,
"beat": "a1-sendback",
"word": "asks",
"start": 130.237,
"end": 130.6
},
{
"line": 19,
"beat": "a1-sendback",
"word": "the",
"start": 130.6,
"end": 130.7
},
{
"line": 19,
"beat": "a1-sendback",
"word": "workbench",
"start": 130.7,
"end": 131.15
},
{
"line": 19,
"beat": "a1-sendback",
"word": "to",
"start": 131.15,
"end": 131.275
},
{
"line": 19,
"beat": "a1-sendback",
"word": "comment",
"start": 131.275,
"end": 131.662
},
{
"line": 19,
"beat": "a1-sendback",
"word": "just",
"start": 131.662,
"end": 131.925
},
{
"line": 19,
"beat": "a1-sendback",
"word": "that",
"start": 131.925,
"end": 132.45
},
{
"line": 19,
"beat": "a1-sendback",
"word": "and",
"start": 132.55,
"end": 132.675
},
{
"line": 19,
"beat": "a1-sendback",
"word": "to",
"start": 132.675,
"end": 132.812
},
{
"line": 19,
"beat": "a1-sendback",
"word": "label",
"start": 132.812,
"end": 133.187
},
{
"line": 19,
"beat": "a1-sendback",
"word": "the",
"start": 133.187,
"end": 133.325
},
{
"line": 19,
"beat": "a1-sendback",
"word": "issue",
"start": 133.325,
"end": 133.725
},
{
"line": 19,
"beat": "a1-sendback",
"word": "needs",
"start": 133.725,
"end": 133.962
},
{
"line": 19,
"beat": "a1-sendback",
"word": "a",
"start": 133.962,
"end": 134.062
},
{
"line": 19,
"beat": "a1-sendback",
"word": "fix",
"start": 134.062,
"end": 134.725
},
{
"line": 20,
"beat": "a1-sendback",
"word": "That",
"start": 135.95,
"end": 136.162
},
{
"line": 20,
"beat": "a1-sendback",
"word": "works",
"start": 136.162,
"end": 136.437
},
{
"line": 20,
"beat": "a1-sendback",
"word": "too",
"start": 136.437,
"end": 136.95
},
{
"line": 20,
"beat": "a1-sendback",
"word": "this",
"start": 137.1,
"end": 137.3
},
{
"line": 20,
"beat": "a1-sendback",
"word": "back",
"start": 137.3,
"end": 137.537
},
{
"line": 20,
"beat": "a1-sendback",
"word": "and",
"start": 137.537,
"end": 137.662
},
{
"line": 20,
"beat": "a1-sendback",
"word": "forth",
"start": 137.662,
"end": 138.1
},
{
"line": 20,
"beat": "a1-sendback",
"word": "is",
"start": 138.1,
"end": 138.212
},
{
"line": 20,
"beat": "a1-sendback",
"word": "what",
"start": 138.212,
"end": 138.337
},
{
"line": 20,
"beat": "a1-sendback",
"word": "the",
"start": 138.337,
"end": 138.425
},
{
"line": 20,
"beat": "a1-sendback",
"word": "workbench",
"start": 138.425,
"end": 138.875
},
{
"line": 20,
"beat": "a1-sendback",
"word": "is",
"start": 138.875,
"end": 139.025
},
{
"line": 20,
"beat": "a1-sendback",
"word": "for",
"start": 139.025,
"end": 139.55
},
{
"line": 21,
"beat": "a1-closehere",
"word": "Alice",
"start": 142.225,
"end": 142.55
},
{
"line": 21,
"beat": "a1-closehere",
"word": "pushes",
"start": 142.55,
"end": 142.975
},
{
"line": 21,
"beat": "a1-closehere",
"word": "another",
"start": 142.975,
"end": 143.362
},
{
"line": 21,
"beat": "a1-closehere",
"word": "fix",
"start": 143.362,
"end": 143.875
},
{
"line": 21,
"beat": "a1-closehere",
"word": "and",
"start": 143.95,
"end": 144.075
},
{
"line": 21,
"beat": "a1-closehere",
"word": "this",
"start": 144.075,
"end": 144.275
},
{
"line": 21,
"beat": "a1-closehere",
"word": "time",
"start": 144.275,
"end": 144.512
},
{
"line": 21,
"beat": "a1-closehere",
"word": "it",
"start": 144.512,
"end": 144.6
},
{
"line": 21,
"beat": "a1-closehere",
"word": "works",
"start": 144.6,
"end": 145.35
},
{
"line": 21,
"beat": "a1-closehere",
"word": "So",
"start": 145.487,
"end": 145.675
},
{
"line": 21,
"beat": "a1-closehere",
"word": "Bob",
"start": 145.675,
"end": 146.012
},
{
"line": 21,
"beat": "a1-closehere",
"word": "asks",
"start": 146.012,
"end": 146.375
},
{
"line": 21,
"beat": "a1-closehere",
"word": "the",
"start": 146.375,
"end": 146.475
},
{
"line": 21,
"beat": "a1-closehere",
"word": "workbench",
"start": 146.475,
"end": 146.95
},
{
"line": 21,
"beat": "a1-closehere",
"word": "to",
"start": 146.95,
"end": 147.075
},
{
"line": 21,
"beat": "a1-closehere",
"word": "close",
"start": 147.075,
"end": 147.387
},
{
"line": 21,
"beat": "a1-closehere",
"word": "the",
"start": 147.387,
"end": 147.525
},
{
"line": 21,
"beat": "a1-closehere",
"word": "issue",
"start": 147.525,
"end": 147.937
},
{
"line": 21,
"beat": "a1-closehere",
"word": "as",
"start": 147.937,
"end": 148.1
},
{
"line": 21,
"beat": "a1-closehere",
"word": "completed",
"start": 148.1,
"end": 148.875
},
{
"line": 21,
"beat": "a1-closehere",
"word": "because",
"start": 148.975,
"end": 149.25
},
{
"line": 21,
"beat": "a1-closehere",
"word": "it",
"start": 149.25,
"end": 149.35
},
{
"line": 21,
"beat": "a1-closehere",
"word": "works",
"start": 149.35,
"end": 149.625
},
{
"line": 21,
"beat": "a1-closehere",
"word": "now",
"start": 149.625,
"end": 150.225
},
{
"line": 22,
"beat": "a1-closehere",
"word": "It",
"start": 151.025,
"end": 151.15
},
{
"line": 22,
"beat": "a1-closehere",
"word": "closes",
"start": 151.15,
"end": 151.675
},
{
"line": 22,
"beat": "a1-closehere",
"word": "but",
"start": 151.725,
"end": 151.863
},
{
"line": 22,
"beat": "a1-closehere",
"word": "it",
"start": 151.863,
"end": 151.95
},
{
"line": 22,
"beat": "a1-closehere",
"word": "is",
"start": 151.95,
"end": 152.113
},
{
"line": 22,
"beat": "a1-closehere",
"word": "marked",
"start": 152.113,
"end": 152.45
},
{
"line": 22,
"beat": "a1-closehere",
"word": "yellow",
"start": 152.45,
"end": 153.05
},
{
"line": 22,
"beat": "a1-closehere",
"word": "Bob",
"start": 153.213,
"end": 153.537
},
{
"line": 22,
"beat": "a1-closehere",
"word": "used",
"start": 153.537,
"end": 153.825
},
{
"line": 22,
"beat": "a1-closehere",
"word": "the",
"start": 153.825,
"end": 153.925
},
{
"line": 22,
"beat": "a1-closehere",
"word": "wrong",
"start": 153.925,
"end": 154.25
},
{
"line": 22,
"beat": "a1-closehere",
"word": "agent",
"start": 154.25,
"end": 155.025
},
{
"line": 22,
"beat": "a1-closehere",
"word": "The",
"start": 155.138,
"end": 155.275
},
{
"line": 22,
"beat": "a1-closehere",
"word": "workbench",
"start": 155.275,
"end": 155.85
},
{
"line": 22,
"beat": "a1-closehere",
"word": "is",
"start": 155.85,
"end": 156.0
},
{
"line": 22,
"beat": "a1-closehere",
"word": "for",
"start": 156.0,
"end": 156.113
},
{
"line": 22,
"beat": "a1-closehere",
"word": "the",
"start": 156.113,
"end": 156.2
},
{
"line": 22,
"beat": "a1-closehere",
"word": "back",
"start": 156.2,
"end": 156.488
},
{
"line": 22,
"beat": "a1-closehere",
"word": "and",
"start": 156.488,
"end": 156.625
},
{
"line": 22,
"beat": "a1-closehere",
"word": "forth",
"start": 156.625,
"end": 157.0
},
{
"line": 22,
"beat": "a1-closehere",
"word": "between",
"start": 157.0,
"end": 157.325
},
{
"line": 22,
"beat": "a1-closehere",
"word": "developer",
"start": 157.325,
"end": 157.875
},
{
"line": 22,
"beat": "a1-closehere",
"word": "and",
"start": 157.875,
"end": 158.0
},
{
"line": 22,
"beat": "a1-closehere",
"word": "tester",
"start": 158.0,
"end": 158.775
},
{
"line": 22,
"beat": "a1-closehere",
"word": "and",
"start": 158.875,
"end": 158.988
},
{
"line": 22,
"beat": "a1-closehere",
"word": "its",
"start": 158.988,
"end": 159.1
},
{
"line": 22,
"beat": "a1-closehere",
"word": "description",
"start": 159.1,
"end": 159.7
},
{
"line": 22,
"beat": "a1-closehere",
"word": "says",
"start": 159.7,
"end": 159.95
},
{
"line": 22,
"beat": "a1-closehere",
"word": "it",
"start": 159.95,
"end": 160.113
},
{
"line": 22,
"beat": "a1-closehere",
"word": "never",
"start": 160.113,
"end": 160.425
},
{
"line": 22,
"beat": "a1-closehere",
"word": "closes",
"start": 160.425,
"end": 160.875
},
{
"line": 22,
"beat": "a1-closehere",
"word": "issues",
"start": 160.875,
"end": 161.775
},
{
"line": 22,
"beat": "a1-closehere",
"word": "The",
"start": 161.912,
"end": 162.062
},
{
"line": 22,
"beat": "a1-closehere",
"word": "close",
"start": 162.062,
"end": 162.55
},
{
"line": 22,
"beat": "a1-closehere",
"word": "belongs",
"start": 162.55,
"end": 163.1
},
{
"line": 22,
"beat": "a1-closehere",
"word": "in",
"start": 163.1,
"end": 163.263
},
{
"line": 22,
"beat": "a1-closehere",
"word": "Q-A",
"start": 163.263,
"end": 163.713
},
{
"line": 22,
"beat": "a1-closehere",
"word": "sign-off",
"start": 163.713,
"end": 164.5
},
{
"line": 23,
"beat": "reopen",
"word": "For",
"start": 167.125,
"end": 167.237
},
{
"line": 23,
"beat": "reopen",
"word": "the",
"start": 167.237,
"end": 167.338
},
{
"line": 23,
"beat": "reopen",
"word": "sake",
"start": 167.338,
"end": 167.612
},
{
"line": 23,
"beat": "reopen",
"word": "of",
"start": 167.612,
"end": 167.688
},
{
"line": 23,
"beat": "reopen",
"word": "the",
"start": 167.688,
"end": 167.775
},
{
"line": 23,
"beat": "reopen",
"word": "demo",
"start": 167.775,
"end": 168.35
},
{
"line": 23,
"beat": "reopen",
"word": "we",
"start": 168.475,
"end": 168.625
},
{
"line": 23,
"beat": "reopen",
"word": "reopen",
"start": 168.625,
"end": 169.012
},
{
"line": 23,
"beat": "reopen",
"word": "the",
"start": 169.012,
"end": 169.125
},
{
"line": 23,
"beat": "reopen",
"word": "issue",
"start": 169.125,
"end": 169.525
},
{
"line": 23,
"beat": "reopen",
"word": "after",
"start": 169.525,
"end": 169.8
},
{
"line": 23,
"beat": "reopen",
"word": "each",
"start": 169.8,
"end": 170.025
},
{
"line": 23,
"beat": "reopen",
"word": "close",
"start": 170.025,
"end": 170.675
},
{
"line": 23,
"beat": "reopen",
"word": "so",
"start": 170.775,
"end": 170.925
},
{
"line": 23,
"beat": "reopen",
"word": "we",
"start": 170.925,
"end": 171.05
},
{
"line": 23,
"beat": "reopen",
"word": "can",
"start": 171.05,
"end": 171.175
},
{
"line": 23,
"beat": "reopen",
"word": "try",
"start": 171.175,
"end": 171.45
},
{
"line": 23,
"beat": "reopen",
"word": "each",
"start": 171.45,
"end": 171.675
},
{
"line": 23,
"beat": "reopen",
"word": "way",
"start": 171.675,
"end": 171.862
},
{
"line": 23,
"beat": "reopen",
"word": "of",
"start": 171.862,
"end": 171.975
},
{
"line": 23,
"beat": "reopen",
"word": "closing",
"start": 171.975,
"end": 172.4
},
{
"line": 23,
"beat": "reopen",
"word": "it",
"start": 172.4,
"end": 172.825
},
{
"line": 24,
"beat": "a1-close",
"word": "Next",
"start": 174.0,
"end": 174.45
},
{
"line": 24,
"beat": "a1-close",
"word": "Alice",
"start": 174.525,
"end": 174.825
},
{
"line": 24,
"beat": "a1-close",
"word": "asks",
"start": 174.825,
"end": 175.15
},
{
"line": 24,
"beat": "a1-close",
"word": "Q-A",
"start": 175.15,
"end": 175.45
},
{
"line": 24,
"beat": "a1-close",
"word": "sign-off",
"start": 175.45,
"end": 176.137
},
{
"line": 24,
"beat": "a1-close",
"word": "to",
"start": 176.137,
"end": 176.275
},
{
"line": 24,
"beat": "a1-close",
"word": "close",
"start": 176.275,
"end": 176.587
},
{
"line": 24,
"beat": "a1-close",
"word": "the",
"start": 176.587,
"end": 176.7
},
{
"line": 24,
"beat": "a1-close",
"word": "issue",
"start": 176.7,
"end": 177.062
},
{
"line": 24,
"beat": "a1-close",
"word": "as",
"start": 177.062,
"end": 177.225
},
{
"line": 24,
"beat": "a1-close",
"word": "completed",
"start": 177.225,
"end": 178.0
},
{
"line": 25,
"beat": "a1-close",
"word": "It",
"start": 181.225,
"end": 181.35
},
{
"line": 25,
"beat": "a1-close",
"word": "closes",
"start": 181.35,
"end": 181.925
},
{
"line": 25,
"beat": "a1-close",
"word": "and",
"start": 182.012,
"end": 182.175
},
{
"line": 25,
"beat": "a1-close",
"word": "it",
"start": 182.175,
"end": 182.3
},
{
"line": 25,
"beat": "a1-close",
"word": "is",
"start": 182.3,
"end": 182.463
},
{
"line": 25,
"beat": "a1-close",
"word": "yellow",
"start": 182.463,
"end": 182.8
},
{
"line": 25,
"beat": "a1-close",
"word": "again",
"start": 182.8,
"end": 183.55
},
{
"line": 25,
"beat": "a1-close",
"word": "Alice",
"start": 183.725,
"end": 184.15
},
{
"line": 25,
"beat": "a1-close",
"word": "is",
"start": 184.15,
"end": 184.3
},
{
"line": 25,
"beat": "a1-close",
"word": "the",
"start": 184.3,
"end": 184.4
},
{
"line": 25,
"beat": "a1-close",
"word": "developer",
"start": 184.4,
"end": 185.175
},
{
"line": 25,
"beat": "a1-close",
"word": "and",
"start": 185.275,
"end": 185.4
},
{
"line": 25,
"beat": "a1-close",
"word": "proper",
"start": 185.4,
"end": 185.775
},
{
"line": 25,
"beat": "a1-close",
"word": "procedure",
"start": 185.775,
"end": 186.275
},
{
"line": 25,
"beat": "a1-close",
"word": "has",
"start": 186.275,
"end": 186.463
},
{
"line": 25,
"beat": "a1-close",
"word": "the",
"start": 186.463,
"end": 186.55
},
{
"line": 25,
"beat": "a1-close",
"word": "tester",
"start": 186.55,
"end": 186.95
},
{
"line": 25,
"beat": "a1-close",
"word": "verify",
"start": 186.95,
"end": 187.425
},
{
"line": 25,
"beat": "a1-close",
"word": "a",
"start": 187.425,
"end": 187.55
},
{
"line": 25,
"beat": "a1-close",
"word": "fix",
"start": 187.55,
"end": 188.062
},
{
"line": 25,
"beat": "a1-close",
"word": "and",
"start": 188.062,
"end": 188.213
},
{
"line": 25,
"beat": "a1-close",
"word": "close",
"start": 188.213,
"end": 188.537
},
{
"line": 25,
"beat": "a1-close",
"word": "the",
"start": 188.537,
"end": 188.65
},
{
"line": 25,
"beat": "a1-close",
"word": "issue",
"start": 188.65,
"end": 189.275
},
{
"line": 25,
"beat": "a1-close",
"word": "not",
"start": 189.425,
"end": 189.637
},
{
"line": 25,
"beat": "a1-close",
"word": "the",
"start": 189.637,
"end": 189.725
},
{
"line": 25,
"beat": "a1-close",
"word": "developer",
"start": 189.725,
"end": 190.2
},
{
"line": 25,
"beat": "a1-close",
"word": "who",
"start": 190.2,
"end": 190.325
},
{
"line": 25,
"beat": "a1-close",
"word": "made",
"start": 190.325,
"end": 190.55
},
{
"line": 25,
"beat": "a1-close",
"word": "it",
"start": 190.55,
"end": 191.0
},
{
"line": 26,
"beat": "a1-signoff",
"word": "Then",
"start": 193.625,
"end": 193.8
},
{
"line": 26,
"beat": "a1-signoff",
"word": "Bob",
"start": 193.8,
"end": 194.075
},
{
"line": 26,
"beat": "a1-signoff",
"word": "asks",
"start": 194.075,
"end": 194.4
},
{
"line": 26,
"beat": "a1-signoff",
"word": "Q-A",
"start": 194.4,
"end": 194.725
},
{
"line": 26,
"beat": "a1-signoff",
"word": "sign-off",
"start": 194.725,
"end": 195.225
},
{
"line": 26,
"beat": "a1-signoff",
"word": "on",
"start": 195.262,
"end": 195.4
},
{
"line": 26,
"beat": "a1-signoff",
"word": "issue",
"start": 195.4,
"end": 195.675
},
{
"line": 26,
"beat": "a1-signoff",
"word": "one",
"start": 195.675,
"end": 195.85
},
{
"line": 26,
"beat": "a1-signoff",
"word": "to",
"start": 195.9,
"end": 196.0
},
{
"line": 26,
"beat": "a1-signoff",
"word": "comment",
"start": 196.0,
"end": 196.9
},
{
"line": 26,
"beat": "a1-signoff",
"word": "sign-off",
"start": 197.088,
"end": 197.725
},
{
"line": 26,
"beat": "a1-signoff",
"word": "verified",
"start": 197.8,
"end": 198.525
},
{
"line": 26,
"beat": "a1-signoff",
"word": "Alice",
"start": 198.637,
"end": 199.0
},
{
"line": 26,
"beat": "a1-signoff",
"word": "resolved",
"start": 199.0,
"end": 199.525
},
{
"line": 26,
"beat": "a1-signoff",
"word": "it",
"start": 199.525,
"end": 199.825
},
{
"line": 26,
"beat": "a1-signoff",
"word": "for",
"start": 199.9,
"end": 200.025
},
{
"line": 26,
"beat": "a1-signoff",
"word": "any",
"start": 200.025,
"end": 200.225
},
{
"line": 26,
"beat": "a1-signoff",
"word": "questions",
"start": 200.225,
"end": 200.825
},
{
"line": 26,
"beat": "a1-signoff",
"word": "reach",
"start": 200.9,
"end": 201.15
},
{
"line": 26,
"beat": "a1-signoff",
"word": "Alice",
"start": 201.15,
"end": 201.5
},
{
"line": 26,
"beat": "a1-signoff",
"word": "at",
"start": 201.5,
"end": 201.588
},
{
"line": 26,
"beat": "a1-signoff",
"word": "her",
"start": 201.588,
"end": 201.7
},
{
"line": 26,
"beat": "a1-signoff",
"word": "email",
"start": 201.7,
"end": 202.475
},
{
"line": 26,
"beat": "a1-signoff",
"word": "And",
"start": 202.575,
"end": 202.737
},
{
"line": 26,
"beat": "a1-signoff",
"word": "then",
"start": 202.737,
"end": 202.95
},
{
"line": 26,
"beat": "a1-signoff",
"word": "to",
"start": 202.95,
"end": 203.075
},
{
"line": 26,
"beat": "a1-signoff",
"word": "close",
"start": 203.075,
"end": 203.387
},
{
"line": 26,
"beat": "a1-signoff",
"word": "the",
"start": 203.387,
"end": 203.525
},
{
"line": 26,
"beat": "a1-signoff",
"word": "issue",
"start": 203.525,
"end": 204.2
},
{
"line": 27,
"beat": "a1-signoff",
"word": "It",
"start": 207.925,
"end": 208.038
},
{
"line": 27,
"beat": "a1-signoff",
"word": "is",
"start": 208.038,
"end": 208.175
},
{
"line": 27,
"beat": "a1-signoff",
"word": "posted",
"start": 208.175,
"end": 208.6
},
{
"line": 27,
"beat": "a1-signoff",
"word": "to",
"start": 208.6,
"end": 208.688
},
{
"line": 27,
"beat": "a1-signoff",
"word": "a",
"start": 208.688,
"end": 208.788
},
{
"line": 27,
"beat": "a1-signoff",
"word": "public",
"start": 208.788,
"end": 209.2
},
{
"line": 27,
"beat": "a1-signoff",
"word": "GitHub",
"start": 209.2,
"end": 209.575
},
{
"line": 27,
"beat": "a1-signoff",
"word": "issue",
"start": 209.575,
"end": 210.1
},
{
"line": 27,
"beat": "a1-signoff",
"word": "personal",
"start": 210.225,
"end": 210.7
},
{
"line": 27,
"beat": "a1-signoff",
"word": "data",
"start": 210.7,
"end": 211.088
},
{
"line": 27,
"beat": "a1-signoff",
"word": "and",
"start": 211.088,
"end": 211.225
},
{
"line": 27,
"beat": "a1-signoff",
"word": "all",
"start": 211.225,
"end": 211.9
},
{
"line": 27,
"beat": "a1-signoff",
"word": "Yellow",
"start": 212.088,
"end": 212.65
},
{
"line": 27,
"beat": "a1-signoff",
"word": "this",
"start": 212.775,
"end": 212.975
},
{
"line": 27,
"beat": "a1-signoff",
"word": "data",
"start": 212.975,
"end": 213.275
},
{
"line": 27,
"beat": "a1-signoff",
"word": "should",
"start": 213.275,
"end": 213.425
},
{
"line": 27,
"beat": "a1-signoff",
"word": "not",
"start": 213.425,
"end": 213.65
},
{
"line": 27,
"beat": "a1-signoff",
"word": "leave",
"start": 213.65,
"end": 213.888
},
{
"line": 27,
"beat": "a1-signoff",
"word": "the",
"start": 213.888,
"end": 213.975
},
{
"line": 27,
"beat": "a1-signoff",
"word": "company",
"start": 213.975,
"end": 214.75
},
{
"line": 28,
"beat": "act1end",
"word": "Every",
"start": 217.375,
"end": 217.675
},
{
"line": 28,
"beat": "act1end",
"word": "request",
"start": 217.675,
"end": 218.125
},
{
"line": 28,
"beat": "act1end",
"word": "went",
"start": 218.125,
"end": 218.338
},
{
"line": 28,
"beat": "act1end",
"word": "through",
"start": 218.338,
"end": 219.0
},
{
"line": 28,
"beat": "act1end",
"word": "There",
"start": 219.2,
"end": 219.338
},
{
"line": 28,
"beat": "act1end",
"word": "are",
"start": 219.338,
"end": 219.425
},
{
"line": 28,
"beat": "act1end",
"word": "no",
"start": 219.425,
"end": 219.65
},
{
"line": 28,
"beat": "act1end",
"word": "restrictions",
"start": 219.65,
"end": 220.312
},
{
"line": 28,
"beat": "act1end",
"word": "on",
"start": 220.312,
"end": 220.487
},
{
"line": 28,
"beat": "act1end",
"word": "agent",
"start": 220.487,
"end": 220.838
},
{
"line": 28,
"beat": "act1end",
"word": "activity",
"start": 220.838,
"end": 221.65
},
{
"line": 28,
"beat": "act1end",
"word": "either",
"start": 221.787,
"end": 222.1
},
{
"line": 28,
"beat": "act1end",
"word": "by",
"start": 222.1,
"end": 222.3
},
{
"line": 28,
"beat": "act1end",
"word": "intent",
"start": 222.3,
"end": 222.95
},
{
"line": 28,
"beat": "act1end",
"word": "or",
"start": 222.95,
"end": 223.125
},
{
"line": 28,
"beat": "act1end",
"word": "by",
"start": 223.125,
"end": 223.325
},
{
"line": 28,
"beat": "act1end",
"word": "security",
"start": 223.325,
"end": 223.8
},
{
"line": 28,
"beat": "act1end",
"word": "considerations",
"start": 223.8,
"end": 224.975
},
{
"line": 29,
"beat": "plug",
"word": "This",
"start": 226.6,
"end": 226.8
},
{
"line": 29,
"beat": "plug",
"word": "is",
"start": 226.8,
"end": 226.95
},
{
"line": 29,
"beat": "plug",
"word": "where",
"start": 226.95,
"end": 227.162
},
{
"line": 29,
"beat": "plug",
"word": "Sentry",
"start": 227.162,
"end": 227.55
},
{
"line": 29,
"beat": "plug",
"word": "comes",
"start": 227.55,
"end": 227.875
},
{
"line": 29,
"beat": "plug",
"word": "in",
"start": 227.875,
"end": 228.375
},
{
"line": 30,
"beat": "plug",
"word": "We've",
"start": 229.175,
"end": 229.388
},
{
"line": 30,
"beat": "plug",
"word": "added",
"start": 229.388,
"end": 229.725
},
{
"line": 30,
"beat": "plug",
"word": "an",
"start": 229.725,
"end": 229.9
},
{
"line": 30,
"beat": "plug",
"word": "M-C-P",
"start": 229.9,
"end": 230.688
},
{
"line": 30,
"beat": "plug",
"word": "policy",
"start": 230.688,
"end": 231.138
},
{
"line": 30,
"beat": "plug",
"word": "enforcement",
"start": 231.138,
"end": 231.7
},
{
"line": 30,
"beat": "plug",
"word": "point",
"start": 231.7,
"end": 232.025
},
{
"line": 30,
"beat": "plug",
"word": "to",
"start": 232.025,
"end": 232.15
},
{
"line": 30,
"beat": "plug",
"word": "Dam's",
"start": 232.15,
"end": 232.55
},
{
"line": 30,
"beat": "plug",
"word": "gateway",
"start": 232.55,
"end": 233.375
},
{
"line": 30,
"beat": "plug",
"word": "every",
"start": 233.588,
"end": 233.963
},
{
"line": 30,
"beat": "plug",
"word": "M-C-P",
"start": 233.963,
"end": 234.688
},
{
"line": 30,
"beat": "plug",
"word": "tool",
"start": 234.688,
"end": 234.975
},
{
"line": 30,
"beat": "plug",
"word": "call",
"start": 234.975,
"end": 235.275
},
{
"line": 30,
"beat": "plug",
"word": "an",
"start": 235.275,
"end": 235.413
},
{
"line": 30,
"beat": "plug",
"word": "agent",
"start": 235.413,
"end": 235.75
},
{
"line": 30,
"beat": "plug",
"word": "makes",
"start": 235.75,
"end": 236.075
},
{
"line": 30,
"beat": "plug",
"word": "is",
"start": 236.075,
"end": 236.238
},
{
"line": 30,
"beat": "plug",
"word": "checked",
"start": 236.238,
"end": 236.525
},
{
"line": 30,
"beat": "plug",
"word": "there",
"start": 236.525,
"end": 237.025
},
{
"line": 30,
"beat": "plug",
"word": "before",
"start": 237.125,
"end": 237.463
},
{
"line": 30,
"beat": "plug",
"word": "its",
"start": 237.463,
"end": 237.6
},
{
"line": 30,
"beat": "plug",
"word": "credential",
"start": 237.6,
"end": 238.125
},
{
"line": 30,
"beat": "plug",
"word": "is",
"start": 238.125,
"end": 238.25
},
{
"line": 30,
"beat": "plug",
"word": "attached",
"start": 238.25,
"end": 239.05
},
{
"line": 31,
"beat": "plug",
"word": "Sentry's",
"start": 239.9,
"end": 240.325
},
{
"line": 31,
"beat": "plug",
"word": "capabilities",
"start": 240.325,
"end": 241.05
},
{
"line": 31,
"beat": "plug",
"word": "cover",
"start": 241.05,
"end": 241.375
},
{
"line": 31,
"beat": "plug",
"word": "both",
"start": 241.375,
"end": 241.638
},
{
"line": 31,
"beat": "plug",
"word": "the",
"start": 241.638,
"end": 241.788
},
{
"line": 31,
"beat": "plug",
"word": "agent's",
"start": 241.788,
"end": 242.225
},
{
"line": 31,
"beat": "plug",
"word": "intent",
"start": 242.225,
"end": 242.963
},
{
"line": 31,
"beat": "plug",
"word": "and",
"start": 242.963,
"end": 243.138
},
{
"line": 31,
"beat": "plug",
"word": "the",
"start": 243.138,
"end": 243.225
},
{
"line": 31,
"beat": "plug",
"word": "company's",
"start": 243.225,
"end": 243.75
},
{
"line": 31,
"beat": "plug",
"word": "policy",
"start": 243.75,
"end": 244.525
},
{
"line": 31,
"beat": "plug",
"word": "for",
"start": 244.65,
"end": 244.813
},
{
"line": 31,
"beat": "plug",
"word": "access",
"start": 244.813,
"end": 245.25
},
{
"line": 31,
"beat": "plug",
"word": "control",
"start": 245.25,
"end": 245.963
},
{
"line": 31,
"beat": "plug",
"word": "and",
"start": 245.963,
"end": 246.175
},
{
"line": 31,
"beat": "plug",
"word": "for",
"start": 246.175,
"end": 246.338
},
{
"line": 31,
"beat": "plug",
"word": "data",
"start": 246.338,
"end": 246.675
},
{
"line": 31,
"beat": "plug",
"word": "governance",
"start": 246.675,
"end": 247.45
},
{
"line": 32,
"beat": "plug",
"word": "We",
"start": 248.25,
"end": 248.388
},
{
"line": 32,
"beat": "plug",
"word": "first",
"start": 248.388,
"end": 248.675
},
{
"line": 32,
"beat": "plug",
"word": "turn",
"start": 248.675,
"end": 248.938
},
{
"line": 32,
"beat": "plug",
"word": "Sentry",
"start": 248.938,
"end": 249.35
},
{
"line": 32,
"beat": "plug",
"word": "on",
"start": 249.35,
"end": 249.513
},
{
"line": 32,
"beat": "plug",
"word": "without",
"start": 249.513,
"end": 249.85
},
{
"line": 32,
"beat": "plug",
"word": "any",
"start": 249.85,
"end": 250.075
},
{
"line": 32,
"beat": "plug",
"word": "company",
"start": 250.075,
"end": 250.425
},
{
"line": 32,
"beat": "plug",
"word": "policy",
"start": 250.425,
"end": 250.838
},
{
"line": 32,
"beat": "plug",
"word": "in",
"start": 250.838,
"end": 250.925
},
{
"line": 32,
"beat": "plug",
"word": "place",
"start": 250.925,
"end": 251.575
},
{
"line": 32,
"beat": "plug",
"word": "and",
"start": 251.675,
"end": 251.838
},
{
"line": 32,
"beat": "plug",
"word": "see",
"start": 251.838,
"end": 252.038
},
{
"line": 32,
"beat": "plug",
"word": "the",
"start": 252.038,
"end": 252.175
},
{
"line": 32,
"beat": "plug",
"word": "effect",
"start": 252.175,
"end": 252.925
},
{
"line": 33,
"beat": "plug",
"word": "Alice",
"start": 253.775,
"end": 254.1
},
{
"line": 33,
"beat": "plug",
"word": "turns",
"start": 254.1,
"end": 254.375
},
{
"line": 33,
"beat": "plug",
"word": "Sentry",
"start": 254.375,
"end": 254.7
},
{
"line": 33,
"beat": "plug",
"word": "on",
"start": 254.7,
"end": 254.925
},
{
"line": 33,
"beat": "plug",
"word": "for",
"start": 254.925,
"end": 255.05
},
{
"line": 33,
"beat": "plug",
"word": "both",
"start": 255.05,
"end": 255.35
},
{
"line": 33,
"beat": "plug",
"word": "agents",
"start": 255.35,
"end": 256.2
},
{
"line": 33,
"beat": "plug",
"word": "Each",
"start": 256.363,
"end": 256.675
},
{
"line": 33,
"beat": "plug",
"word": "gateway",
"start": 256.675,
"end": 257.125
},
{
"line": 33,
"beat": "plug",
"word": "restarts",
"start": 257.125,
"end": 257.775
},
{
"line": 33,
"beat": "plug",
"word": "with",
"start": 257.85,
"end": 257.963
},
{
"line": 33,
"beat": "plug",
"word": "the",
"start": 257.963,
"end": 258.05
},
{
"line": 33,
"beat": "plug",
"word": "policy",
"start": 258.05,
"end": 258.538
},
{
"line": 33,
"beat": "plug",
"word": "check",
"start": 258.538,
"end": 258.788
},
{
"line": 33,
"beat": "plug",
"word": "in",
"start": 258.788,
"end": 258.9
},
{
"line": 33,
"beat": "plug",
"word": "place",
"start": 258.9,
"end": 259.55
},
{
"line": 34,
"beat": "onboard",
"word": "Sentry",
"start": 260.7,
"end": 261.075
},
{
"line": 34,
"beat": "onboard",
"word": "reads",
"start": 261.075,
"end": 261.413
},
{
"line": 34,
"beat": "onboard",
"word": "each",
"start": 261.413,
"end": 261.738
},
{
"line": 34,
"beat": "onboard",
"word": "agent's",
"start": 261.738,
"end": 262.175
},
{
"line": 34,
"beat": "onboard",
"word": "description",
"start": 262.175,
"end": 263.0
},
{
"line": 34,
"beat": "onboard",
"word": "and",
"start": 263.0,
"end": 263.138
},
{
"line": 34,
"beat": "onboard",
"word": "decides",
"start": 263.138,
"end": 263.563
},
{
"line": 34,
"beat": "onboard",
"word": "its",
"start": 263.563,
"end": 263.675
},
{
"line": 34,
"beat": "onboard",
"word": "operations",
"start": 263.675,
"end": 264.725
},
{
"line": 34,
"beat": "onboard",
"word": "down",
"start": 264.85,
"end": 265.125
},
{
"line": 34,
"beat": "onboard",
"word": "to",
"start": 265.125,
"end": 265.225
},
{
"line": 34,
"beat": "onboard",
"word": "what",
"start": 265.225,
"end": 265.363
},
{
"line": 34,
"beat": "onboard",
"word": "a",
"start": 265.363,
"end": 265.463
},
{
"line": 34,
"beat": "onboard",
"word": "single",
"start": 265.463,
"end": 265.85
},
{
"line": 34,
"beat": "onboard",
"word": "tool",
"start": 265.85,
"end": 266.125
},
{
"line": 34,
"beat": "onboard",
"word": "does",
"start": 266.125,
"end": 266.725
},
{
"line": 35,
"beat": "onboard",
"word": "The",
"start": 267.525,
"end": 267.625
},
{
"line": 35,
"beat": "onboard",
"word": "workbench",
"start": 267.625,
"end": 268.175
},
{
"line": 35,
"beat": "onboard",
"word": "may",
"start": 268.175,
"end": 268.363
},
{
"line": 35,
"beat": "onboard",
"word": "comment",
"start": 268.363,
"end": 268.85
},
{
"line": 35,
"beat": "onboard",
"word": "and",
"start": 268.85,
"end": 268.975
},
{
"line": 35,
"beat": "onboard",
"word": "relabel",
"start": 268.975,
"end": 269.55
},
{
"line": 35,
"beat": "onboard",
"word": "but",
"start": 269.625,
"end": 269.775
},
{
"line": 35,
"beat": "onboard",
"word": "not",
"start": 269.775,
"end": 270.0
},
{
"line": 35,
"beat": "onboard",
"word": "close",
"start": 270.0,
"end": 270.775
},
{
"line": 35,
"beat": "onboard",
"word": "Q-A",
"start": 270.938,
"end": 271.263
},
{
"line": 35,
"beat": "onboard",
"word": "sign-off",
"start": 271.263,
"end": 271.775
},
{
"line": 35,
"beat": "onboard",
"word": "may",
"start": 271.775,
"end": 271.963
},
{
"line": 35,
"beat": "onboard",
"word": "comment",
"start": 271.963,
"end": 272.4
},
{
"line": 35,
"beat": "onboard",
"word": "and",
"start": 272.4,
"end": 272.525
},
{
"line": 35,
"beat": "onboard",
"word": "close",
"start": 272.525,
"end": 273.05
},
{
"line": 35,
"beat": "onboard",
"word": "but",
"start": 273.125,
"end": 273.275
},
{
"line": 35,
"beat": "onboard",
"word": "not",
"start": 273.275,
"end": 273.5
},
{
"line": 35,
"beat": "onboard",
"word": "relabel",
"start": 273.5,
"end": 274.3
},
{
"line": 36,
"beat": "onboard",
"word": "Sentry",
"start": 275.15,
"end": 275.5
},
{
"line": 36,
"beat": "onboard",
"word": "writes",
"start": 275.5,
"end": 275.863
},
{
"line": 36,
"beat": "onboard",
"word": "each",
"start": 275.863,
"end": 276.188
},
{
"line": 36,
"beat": "onboard",
"word": "agent's",
"start": 276.188,
"end": 276.663
},
{
"line": 36,
"beat": "onboard",
"word": "rules",
"start": 276.663,
"end": 277.075
},
{
"line": 36,
"beat": "onboard",
"word": "as",
"start": 277.075,
"end": 277.225
},
{
"line": 36,
"beat": "onboard",
"word": "Rego",
"start": 277.225,
"end": 277.925
},
{
"line": 36,
"beat": "onboard",
"word": "which",
"start": 278.05,
"end": 278.288
},
{
"line": 36,
"beat": "onboard",
"word": "O-P-A",
"start": 278.288,
"end": 279.013
},
{
"line": 36,
"beat": "onboard",
"word": "evaluates",
"start": 279.013,
"end": 279.638
},
{
"line": 36,
"beat": "onboard",
"word": "on",
"start": 279.638,
"end": 279.788
},
{
"line": 36,
"beat": "onboard",
"word": "every",
"start": 279.788,
"end": 280.1
},
{
"line": 36,
"beat": "onboard",
"word": "call",
"start": 280.1,
"end": 280.625
},
{
"line": 36,
"beat": "onboard",
"word": "before",
"start": 280.725,
"end": 281.013
},
{
"line": 36,
"beat": "onboard",
"word": "the",
"start": 281.013,
"end": 281.1
},
{
"line": 36,
"beat": "onboard",
"word": "call",
"start": 281.1,
"end": 281.4
},
{
"line": 36,
"beat": "onboard",
"word": "leaves",
"start": 281.4,
"end": 281.713
},
{
"line": 36,
"beat": "onboard",
"word": "the",
"start": 281.713,
"end": 281.8
},
{
"line": 36,
"beat": "onboard",
"word": "gateway",
"start": 281.8,
"end": 282.55
},
{
"line": 37,
"beat": "act2",
"word": "Act",
"start": 285.225,
"end": 285.475
},
{
"line": 37,
"beat": "act2",
"word": "two",
"start": 285.475,
"end": 285.875
},
{
"line": 37,
"beat": "act2",
"word": "Sentry",
"start": 286.0,
"end": 286.6
},
{
"line": 37,
"beat": "act2",
"word": "from",
"start": 286.7,
"end": 286.9
},
{
"line": 37,
"beat": "act2",
"word": "each",
"start": 286.9,
"end": 287.125
},
{
"line": 37,
"beat": "act2",
"word": "agent's",
"start": 287.125,
"end": 287.475
},
{
"line": 37,
"beat": "act2",
"word": "description",
"start": 287.475,
"end": 288.25
},
{
"line": 37,
"beat": "act2",
"word": "starting",
"start": 288.363,
"end": 288.75
},
{
"line": 37,
"beat": "act2",
"word": "again",
"start": 288.75,
"end": 289.075
},
{
"line": 37,
"beat": "act2",
"word": "from",
"start": 289.075,
"end": 289.213
},
{
"line": 37,
"beat": "act2",
"word": "the",
"start": 289.213,
"end": 289.325
},
{
"line": 37,
"beat": "act2",
"word": "open",
"start": 289.325,
"end": 289.625
},
{
"line": 37,
"beat": "act2",
"word": "issue",
"start": 289.625,
"end": 290.3
},
{
"line": 38,
"beat": "a2-handover",
"word": "Alice",
"start": 291.45,
"end": 291.838
},
{
"line": 38,
"beat": "a2-handover",
"word": "asks",
"start": 291.838,
"end": 292.225
},
{
"line": 38,
"beat": "a2-handover",
"word": "for",
"start": 292.225,
"end": 292.338
},
{
"line": 38,
"beat": "a2-handover",
"word": "the",
"start": 292.338,
"end": 292.463
},
{
"line": 38,
"beat": "a2-handover",
"word": "same",
"start": 292.463,
"end": 292.725
},
{
"line": 38,
"beat": "a2-handover",
"word": "hand-over",
"start": 292.725,
"end": 293.6
},
{
"line": 38,
"beat": "a2-handover",
"word": "comment",
"start": 293.763,
"end": 294.275
},
{
"line": 38,
"beat": "a2-handover",
"word": "that",
"start": 294.275,
"end": 294.413
},
{
"line": 38,
"beat": "a2-handover",
"word": "the",
"start": 294.413,
"end": 294.55
},
{
"line": 38,
"beat": "a2-handover",
"word": "fix",
"start": 294.55,
"end": 294.9
},
{
"line": 38,
"beat": "a2-handover",
"word": "is",
"start": 294.9,
"end": 295.05
},
{
"line": 38,
"beat": "a2-handover",
"word": "in",
"start": 295.05,
"end": 295.2
},
{
"line": 38,
"beat": "a2-handover",
"word": "P-R",
"start": 295.2,
"end": 295.575
},
{
"line": 38,
"beat": "a2-handover",
"word": "twelve",
"start": 295.575,
"end": 296.25
},
{
"line": 38,
"beat": "a2-handover",
"word": "and",
"start": 296.35,
"end": 296.475
},
{
"line": 38,
"beat": "a2-handover",
"word": "label",
"start": 296.475,
"end": 296.788
},
{
"line": 38,
"beat": "a2-handover",
"word": "the",
"start": 296.788,
"end": 296.925
},
{
"line": 38,
"beat": "a2-handover",
"word": "issue",
"start": 296.925,
"end": 297.25
},
{
"line": 38,
"beat": "a2-handover",
"word": "ready",
"start": 297.25,
"end": 297.525
},
{
"line": 38,
"beat": "a2-handover",
"word": "for",
"start": 297.525,
"end": 297.65
},
{
"line": 38,
"beat": "a2-handover",
"word": "testing",
"start": 297.65,
"end": 298.4
},
{
"line": 39,
"beat": "a2-handover",
"word": "Sentry",
"start": 302.525,
"end": 302.888
},
{
"line": 39,
"beat": "a2-handover",
"word": "checks",
"start": 302.888,
"end": 303.213
},
{
"line": 39,
"beat": "a2-handover",
"word": "both",
"start": 303.213,
"end": 303.525
},
{
"line": 39,
"beat": "a2-handover",
"word": "calls",
"start": 303.525,
"end": 303.95
},
{
"line": 39,
"beat": "a2-handover",
"word": "against",
"start": 303.95,
"end": 304.288
},
{
"line": 39,
"beat": "a2-handover",
"word": "the",
"start": 304.288,
"end": 304.375
},
{
"line": 39,
"beat": "a2-handover",
"word": "workbench's",
"start": 304.375,
"end": 304.9
},
{
"line": 39,
"beat": "a2-handover",
"word": "description",
"start": 304.9,
"end": 305.725
},
{
"line": 39,
"beat": "a2-handover",
"word": "and",
"start": 305.825,
"end": 305.95
},
{
"line": 39,
"beat": "a2-handover",
"word": "allows",
"start": 305.95,
"end": 306.35
},
{
"line": 39,
"beat": "a2-handover",
"word": "them",
"start": 306.35,
"end": 306.875
},
{
"line": 40,
"beat": "a2-sendback",
"word": "Bob",
"start": 309.475,
"end": 309.763
},
{
"line": 40,
"beat": "a2-sendback",
"word": "sends",
"start": 309.763,
"end": 310.088
},
{
"line": 40,
"beat": "a2-sendback",
"word": "the",
"start": 310.088,
"end": 310.2
},
{
"line": 40,
"beat": "a2-sendback",
"word": "issue",
"start": 310.2,
"end": 310.5
},
{
"line": 40,
"beat": "a2-sendback",
"word": "back",
"start": 310.5,
"end": 310.775
},
{
"line": 40,
"beat": "a2-sendback",
"word": "the",
"start": 310.775,
"end": 310.913
},
{
"line": 40,
"beat": "a2-sendback",
"word": "same",
"start": 310.913,
"end": 311.25
},
{
"line": 40,
"beat": "a2-sendback",
"word": "way",
"start": 311.25,
"end": 311.85
},
{
"line": 40,
"beat": "a2-sendback",
"word": "it",
"start": 312.013,
"end": 312.15
},
{
"line": 40,
"beat": "a2-sendback",
"word": "still",
"start": 312.15,
"end": 312.438
},
{
"line": 40,
"beat": "a2-sendback",
"word": "fails",
"start": 312.438,
"end": 312.863
},
{
"line": 40,
"beat": "a2-sendback",
"word": "on",
"start": 312.863,
"end": 313.025
},
{
"line": 40,
"beat": "a2-sendback",
"word": "Safari",
"start": 313.025,
"end": 313.475
},
{
"line": 40,
"beat": "a2-sendback",
"word": "seventeen",
"start": 313.475,
"end": 314.3
},
{
"line": 40,
"beat": "a2-sendback",
"word": "so",
"start": 314.4,
"end": 314.538
},
{
"line": 40,
"beat": "a2-sendback",
"word": "the",
"start": 314.538,
"end": 314.625
},
{
"line": 40,
"beat": "a2-sendback",
"word": "label",
"start": 314.625,
"end": 315.025
},
{
"line": 40,
"beat": "a2-sendback",
"word": "is",
"start": 315.025,
"end": 315.175
},
{
"line": 40,
"beat": "a2-sendback",
"word": "needs",
"start": 315.175,
"end": 315.413
},
{
"line": 40,
"beat": "a2-sendback",
"word": "a",
"start": 315.413,
"end": 315.513
},
{
"line": 40,
"beat": "a2-sendback",
"word": "fix",
"start": 315.513,
"end": 316.175
},
{
"line": 41,
"beat": "a2-sendback",
"word": "That",
"start": 319.65,
"end": 319.838
},
{
"line": 41,
"beat": "a2-sendback",
"word": "is",
"start": 319.838,
"end": 319.95
},
{
"line": 41,
"beat": "a2-sendback",
"word": "allowed",
"start": 319.95,
"end": 320.288
},
{
"line": 41,
"beat": "a2-sendback",
"word": "too",
"start": 320.288,
"end": 320.85
},
{
"line": 42,
"beat": "a2-closehere",
"word": "Again",
"start": 323.5,
"end": 323.925
},
{
"line": 42,
"beat": "a2-closehere",
"word": "after",
"start": 324.0,
"end": 324.263
},
{
"line": 42,
"beat": "a2-closehere",
"word": "another",
"start": 324.263,
"end": 324.638
},
{
"line": 42,
"beat": "a2-closehere",
"word": "fix",
"start": 324.638,
"end": 325.25
},
{
"line": 42,
"beat": "a2-closehere",
"word": "Bob",
"start": 325.375,
"end": 325.688
},
{
"line": 42,
"beat": "a2-closehere",
"word": "asks",
"start": 325.688,
"end": 326.05
},
{
"line": 42,
"beat": "a2-closehere",
"word": "the",
"start": 326.05,
"end": 326.15
},
{
"line": 42,
"beat": "a2-closehere",
"word": "workbench",
"start": 326.15,
"end": 326.625
},
{
"line": 42,
"beat": "a2-closehere",
"word": "to",
"start": 326.625,
"end": 326.75
},
{
"line": 42,
"beat": "a2-closehere",
"word": "close",
"start": 326.75,
"end": 327.063
},
{
"line": 42,
"beat": "a2-closehere",
"word": "the",
"start": 327.063,
"end": 327.2
},
{
"line": 42,
"beat": "a2-closehere",
"word": "issue",
"start": 327.2,
"end": 327.6
},
{
"line": 42,
"beat": "a2-closehere",
"word": "as",
"start": 327.6,
"end": 327.75
},
{
"line": 42,
"beat": "a2-closehere",
"word": "completed",
"start": 327.75,
"end": 328.55
},
{
"line": 42,
"beat": "a2-closehere",
"word": "because",
"start": 328.65,
"end": 328.913
},
{
"line": 42,
"beat": "a2-closehere",
"word": "it",
"start": 328.913,
"end": 329.0
},
{
"line": 42,
"beat": "a2-closehere",
"word": "works",
"start": 329.0,
"end": 329.3
},
{
"line": 42,
"beat": "a2-closehere",
"word": "now",
"start": 329.3,
"end": 329.875
},
{
"line": 43,
"beat": "a2-closehere",
"word": "This",
"start": 331.5,
"end": 331.675
},
{
"line": 43,
"beat": "a2-closehere",
"word": "time",
"start": 331.675,
"end": 332.05
},
{
"line": 43,
"beat": "a2-closehere",
"word": "it",
"start": 332.1,
"end": 332.225
},
{
"line": 43,
"beat": "a2-closehere",
"word": "is",
"start": 332.225,
"end": 332.375
},
{
"line": 43,
"beat": "a2-closehere",
"word": "denied",
"start": 332.375,
"end": 333.175
},
{
"line": 43,
"beat": "a2-closehere",
"word": "The",
"start": 333.3,
"end": 333.425
},
{
"line": 43,
"beat": "a2-closehere",
"word": "reason",
"start": 333.425,
"end": 333.85
},
{
"line": 43,
"beat": "a2-closehere",
"word": "quotes",
"start": 333.85,
"end": 334.163
},
{
"line": 43,
"beat": "a2-closehere",
"word": "the",
"start": 334.163,
"end": 334.313
},
{
"line": 43,
"beat": "a2-closehere",
"word": "agent's",
"start": 334.313,
"end": 334.775
},
{
"line": 43,
"beat": "a2-closehere",
"word": "own",
"start": 334.775,
"end": 334.938
},
{
"line": 43,
"beat": "a2-closehere",
"word": "description",
"start": 334.938,
"end": 335.775
},
{
"line": 43,
"beat": "a2-closehere",
"word": "it",
"start": 335.888,
"end": 336.025
},
{
"line": 43,
"beat": "a2-closehere",
"word": "never",
"start": 336.025,
"end": 336.325
},
{
"line": 43,
"beat": "a2-closehere",
"word": "closes",
"start": 336.325,
"end": 336.775
},
{
"line": 43,
"beat": "a2-closehere",
"word": "issues",
"start": 336.775,
"end": 337.55
},
{
"line": 44,
"beat": "a2-relabel",
"word": "Bob",
"start": 340.675,
"end": 341.0
},
{
"line": 44,
"beat": "a2-relabel",
"word": "then",
"start": 341.0,
"end": 341.225
},
{
"line": 44,
"beat": "a2-relabel",
"word": "tries",
"start": 341.225,
"end": 341.55
},
{
"line": 44,
"beat": "a2-relabel",
"word": "to",
"start": 341.55,
"end": 341.688
},
{
"line": 44,
"beat": "a2-relabel",
"word": "send",
"start": 341.688,
"end": 341.913
},
{
"line": 44,
"beat": "a2-relabel",
"word": "it",
"start": 341.913,
"end": 342.0
},
{
"line": 44,
"beat": "a2-relabel",
"word": "back",
"start": 342.0,
"end": 342.325
},
{
"line": 44,
"beat": "a2-relabel",
"word": "through",
"start": 342.325,
"end": 342.538
},
{
"line": 44,
"beat": "a2-relabel",
"word": "Q-A",
"start": 342.538,
"end": 342.875
},
{
"line": 44,
"beat": "a2-relabel",
"word": "sign-off",
"start": 342.875,
"end": 343.375
},
{
"line": 44,
"beat": "a2-relabel",
"word": "instead",
"start": 343.375,
"end": 344.175
},
{
"line": 44,
"beat": "a2-relabel",
"word": "setting",
"start": 344.313,
"end": 344.638
},
{
"line": 44,
"beat": "a2-relabel",
"word": "the",
"start": 344.638,
"end": 344.725
},
{
"line": 44,
"beat": "a2-relabel",
"word": "label",
"start": 344.725,
"end": 345.05
},
{
"line": 44,
"beat": "a2-relabel",
"word": "back",
"start": 345.05,
"end": 345.35
},
{
"line": 44,
"beat": "a2-relabel",
"word": "to",
"start": 345.35,
"end": 345.438
},
{
"line": 44,
"beat": "a2-relabel",
"word": "needs",
"start": 345.438,
"end": 345.663
},
{
"line": 44,
"beat": "a2-relabel",
"word": "a",
"start": 345.663,
"end": 345.763
},
{
"line": 44,
"beat": "a2-relabel",
"word": "fix",
"start": 345.763,
"end": 346.425
},
{
"line": 45,
"beat": "a2-relabel",
"word": "This",
"start": 348.55,
"end": 348.75
},
{
"line": 45,
"beat": "a2-relabel",
"word": "is",
"start": 348.75,
"end": 348.9
},
{
"line": 45,
"beat": "a2-relabel",
"word": "denied",
"start": 348.9,
"end": 349.325
},
{
"line": 45,
"beat": "a2-relabel",
"word": "as",
"start": 349.325,
"end": 349.475
},
{
"line": 45,
"beat": "a2-relabel",
"word": "well",
"start": 349.475,
"end": 349.95
},
{
"line": 45,
"beat": "a2-relabel",
"word": "Q-A",
"start": 350.088,
"end": 350.425
},
{
"line": 45,
"beat": "a2-relabel",
"word": "sign-off",
"start": 350.425,
"end": 350.975
},
{
"line": 45,
"beat": "a2-relabel",
"word": "never",
"start": 350.975,
"end": 351.25
},
{
"line": 45,
"beat": "a2-relabel",
"word": "relabels",
"start": 351.25,
"end": 351.775
},
{
"line": 45,
"beat": "a2-relabel",
"word": "issues",
"start": 351.775,
"end": 352.525
},
{
"line": 46,
"beat": "a2-close",
"word": "Then",
"start": 355.65,
"end": 355.813
},
{
"line": 46,
"beat": "a2-close",
"word": "Alice",
"start": 355.813,
"end": 356.15
},
{
"line": 46,
"beat": "a2-close",
"word": "asks",
"start": 356.15,
"end": 356.475
},
{
"line": 46,
"beat": "a2-close",
"word": "Q-A",
"start": 356.475,
"end": 356.8
},
{
"line": 46,
"beat": "a2-close",
"word": "sign-off",
"start": 356.8,
"end": 357.538
},
{
"line": 46,
"beat": "a2-close",
"word": "to",
"start": 357.538,
"end": 357.675
},
{
"line": 46,
"beat": "a2-close",
"word": "close",
"start": 357.675,
"end": 357.988
},
{
"line": 46,
"beat": "a2-close",
"word": "the",
"start": 357.988,
"end": 358.1
},
{
"line": 46,
"beat": "a2-close",
"word": "issue",
"start": 358.1,
"end": 358.488
},
{
"line": 46,
"beat": "a2-close",
"word": "as",
"start": 358.488,
"end": 358.65
},
{
"line": 46,
"beat": "a2-close",
"word": "completed",
"start": 358.65,
"end": 359.425
},
{
"line": 47,
"beat": "a2-close",
"word": "This",
"start": 362.975,
"end": 363.15
},
{
"line": 47,
"beat": "a2-close",
"word": "is",
"start": 363.15,
"end": 363.275
},
{
"line": 47,
"beat": "a2-close",
"word": "allowed",
"start": 363.275,
"end": 363.75
},
{
"line": 47,
"beat": "a2-close",
"word": "but",
"start": 363.8,
"end": 363.95
},
{
"line": 47,
"beat": "a2-close",
"word": "it",
"start": 363.95,
"end": 364.05
},
{
"line": 47,
"beat": "a2-close",
"word": "is",
"start": 364.05,
"end": 364.188
},
{
"line": 47,
"beat": "a2-close",
"word": "still",
"start": 364.188,
"end": 364.475
},
{
"line": 47,
"beat": "a2-close",
"word": "yellow",
"start": 364.475,
"end": 365.2
},
{
"line": 47,
"beat": "a2-close",
"word": "Proper",
"start": 365.388,
"end": 365.775
},
{
"line": 47,
"beat": "a2-close",
"word": "procedure",
"start": 365.775,
"end": 366.275
},
{
"line": 47,
"beat": "a2-close",
"word": "has",
"start": 366.275,
"end": 366.463
},
{
"line": 47,
"beat": "a2-close",
"word": "the",
"start": 366.463,
"end": 366.55
},
{
"line": 47,
"beat": "a2-close",
"word": "tester",
"start": 366.55,
"end": 366.95
},
{
"line": 47,
"beat": "a2-close",
"word": "verify",
"start": 366.95,
"end": 367.45
},
{
"line": 47,
"beat": "a2-close",
"word": "a",
"start": 367.45,
"end": 367.575
},
{
"line": 47,
"beat": "a2-close",
"word": "fix",
"start": 367.575,
"end": 368.088
},
{
"line": 47,
"beat": "a2-close",
"word": "and",
"start": 368.088,
"end": 368.238
},
{
"line": 47,
"beat": "a2-close",
"word": "close",
"start": 368.238,
"end": 368.563
},
{
"line": 47,
"beat": "a2-close",
"word": "the",
"start": 368.563,
"end": 368.675
},
{
"line": 47,
"beat": "a2-close",
"word": "issue",
"start": 368.675,
"end": 369.225
},
{
"line": 47,
"beat": "a2-close",
"word": "not",
"start": 369.35,
"end": 369.563
},
{
"line": 47,
"beat": "a2-close",
"word": "the",
"start": 369.563,
"end": 369.65
},
{
"line": 47,
"beat": "a2-close",
"word": "developer",
"start": 369.65,
"end": 370.55
},
{
"line": 47,
"beat": "a2-close",
"word": "That",
"start": 370.725,
"end": 370.975
},
{
"line": 47,
"beat": "a2-close",
"word": "is",
"start": 370.975,
"end": 371.125
},
{
"line": 47,
"beat": "a2-close",
"word": "company",
"start": 371.125,
"end": 371.475
},
{
"line": 47,
"beat": "a2-close",
"word": "procedure",
"start": 371.475,
"end": 372.175
},
{
"line": 47,
"beat": "a2-close",
"word": "and",
"start": 372.275,
"end": 372.388
},
{
"line": 47,
"beat": "a2-close",
"word": "it",
"start": 372.388,
"end": 372.475
},
{
"line": 47,
"beat": "a2-close",
"word": "has",
"start": 372.475,
"end": 372.725
},
{
"line": 47,
"beat": "a2-close",
"word": "to",
"start": 372.725,
"end": 372.813
},
{
"line": 47,
"beat": "a2-close",
"word": "be",
"start": 372.813,
"end": 372.95
},
{
"line": 47,
"beat": "a2-close",
"word": "enforced",
"start": 372.95,
"end": 373.463
},
{
"line": 47,
"beat": "a2-close",
"word": "on",
"start": 373.463,
"end": 373.563
},
{
"line": 47,
"beat": "a2-close",
"word": "the",
"start": 373.563,
"end": 373.675
},
{
"line": 47,
"beat": "a2-close",
"word": "agent",
"start": 373.675,
"end": 374.4
},
{
"line": 48,
"beat": "a2-signoff",
"word": "With",
"start": 377.0,
"end": 377.138
},
{
"line": 48,
"beat": "a2-signoff",
"word": "the",
"start": 377.138,
"end": 377.25
},
{
"line": 48,
"beat": "a2-signoff",
"word": "issue",
"start": 377.25,
"end": 377.55
},
{
"line": 48,
"beat": "a2-signoff",
"word": "reopened",
"start": 377.55,
"end": 378.15
},
{
"line": 48,
"beat": "a2-signoff",
"word": "Bob",
"start": 378.225,
"end": 378.525
},
{
"line": 48,
"beat": "a2-signoff",
"word": "posts",
"start": 378.525,
"end": 378.913
},
{
"line": 48,
"beat": "a2-signoff",
"word": "the",
"start": 378.913,
"end": 379.013
},
{
"line": 48,
"beat": "a2-signoff",
"word": "same",
"start": 379.013,
"end": 379.338
},
{
"line": 48,
"beat": "a2-signoff",
"word": "sign-off",
"start": 379.338,
"end": 380.15
},
{
"line": 48,
"beat": "a2-signoff",
"word": "verified",
"start": 380.288,
"end": 381.075
},
{
"line": 48,
"beat": "a2-signoff",
"word": "Alice",
"start": 381.213,
"end": 381.575
},
{
"line": 48,
"beat": "a2-signoff",
"word": "resolved",
"start": 381.575,
"end": 382.075
},
{
"line": 48,
"beat": "a2-signoff",
"word": "it",
"start": 382.075,
"end": 382.375
},
{
"line": 48,
"beat": "a2-signoff",
"word": "for",
"start": 382.45,
"end": 382.575
},
{
"line": 48,
"beat": "a2-signoff",
"word": "any",
"start": 382.575,
"end": 382.775
},
{
"line": 48,
"beat": "a2-signoff",
"word": "questions",
"start": 382.775,
"end": 383.4
},
{
"line": 48,
"beat": "a2-signoff",
"word": "reach",
"start": 383.475,
"end": 383.725
},
{
"line": 48,
"beat": "a2-signoff",
"word": "Alice",
"start": 383.725,
"end": 384.1
},
{
"line": 48,
"beat": "a2-signoff",
"word": "at",
"start": 384.1,
"end": 384.188
},
{
"line": 48,
"beat": "a2-signoff",
"word": "her",
"start": 384.188,
"end": 384.3
},
{
"line": 48,
"beat": "a2-signoff",
"word": "email",
"start": 384.3,
"end": 385.125
},
{
"line": 48,
"beat": "a2-signoff",
"word": "And",
"start": 385.225,
"end": 385.363
},
{
"line": 48,
"beat": "a2-signoff",
"word": "then",
"start": 385.363,
"end": 385.563
},
{
"line": 48,
"beat": "a2-signoff",
"word": "Q-A",
"start": 385.563,
"end": 385.938
},
{
"line": 48,
"beat": "a2-signoff",
"word": "sign-off",
"start": 385.938,
"end": 386.45
},
{
"line": 48,
"beat": "a2-signoff",
"word": "should",
"start": 386.45,
"end": 386.625
},
{
"line": 48,
"beat": "a2-signoff",
"word": "close",
"start": 386.625,
"end": 386.938
},
{
"line": 48,
"beat": "a2-signoff",
"word": "the",
"start": 386.938,
"end": 387.075
},
{
"line": 48,
"beat": "a2-signoff",
"word": "issue",
"start": 387.075,
"end": 387.75
},
{
"line": 49,
"beat": "a2-signoff",
"word": "This",
"start": 391.4,
"end": 391.575
},
{
"line": 49,
"beat": "a2-signoff",
"word": "is",
"start": 391.575,
"end": 391.7
},
{
"line": 49,
"beat": "a2-signoff",
"word": "allowed",
"start": 391.7,
"end": 392.125
},
{
"line": 49,
"beat": "a2-signoff",
"word": "too",
"start": 392.125,
"end": 392.575
},
{
"line": 49,
"beat": "a2-signoff",
"word": "and",
"start": 392.688,
"end": 392.863
},
{
"line": 49,
"beat": "a2-signoff",
"word": "still",
"start": 392.863,
"end": 393.15
},
{
"line": 49,
"beat": "a2-signoff",
"word": "yellow",
"start": 393.15,
"end": 393.95
},
{
"line": 49,
"beat": "a2-signoff",
"word": "The",
"start": 394.113,
"end": 394.288
},
{
"line": 49,
"beat": "a2-signoff",
"word": "agent",
"start": 394.288,
"end": 394.725
},
{
"line": 49,
"beat": "a2-signoff",
"word": "is",
"start": 394.725,
"end": 394.888
},
{
"line": 49,
"beat": "a2-signoff",
"word": "only",
"start": 394.888,
"end": 395.2
},
{
"line": 49,
"beat": "a2-signoff",
"word": "concerned",
"start": 395.2,
"end": 395.738
},
{
"line": 49,
"beat": "a2-signoff",
"word": "with",
"start": 395.738,
"end": 395.863
},
{
"line": 49,
"beat": "a2-signoff",
"word": "its",
"start": 395.863,
"end": 396.05
},
{
"line": 49,
"beat": "a2-signoff",
"word": "own",
"start": 396.05,
"end": 396.313
},
{
"line": 49,
"beat": "a2-signoff",
"word": "job",
"start": 396.313,
"end": 396.95
},
{
"line": 49,
"beat": "a2-signoff",
"word": "data",
"start": 397.088,
"end": 397.425
},
{
"line": 49,
"beat": "a2-signoff",
"word": "governance",
"start": 397.425,
"end": 397.9
},
{
"line": 49,
"beat": "a2-signoff",
"word": "and",
"start": 397.9,
"end": 398.025
},
{
"line": 49,
"beat": "a2-signoff",
"word": "company",
"start": 398.025,
"end": 398.4
},
{
"line": 49,
"beat": "a2-signoff",
"word": "policy",
"start": 398.4,
"end": 399.1
},
{
"line": 49,
"beat": "a2-signoff",
"word": "have",
"start": 399.1,
"end": 399.3
},
{
"line": 49,
"beat": "a2-signoff",
"word": "to",
"start": 399.3,
"end": 399.388
},
{
"line": 49,
"beat": "a2-signoff",
"word": "be",
"start": 399.388,
"end": 399.525
},
{
"line": 49,
"beat": "a2-signoff",
"word": "enforced",
"start": 399.525,
"end": 400.038
},
{
"line": 49,
"beat": "a2-signoff",
"word": "on",
"start": 400.038,
"end": 400.138
},
{
"line": 49,
"beat": "a2-signoff",
"word": "the",
"start": 400.138,
"end": 400.25
},
{
"line": 49,
"beat": "a2-signoff",
"word": "agent",
"start": 400.25,
"end": 400.975
},
{
"line": 50,
"beat": "act2end",
"word": "Now",
"start": 403.65,
"end": 403.963
},
{
"line": 50,
"beat": "act2end",
"word": "let's",
"start": 403.963,
"end": 404.213
},
{
"line": 50,
"beat": "act2end",
"word": "address",
"start": 404.213,
"end": 404.588
},
{
"line": 50,
"beat": "act2end",
"word": "access",
"start": 404.588,
"end": 405.05
},
{
"line": 50,
"beat": "act2end",
"word": "control",
"start": 405.05,
"end": 405.8
},
{
"line": 50,
"beat": "act2end",
"word": "and",
"start": 405.8,
"end": 405.95
},
{
"line": 50,
"beat": "act2end",
"word": "data",
"start": 405.95,
"end": 406.275
},
{
"line": 50,
"beat": "act2end",
"word": "governance",
"start": 406.275,
"end": 406.888
},
{
"line": 50,
"beat": "act2end",
"word": "in",
"start": 406.888,
"end": 406.988
},
{
"line": 50,
"beat": "act2end",
"word": "a",
"start": 406.988,
"end": 407.075
},
{
"line": 50,
"beat": "act2end",
"word": "company",
"start": 407.075,
"end": 407.475
},
{
"line": 50,
"beat": "act2end",
"word": "policy",
"start": 407.475,
"end": 408.325
},
{
"line": 50,
"beat": "act2end",
"word": "that",
"start": 408.325,
"end": 408.488
},
{
"line": 50,
"beat": "act2end",
"word": "applies",
"start": 408.488,
"end": 408.95
},
{
"line": 50,
"beat": "act2end",
"word": "to",
"start": 408.95,
"end": 409.088
},
{
"line": 50,
"beat": "act2end",
"word": "all",
"start": 409.088,
"end": 409.35
},
{
"line": 50,
"beat": "act2end",
"word": "agents",
"start": 409.35,
"end": 409.85
},
{
"line": 50,
"beat": "act2end",
"word": "in",
"start": 409.85,
"end": 409.938
},
{
"line": 50,
"beat": "act2end",
"word": "the",
"start": 409.938,
"end": 410.025
},
{
"line": 50,
"beat": "act2end",
"word": "system",
"start": 410.025,
"end": 410.75
},
{
"line": 51,
"beat": "policy",
"word": "An",
"start": 412.35,
"end": 412.488
},
{
"line": 51,
"beat": "policy",
"word": "operator",
"start": 412.488,
"end": 413.1
},
{
"line": 51,
"beat": "policy",
"word": "writes",
"start": 413.1,
"end": 413.4
},
{
"line": 51,
"beat": "policy",
"word": "the",
"start": 413.4,
"end": 413.5
},
{
"line": 51,
"beat": "policy",
"word": "company",
"start": 413.5,
"end": 413.9
},
{
"line": 51,
"beat": "policy",
"word": "security",
"start": 413.9,
"end": 414.375
},
{
"line": 51,
"beat": "policy",
"word": "policy",
"start": 414.375,
"end": 414.863
},
{
"line": 51,
"beat": "policy",
"word": "once",
"start": 414.863,
"end": 415.5
},
{
"line": 51,
"beat": "policy",
"word": "in",
"start": 415.625,
"end": 415.775
},
{
"line": 51,
"beat": "policy",
"word": "plain",
"start": 415.775,
"end": 416.1
},
{
"line": 51,
"beat": "policy",
"word": "language",
"start": 416.1,
"end": 416.775
},
{
"line": 51,
"beat": "policy",
"word": "on",
"start": 416.875,
"end": 417.088
},
{
"line": 51,
"beat": "policy",
"word": "Dam's",
"start": 417.088,
"end": 417.513
},
{
"line": 51,
"beat": "policy",
"word": "operator",
"start": 417.513,
"end": 418.05
},
{
"line": 51,
"beat": "policy",
"word": "page",
"start": 418.05,
"end": 418.7
},
{
"line": 52,
"beat": "policy-text",
"word": "Developers",
"start": 419.85,
"end": 420.475
},
{
"line": 52,
"beat": "policy-text",
"word": "and",
"start": 420.475,
"end": 420.6
},
{
"line": 52,
"beat": "policy-text",
"word": "testers",
"start": 420.6,
"end": 421.163
},
{
"line": 52,
"beat": "policy-text",
"word": "may",
"start": 421.163,
"end": 421.413
},
{
"line": 52,
"beat": "policy-text",
"word": "read",
"start": 421.413,
"end": 421.675
},
{
"line": 52,
"beat": "policy-text",
"word": "issues",
"start": 421.675,
"end": 422.375
},
{
"line": 52,
"beat": "policy-text",
"word": "comment",
"start": 422.488,
"end": 422.888
},
{
"line": 52,
"beat": "policy-text",
"word": "on",
"start": 422.888,
"end": 423.038
},
{
"line": 52,
"beat": "policy-text",
"word": "them",
"start": 423.038,
"end": 423.338
},
{
"line": 52,
"beat": "policy-text",
"word": "and",
"start": 423.338,
"end": 423.5
},
{
"line": 52,
"beat": "policy-text",
"word": "label",
"start": 423.5,
"end": 423.875
},
{
"line": 52,
"beat": "policy-text",
"word": "them",
"start": 423.875,
"end": 424.575
},
{
"line": 52,
"beat": "policy-text",
"word": "Only",
"start": 424.738,
"end": 425.063
},
{
"line": 52,
"beat": "policy-text",
"word": "testers",
"start": 425.063,
"end": 425.575
},
{
"line": 52,
"beat": "policy-text",
"word": "close",
"start": 425.575,
"end": 425.888
},
{
"line": 52,
"beat": "policy-text",
"word": "an",
"start": 425.888,
"end": 425.975
},
{
"line": 52,
"beat": "policy-text",
"word": "issue",
"start": 425.975,
"end": 426.625
},
{
"line": 52,
"beat": "policy-text",
"word": "and",
"start": 426.75,
"end": 426.863
},
{
"line": 52,
"beat": "policy-text",
"word": "the",
"start": 426.863,
"end": 426.963
},
{
"line": 52,
"beat": "policy-text",
"word": "Q-A",
"start": 426.963,
"end": 427.338
},
{
"line": 52,
"beat": "policy-text",
"word": "sign-off",
"start": 427.338,
"end": 427.8
},
{
"line": 52,
"beat": "policy-text",
"word": "agent",
"start": 427.8,
"end": 428.238
},
{
"line": 52,
"beat": "policy-text",
"word": "is",
"start": 428.238,
"end": 428.413
},
{
"line": 52,
"beat": "policy-text",
"word": "used",
"start": 428.413,
"end": 428.75
},
{
"line": 52,
"beat": "policy-text",
"word": "by",
"start": 428.75,
"end": 428.938
},
{
"line": 52,
"beat": "policy-text",
"word": "testers",
"start": 428.938,
"end": 429.425
},
{
"line": 52,
"beat": "policy-text",
"word": "only",
"start": 429.425,
"end": 430.25
},
{
"line": 52,
"beat": "policy-text",
"word": "Personal",
"start": 430.438,
"end": 430.95
},
{
"line": 52,
"beat": "policy-text",
"word": "data",
"start": 430.95,
"end": 431.363
},
{
"line": 52,
"beat": "policy-text",
"word": "and",
"start": 431.363,
"end": 431.488
},
{
"line": 52,
"beat": "policy-text",
"word": "restricted",
"start": 431.488,
"end": 432.025
},
{
"line": 52,
"beat": "policy-text",
"word": "data",
"start": 432.025,
"end": 432.675
},
{
"line": 52,
"beat": "policy-text",
"word": "must",
"start": 432.675,
"end": 432.95
},
{
"line": 52,
"beat": "policy-text",
"word": "not",
"start": 432.95,
"end": 433.163
},
{
"line": 52,
"beat": "policy-text",
"word": "be",
"start": 433.163,
"end": 433.313
},
{
"line": 52,
"beat": "policy-text",
"word": "sent",
"start": 433.313,
"end": 433.638
},
{
"line": 52,
"beat": "policy-text",
"word": "to",
"start": 433.638,
"end": 433.775
},
{
"line": 52,
"beat": "policy-text",
"word": "an",
"start": 433.775,
"end": 433.925
},
{
"line": 52,
"beat": "policy-text",
"word": "untrusted",
"start": 433.925,
"end": 434.625
},
{
"line": 52,
"beat": "policy-text",
"word": "external",
"start": 434.625,
"end": 435.175
},
{
"line": 52,
"beat": "policy-text",
"word": "destination",
"start": 435.175,
"end": 436.25
},
{
"line": 53,
"beat": "split",
"word": "Sentry",
"start": 437.375,
"end": 437.725
},
{
"line": 53,
"beat": "split",
"word": "takes",
"start": 437.725,
"end": 438.063
},
{
"line": 53,
"beat": "split",
"word": "in",
"start": 438.063,
"end": 438.163
},
{
"line": 53,
"beat": "split",
"word": "the",
"start": 438.163,
"end": 438.25
},
{
"line": 53,
"beat": "split",
"word": "security",
"start": 438.25,
"end": 438.75
},
{
"line": 53,
"beat": "split",
"word": "policy",
"start": 438.75,
"end": 439.388
},
{
"line": 53,
"beat": "split",
"word": "and",
"start": 439.388,
"end": 439.55
},
{
"line": 53,
"beat": "split",
"word": "builds",
"start": 439.55,
"end": 439.9
},
{
"line": 53,
"beat": "split",
"word": "the",
"start": 439.9,
"end": 440.0
},
{
"line": 53,
"beat": "split",
"word": "corresponding",
"start": 440.0,
"end": 440.775
},
{
"line": 53,
"beat": "split",
"word": "rules",
"start": 440.775,
"end": 441.65
},
{
"line": 53,
"beat": "split",
"word": "access",
"start": 441.813,
"end": 442.3
},
{
"line": 53,
"beat": "split",
"word": "control",
"start": 442.3,
"end": 442.725
},
{
"line": 53,
"beat": "split",
"word": "rules",
"start": 442.725,
"end": 443.325
},
{
"line": 53,
"beat": "split",
"word": "for",
"start": 443.425,
"end": 443.55
},
{
"line": 53,
"beat": "split",
"word": "developers",
"start": 443.55,
"end": 444.175
},
{
"line": 53,
"beat": "split",
"word": "and",
"start": 444.175,
"end": 444.3
},
{
"line": 53,
"beat": "split",
"word": "testers",
"start": 444.3,
"end": 444.975
},
{
"line": 53,
"beat": "split",
"word": "and",
"start": 445.05,
"end": 445.213
},
{
"line": 53,
"beat": "split",
"word": "data",
"start": 445.213,
"end": 445.525
},
{
"line": 53,
"beat": "split",
"word": "governance",
"start": 445.525,
"end": 445.975
},
{
"line": 53,
"beat": "split",
"word": "rules",
"start": 445.975,
"end": 446.5
},
{
"line": 53,
"beat": "split",
"word": "for",
"start": 446.575,
"end": 446.7
},
{
"line": 53,
"beat": "split",
"word": "personal",
"start": 446.7,
"end": 447.175
},
{
"line": 53,
"beat": "split",
"word": "data",
"start": 447.175,
"end": 447.8
},
{
"line": 54,
"beat": "split",
"word": "On",
"start": 448.6,
"end": 448.713
},
{
"line": 54,
"beat": "split",
"word": "the",
"start": 448.713,
"end": 448.8
},
{
"line": 54,
"beat": "split",
"word": "right",
"start": 448.8,
"end": 449.125
},
{
"line": 54,
"beat": "split",
"word": "the",
"start": 449.2,
"end": 449.35
},
{
"line": 54,
"beat": "split",
"word": "same",
"start": 449.35,
"end": 449.7
},
{
"line": 54,
"beat": "split",
"word": "rules",
"start": 449.7,
"end": 450.05
},
{
"line": 54,
"beat": "split",
"word": "as",
"start": 450.05,
"end": 450.213
},
{
"line": 54,
"beat": "split",
"word": "O-P-A",
"start": 450.213,
"end": 450.938
},
{
"line": 54,
"beat": "split",
"word": "evaluates",
"start": 450.938,
"end": 451.6
},
{
"line": 54,
"beat": "split",
"word": "them",
"start": 451.6,
"end": 451.975
},
{
"line": 54,
"beat": "split",
"word": "in",
"start": 452.075,
"end": 452.2
},
{
"line": 54,
"beat": "split",
"word": "Rego",
"start": 452.2,
"end": 453.075
},
{
"line": 54,
"beat": "split",
"word": "The",
"start": 453.263,
"end": 453.4
},
{
"line": 54,
"beat": "split",
"word": "roles",
"start": 453.4,
"end": 453.8
},
{
"line": 54,
"beat": "split",
"word": "come",
"start": 453.8,
"end": 454.025
},
{
"line": 54,
"beat": "split",
"word": "from",
"start": 454.025,
"end": 454.225
},
{
"line": 54,
"beat": "split",
"word": "Key-cloak",
"start": 454.225,
"end": 454.95
},
{
"line": 54,
"beat": "split",
"word": "and",
"start": 455.05,
"end": 455.175
},
{
"line": 54,
"beat": "split",
"word": "no",
"start": 455.175,
"end": 455.413
},
{
"line": 54,
"beat": "split",
"word": "agent",
"start": 455.413,
"end": 455.875
},
{
"line": 54,
"beat": "split",
"word": "or",
"start": 455.875,
"end": 456.025
},
{
"line": 54,
"beat": "split",
"word": "gateway",
"start": 456.025,
"end": 456.4
},
{
"line": 54,
"beat": "split",
"word": "restarts",
"start": 456.4,
"end": 457.25
},
{
"line": 55,
"beat": "act3",
"word": "Act",
"start": 460.375,
"end": 460.625
},
{
"line": 55,
"beat": "act3",
"word": "three",
"start": 460.625,
"end": 461.075
},
{
"line": 55,
"beat": "act3",
"word": "Sentry",
"start": 461.25,
"end": 461.85
},
{
"line": 55,
"beat": "act3",
"word": "with",
"start": 461.925,
"end": 462.063
},
{
"line": 55,
"beat": "act3",
"word": "the",
"start": 462.063,
"end": 462.15
},
{
"line": 55,
"beat": "act3",
"word": "company",
"start": 462.15,
"end": 462.5
},
{
"line": 55,
"beat": "act3",
"word": "security",
"start": 462.5,
"end": 462.925
},
{
"line": 55,
"beat": "act3",
"word": "policy",
"start": 462.925,
"end": 463.7
},
{
"line": 56,
"beat": "a3-handover",
"word": "Alice",
"start": 464.85,
"end": 465.175
},
{
"line": 56,
"beat": "a3-handover",
"word": "hands",
"start": 465.175,
"end": 465.438
},
{
"line": 56,
"beat": "a3-handover",
"word": "the",
"start": 465.438,
"end": 465.55
},
{
"line": 56,
"beat": "a3-handover",
"word": "issue",
"start": 465.55,
"end": 465.9
},
{
"line": 56,
"beat": "a3-handover",
"word": "over",
"start": 465.9,
"end": 466.125
},
{
"line": 56,
"beat": "a3-handover",
"word": "to",
"start": 466.125,
"end": 466.225
},
{
"line": 56,
"beat": "a3-handover",
"word": "Bob",
"start": 466.225,
"end": 466.488
},
{
"line": 56,
"beat": "a3-handover",
"word": "again",
"start": 466.488,
"end": 467.15
},
{
"line": 56,
"beat": "a3-handover",
"word": "the",
"start": 467.275,
"end": 467.425
},
{
"line": 56,
"beat": "a3-handover",
"word": "fix",
"start": 467.425,
"end": 467.8
},
{
"line": 56,
"beat": "a3-handover",
"word": "is",
"start": 467.8,
"end": 467.925
},
{
"line": 56,
"beat": "a3-handover",
"word": "in",
"start": 467.925,
"end": 468.075
},
{
"line": 56,
"beat": "a3-handover",
"word": "P-R",
"start": 468.075,
"end": 468.425
},
{
"line": 56,
"beat": "a3-handover",
"word": "twelve",
"start": 468.425,
"end": 468.975
},
{
"line": 56,
"beat": "a3-handover",
"word": "ready",
"start": 469.075,
"end": 469.325
},
{
"line": 56,
"beat": "a3-handover",
"word": "for",
"start": 469.325,
"end": 469.45
},
{
"line": 56,
"beat": "a3-handover",
"word": "testing",
"start": 469.45,
"end": 470.175
},
{
"line": 57,
"beat": "a3-handover",
"word": "That",
"start": 476.025,
"end": 476.25
},
{
"line": 57,
"beat": "a3-handover",
"word": "still",
"start": 476.25,
"end": 476.5
},
{
"line": 57,
"beat": "a3-handover",
"word": "works",
"start": 476.5,
"end": 477.125
},
{
"line": 58,
"beat": "a3-sendback",
"word": "And",
"start": 479.25,
"end": 479.4
},
{
"line": 58,
"beat": "a3-sendback",
"word": "Bob",
"start": 479.4,
"end": 479.738
},
{
"line": 58,
"beat": "a3-sendback",
"word": "sends",
"start": 479.738,
"end": 480.038
},
{
"line": 58,
"beat": "a3-sendback",
"word": "it",
"start": 480.038,
"end": 480.125
},
{
"line": 58,
"beat": "a3-sendback",
"word": "back",
"start": 480.125,
"end": 480.775
},
{
"line": 58,
"beat": "a3-sendback",
"word": "it",
"start": 480.913,
"end": 481.05
},
{
"line": 58,
"beat": "a3-sendback",
"word": "still",
"start": 481.05,
"end": 481.338
},
{
"line": 58,
"beat": "a3-sendback",
"word": "fails",
"start": 481.338,
"end": 481.738
},
{
"line": 58,
"beat": "a3-sendback",
"word": "on",
"start": 481.738,
"end": 481.9
},
{
"line": 58,
"beat": "a3-sendback",
"word": "Safari",
"start": 481.9,
"end": 482.35
},
{
"line": 58,
"beat": "a3-sendback",
"word": "seventeen",
"start": 482.35,
"end": 483.15
},
{
"line": 58,
"beat": "a3-sendback",
"word": "so",
"start": 483.25,
"end": 483.388
},
{
"line": 58,
"beat": "a3-sendback",
"word": "the",
"start": 483.388,
"end": 483.5
},
{
"line": 58,
"beat": "a3-sendback",
"word": "label",
"start": 483.5,
"end": 483.9
},
{
"line": 58,
"beat": "a3-sendback",
"word": "is",
"start": 483.9,
"end": 484.05
},
{
"line": 58,
"beat": "a3-sendback",
"word": "needs",
"start": 484.05,
"end": 484.288
},
{
"line": 58,
"beat": "a3-sendback",
"word": "a",
"start": 484.288,
"end": 484.388
},
{
"line": 58,
"beat": "a3-sendback",
"word": "fix",
"start": 484.388,
"end": 485.05
},
{
"line": 59,
"beat": "a3-sendback",
"word": "That",
"start": 489.375,
"end": 489.538
},
{
"line": 59,
"beat": "a3-sendback",
"word": "works",
"start": 489.538,
"end": 489.813
},
{
"line": 59,
"beat": "a3-sendback",
"word": "too",
"start": 489.813,
"end": 490.175
},
{
"line": 59,
"beat": "a3-sendback",
"word": "as",
"start": 490.25,
"end": 490.363
},
{
"line": 59,
"beat": "a3-sendback",
"word": "it",
"start": 490.363,
"end": 490.463
},
{
"line": 59,
"beat": "a3-sendback",
"word": "should",
"start": 490.463,
"end": 491.025
},
{
"line": 60,
"beat": "a3-close",
"word": "After",
"start": 493.175,
"end": 493.45
},
{
"line": 60,
"beat": "a3-close",
"word": "one",
"start": 493.45,
"end": 493.588
},
{
"line": 60,
"beat": "a3-close",
"word": "more",
"start": 493.588,
"end": 493.813
},
{
"line": 60,
"beat": "a3-close",
"word": "fix",
"start": 493.813,
"end": 494.375
},
{
"line": 60,
"beat": "a3-close",
"word": "Alice",
"start": 494.513,
"end": 494.85
},
{
"line": 60,
"beat": "a3-close",
"word": "asks",
"start": 494.85,
"end": 495.175
},
{
"line": 60,
"beat": "a3-close",
"word": "Q-A",
"start": 495.175,
"end": 495.5
},
{
"line": 60,
"beat": "a3-close",
"word": "sign-off",
"start": 495.5,
"end": 496.0
},
{
"line": 60,
"beat": "a3-close",
"word": "to",
"start": 496.0,
"end": 496.125
},
{
"line": 60,
"beat": "a3-close",
"word": "close",
"start": 496.125,
"end": 496.438
},
{
"line": 60,
"beat": "a3-close",
"word": "the",
"start": 496.438,
"end": 496.55
},
{
"line": 60,
"beat": "a3-close",
"word": "issue",
"start": 496.55,
"end": 497.175
},
{
"line": 61,
"beat": "a3-close",
"word": "This",
"start": 500.525,
"end": 500.725
},
{
"line": 61,
"beat": "a3-close",
"word": "time",
"start": 500.725,
"end": 501.125
},
{
"line": 61,
"beat": "a3-close",
"word": "it",
"start": 501.213,
"end": 501.35
},
{
"line": 61,
"beat": "a3-close",
"word": "is",
"start": 501.35,
"end": 501.5
},
{
"line": 61,
"beat": "a3-close",
"word": "denied",
"start": 501.5,
"end": 502.4
},
{
"line": 61,
"beat": "a3-close",
"word": "The",
"start": 502.538,
"end": 502.688
},
{
"line": 61,
"beat": "a3-close",
"word": "user",
"start": 502.688,
"end": 503.05
},
{
"line": 61,
"beat": "a3-close",
"word": "is",
"start": 503.05,
"end": 503.225
},
{
"line": 61,
"beat": "a3-close",
"word": "Alice",
"start": 503.225,
"end": 503.75
},
{
"line": 61,
"beat": "a3-close",
"word": "a",
"start": 503.825,
"end": 503.9
},
{
"line": 61,
"beat": "a3-close",
"word": "developer",
"start": 503.9,
"end": 504.75
},
{
"line": 61,
"beat": "a3-close",
"word": "and",
"start": 504.85,
"end": 504.963
},
{
"line": 61,
"beat": "a3-close",
"word": "the",
"start": 504.963,
"end": 505.05
},
{
"line": 61,
"beat": "a3-close",
"word": "company",
"start": 505.05,
"end": 505.475
},
{
"line": 61,
"beat": "a3-close",
"word": "security",
"start": 505.475,
"end": 506.025
},
{
"line": 61,
"beat": "a3-close",
"word": "policy",
"start": 506.025,
"end": 506.538
},
{
"line": 61,
"beat": "a3-close",
"word": "states",
"start": 506.538,
"end": 506.925
},
{
"line": 61,
"beat": "a3-close",
"word": "that",
"start": 506.925,
"end": 507.113
},
{
"line": 61,
"beat": "a3-close",
"word": "only",
"start": 507.113,
"end": 507.438
},
{
"line": 61,
"beat": "a3-close",
"word": "testers",
"start": 507.438,
"end": 507.963
},
{
"line": 61,
"beat": "a3-close",
"word": "close",
"start": 507.963,
"end": 508.288
},
{
"line": 61,
"beat": "a3-close",
"word": "an",
"start": 508.288,
"end": 508.375
},
{
"line": 61,
"beat": "a3-close",
"word": "issue",
"start": 508.375,
"end": 509.1
},
{
"line": 61,
"beat": "a3-close",
"word": "and",
"start": 509.225,
"end": 509.338
},
{
"line": 61,
"beat": "a3-close",
"word": "that",
"start": 509.338,
"end": 509.438
},
{
"line": 61,
"beat": "a3-close",
"word": "the",
"start": 509.438,
"end": 509.538
},
{
"line": 61,
"beat": "a3-close",
"word": "Q-A",
"start": 509.538,
"end": 509.988
},
{
"line": 61,
"beat": "a3-close",
"word": "sign-off",
"start": 509.988,
"end": 510.45
},
{
"line": 61,
"beat": "a3-close",
"word": "agent",
"start": 510.45,
"end": 510.888
},
{
"line": 61,
"beat": "a3-close",
"word": "is",
"start": 510.888,
"end": 511.063
},
{
"line": 61,
"beat": "a3-close",
"word": "used",
"start": 511.063,
"end": 511.45
},
{
"line": 61,
"beat": "a3-close",
"word": "by",
"start": 511.45,
"end": 511.625
},
{
"line": 61,
"beat": "a3-close",
"word": "testers",
"start": 511.625,
"end": 512.1
},
{
"line": 61,
"beat": "a3-close",
"word": "only",
"start": 512.1,
"end": 512.825
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "Bob",
"start": 515.95,
"end": 516.2
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "verifies",
"start": 516.2,
"end": 516.775
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "the",
"start": 516.775,
"end": 516.888
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "fix",
"start": 516.888,
"end": 517.275
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "and",
"start": 517.275,
"end": 517.4
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "tries",
"start": 517.4,
"end": 517.713
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "the",
"start": 517.713,
"end": 517.813
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "same",
"start": 517.813,
"end": 518.113
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "sign-off",
"start": 518.113,
"end": 518.975
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "verified",
"start": 519.113,
"end": 519.925
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "Alice",
"start": 520.063,
"end": 520.425
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "resolved",
"start": 520.425,
"end": 520.95
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "it",
"start": 520.95,
"end": 521.25
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "for",
"start": 521.325,
"end": 521.45
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "any",
"start": 521.45,
"end": 521.65
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "questions",
"start": 521.65,
"end": 522.275
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "reach",
"start": 522.35,
"end": 522.6
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "Alice",
"start": 522.6,
"end": 522.95
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "at",
"start": 522.95,
"end": 523.038
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "her",
"start": 523.038,
"end": 523.175
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "email",
"start": 523.175,
"end": 523.8
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "and",
"start": 523.9,
"end": 524.013
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "then",
"start": 524.013,
"end": 524.238
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "close",
"start": 524.238,
"end": 524.563
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "the",
"start": 524.563,
"end": 524.7
},
{
"line": 62,
"beat": "a3-signoff-email",
"word": "issue",
"start": 524.7,
"end": 525.375
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "Denied",
"start": 530.3,
"end": 531.075
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "the",
"start": 531.2,
"end": 531.325
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "company",
"start": 531.325,
"end": 531.725
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "security",
"start": 531.725,
"end": 532.25
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "policy",
"start": 532.25,
"end": 532.95
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "states",
"start": 532.95,
"end": 533.325
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "that",
"start": 533.325,
"end": 533.538
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "personal",
"start": 533.538,
"end": 534.025
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "data",
"start": 534.025,
"end": 534.675
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "must",
"start": 534.675,
"end": 534.95
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "not",
"start": 534.95,
"end": 535.163
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "be",
"start": 535.163,
"end": 535.313
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "sent",
"start": 535.313,
"end": 535.6
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "to",
"start": 535.6,
"end": 535.713
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "an",
"start": 535.713,
"end": 535.85
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "untrusted",
"start": 535.85,
"end": 536.525
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "external",
"start": 536.525,
"end": 537.0
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "destination",
"start": 537.0,
"end": 538.0
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "such",
"start": 538.138,
"end": 538.375
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "as",
"start": 538.375,
"end": 538.538
},
{
"line": 63,
"beat": "a3-signoff-email",
"word": "GitHub",
"start": 538.538,
"end": 539.275
},
{
"line": 64,
"beat": "a3-signoff",
"word": "Bob",
"start": 542.45,
"end": 542.725
},
{
"line": 64,
"beat": "a3-signoff",
"word": "writes",
"start": 542.725,
"end": 543.0
},
{
"line": 64,
"beat": "a3-signoff",
"word": "the",
"start": 543.0,
"end": 543.113
},
{
"line": 64,
"beat": "a3-signoff",
"word": "sign-off",
"start": 543.113,
"end": 543.588
},
{
"line": 64,
"beat": "a3-signoff",
"word": "without",
"start": 543.588,
"end": 543.888
},
{
"line": 64,
"beat": "a3-signoff",
"word": "the",
"start": 543.888,
"end": 543.975
},
{
"line": 64,
"beat": "a3-signoff",
"word": "personal",
"start": 543.975,
"end": 544.45
},
{
"line": 64,
"beat": "a3-signoff",
"word": "data",
"start": 544.45,
"end": 545.025
},
{
"line": 64,
"beat": "a3-signoff",
"word": "verified",
"start": 545.188,
"end": 545.738
},
{
"line": 64,
"beat": "a3-signoff",
"word": "on",
"start": 545.738,
"end": 545.9
},
{
"line": 64,
"beat": "a3-signoff",
"word": "Safari",
"start": 545.9,
"end": 546.413
},
{
"line": 64,
"beat": "a3-signoff",
"word": "seventeen",
"start": 546.413,
"end": 547.025
},
{
"line": 64,
"beat": "a3-signoff",
"word": "and",
"start": 547.025,
"end": 547.15
},
{
"line": 64,
"beat": "a3-signoff",
"word": "eighteen",
"start": 547.15,
"end": 547.825
},
{
"line": 64,
"beat": "a3-signoff",
"word": "fixed",
"start": 547.938,
"end": 548.3
},
{
"line": 64,
"beat": "a3-signoff",
"word": "in",
"start": 548.3,
"end": 548.45
},
{
"line": 64,
"beat": "a3-signoff",
"word": "P-R",
"start": 548.45,
"end": 548.8
},
{
"line": 64,
"beat": "a3-signoff",
"word": "twelve",
"start": 548.8,
"end": 549.6
},
{
"line": 64,
"beat": "a3-signoff",
"word": "And",
"start": 549.725,
"end": 549.888
},
{
"line": 64,
"beat": "a3-signoff",
"word": "then",
"start": 549.888,
"end": 550.113
},
{
"line": 64,
"beat": "a3-signoff",
"word": "close",
"start": 550.113,
"end": 550.438
},
{
"line": 64,
"beat": "a3-signoff",
"word": "the",
"start": 550.438,
"end": 550.575
},
{
"line": 64,
"beat": "a3-signoff",
"word": "issue",
"start": 550.575,
"end": 551.25
},
{
"line": 65,
"beat": "a3-signoff",
"word": "Now",
"start": 553.55,
"end": 553.738
},
{
"line": 65,
"beat": "a3-signoff",
"word": "the",
"start": 553.738,
"end": 553.825
},
{
"line": 65,
"beat": "a3-signoff",
"word": "comment",
"start": 553.825,
"end": 554.188
},
{
"line": 65,
"beat": "a3-signoff",
"word": "is",
"start": 554.188,
"end": 554.325
},
{
"line": 65,
"beat": "a3-signoff",
"word": "posted",
"start": 554.325,
"end": 554.95
},
{
"line": 65,
"beat": "a3-signoff",
"word": "and",
"start": 555.025,
"end": 555.138
},
{
"line": 65,
"beat": "a3-signoff",
"word": "the",
"start": 555.138,
"end": 555.25
},
{
"line": 65,
"beat": "a3-signoff",
"word": "issue",
"start": 555.25,
"end": 555.6
},
{
"line": 65,
"beat": "a3-signoff",
"word": "is",
"start": 555.6,
"end": 555.763
},
{
"line": 65,
"beat": "a3-signoff",
"word": "closed",
"start": 555.763,
"end": 556.175
},
{
"line": 65,
"beat": "a3-signoff",
"word": "as",
"start": 556.175,
"end": 556.325
},
{
"line": 65,
"beat": "a3-signoff",
"word": "completed",
"start": 556.325,
"end": 557.25
},
{
"line": 65,
"beat": "a3-signoff",
"word": "Green",
"start": 557.45,
"end": 558.2
},
{
"line": 65,
"beat": "a3-signoff",
"word": "the",
"start": 558.325,
"end": 558.45
},
{
"line": 65,
"beat": "a3-signoff",
"word": "right",
"start": 558.45,
"end": 558.725
},
{
"line": 65,
"beat": "a3-signoff",
"word": "agent",
"start": 558.725,
"end": 559.25
},
{
"line": 65,
"beat": "a3-signoff",
"word": "the",
"start": 559.325,
"end": 559.45
},
{
"line": 65,
"beat": "a3-signoff",
"word": "right",
"start": 559.45,
"end": 559.738
},
{
"line": 65,
"beat": "a3-signoff",
"word": "user",
"start": 559.738,
"end": 560.15
},
{
"line": 65,
"beat": "a3-signoff",
"word": "and",
"start": 560.15,
"end": 560.288
},
{
"line": 65,
"beat": "a3-signoff",
"word": "role",
"start": 560.288,
"end": 560.7
},
{
"line": 65,
"beat": "a3-signoff",
"word": "and",
"start": 560.775,
"end": 560.9
},
{
"line": 65,
"beat": "a3-signoff",
"word": "no",
"start": 560.9,
"end": 561.1
},
{
"line": 65,
"beat": "a3-signoff",
"word": "personal",
"start": 561.1,
"end": 561.575
},
{
"line": 65,
"beat": "a3-signoff",
"word": "data",
"start": 561.575,
"end": 562.2
},
{
"line": 66,
"beat": "summary",
"word": "Here",
"start": 564.85,
"end": 565.013
},
{
"line": 66,
"beat": "summary",
"word": "is",
"start": 565.013,
"end": 565.15
},
{
"line": 66,
"beat": "summary",
"word": "every",
"start": 565.15,
"end": 565.438
},
{
"line": 66,
"beat": "summary",
"word": "request",
"start": 565.438,
"end": 565.975
},
{
"line": 66,
"beat": "summary",
"word": "act",
"start": 566.075,
"end": 566.325
},
{
"line": 66,
"beat": "summary",
"word": "by",
"start": 566.325,
"end": 566.513
},
{
"line": 66,
"beat": "summary",
"word": "act",
"start": 566.513,
"end": 567.125
},
{
"line": 67,
"beat": "summary",
"word": "With",
"start": 567.925,
"end": 568.1
},
{
"line": 67,
"beat": "summary",
"word": "Dam",
"start": 568.1,
"end": 568.4
},
{
"line": 67,
"beat": "summary",
"word": "as",
"start": 568.4,
"end": 568.525
},
{
"line": 67,
"beat": "summary",
"word": "it",
"start": 568.525,
"end": 568.663
},
{
"line": 67,
"beat": "summary",
"word": "is",
"start": 568.663,
"end": 568.975
},
{
"line": 67,
"beat": "summary",
"word": "every",
"start": 569.075,
"end": 569.375
},
{
"line": 67,
"beat": "summary",
"word": "request",
"start": 569.375,
"end": 569.775
},
{
"line": 67,
"beat": "summary",
"word": "goes",
"start": 569.775,
"end": 570.05
},
{
"line": 67,
"beat": "summary",
"word": "through",
"start": 570.05,
"end": 570.525
},
{
"line": 67,
"beat": "summary",
"word": "the",
"start": 570.65,
"end": 570.788
},
{
"line": 67,
"beat": "summary",
"word": "yellow",
"start": 570.788,
"end": 571.113
},
{
"line": 67,
"beat": "summary",
"word": "ones",
"start": 571.113,
"end": 571.388
},
{
"line": 67,
"beat": "summary",
"word": "included",
"start": 571.388,
"end": 572.15
},
{
"line": 68,
"beat": "summary",
"word": "With",
"start": 572.95,
"end": 573.138
},
{
"line": 68,
"beat": "summary",
"word": "Sentry",
"start": 573.138,
"end": 573.575
},
{
"line": 68,
"beat": "summary",
"word": "and",
"start": 573.575,
"end": 573.7
},
{
"line": 68,
"beat": "summary",
"word": "each",
"start": 573.7,
"end": 573.938
},
{
"line": 68,
"beat": "summary",
"word": "agent's",
"start": 573.938,
"end": 574.325
},
{
"line": 68,
"beat": "summary",
"word": "description",
"start": 574.325,
"end": 575.175
},
{
"line": 68,
"beat": "summary",
"word": "each",
"start": 575.3,
"end": 575.563
},
{
"line": 68,
"beat": "summary",
"word": "agent",
"start": 575.563,
"end": 575.95
},
{
"line": 68,
"beat": "summary",
"word": "keeps",
"start": 575.95,
"end": 576.25
},
{
"line": 68,
"beat": "summary",
"word": "to",
"start": 576.25,
"end": 576.388
},
{
"line": 68,
"beat": "summary",
"word": "its",
"start": 576.388,
"end": 576.575
},
{
"line": 68,
"beat": "summary",
"word": "job",
"start": 576.575,
"end": 577.175
},
{
"line": 69,
"beat": "summary",
"word": "With",
"start": 578.0,
"end": 578.163
},
{
"line": 69,
"beat": "summary",
"word": "the",
"start": 578.163,
"end": 578.25
},
{
"line": 69,
"beat": "summary",
"word": "company",
"start": 578.25,
"end": 578.65
},
{
"line": 69,
"beat": "summary",
"word": "security",
"start": 578.65,
"end": 579.125
},
{
"line": 69,
"beat": "summary",
"word": "policy",
"start": 579.125,
"end": 579.8
},
{
"line": 69,
"beat": "summary",
"word": "the",
"start": 579.925,
"end": 580.05
},
{
"line": 69,
"beat": "summary",
"word": "user",
"start": 580.05,
"end": 580.425
},
{
"line": 69,
"beat": "summary",
"word": "and",
"start": 580.425,
"end": 580.55
},
{
"line": 69,
"beat": "summary",
"word": "role",
"start": 580.55,
"end": 581.05
},
{
"line": 69,
"beat": "summary",
"word": "and",
"start": 581.125,
"end": 581.238
},
{
"line": 69,
"beat": "summary",
"word": "the",
"start": 581.238,
"end": 581.325
},
{
"line": 69,
"beat": "summary",
"word": "data",
"start": 581.325,
"end": 581.8
},
{
"line": 69,
"beat": "summary",
"word": "are",
"start": 581.9,
"end": 582.0
},
{
"line": 69,
"beat": "summary",
"word": "decided",
"start": 582.0,
"end": 582.488
},
{
"line": 69,
"beat": "summary",
"word": "too",
"start": 582.488,
"end": 583.075
},
{
"line": 70,
"beat": "close",
"word": "A",
"start": 587.2,
"end": 587.288
},
{
"line": 70,
"beat": "close",
"word": "call",
"start": 587.288,
"end": 587.6
},
{
"line": 70,
"beat": "close",
"word": "goes",
"start": 587.6,
"end": 587.85
},
{
"line": 70,
"beat": "close",
"word": "through",
"start": 587.85,
"end": 588.225
},
{
"line": 70,
"beat": "close",
"word": "only",
"start": 588.225,
"end": 588.588
},
{
"line": 70,
"beat": "close",
"word": "when",
"start": 588.588,
"end": 588.713
},
{
"line": 70,
"beat": "close",
"word": "the",
"start": 588.713,
"end": 588.838
},
{
"line": 70,
"beat": "close",
"word": "agent's",
"start": 588.838,
"end": 589.213
},
{
"line": 70,
"beat": "close",
"word": "intent",
"start": 589.213,
"end": 589.975
},
{
"line": 70,
"beat": "close",
"word": "and",
"start": 589.975,
"end": 590.113
},
{
"line": 70,
"beat": "close",
"word": "the",
"start": 590.113,
"end": 590.2
},
{
"line": 70,
"beat": "close",
"word": "company's",
"start": 590.2,
"end": 590.65
},
{
"line": 70,
"beat": "close",
"word": "policy",
"start": 590.65,
"end": 591.188
},
{
"line": 70,
"beat": "close",
"word": "both",
"start": 591.188,
"end": 591.5
},
{
"line": 70,
"beat": "close",
"word": "allow",
"start": 591.5,
"end": 591.8
},
{
"line": 70,
"beat": "close",
"word": "it",
"start": 591.8,
"end": 592.225
},
{
"line": 71,
"beat": "close",
"word": "Dam",
"start": 593.075,
"end": 593.4
},
{
"line": 71,
"beat": "close",
"word": "with",
"start": 593.4,
"end": 593.575
},
{
"line": 71,
"beat": "close",
"word": "granular",
"start": 593.575,
"end": 594.125
},
{
"line": 71,
"beat": "close",
"word": "security",
"start": 594.125,
"end": 594.95
},
{
"line": 71,
"beat": "close",
"word": "control",
"start": 595.075,
"end": 595.638
},
{
"line": 71,
"beat": "close",
"word": "every",
"start": 595.638,
"end": 595.988
},
{
"line": 71,
"beat": "close",
"word": "tool",
"start": 595.988,
"end": 596.275
},
{
"line": 71,
"beat": "close",
"word": "call",
"start": 596.275,
"end": 596.7
},
{
"line": 71,
"beat": "close",
"word": "and",
"start": 596.775,
"end": 596.938
},
{
"line": 71,
"beat": "close",
"word": "every",
"start": 596.938,
"end": 597.25
},
{
"line": 71,
"beat": "close",
"word": "piece",
"start": 597.25,
"end": 597.513
},
{
"line": 71,
"beat": "close",
"word": "of",
"start": 597.513,
"end": 597.6
},
{
"line": 71,
"beat": "close",
"word": "data",
"start": 597.6,
"end": 598.2
},
{
"line": 72,
"beat": "close",
"word": "Sentry",
"start": 599.125,
"end": 599.475
},
{
"line": 72,
"beat": "close",
"word": "on",
"start": 599.513,
"end": 599.625
},
{
"line": 72,
"beat": "close",
"word": "Dam",
"start": 599.625,
"end": 600.25
}
];
