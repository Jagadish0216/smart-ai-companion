# Step 51: guarded physical demonstration readiness

Baseline: `7dbf2ad1cb5512d5fcad302dd931964b15e9e002`. This is release preparation,
not physical acceptance. No Pi inference, audio test, model download, restart,
unload, firmware change or deployment was performed during this milestone.

## What is available

`scripts/validate_offline_voice.py --operator-live-trial --production-qwen`
is a single physical microphone-to-speaker cycle through the existing
`voice_loop.run_voice_cycle`, production voice policy, AssistantService,
Whisper.cpp, persistent Piper, ALSA and configured TFT implementation.
It uses disposable public RAG/conversation fixtures and SQLite/media/cache state.
It never connects the child to the production database. No personal history or
production documents are loaded; production retrieval algorithms and memory rules
remain active against isolated fixtures. This tests production code/settings with
public fixture state, not the running user's session or production corpus.

The new mode checks the configured engine is local, the configured model is
`qwen3.5:4b-q4_K_M`, context is 4096, and host matches the explicit loopback endpoint.
It refuses to substitute a different production model or context. Installed GGUF
identity must have the existing known digest prefix `d8b0f5e9760c`; the full installed
digest is then matched against the sole resident model and passed to the child.
Tag/digest/context mismatches, missing residency and any other resident model
refuse the trial. The runner never prepares/warms/unloads a model or restarts Ollama.

Unlike the older default Llama 1B diagnostic, the explicit Qwen mode does not
rewrite messages, seed, temperature, batch/thread options, generation cap,
keep-alive, think setting, STT/TTS timeouts or model timeout. The production
policy's effective answer budget is retained, with no diagnostic 48-token cap or
25-word suffix added by the runner. Public fixture questions can themselves
request brevity. Audio capture, channel routing, VAD/calibration, gain, buffering,
leading silence, player and serial protocol are unchanged. Online retrieval,
online persistence and device actions are disabled in this isolated child only;
Python internet sockets and HTTP redirects/proxies are blocked. Native STT/TTS
are the existing offline local binaries/packages, not new cloud adapters.

The child requires real Whisper.cpp/Piper/ALSA configuration and enabled TFT.
An enabled display object is not proof the physical TFT received/rendered data.
Manual observation is required. A strict public transcript match precedes LLM
service dispatch: a mismatch is refused without saving/echoing the captured words.
Speak exactly the printed question. Normalization permits capitalization and
punctuation differences; extra recognized words still refuse the trial.

## Guard and reporting contract

- Linux plus `--operator-live-trial` are required. Qwen mode cannot run as synthetic.
- Initial temperature must be **below 50 C**. Missing/nonfinite telemetry,
  current undervoltage/frequency capping/throttling/soft temperature limit or
  less than 512 MiB available RAM refuse startup. Existing resource policy can
  impose stricter generation restrictions. Historical throttle bits alone are
  not current pressure.
- The parent samples temperature, RAM, power/throttle and measured CPU clock,
  checks worker exit and model residency during the cycle. Poll sleep is 0.5 s;
  telemetry and bounded metadata I/O add observation latency. This is sampled
  protection, not a hard real-time cutoff. The existing pre-inference cool gate
  (below 55 C) remains in addition to the below-50 startup gate.
- At observed **70 C**, resource pressure or unavailable required telemetry,
  monitoring stops the owned process group. Overall Qwen child deadline is
  **120 s**, starting after spawn and including migration, display setup,
  microphone wait, STT, generation, synthesis and playback. Detection and
  terminate/reap grace add bounded normal cleanup time; it is not an exact
  120-second audible duration guarantee. The old diagnostic remains 60 s.
- Only the newly owned Linux session and descendants are signaled (TERM then
  KILL/reap). Existing Ollama/services are outside it. Ctrl+C uses the same
  cleanup path. Killing the client **does not prove immediate backend request
  cancellation**. Observe telemetry/backend state and wait for activity to
  settle before considering any separately approved trial. Never repeat blindly.
