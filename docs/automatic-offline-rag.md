# Automatic offline factual grounding

The service now enables the router's existing implicit lexical retrieval for
ordinary factual questions and explanations. Explicit online/dynamic routing and
device/action decisions still run first. Greetings and conversational followups
do not trigger a lexical scan. Explicit document/project requests retain their
existing behavior. Offline current-fact requests still abstain without current
evidence.

The existing SQLite retriever, indexed documents, expiry filter, minimum lexical
coverage and distinct-match rule are reused. Implicit candidates are checked
against that same content threshold before context is admitted. Empty matches
keep the ordinary local response. No embedding/model/API, index change or cache
of private document content is introduced.

Generation keeps the existing top-k, character and output-token budgets. Local
evidence has explicit precedence over model memory; conflicting or insufficient
evidence must be reported rather than filled with model guesses. The existing
source metadata remains attached. The hardcoded general Pi reference is not
injected into local-RAG answers.

A deliberately small fast path handles only an exact question like
`What CPU does Raspberry Pi 5 use?` and whole retrieved chunks exactly formatted
as `Raspberry Pi 5 CPU: BCM2712` (optional Broadcom label and period). Every
retrieved chunk must be such a card; saved online snippets are excluded. A single
unambiguous value yields one source-supported sentence with attribution metadata
and zero model generation. Different card values cause direct abstention.
Paragraphs, extra instructions, multiple claims or other query shapes use normal
grounded generation, never arbitrary snippet copying. This validates the shape
and agreement of retrieved evidence, not the independent truth of manual notes.
No existing knowledge documents are edited or reformatted.

Affected tests: `assistant.tests_automatic_offline_rag`, `assistant.tests_routing`,
`assistant.tests_intelligence_reliability`, `knowledge_base.tests_rag`,
`assistant.tests_streaming`, `assistant.tests_execution_policy` (122 passed).
Django checks passed. Tests use isolated in-memory storage and synthetic data;
no physical Pi latency, model throughput or production data tests are claimed.

Deploy only the reviewed fix commit onto Pi baseline
`12f238689a74eaf29931319392f7f116a08aabc1`, with clean tracked files. Untracked
knowledge is allowed. Fetch and cherry-pick this single commit, run the affected
tests with `--settings=config.test_settings`, then run Django checks. Stop on
conflicts; `git cherry-pick --abort` preserves the pre-deployment checkout. No
reset/stash/clean, migration, knowledge ingestion, model change or service command
is needed for these checks. Existing long-lived processes pick up the code on
their next normal reload; this workflow does not restart services. Record the
cherry-picked SHA and roll back with `git revert <that-SHA>` if necessary.
