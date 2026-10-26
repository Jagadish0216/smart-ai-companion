# Development validation — 26 October 2026

Validated in the isolated `feature/complete-knowledge-system` checkout based on
`30743607fdcae68ef81991f1e2610eec957879bc` using the existing Python environment.

- Full regression: 1,595 tests, OK (one skipped), 73.444 seconds.
- Focused integrated acceptance: 17 tests, OK, including permanent evidence,
  expiry, paraphrases, separate-process offline restart, document/page retrieval,
  scoped abstention, actual bundled indexing, and SQLite backup/schema rollback.
- Django system check: no issues. Migration drift check: no changes detected.
- Git Bash syntax check passed for both deployment and acceptance scripts.
- All 18 staged upstream reference texts and their licenses match manifest hashes.
- Authored-file whitespace checks passed. Unmodified upstream reStructuredText
  heading underlines and trailing blank lines are intentionally preserved.

Legacy model/resource tests explicitly disable the new production evidence gate
because their generation fixtures have no corpus. Integrated acceptance enables
that gate and checks abstention without model inference when evidence is absent.
Network/model transports are mocked in synthetic tests. These results do not
establish live publisher availability, Raspberry Pi hardware performance or
microphone/speaker behavior. The supplied Pi acceptance script checks live
retrieval, application HTTP, services, indexed references and runtime diagnostics.
The browser tool could not reach the local preview, so visual verification is
outstanding. Broad civic/history/science/entertainment and dated 2026 library
coverage also remains unpopulated, as exposed in the manifest and dashboard.