- Existing STT/Piper/ALSA timeout, exit and stream checks handle stage failures;
  a stuck live worker is bounded by the parent deadline. There is no independent
  heartbeat proving each native worker is making progress.
- One capture cycle and at most one generation request; no automatic retry,
  no prefix warm request and no follow-on test. The shared diagnostic lock
  prevents another participating harness, not an unrelated application. Operator
  must ensure nobody else is using microphone, speaker, TFT or inference.
- The parent displays a fixed MICROPHONE READY cue once it observes the child's
  capture-ready mark. Do not speak at the earlier LISTENING transition; adaptive
  calibration is still running then. Polling means the cue may arrive later than
  actual readiness and consumes part of the existing start timeout. No timeout
  or calibration default is extended.
- A new JSON path is mandatory; existing reports are never overwritten. The
  temporary child trace is copied even on parent abort and the temporary DB/audio
  is removed. Child console output is suppressed. Public fixture answer (bounded
  to 8192 characters), categorical routing, numeric metrics, display state calls,
  first-occurrence monotonic events and bounded-trial telemetry are retained.
  No raw audio, unmatched transcript, arbitrary exception text, secrets or
  production histories are exported. Keep reports local; delete them when no
  longer needed. Storage is limited per invocation by the trial/sample budget,
  not a global automatic archive-retention service.
- Capture start timeout is explicit as `capture_start_timeout`; cancellation,
  timeout and other child errors have categorical codes. Parent thermal/resource,
  model-residency and deadline reasons remain distinct. Worker failure is checked
  before a post-completion residency assertion. Partial event traces are not a
  completed sample. A failed cycle is never automatically rerun.

`sample_complete_quality_pending` means required software events occurred in
valid order. It does not certify answer quality, audible speech or TFT pixels.
`alsa_first_pcm_accepted` is the existing pipe-write observer, not acoustic onset,
and can include software preparation boundaries. `piper_first_audio` is the
first returned WAV, not an externally measured first sample. `cycle` measures
capture start to cycle end, excluding migration/setup before capture; the parent
deadline also includes setup. Do not add overlapping synthesis/playback durations.
Use external synchronized recording to measure true acoustic onset. Missing
cache counters remain unknown; residency is not proof of prefix-cache reuse.

## One consolidated Pi command sequence (operator only; not executed here)

First place the reviewed Step 51 commit in the existing feature checkout using
your normal update procedure. Substitute its actual path below. Compare `git log`
with the exact delivered commit SHA before continuing. Do not edit `.env`, install
packages, start a cold inference or run an unguarded voice loop to prepare this test.
If Qwen is not already resident, stop: cold-load preparation needs its own guarded
approval and is outside this single warm physical trial.

```bash
cd /path/to/existing/smart-ai-companion &&
test "$(git branch --show-current)" = codex/offline-model-selection &&
git log -1 --format='%H %s' &&
env VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS=30 \
  ./venv/bin/python scripts/validate_offline_voice.py \
  --operator-live-trial --production-qwen --fixture fact \
  --output "$HOME/demo-step51-$(date +%Y%m%d-%H%M%S).json"
```

This runs exactly one `fact` cycle. The setting applies to that child process tree
only and does not edit `.env`. Leave it unset for baseline buffering. The existing
first-sentence setting releases safe complete sentences of at least 30 characters;
it is not a promise that every first sentence releases early. Very short sentences,
lookahead, protected numbers/abbreviations/URLs and incomplete clauses retain the
existing safe behavior. Existing first-chunk clause thresholds stay unchanged.

Do not paste commands in a retry loop. To inspect a completed report without
inference: `python3 -m json.tool /actual/path/to/the/report.json`.

## Manual operator sequence and acceptance checklist

1. Check fan spins freely, its dedicated header connection is secure, supply is
   appropriate/stable and power warning is absent. Allow cooling below 50 C.
   Confirm the environment is fully assembled before any live trial.
