# Repository cleanup audit

Audit date: 1 November 2026 (Asia/Kolkata). Repository:
https://github.com/Jagadish0216/smart-ai-companion

## Scope and baseline

All **637 tracked baseline files**, totaling **16,302,735 bytes**, were enumerated,
hashed and classified, with reference searches and source/text scanning. This is
a file-level inventory, not a claim that every line received manual review.
The [CSV inventory](repository-audit/baseline-inventory.csv) records path, type,
size, purpose, reference evidence, intentional generated/source status, role,
disposition and rationale. [Summary JSON](repository-audit/baseline-summary.json)
records duplicate sets, largest files, redacted security candidates and every
remote branch's ancestry counts and pinned SHA. Generic inventory purposes are
subsystem classifications; a missing textual reference does not prove dead code.

Baseline categories: hardware 307, docs 91, assistant 76, static 52, system 27,
knowledge_base 24, templates 15, conversations 10, dashboard 9, deployment 6,
scripts 6, config 5, firmware 3, and six root files. No applicable AGENTS.md was
found by the repository search. No GitHub Actions workflow or project-wide
license was present. No package.json/build pipeline was present in this baseline;
browser checks are standalone CJS scripts. Native firmware and CAD are independent
entry points, not unused Django code.

The original Windows checkout is on `feature/assistant-document-chat-ui` and
contains extensive modified/untracked release material, local ZIPs, document
artifacts and nested checkouts. Those are preserved in place and excluded from
the clean GitHub branch. They were identified as local work, not declared trash.
An isolated `.portfolio-cleanup` worktree on `portfolio/final-repository-cleanup`
starts at main `f59da97d270ebe1157dac65e9862ec8ed77116cd`.

## Branch synchronization

Remote metadata was fetched. The connected GitHub search returned zero open
issues/pull requests at inspection time. Main's integrated history includes older
memory, dashboard, enclosure, display, voice-transcript, wake-word and knowledge
milestones: their branch-only ancestry counts are zero. Later feature branches
contain additional source work (knowledge UI, reliability, online evidence,
physical voice/RAG and fixture fixes); ancestry alone does not certify them as
the final deployment. Their tips remain unchanged.

The response-control release branch contains two commits and 14 added package
files over main. Its manifest targets a separate Pi snapshot, not main's root
application. [DEPLOYMENT.md](DEPLOYMENT.md) preserves full commit references,
snapshot provenance, safe synchronization requirements and rollback boundaries.
No release patch was applied and no branches were merged or deleted. The later
Pi `services.py` hotfix and RAG environment changes are still not synchronized.

## Changes and dispositions

- Replaced the 1,200-plus-line root README with a roughly 1,650-word portfolio
  presentation covering actual capabilities, optional paths and limits.
- Preserved the old root content in `docs/ENGINEERING_REFERENCE.md`, corrected
  its relative Markdown links, and labeled old benchmarks/models as historical.
- Added architecture, desktop setup, deployment provenance, hardware index,
  testing and troubleshooting guides, release checklist and unpublished
  announcement preparation notes.
- Added a repeatable standard-library audit utility and checked-in baseline
  inventory, branch/security/duplicate summary and dependency advisory IDs.
- Added narrow ignores for local validation products and binary treatment for
  meshes/images/archives. Existing tracked evidence remains tracked.
- Updated only DRF 3.18.1 → 3.18.2 and pypdf 6.19.0 → 6.21.4 to address current
  scanner findings, with isolated installation and full regression verification.

**Deleted files: 0. Archived/removed source files: 0.** The historical README
content is retained in a new reference file; no originals were destructively
moved. No repository-size reduction is claimed. Git object storage measured
100.11 MiB loose plus 14.01 MiB packed in the shared local multi-worktree repo;
this includes other branches/history and is not the public checkout size.
Git reported two pre-existing/local housekeeping artifacts; no pruning was done.

## Screenshots and CAD

Two real screenshots were visually inspected and selected for README embeds.
Both explicitly show mock-mode development UI; captions disclose that scope.
Responsive screenshot matrices, local/baseline/live comparison captures and JSON
traces are retained because the acceptance documents refer to them. They are
not a redundant photo gallery; no final hardware photo or video is invented.

There are 55 exact-content duplicate sets, of which 54 have nonzero content.
Empty Python package markers must remain. Repeated CAD head meshes across the
original and optimized designs are frozen engineering geometry with validation
and packaging references. Source, STL manufacturing sets, histories, logs,
clearance/volume checks, BOM, wiring and print instructions remain intact.
The largest tracked file is an optimized bottom-cover mesh (847,184 bytes),
not a giant accidental archive. Build/package scripts explain regeneration;
ignored locally generated ZIPs are not promised public download assets.

## Security and privacy

The tracked-text heuristic found 17 candidates: nine credential-like test
assignments and eight private-address examples/fixtures. Manual context review
identified test credentials, example firmware configuration and documented
NetworkManager addressing, not confirmed active secrets. No token-shaped or
private-key candidates were found. No tracked live .env, runtime database or
private firmware secrets.h was identified. Values are not reproduced in reports.
The default Django development key is explicitly insecure, not an active secret.

This is not a complete Git-history secret scan or a proof of absence. If a live
secret is later discovered, rotate it through the owner; deleting the current
file does not erase history. No history rewrite or credential rotation occurred.

Default DEBUG=True, the development key, cookie/HTTPS omissions and public API
surfaces need operator attention before shared exposure. Django deploy checks
report six warnings. Document writes use staff session/CSRF checks, while chat,
history/dashboard and selected system endpoints do not constitute a hardened
multi-user boundary. Secure setup guidance was added; GPIO, audio, networking,
service templates and existing application behavior were not changed.

## Dependencies and licensing

Imports, provider factories, native subprocess paths, firmware requirements and
tests show dependencies can support optional/independent paths. None was removed.
Python 3.10 was exercised; Linux >=3.12 conditional ONNX packages and older
TFLite/wake-word constraints are documented, not ARM64-certified by this audit.

pip-audit initially rejected the unpinned python-dotenv range in --no-deps mode.
An audit-only manifest represented it with installed version 1.2.3 without
altering the source range. The initial direct-dependency scan found 21 advisories
in two packages: three for DRF, eighteen for pypdf. See
[advisory IDs and fixed versions](repository-audit/dependency-advisories.json).
Only those two packages were updated. Scan scope excludes transitive resolution,
Linux-only conditional packages, Ollama/native speech tools and firmware libraries.
Passing pip check is dependency consistency, not security certification.

Third-party Lucide attribution remains. No project-wide license was invented;
software/CAD ownership and contributor consent need review before adding a grant.

## Remaining decisions

Supply approved physical media and verified credits; confirm the printed enclosure
variant; reconcile the final deployed source; repeat hardware/ARM64 validation;
review access controls and full-history/transitive security scans; approve licensing
and the final PR. Suggested metadata is in the release checklist and was not
applied. No live Pi operation or social publication was performed.
