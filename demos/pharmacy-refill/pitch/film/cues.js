window.LINES = [
 {
  "n": 1,
  "beat": "app",
  "text": "Rosa needs a refill.",
  "start": 0.4,
  "end": 2.25
 },
 {
  "n": 2,
  "beat": "app",
  "text": "She taps Refill my prescription in her pharmacy's mobile app, which triggers an agentic flow.",
  "start": 2.55,
  "end": 8.2
 },
 {
  "n": 3,
  "beat": "flow",
  "text": "That one tap sets off twenty-six agent and tool interactions.",
  "start": 8.8,
  "end": 12.825
 },
 {
  "n": 4,
  "beat": "flow",
  "text": "Sentry tracks the data through each one, and checks it against company policy.",
  "start": 13.125,
  "end": 17.775
 },
 {
  "n": 5,
  "beat": "flow",
  "text": "One, it marks as risky. Let's see why.",
  "start": 18.075,
  "end": 20.9
 },
 {
  "n": 6,
  "beat": "intro",
  "text": "Sentry is data governance on top of Cortex, which monitors the traffic between agents and everything they touch.",
  "start": 21.5,
  "end": 28.475
 },
 {
  "n": 7,
  "beat": "intro",
  "text": "It's built on lineage, classification, and risk assessment.",
  "start": 28.775,
  "end": 32.55
 },
 {
  "n": 8,
  "beat": "rule",
  "text": "Let's take one rule from the company policy: personal data must not leave the organization.",
  "start": 33.15,
  "end": 38.95
 },
 {
  "n": 9,
  "beat": "l1",
  "text": "Level one.",
  "start": 39.85,
  "end": 41.35
 },
 {
  "n": 10,
  "beat": "l1",
  "text": "In Rosa's record, the context is all there: her name, and beside it, her medical record number.",
  "start": 41.65,
  "end": 47.7
 },
 {
  "n": 11,
  "beat": "l1",
  "text": "Both are personal data, and the vendor is external, outside the organization. Sending them there breaks the policy, so Sentry blocks it.",
  "start": 48.0,
  "end": 56.475
 },
 {
  "n": 12,
  "beat": "l1",
  "text": "With full context at the point of decision, this is local enforcement. Most data governance tools can do it, and so does Sentry.",
  "start": 57.125,
  "end": 65.15
 },
 {
  "n": 13,
  "beat": "l1",
  "text": "But what happens when the context is gone?",
  "start": 65.45,
  "end": 68.1
 },
 {
  "n": 14,
  "beat": "l2",
  "text": "Level two. The app now sends only the number.",
  "start": 68.7,
  "end": 71.875
 },
 {
  "n": 15,
  "beat": "l2",
  "text": "At release, it's out of context, so given only the local context, it's classified as a generic operational ID.",
  "start": 72.175,
  "end": 79.9
 },
 {
  "n": 16,
  "beat": "l2",
  "text": "Local enforcement would let it through.",
  "start": 80.2,
  "end": 82.65
 },
 {
  "n": 17,
  "beat": "l2",
  "text": "But Sentry's lineage carries the context with the data: four hops back, that number was Rosa's medical record number.",
  "start": 82.95,
  "end": 90.3
 },
 {
  "n": 18,
  "beat": "l2",
  "text": "It's still personal data, so sending it breaks the policy, and it's blocked.",
  "start": 90.6,
  "end": 95.05
 },
 {
  "n": 19,
  "beat": "l3",
  "text": "With full context, Sentry blocks. Four hops downstream and out of context, it still catches the leak.",
  "start": 96.0,
  "end": 102.525
 },
 {
  "n": 20,
  "beat": "l3",
  "text": "But what if the leak comes from a different session altogether?",
  "start": 102.825,
  "end": 106.2
 },
 {
  "n": 21,
  "beat": "l3",
  "text": "A nightly job pulls the number from a shared database, with no name and no context.",
  "start": 106.5,
  "end": 111.9
 },
 {
  "n": 22,
  "beat": "l3",
  "text": "Sentry's cross-session lineage carries the full context across sessions, so this leak is blocked too.",
  "start": 112.2,
  "end": 118.325
 },
 {
  "n": 23,
  "beat": "fix",
  "text": "Sentry doesn't fix the app; it enforces the policy.",
  "start": 119.275,
  "end": 122.8
 },
 {
  "n": 24,
  "beat": "fix",
  "text": "Once the app is fixed so it no longer releases personal data, sending the vendor only what it needs, Sentry lets it through.",
  "start": 123.1,
  "end": 130.35
 },
 {
  "n": 25,
  "beat": "fix",
  "text": "This way, Rosa gets her refill, and the policy holds, keeping the company in line with data protection regulations.",
  "start": 130.65,
  "end": 137.825
 },
 {
  "n": 26,
  "beat": "close",
  "text": "Local enforcement sees one message. Sentry sees its whole history, protecting data locally, within a session, and across session boundaries.",
  "start": 140.225,
  "end": 149.125
 },
 {
  "n": 27,
  "beat": "close",
  "text": "Sentry, with lineage and risk, is live in the next release.",
  "start": 149.425,
  "end": 153.225
 }
];
window.WORDS = [
{
"line": 1,
"beat": "app",
"word": "Rosa",
"start": 0.675,
"end": 1.025
},
{
"line": 1,
"beat": "app",
"word": "needs",
"start": 1.025,
"end": 1.263
},
{
"line": 1,
"beat": "app",
"word": "a",
"start": 1.263,
"end": 1.325
},
{
"line": 1,
"beat": "app",
"word": "refill",
"start": 1.325,
"end": 2.0
},
{
"line": 2,
"beat": "app",
"word": "She",
"start": 2.8,
"end": 2.925
},
{
"line": 2,
"beat": "app",
"word": "taps",
"start": 2.925,
"end": 3.287
},
{
"line": 2,
"beat": "app",
"word": "Refill",
"start": 3.287,
"end": 3.662
},
{
"line": 2,
"beat": "app",
"word": "my",
"start": 3.662,
"end": 3.8
},
{
"line": 2,
"beat": "app",
"word": "prescription",
"start": 3.8,
"end": 4.438
},
{
"line": 2,
"beat": "app",
"word": "in",
"start": 4.438,
"end": 4.512
},
{
"line": 2,
"beat": "app",
"word": "her",
"start": 4.512,
"end": 4.625
},
{
"line": 2,
"beat": "app",
"word": "pharmacy's",
"start": 4.625,
"end": 5.175
},
{
"line": 2,
"beat": "app",
"word": "mobile",
"start": 5.175,
"end": 5.5
},
{
"line": 2,
"beat": "app",
"word": "app",
"start": 5.5,
"end": 5.975
},
{
"line": 2,
"beat": "app",
"word": "which",
"start": 6.075,
"end": 6.287
},
{
"line": 2,
"beat": "app",
"word": "triggers",
"start": 6.287,
"end": 6.712
},
{
"line": 2,
"beat": "app",
"word": "an",
"start": 6.712,
"end": 6.837
},
{
"line": 2,
"beat": "app",
"word": "agentic",
"start": 6.837,
"end": 7.35
},
{
"line": 2,
"beat": "app",
"word": "flow",
"start": 7.35,
"end": 7.95
},
{
"line": 3,
"beat": "flow",
"word": "That",
"start": 9.025,
"end": 9.175
},
{
"line": 3,
"beat": "flow",
"word": "one",
"start": 9.175,
"end": 9.4
},
{
"line": 3,
"beat": "flow",
"word": "tap",
"start": 9.4,
"end": 9.7
},
{
"line": 3,
"beat": "flow",
"word": "sets",
"start": 9.7,
"end": 9.887
},
{
"line": 3,
"beat": "flow",
"word": "off",
"start": 9.887,
"end": 10.075
},
{
"line": 3,
"beat": "flow",
"word": "twenty-six",
"start": 10.075,
"end": 10.625
},
{
"line": 3,
"beat": "flow",
"word": "agent",
"start": 10.625,
"end": 11.237
},
{
"line": 3,
"beat": "flow",
"word": "and",
"start": 11.237,
"end": 11.387
},
{
"line": 3,
"beat": "flow",
"word": "tool",
"start": 11.387,
"end": 11.662
},
{
"line": 3,
"beat": "flow",
"word": "interactions",
"start": 11.662,
"end": 12.6
},
{
"line": 4,
"beat": "flow",
"word": "Sentry",
"start": 13.4,
"end": 13.75
},
{
"line": 4,
"beat": "flow",
"word": "tracks",
"start": 13.75,
"end": 14.088
},
{
"line": 4,
"beat": "flow",
"word": "the",
"start": 14.088,
"end": 14.175
},
{
"line": 4,
"beat": "flow",
"word": "data",
"start": 14.175,
"end": 14.525
},
{
"line": 4,
"beat": "flow",
"word": "through",
"start": 14.525,
"end": 14.725
},
{
"line": 4,
"beat": "flow",
"word": "each",
"start": 14.725,
"end": 14.95
},
{
"line": 4,
"beat": "flow",
"word": "one",
"start": 14.95,
"end": 15.45
},
{
"line": 4,
"beat": "flow",
"word": "and",
"start": 15.55,
"end": 15.688
},
{
"line": 4,
"beat": "flow",
"word": "checks",
"start": 15.688,
"end": 16.0
},
{
"line": 4,
"beat": "flow",
"word": "it",
"start": 16.0,
"end": 16.125
},
{
"line": 4,
"beat": "flow",
"word": "against",
"start": 16.125,
"end": 16.45
},
{
"line": 4,
"beat": "flow",
"word": "company",
"start": 16.45,
"end": 16.8
},
{
"line": 4,
"beat": "flow",
"word": "policy",
"start": 16.8,
"end": 17.55
},
{
"line": 5,
"beat": "flow",
"word": "One",
"start": 18.35,
"end": 18.575
},
{
"line": 5,
"beat": "flow",
"word": "it",
"start": 18.625,
"end": 18.712
},
{
"line": 5,
"beat": "flow",
"word": "marks",
"start": 18.712,
"end": 18.962
},
{
"line": 5,
"beat": "flow",
"word": "as",
"start": 18.962,
"end": 19.1
},
{
"line": 5,
"beat": "flow",
"word": "risky",
"start": 19.1,
"end": 19.625
},
{
"line": 5,
"beat": "flow",
"word": "Let's",
"start": 19.75,
"end": 19.95
},
{
"line": 5,
"beat": "flow",
"word": "see",
"start": 19.95,
"end": 20.125
},
{
"line": 5,
"beat": "flow",
"word": "why",
"start": 20.125,
"end": 20.625
},
{
"line": 6,
"beat": "intro",
"word": "Sentry",
"start": 21.75,
"end": 22.188
},
{
"line": 6,
"beat": "intro",
"word": "is",
"start": 22.188,
"end": 22.363
},
{
"line": 6,
"beat": "intro",
"word": "data",
"start": 22.363,
"end": 22.7
},
{
"line": 6,
"beat": "intro",
"word": "governance",
"start": 22.7,
"end": 23.175
},
{
"line": 6,
"beat": "intro",
"word": "on",
"start": 23.175,
"end": 23.325
},
{
"line": 6,
"beat": "intro",
"word": "top",
"start": 23.325,
"end": 23.562
},
{
"line": 6,
"beat": "intro",
"word": "of",
"start": 23.562,
"end": 23.65
},
{
"line": 6,
"beat": "intro",
"word": "Cortex",
"start": 23.65,
"end": 24.55
},
{
"line": 6,
"beat": "intro",
"word": "which",
"start": 24.65,
"end": 24.863
},
{
"line": 6,
"beat": "intro",
"word": "monitors",
"start": 24.863,
"end": 25.387
},
{
"line": 6,
"beat": "intro",
"word": "the",
"start": 25.387,
"end": 25.475
},
{
"line": 6,
"beat": "intro",
"word": "traffic",
"start": 25.475,
"end": 25.925
},
{
"line": 6,
"beat": "intro",
"word": "between",
"start": 25.925,
"end": 26.288
},
{
"line": 6,
"beat": "intro",
"word": "agents",
"start": 26.288,
"end": 26.887
},
{
"line": 6,
"beat": "intro",
"word": "and",
"start": 26.887,
"end": 27.038
},
{
"line": 6,
"beat": "intro",
"word": "everything",
"start": 27.038,
"end": 27.525
},
{
"line": 6,
"beat": "intro",
"word": "they",
"start": 27.525,
"end": 27.675
},
{
"line": 6,
"beat": "intro",
"word": "touch",
"start": 27.675,
"end": 28.225
},
{
"line": 7,
"beat": "intro",
"word": "It's",
"start": 29.0,
"end": 29.225
},
{
"line": 7,
"beat": "intro",
"word": "built",
"start": 29.225,
"end": 29.463
},
{
"line": 7,
"beat": "intro",
"word": "on",
"start": 29.463,
"end": 29.625
},
{
"line": 7,
"beat": "intro",
"word": "lineage",
"start": 29.625,
"end": 30.225
},
{
"line": 7,
"beat": "intro",
"word": "classification",
"start": 30.325,
"end": 31.15
},
{
"line": 7,
"beat": "intro",
"word": "and",
"start": 31.2,
"end": 31.325
},
{
"line": 7,
"beat": "intro",
"word": "risk",
"start": 31.325,
"end": 31.563
},
{
"line": 7,
"beat": "intro",
"word": "assessment",
"start": 31.563,
"end": 32.325
},
{
"line": 8,
"beat": "rule",
"word": "Let's",
"start": 33.4,
"end": 33.65
},
{
"line": 8,
"beat": "rule",
"word": "take",
"start": 33.65,
"end": 33.888
},
{
"line": 8,
"beat": "rule",
"word": "one",
"start": 33.888,
"end": 34.125
},
{
"line": 8,
"beat": "rule",
"word": "rule",
"start": 34.125,
"end": 34.425
},
{
"line": 8,
"beat": "rule",
"word": "from",
"start": 34.425,
"end": 34.563
},
{
"line": 8,
"beat": "rule",
"word": "the",
"start": 34.563,
"end": 34.65
},
{
"line": 8,
"beat": "rule",
"word": "company",
"start": 34.65,
"end": 35.0
},
{
"line": 8,
"beat": "rule",
"word": "policy",
"start": 35.0,
"end": 35.75
},
{
"line": 8,
"beat": "rule",
"word": "personal",
"start": 35.875,
"end": 36.375
},
{
"line": 8,
"beat": "rule",
"word": "data",
"start": 36.375,
"end": 36.838
},
{
"line": 8,
"beat": "rule",
"word": "must",
"start": 36.838,
"end": 37.1
},
{
"line": 8,
"beat": "rule",
"word": "not",
"start": 37.1,
"end": 37.35
},
{
"line": 8,
"beat": "rule",
"word": "leave",
"start": 37.35,
"end": 37.588
},
{
"line": 8,
"beat": "rule",
"word": "the",
"start": 37.588,
"end": 37.7
},
{
"line": 8,
"beat": "rule",
"word": "organization",
"start": 37.7,
"end": 38.725
},
{
"line": 9,
"beat": "l1",
"word": "Level",
"start": 40.2,
"end": 40.525
},
{
"line": 9,
"beat": "l1",
"word": "one",
"start": 40.525,
"end": 41.075
},
{
"line": 10,
"beat": "l1",
"word": "In",
"start": 41.925,
"end": 42.05
},
{
"line": 10,
"beat": "l1",
"word": "Rosa's",
"start": 42.05,
"end": 42.475
},
{
"line": 10,
"beat": "l1",
"word": "record",
"start": 42.475,
"end": 42.9
},
{
"line": 10,
"beat": "l1",
"word": "the",
"start": 42.975,
"end": 43.075
},
{
"line": 10,
"beat": "l1",
"word": "context",
"start": 43.075,
"end": 43.637
},
{
"line": 10,
"beat": "l1",
"word": "is",
"start": 43.637,
"end": 43.75
},
{
"line": 10,
"beat": "l1",
"word": "all",
"start": 43.75,
"end": 43.95
},
{
"line": 10,
"beat": "l1",
"word": "there",
"start": 43.95,
"end": 44.525
},
{
"line": 10,
"beat": "l1",
"word": "her",
"start": 44.65,
"end": 44.825
},
{
"line": 10,
"beat": "l1",
"word": "name",
"start": 44.825,
"end": 45.275
},
{
"line": 10,
"beat": "l1",
"word": "and",
"start": 45.35,
"end": 45.462
},
{
"line": 10,
"beat": "l1",
"word": "beside",
"start": 45.462,
"end": 45.85
},
{
"line": 10,
"beat": "l1",
"word": "it",
"start": 45.85,
"end": 45.975
},
{
"line": 10,
"beat": "l1",
"word": "her",
"start": 46.012,
"end": 46.125
},
{
"line": 10,
"beat": "l1",
"word": "medical",
"start": 46.125,
"end": 46.525
},
{
"line": 10,
"beat": "l1",
"word": "record",
"start": 46.525,
"end": 46.875
},
{
"line": 10,
"beat": "l1",
"word": "number",
"start": 46.875,
"end": 47.45
},
{
"line": 11,
"beat": "l1",
"word": "Both",
"start": 48.25,
"end": 48.525
},
{
"line": 11,
"beat": "l1",
"word": "are",
"start": 48.525,
"end": 48.687
},
{
"line": 11,
"beat": "l1",
"word": "personal",
"start": 48.687,
"end": 49.175
},
{
"line": 11,
"beat": "l1",
"word": "data",
"start": 49.175,
"end": 49.675
},
{
"line": 11,
"beat": "l1",
"word": "and",
"start": 49.775,
"end": 49.887
},
{
"line": 11,
"beat": "l1",
"word": "the",
"start": 49.887,
"end": 49.975
},
{
"line": 11,
"beat": "l1",
"word": "vendor",
"start": 49.975,
"end": 50.375
},
{
"line": 11,
"beat": "l1",
"word": "is",
"start": 50.375,
"end": 50.525
},
{
"line": 11,
"beat": "l1",
"word": "external",
"start": 50.525,
"end": 51.2
},
{
"line": 11,
"beat": "l1",
"word": "outside",
"start": 51.275,
"end": 51.712
},
{
"line": 11,
"beat": "l1",
"word": "the",
"start": 51.712,
"end": 51.825
},
{
"line": 11,
"beat": "l1",
"word": "organization",
"start": 51.825,
"end": 52.975
},
{
"line": 11,
"beat": "l1",
"word": "Sending",
"start": 53.125,
"end": 53.487
},
{
"line": 11,
"beat": "l1",
"word": "them",
"start": 53.487,
"end": 53.637
},
{
"line": 11,
"beat": "l1",
"word": "there",
"start": 53.637,
"end": 53.8
},
{
"line": 11,
"beat": "l1",
"word": "breaks",
"start": 53.8,
"end": 54.112
},
{
"line": 11,
"beat": "l1",
"word": "the",
"start": 54.112,
"end": 54.2
},
{
"line": 11,
"beat": "l1",
"word": "policy",
"start": 54.2,
"end": 54.8
},
{
"line": 11,
"beat": "l1",
"word": "so",
"start": 54.9,
"end": 55.087
},
{
"line": 11,
"beat": "l1",
"word": "Sentry",
"start": 55.087,
"end": 55.475
},
{
"line": 11,
"beat": "l1",
"word": "blocks",
"start": 55.475,
"end": 55.875
},
{
"line": 11,
"beat": "l1",
"word": "it",
"start": 55.875,
"end": 56.25
},
{
"line": 12,
"beat": "l1",
"word": "With",
"start": 57.35,
"end": 57.537
},
{
"line": 12,
"beat": "l1",
"word": "full",
"start": 57.537,
"end": 57.775
},
{
"line": 12,
"beat": "l1",
"word": "context",
"start": 57.775,
"end": 58.362
},
{
"line": 12,
"beat": "l1",
"word": "at",
"start": 58.362,
"end": 58.437
},
{
"line": 12,
"beat": "l1",
"word": "the",
"start": 58.437,
"end": 58.525
},
{
"line": 12,
"beat": "l1",
"word": "point",
"start": 58.525,
"end": 58.737
},
{
"line": 12,
"beat": "l1",
"word": "of",
"start": 58.737,
"end": 58.812
},
{
"line": 12,
"beat": "l1",
"word": "decision",
"start": 58.812,
"end": 59.475
},
{
"line": 12,
"beat": "l1",
"word": "this",
"start": 59.55,
"end": 59.75
},
{
"line": 12,
"beat": "l1",
"word": "is",
"start": 59.75,
"end": 59.912
},
{
"line": 12,
"beat": "l1",
"word": "local",
"start": 59.912,
"end": 60.275
},
{
"line": 12,
"beat": "l1",
"word": "enforcement",
"start": 60.275,
"end": 61.35
},
{
"line": 12,
"beat": "l1",
"word": "Most",
"start": 61.5,
"end": 61.85
},
{
"line": 12,
"beat": "l1",
"word": "data",
"start": 61.85,
"end": 62.15
},
{
"line": 12,
"beat": "l1",
"word": "governance",
"start": 62.15,
"end": 62.6
},
{
"line": 12,
"beat": "l1",
"word": "tools",
"start": 62.6,
"end": 62.975
},
{
"line": 12,
"beat": "l1",
"word": "can",
"start": 62.975,
"end": 63.15
},
{
"line": 12,
"beat": "l1",
"word": "do",
"start": 63.15,
"end": 63.325
},
{
"line": 12,
"beat": "l1",
"word": "it",
"start": 63.325,
"end": 63.625
},
{
"line": 12,
"beat": "l1",
"word": "and",
"start": 63.7,
"end": 63.837
},
{
"line": 12,
"beat": "l1",
"word": "so",
"start": 63.837,
"end": 64.025
},
{
"line": 12,
"beat": "l1",
"word": "does",
"start": 64.025,
"end": 64.225
},
{
"line": 12,
"beat": "l1",
"word": "Sentry",
"start": 64.225,
"end": 64.9
},
{
"line": 13,
"beat": "l1",
"word": "But",
"start": 65.675,
"end": 65.8
},
{
"line": 13,
"beat": "l1",
"word": "what",
"start": 65.8,
"end": 65.95
},
{
"line": 13,
"beat": "l1",
"word": "happens",
"start": 65.95,
"end": 66.362
},
{
"line": 13,
"beat": "l1",
"word": "when",
"start": 66.362,
"end": 66.487
},
{
"line": 13,
"beat": "l1",
"word": "the",
"start": 66.487,
"end": 66.575
},
{
"line": 13,
"beat": "l1",
"word": "context",
"start": 66.575,
"end": 67.112
},
{
"line": 13,
"beat": "l1",
"word": "is",
"start": 67.112,
"end": 67.25
},
{
"line": 13,
"beat": "l1",
"word": "gone",
"start": 67.25,
"end": 67.85
},
{
"line": 14,
"beat": "l2",
"word": "Level",
"start": 68.95,
"end": 69.287
},
{
"line": 14,
"beat": "l2",
"word": "two",
"start": 69.287,
"end": 69.7
},
{
"line": 14,
"beat": "l2",
"word": "The",
"start": 69.825,
"end": 69.975
},
{
"line": 14,
"beat": "l2",
"word": "app",
"start": 69.975,
"end": 70.2
},
{
"line": 14,
"beat": "l2",
"word": "now",
"start": 70.2,
"end": 70.425
},
{
"line": 14,
"beat": "l2",
"word": "sends",
"start": 70.425,
"end": 70.7
},
{
"line": 14,
"beat": "l2",
"word": "only",
"start": 70.7,
"end": 70.987
},
{
"line": 14,
"beat": "l2",
"word": "the",
"start": 70.987,
"end": 71.075
},
{
"line": 14,
"beat": "l2",
"word": "number",
"start": 71.075,
"end": 71.625
},
{
"line": 15,
"beat": "l2",
"word": "At",
"start": 72.4,
"end": 72.525
},
{
"line": 15,
"beat": "l2",
"word": "release",
"start": 72.525,
"end": 73.1
},
{
"line": 15,
"beat": "l2",
"word": "it's",
"start": 73.175,
"end": 73.35
},
{
"line": 15,
"beat": "l2",
"word": "out",
"start": 73.35,
"end": 73.487
},
{
"line": 15,
"beat": "l2",
"word": "of",
"start": 73.487,
"end": 73.575
},
{
"line": 15,
"beat": "l2",
"word": "context",
"start": 73.575,
"end": 74.425
},
{
"line": 15,
"beat": "l2",
"word": "so",
"start": 74.525,
"end": 74.7
},
{
"line": 15,
"beat": "l2",
"word": "given",
"start": 74.7,
"end": 75.0
},
{
"line": 15,
"beat": "l2",
"word": "only",
"start": 75.0,
"end": 75.3
},
{
"line": 15,
"beat": "l2",
"word": "the",
"start": 75.3,
"end": 75.425
},
{
"line": 15,
"beat": "l2",
"word": "local",
"start": 75.425,
"end": 75.75
},
{
"line": 15,
"beat": "l2",
"word": "context",
"start": 75.75,
"end": 76.7
},
{
"line": 15,
"beat": "l2",
"word": "it's",
"start": 76.825,
"end": 77.025
},
{
"line": 15,
"beat": "l2",
"word": "classified",
"start": 77.025,
"end": 77.7
},
{
"line": 15,
"beat": "l2",
"word": "as",
"start": 77.7,
"end": 77.812
},
{
"line": 15,
"beat": "l2",
"word": "a",
"start": 77.812,
"end": 77.875
},
{
"line": 15,
"beat": "l2",
"word": "generic",
"start": 77.875,
"end": 78.337
},
{
"line": 15,
"beat": "l2",
"word": "operational",
"start": 78.337,
"end": 79.0
},
{
"line": 15,
"beat": "l2",
"word": "ID",
"start": 79.0,
"end": 79.675
},
{
"line": 16,
"beat": "l2",
"word": "Local",
"start": 80.475,
"end": 80.8
},
{
"line": 16,
"beat": "l2",
"word": "enforcement",
"start": 80.8,
"end": 81.5
},
{
"line": 16,
"beat": "l2",
"word": "would",
"start": 81.5,
"end": 81.625
},
{
"line": 16,
"beat": "l2",
"word": "let",
"start": 81.625,
"end": 81.762
},
{
"line": 16,
"beat": "l2",
"word": "it",
"start": 81.762,
"end": 81.862
},
{
"line": 16,
"beat": "l2",
"word": "through",
"start": 81.862,
"end": 82.4
},
{
"line": 17,
"beat": "l2",
"word": "But",
"start": 83.15,
"end": 83.287
},
{
"line": 17,
"beat": "l2",
"word": "Sentry's",
"start": 83.287,
"end": 83.75
},
{
"line": 17,
"beat": "l2",
"word": "lineage",
"start": 83.75,
"end": 84.237
},
{
"line": 17,
"beat": "l2",
"word": "carries",
"start": 84.237,
"end": 84.637
},
{
"line": 17,
"beat": "l2",
"word": "the",
"start": 84.637,
"end": 84.725
},
{
"line": 17,
"beat": "l2",
"word": "context",
"start": 84.725,
"end": 85.312
},
{
"line": 17,
"beat": "l2",
"word": "with",
"start": 85.312,
"end": 85.412
},
{
"line": 17,
"beat": "l2",
"word": "the",
"start": 85.412,
"end": 85.5
},
{
"line": 17,
"beat": "l2",
"word": "data",
"start": 85.5,
"end": 86.175
},
{
"line": 17,
"beat": "l2",
"word": "four",
"start": 86.35,
"end": 86.65
},
{
"line": 17,
"beat": "l2",
"word": "hops",
"start": 86.65,
"end": 86.95
},
{
"line": 17,
"beat": "l2",
"word": "back",
"start": 86.95,
"end": 87.45
},
{
"line": 17,
"beat": "l2",
"word": "that",
"start": 87.55,
"end": 87.775
},
{
"line": 17,
"beat": "l2",
"word": "number",
"start": 87.775,
"end": 88.05
},
{
"line": 17,
"beat": "l2",
"word": "was",
"start": 88.05,
"end": 88.237
},
{
"line": 17,
"beat": "l2",
"word": "Rosa's",
"start": 88.237,
"end": 88.712
},
{
"line": 17,
"beat": "l2",
"word": "medical",
"start": 88.712,
"end": 89.125
},
{
"line": 17,
"beat": "l2",
"word": "record",
"start": 89.125,
"end": 89.462
},
{
"line": 17,
"beat": "l2",
"word": "number",
"start": 89.462,
"end": 90.05
},
{
"line": 18,
"beat": "l2",
"word": "It's",
"start": 90.825,
"end": 90.962
},
{
"line": 18,
"beat": "l2",
"word": "still",
"start": 90.962,
"end": 91.225
},
{
"line": 18,
"beat": "l2",
"word": "personal",
"start": 91.225,
"end": 91.65
},
{
"line": 18,
"beat": "l2",
"word": "data",
"start": 91.65,
"end": 92.125
},
{
"line": 18,
"beat": "l2",
"word": "so",
"start": 92.25,
"end": 92.437
},
{
"line": 18,
"beat": "l2",
"word": "sending",
"start": 92.437,
"end": 92.787
},
{
"line": 18,
"beat": "l2",
"word": "it",
"start": 92.787,
"end": 92.875
},
{
"line": 18,
"beat": "l2",
"word": "breaks",
"start": 92.875,
"end": 93.162
},
{
"line": 18,
"beat": "l2",
"word": "the",
"start": 93.162,
"end": 93.25
},
{
"line": 18,
"beat": "l2",
"word": "policy",
"start": 93.25,
"end": 93.8
},
{
"line": 18,
"beat": "l2",
"word": "and",
"start": 93.875,
"end": 93.987
},
{
"line": 18,
"beat": "l2",
"word": "it's",
"start": 93.987,
"end": 94.15
},
{
"line": 18,
"beat": "l2",
"word": "blocked",
"start": 94.15,
"end": 94.8
},
{
"line": 19,
"beat": "l3",
"word": "With",
"start": 96.225,
"end": 96.4
},
{
"line": 19,
"beat": "l3",
"word": "full",
"start": 96.4,
"end": 96.625
},
{
"line": 19,
"beat": "l3",
"word": "context",
"start": 96.625,
"end": 97.325
},
{
"line": 19,
"beat": "l3",
"word": "Sentry",
"start": 97.4,
"end": 97.725
},
{
"line": 19,
"beat": "l3",
"word": "blocks",
"start": 97.725,
"end": 98.475
},
{
"line": 19,
"beat": "l3",
"word": "Four",
"start": 98.6,
"end": 98.875
},
{
"line": 19,
"beat": "l3",
"word": "hops",
"start": 98.875,
"end": 99.175
},
{
"line": 19,
"beat": "l3",
"word": "downstream",
"start": 99.175,
"end": 99.8
},
{
"line": 19,
"beat": "l3",
"word": "and",
"start": 99.8,
"end": 99.912
},
{
"line": 19,
"beat": "l3",
"word": "out",
"start": 99.912,
"end": 100.037
},
{
"line": 19,
"beat": "l3",
"word": "of",
"start": 100.037,
"end": 100.125
},
{
"line": 19,
"beat": "l3",
"word": "context",
"start": 100.125,
"end": 100.775
},
{
"line": 19,
"beat": "l3",
"word": "it",
"start": 100.85,
"end": 100.975
},
{
"line": 19,
"beat": "l3",
"word": "still",
"start": 100.975,
"end": 101.25
},
{
"line": 19,
"beat": "l3",
"word": "catches",
"start": 101.25,
"end": 101.65
},
{
"line": 19,
"beat": "l3",
"word": "the",
"start": 101.65,
"end": 101.75
},
{
"line": 19,
"beat": "l3",
"word": "leak",
"start": 101.75,
"end": 102.275
},
{
"line": 20,
"beat": "l3",
"word": "But",
"start": 103.05,
"end": 103.175
},
{
"line": 20,
"beat": "l3",
"word": "what",
"start": 103.175,
"end": 103.312
},
{
"line": 20,
"beat": "l3",
"word": "if",
"start": 103.312,
"end": 103.45
},
{
"line": 20,
"beat": "l3",
"word": "the",
"start": 103.45,
"end": 103.575
},
{
"line": 20,
"beat": "l3",
"word": "leak",
"start": 103.575,
"end": 103.875
},
{
"line": 20,
"beat": "l3",
"word": "comes",
"start": 103.875,
"end": 104.137
},
{
"line": 20,
"beat": "l3",
"word": "from",
"start": 104.137,
"end": 104.262
},
{
"line": 20,
"beat": "l3",
"word": "a",
"start": 104.262,
"end": 104.325
},
{
"line": 20,
"beat": "l3",
"word": "different",
"start": 104.325,
"end": 104.625
},
{
"line": 20,
"beat": "l3",
"word": "session",
"start": 104.625,
"end": 105.112
},
{
"line": 20,
"beat": "l3",
"word": "altogether",
"start": 105.112,
"end": 105.95
},
{
"line": 21,
"beat": "l3",
"word": "A",
"start": 106.75,
"end": 106.825
},
{
"line": 21,
"beat": "l3",
"word": "nightly",
"start": 106.825,
"end": 107.187
},
{
"line": 21,
"beat": "l3",
"word": "job",
"start": 107.187,
"end": 107.537
},
{
"line": 21,
"beat": "l3",
"word": "pulls",
"start": 107.537,
"end": 107.862
},
{
"line": 21,
"beat": "l3",
"word": "the",
"start": 107.862,
"end": 107.95
},
{
"line": 21,
"beat": "l3",
"word": "number",
"start": 107.95,
"end": 108.25
},
{
"line": 21,
"beat": "l3",
"word": "from",
"start": 108.25,
"end": 108.387
},
{
"line": 21,
"beat": "l3",
"word": "a",
"start": 108.387,
"end": 108.487
},
{
"line": 21,
"beat": "l3",
"word": "shared",
"start": 108.487,
"end": 108.775
},
{
"line": 21,
"beat": "l3",
"word": "database",
"start": 108.775,
"end": 109.6
},
{
"line": 21,
"beat": "l3",
"word": "with",
"start": 109.7,
"end": 109.887
},
{
"line": 21,
"beat": "l3",
"word": "no",
"start": 109.887,
"end": 110.125
},
{
"line": 21,
"beat": "l3",
"word": "name",
"start": 110.125,
"end": 110.5
},
{
"line": 21,
"beat": "l3",
"word": "and",
"start": 110.5,
"end": 110.625
},
{
"line": 21,
"beat": "l3",
"word": "no",
"start": 110.625,
"end": 110.8
},
{
"line": 21,
"beat": "l3",
"word": "context",
"start": 110.8,
"end": 111.65
},
{
"line": 22,
"beat": "l3",
"word": "Sentry's",
"start": 112.45,
"end": 112.875
},
{
"line": 22,
"beat": "l3",
"word": "cross-session",
"start": 112.875,
"end": 113.525
},
{
"line": 22,
"beat": "l3",
"word": "lineage",
"start": 113.525,
"end": 113.987
},
{
"line": 22,
"beat": "l3",
"word": "carries",
"start": 113.987,
"end": 114.387
},
{
"line": 22,
"beat": "l3",
"word": "the",
"start": 114.387,
"end": 114.487
},
{
"line": 22,
"beat": "l3",
"word": "full",
"start": 114.487,
"end": 114.725
},
{
"line": 22,
"beat": "l3",
"word": "context",
"start": 114.725,
"end": 115.262
},
{
"line": 22,
"beat": "l3",
"word": "across",
"start": 115.262,
"end": 115.575
},
{
"line": 22,
"beat": "l3",
"word": "sessions",
"start": 115.575,
"end": 116.3
},
{
"line": 22,
"beat": "l3",
"word": "so",
"start": 116.4,
"end": 116.575
},
{
"line": 22,
"beat": "l3",
"word": "this",
"start": 116.575,
"end": 116.812
},
{
"line": 22,
"beat": "l3",
"word": "leak",
"start": 116.812,
"end": 117.062
},
{
"line": 22,
"beat": "l3",
"word": "is",
"start": 117.062,
"end": 117.2
},
{
"line": 22,
"beat": "l3",
"word": "blocked",
"start": 117.2,
"end": 117.562
},
{
"line": 22,
"beat": "l3",
"word": "too",
"start": 117.562,
"end": 118.075
},
{
"line": 23,
"beat": "fix",
"word": "Sentry",
"start": 119.55,
"end": 119.9
},
{
"line": 23,
"beat": "fix",
"word": "doesn't",
"start": 119.9,
"end": 120.175
},
{
"line": 23,
"beat": "fix",
"word": "fix",
"start": 120.175,
"end": 120.412
},
{
"line": 23,
"beat": "fix",
"word": "the",
"start": 120.412,
"end": 120.55
},
{
"line": 23,
"beat": "fix",
"word": "app",
"start": 120.55,
"end": 120.95
},
{
"line": 23,
"beat": "fix",
"word": "it",
"start": 121.05,
"end": 121.137
},
{
"line": 23,
"beat": "fix",
"word": "enforces",
"start": 121.137,
"end": 121.712
},
{
"line": 23,
"beat": "fix",
"word": "the",
"start": 121.712,
"end": 121.8
},
{
"line": 23,
"beat": "fix",
"word": "policy",
"start": 121.8,
"end": 122.55
},
{
"line": 24,
"beat": "fix",
"word": "Once",
"start": 123.35,
"end": 123.562
},
{
"line": 24,
"beat": "fix",
"word": "the",
"start": 123.562,
"end": 123.7
},
{
"line": 24,
"beat": "fix",
"word": "app",
"start": 123.7,
"end": 123.887
},
{
"line": 24,
"beat": "fix",
"word": "is",
"start": 123.887,
"end": 124.025
},
{
"line": 24,
"beat": "fix",
"word": "fixed",
"start": 124.025,
"end": 124.45
},
{
"line": 24,
"beat": "fix",
"word": "so",
"start": 124.45,
"end": 124.587
},
{
"line": 24,
"beat": "fix",
"word": "it",
"start": 124.587,
"end": 124.675
},
{
"line": 24,
"beat": "fix",
"word": "no",
"start": 124.675,
"end": 124.85
},
{
"line": 24,
"beat": "fix",
"word": "longer",
"start": 124.85,
"end": 125.15
},
{
"line": 24,
"beat": "fix",
"word": "releases",
"start": 125.15,
"end": 125.65
},
{
"line": 24,
"beat": "fix",
"word": "personal",
"start": 125.65,
"end": 126.05
},
{
"line": 24,
"beat": "fix",
"word": "data",
"start": 126.05,
"end": 126.575
},
{
"line": 24,
"beat": "fix",
"word": "sending",
"start": 126.712,
"end": 127.062
},
{
"line": 24,
"beat": "fix",
"word": "the",
"start": 127.062,
"end": 127.15
},
{
"line": 24,
"beat": "fix",
"word": "vendor",
"start": 127.15,
"end": 127.537
},
{
"line": 24,
"beat": "fix",
"word": "only",
"start": 127.537,
"end": 127.85
},
{
"line": 24,
"beat": "fix",
"word": "what",
"start": 127.85,
"end": 127.987
},
{
"line": 24,
"beat": "fix",
"word": "it",
"start": 127.987,
"end": 128.075
},
{
"line": 24,
"beat": "fix",
"word": "needs",
"start": 128.075,
"end": 128.675
},
{
"line": 24,
"beat": "fix",
"word": "Sentry",
"start": 128.825,
"end": 129.225
},
{
"line": 24,
"beat": "fix",
"word": "lets",
"start": 129.225,
"end": 129.437
},
{
"line": 24,
"beat": "fix",
"word": "it",
"start": 129.437,
"end": 129.55
},
{
"line": 24,
"beat": "fix",
"word": "through",
"start": 129.55,
"end": 130.1
},
{
"line": 25,
"beat": "fix",
"word": "This",
"start": 130.9,
"end": 131.1
},
{
"line": 25,
"beat": "fix",
"word": "way",
"start": 131.1,
"end": 131.425
},
{
"line": 25,
"beat": "fix",
"word": "Rosa",
"start": 131.512,
"end": 131.9
},
{
"line": 25,
"beat": "fix",
"word": "gets",
"start": 131.9,
"end": 132.112
},
{
"line": 25,
"beat": "fix",
"word": "her",
"start": 132.112,
"end": 132.25
},
{
"line": 25,
"beat": "fix",
"word": "refill",
"start": 132.25,
"end": 132.9
},
{
"line": 25,
"beat": "fix",
"word": "and",
"start": 133.0,
"end": 133.112
},
{
"line": 25,
"beat": "fix",
"word": "the",
"start": 133.112,
"end": 133.2
},
{
"line": 25,
"beat": "fix",
"word": "policy",
"start": 133.2,
"end": 133.65
},
{
"line": 25,
"beat": "fix",
"word": "holds",
"start": 133.65,
"end": 134.225
},
{
"line": 25,
"beat": "fix",
"word": "keeping",
"start": 134.325,
"end": 134.637
},
{
"line": 25,
"beat": "fix",
"word": "the",
"start": 134.637,
"end": 134.725
},
{
"line": 25,
"beat": "fix",
"word": "company",
"start": 134.725,
"end": 135.125
},
{
"line": 25,
"beat": "fix",
"word": "in",
"start": 135.125,
"end": 135.275
},
{
"line": 25,
"beat": "fix",
"word": "line",
"start": 135.275,
"end": 135.65
},
{
"line": 25,
"beat": "fix",
"word": "with",
"start": 135.65,
"end": 135.837
},
{
"line": 25,
"beat": "fix",
"word": "data",
"start": 135.837,
"end": 136.15
},
{
"line": 25,
"beat": "fix",
"word": "protection",
"start": 136.15,
"end": 136.625
},
{
"line": 25,
"beat": "fix",
"word": "regulations",
"start": 136.625,
"end": 137.6
},
{
"line": 26,
"beat": "close",
"word": "Local",
"start": 140.5,
"end": 140.85
},
{
"line": 26,
"beat": "close",
"word": "enforcement",
"start": 140.85,
"end": 141.588
},
{
"line": 26,
"beat": "close",
"word": "sees",
"start": 141.588,
"end": 141.9
},
{
"line": 26,
"beat": "close",
"word": "one",
"start": 141.9,
"end": 142.1
},
{
"line": 26,
"beat": "close",
"word": "message",
"start": 142.1,
"end": 142.925
},
{
"line": 26,
"beat": "close",
"word": "Sentry",
"start": 143.1,
"end": 143.562
},
{
"line": 26,
"beat": "close",
"word": "sees",
"start": 143.562,
"end": 143.838
},
{
"line": 26,
"beat": "close",
"word": "its",
"start": 143.838,
"end": 143.975
},
{
"line": 26,
"beat": "close",
"word": "whole",
"start": 143.975,
"end": 144.225
},
{
"line": 26,
"beat": "close",
"word": "history",
"start": 144.225,
"end": 144.8
},
{
"line": 26,
"beat": "close",
"word": "protecting",
"start": 144.9,
"end": 145.45
},
{
"line": 26,
"beat": "close",
"word": "data",
"start": 145.45,
"end": 145.725
},
{
"line": 26,
"beat": "close",
"word": "locally",
"start": 145.725,
"end": 146.3
},
{
"line": 26,
"beat": "close",
"word": "within",
"start": 146.4,
"end": 146.612
},
{
"line": 26,
"beat": "close",
"word": "a",
"start": 146.612,
"end": 146.675
},
{
"line": 26,
"beat": "close",
"word": "session",
"start": 146.675,
"end": 147.2
},
{
"line": 26,
"beat": "close",
"word": "and",
"start": 147.275,
"end": 147.387
},
{
"line": 26,
"beat": "close",
"word": "across",
"start": 147.387,
"end": 147.75
},
{
"line": 26,
"beat": "close",
"word": "session",
"start": 147.75,
"end": 148.075
},
{
"line": 26,
"beat": "close",
"word": "boundaries",
"start": 148.075,
"end": 148.9
},
{
"line": 27,
"beat": "close",
"word": "Sentry",
"start": 149.7,
"end": 150.125
},
{
"line": 27,
"beat": "close",
"word": "with",
"start": 150.175,
"end": 150.35
},
{
"line": 27,
"beat": "close",
"word": "lineage",
"start": 150.35,
"end": 150.738
},
{
"line": 27,
"beat": "close",
"word": "and",
"start": 150.738,
"end": 150.85
},
{
"line": 27,
"beat": "close",
"word": "risk",
"start": 150.85,
"end": 151.325
},
{
"line": 27,
"beat": "close",
"word": "is",
"start": 151.425,
"end": 151.575
},
{
"line": 27,
"beat": "close",
"word": "live",
"start": 151.575,
"end": 151.888
},
{
"line": 27,
"beat": "close",
"word": "in",
"start": 151.888,
"end": 151.963
},
{
"line": 27,
"beat": "close",
"word": "the",
"start": 151.963,
"end": 152.05
},
{
"line": 27,
"beat": "close",
"word": "next",
"start": 152.05,
"end": 152.312
},
{
"line": 27,
"beat": "close",
"word": "release",
"start": 152.312,
"end": 152.975
}
];