2. Confirm existing ALSA devices (`arecord -l`, `aplay -l`) match the existing
   configured devices. Do not change routing or mix channels. This pipeline reads
   left-channel native 48 kHz stereo S32_LE for VAD and normalizes left channel to
   mono 16 kHz for STT. A microphone wired/output on the other channel can look
   silent; only physical inspection can establish the actual issue.
3. Ensure no active voice session or inference is contending with this trial;
   do not stop unrelated services. Check exact Qwen resident state; the runner
   checks it again and refuses missing/wrong state. Leave network physically
   disconnected/disabled through your existing offline setup if practical; do not
   reconfigure network services as part of this code milestone.
4. Run the single command. Read the displayed public question. Stay silent during
   warm-up and calibration, wait for **MICROPHONE READY**, then speak clearly at
   normal volume/distance: “Name India's capital. Is it north or south of the
   equator?” Use no private speech nearby. Keep room noise stable.
5. Watch LISTENING -> THINKING -> SPEAKING -> READY physically on the TFT. LISTENING
   alone is not capture-ready. Verify the actual speaker plays a clear first word,
   full natural answer and no missing/truncated ending. Do not infer pixels/audio
   from callbacks alone.
6. Check recognized speech passed the public-match gate and the answer says
   **New Delhi, north of the equator**. Software completion still requires human
   factual, completeness and audio review. Record manual pass/fail separately;
   the script leaves `quality_accepted` and physical verification false.
7. Review JSON status/reason, missing events, timing order, cache unknown/known,
   temperature throughout, current throttle bits and RAM. Do not present Windows
   mock timings or direct-text 10.25 s as voice performance guarantees.
8. For a VAD timeout, the trial exits and preserves partial trace. Check ERROR/
   closed display behavior, microphone channel, noise and calibration discipline.
   Do not immediately rerun. After cooldown and cause review, an operator may
   explicitly authorize a new single cycle to establish recovery (fresh VAD state).
   Automatic recovery/retry is not part of this runner.
9. RAG and memory require **separate operator approvals and single cycles**, not
   extra requests in the command above. For RAG, change only `--fixture fact` to
   `--fixture rag` in a new approved invocation and speak its exact printed public
   question. Expected local fact: Lumen uses **3.3 V** and direct **5 V** signal is
   unsafe; no invented specifications. For `--fixture memory`, expected codename
   is **Amber Finch**, seeded in isolated older conversation history. For
   `--fixture followup`, the seeded RAM/SSD discussion must resolve to **SSD**
   retaining data without power. Each trial gets a fresh isolated DB and report;
   none validates private production history. Human review is mandatory.
10. Approve the physical demonstration only after microphone/STT, complete factual
    answer, Piper/actual speaker, TFT, offline behavior and thermal checks pass.
    RAG, memory, follow-up and timeout recovery remain explicitly unverified until
    their separately authorized physical checks pass. Do not label synthetic
    acceptance or a single correct factual sample as a representative quality pass.

## VAD findings and conservative decision

Source: `assistant/voice/capture_vad.py`,
`voice_loop.record_audio_with_capture_vad`, `_normalized_alsa_left_rms` and
`normalize_audio`. Defaults are unchanged: 0.02 start floor, 0.5 s median
calibration, 60 ms onset confirmation, 300 ms minimum voiced speech, 0.8 s endpoint
silence, 5 s start timeout including calibration, 10 s maximum speech capture.
The 1 s warm-up sleeps after starting capture; input pipe/device buffering can
therefore make capture readiness differ from real room time. No acoustic timestamp
or hardware buffer flush is introduced in this release.

Adaptive start = max(configured floor, min(1, 2.5*median noise + .002)). Floor and
thresholds freeze after calibration, including while waiting. There is no current
aggressive post-calibration ratchet. Changing ambient conditions, low input level,
wrong channel, early speech or speech contaminating the median calibration can
explain missed onset in principle; **the observed Pi cause is not proven**. For
example, synthetic continuous RMS .06 during calibration produces .152 start
threshold, so the same amplitude never triggers. Starting immediately after a
quiet calibration cue succeeds in fixtures. A single loud spike is rejected by
median calibration/onset confirmation; short pauses preserve accepted speech.
Constant background noise and silence reach bounded start timeout.

No global threshold lowering, routing change, calibration change or new adaptive
ceiling is enabled. Physical validation is required before considering those.

## Evidence, tests and file inventory

Previously observed (operator supplied, not remeasured here): 65.11 s successful
physical interaction including 46.07 s cold load; warm direct first text 6.02 s,
full answer 10.25 s; one Whisper stage 3.304 s; warm short trial peak 61.7 C.
Backend full-reprocessing log is an observation, not proof of an architectural
cache cause or proof the production voice prompt equals the direct benchmark.
No new physical result or latency improvement is claimed.

Windows deterministic coverage includes nine new amplitude/channel fixtures,
production control preservation, Qwen full resident matching, strict startup gate,
120 s deadline/resource pressure, one-time sanitized ready cue, parent abort cleanup
and partial trace retention, failed-worker residency precedence, and the actual
voice/service path with fake STT/Piper/ALSA/TFT. Existing first-sentence regression
fixtures compare default and 30-character buffering, text reconstruction, grounding
and safety sentences, overlap, cancellation and Piper compatibility. These are
simulations of contracts, not measurements of Raspberry Pi performance.

Windows verification: focused suite **267 tests, OK (9.260 s)**; complete suite
**1213 tests, OK, one platform skip (55.861 s)**. Commands used:

```powershell
venv/Scripts/python.exe manage.py test assistant.tests_offline_voice_validation assistant.tests_demo_readiness assistant.tests_capture_vad assistant.tests_first_sentence_release assistant.tests_voice_loop assistant.tests_voice_stream assistant.tests_latency_trace assistant.tests_qwen_prompt_processing --noinput
venv/Scripts/python.exe manage.py test --noinput
```

Test temporary storage was redirected to the existing workspace `.test-tmp`.
Times are Windows suite runtimes, not device latency.

Changed files:
- `scripts/validate_offline_voice.py`: explicit production Qwen diagnostic mode,
  strict startup gate, preserved request controls, public ready cue, partial errors.
- `assistant/tests_offline_voice_validation.py`: Qwen control/guard/cleanup fixtures.
- `assistant/tests_demo_readiness.py`: deterministic public VAD/channel fixtures.
- `docs/demo-readiness-step51.md`: this operator procedure, limitations and rollback.
- `docs/demo-step51-focused-tests.log`: focused Windows regression evidence.
- `docs/demo-step51-windows-tests.log`: complete Windows regression evidence.

## Immediate rollback and remaining blockers

During a trial press Ctrl+C once and wait for parent cleanup. It signals only the
owned session; do not use `pkill`, stop Ollama or unload Qwen. Observe resource and
backend activity settling before any further use. Remove no production data.
The one-process sentence option ends with the command, so no settings rollback is
needed. Do not run the unguarded ordinary voice loop as a fallback.

For code rollback, leave this runner unused or switch an otherwise clean dedicated
checkout to the preceding baseline through your usual reviewed rollback procedure.
Do not reset a checkout containing unrelated work. On the shared branch prefer a
reviewed `git revert <Step-51-commit>` rather than rewriting branch history. No
migration, package, systemd, firmware, routing or `.env` rollback is required.

Remaining blockers: newly assembled hardware has not been physically verified in
this milestone; Qwen must already be resident with matching identity/context;
fan/power/temperature, correct microphone channel and room calibration need local
confirmation; actual speaker first word and TFT states require human observation;
RAG/memory/follow-up and post-timeout recovery need separately approved physical
checks. Ordinary continuous `voice_loop.py` still has no automatic continuous
thermal cutoff; this one-cycle runner does not make an unattended loop safe.
