# Offline STT trial preparation and opt-in speaker profile

Baseline: `7d3383a3fc05bb63cbbaaf9388bc365f120f79aa`, branch
`codex/offline-model-selection`. No Pi inference, package installation, model
download, microphone recording, playback, service restart or deployment was run.

## Recommendation and acceptance status

**Keep the existing Whisper base.en provider for the demonstration. Trial
Moonshine English Small Streaming in a separate environment only after operator
approval.** Do not change the factory or fallback yet: no measured Moonshine
accuracy, latency or Qwen coexistence result exists for this microphone/Pi.
Whisper small.en's supplied 12.09 s result does not justify switching to it.
The speaker profile is implemented and mock-tested, ready for a supervised
physical evaluation, not enabled or physically accepted for Piper speech.

The operator reports an 8-second left-channel INMP441 recording at 1 metre and
these results, which were not remeasured here:

| Engine | Supplied execution time | Supplied raw recognition | Calculated normalized WER |
| --- | --- | --- | --- |
| Whisper base.en, 141.1 MiB | 2.33 s | Hello, my testing 1, 2, 3. This is my day at companion. | 36.36%: 3 substitutions, 0 deletions, 1 insertion |
| Whisper small.en, 465 MiB | 12.09 s | hello my testing one two three is a smart egg companion | 36.36%: 4 substitutions, 0 deletions, 0 insertions |

Reference: “Hello mic testing one two three this is smart AI companion”. There
are 11 reference words. The new scorer lowercases ASCII English, splits punctuation
and maps standalone digits 0–10 to their English words. It does not merge/split AI
initials, normalize synonyms, repair grammar or silently correct recognition.
Levenshtein ties prefer substitution, deletion, then insertion. Different scoring
policies can produce different error counts. One utterance cannot establish
general quality or superiority at 1 metre.

## Official compatibility evidence

Research read on 2026-10-20. Official source examined at
[`234f60faa0eb388b01cdf7e60aca232af37aefda`](https://github.com/moonshine-ai/moonshine/tree/234f60faa0eb388b01cdf7e60aca232af37aefda).
Mutable documentation and wheel metadata must be rechecked before an approved
installation; no claim is made about already installed Pi dependencies.

| Item | Evidence and limit |
| --- | --- |
| Model/license | Official catalog lists English Small Streaming, 123M parameters, MIT. Reported public-dataset WER is not a prediction of this user's audio quality. [Available models](https://moonshine-voice.readthedocs.io/en/latest/models/available-models/), [license](https://github.com/moonshine-ai/moonshine/blob/234f60faa0eb388b01cdf7e60aca232af37aefda/LICENSE). |
| ARM64/Python | PyPI 0.1.5 publishes `py3-none-manylinux_2_31_aarch64` (18,505,651 bytes) and `py3-none-manylinux_2_34_aarch64` (18,634,500 bytes). These are Python 3 ABI-independent tags, not cp313-specific wheels; metadata requires Python >=3.8. Python 3.13 is within that range. Actual Debian 13 glibc, native loading and dependencies must still be verified locally. [PyPI metadata](https://pypi.org/pypi/moonshine-voice/0.1.5/json). |
| Installation footprint | Base dependencies: numpy, sounddevice, requests, tqdm, filelock, platformdirs, google-crc32c. No torch/transformers extra is needed for this trial. Expanded wheel, dependencies, PortAudio/native runtime availability and total disk/RAM footprint have not been measured. Do not use upstream's systemwide `--break-system-packages` example. [Package declaration](https://github.com/moonshine-ai/moonshine/blob/234f60faa0eb388b01cdf7e60aca232af37aefda/language-bindings/python/pyproject.toml). |
| Model download footprint | Pinned native registry's eight required English `small-streaming-en/quantized_26_08_21` files total **142,300,974 bytes (~135.71 MiB)**; optional attention decoder and spelling/diarization assets excluded. This is registry metadata, not a completed download or full install footprint. [Catalog](https://github.com/moonshine-ai/moonshine/blob/234f60faa0eb388b01cdf7e60aca232af37aefda/core/moonshine-model-catalog.cpp), [file sizes](https://github.com/moonshine-ai/moonshine/blob/234f60faa0eb388b01cdf7e60aca232af37aefda/core/moonshine-model-file-metadata.generated.cpp). |
| Offline API | Direct `Transcriber(model_path=..., model_arch=ModelArch.SMALL_STREAMING)` loads supplied files. `start`, `add_audio`, `stop` feed mono floats; stop returns final transcript. Recommended-model/microphone helpers can download on first load and currently default English to Medium, so the benchmark does not use them. [Transcription guide](https://moonshine-voice.readthedocs.io/en/latest/using/transcription/), [Python implementation](https://github.com/moonshine-ai/moonshine/blob/234f60faa0eb388b01cdf7e60aca232af37aefda/language-bindings/python/src/moonshine_voice/transcriber.py). |
| CPU/RAM | Native ARM64 distribution exists, but no official minimum working set for this exact workload/Qwen combination was established. Model-file size and parameter count are not peak runtime RAM. CPU throughput, working set and thermal effect on this Pi remain unknown. The benchmark measures them rather than promising realtime operation. [Official platform guide](https://moonshine-voice.readthedocs.io/en/latest/quickstart/). |

The benchmark pins Python package 0.1.5 to the reviewed API. Required local files:
adapter.ort, cross_kv.ort, decoder_kv.ort, encoder.ort, frontend.model.ort,
frontend.weights.ort, streaming_config.json, tokenizer.bin. The operator must
obtain the English Small artifact set with official manifest integrity checks
after separate download approval. The candidate verifies every required byte count and CRC32C against the pinned
official English Small registry before native loading; SHA256/byte fingerprints
are also reported. Other model/language/version artifacts fail closed rather than
being substituted. CRC32C is an artifact integrity check, not a substitute for
trusted distribution/provenance. No model helper/download API is called.

If wheels or base native dependencies do not work in the isolated Python 3.13
environment, stop and retain Whisper base.en. Report the missing native dependency
or ABI error locally; no Docker, compilation, global packages or system updates
are authorized by this milestone. A metadata-compatible wheel is not a native
runtime acceptance pass. The read-only check below intentionally does not load a
native library or transcribe anything.

## Next Pi step: one read-only command

From the reviewed feature checkout, using the existing production venv:

```bash
./venv/bin/python scripts/compare_offline_stt.py
```

This reads platform/Python/glibc and installed package **metadata only**, makes no
network request and does not create a report, import Moonshine native code,
record, play, infer or connect to a database. It is useful even if Moonshine is
not installed. It is not an instruction to install it in production.

## Supervised recording-only benchmark, after separate approval

Before any download/inference, approve the exact wheel/dependency plan and English
Small manifest. Use a separate venv, not production `venv`, and do not install
extras. Stage verified model files and dependencies there. This repository changes
no requirements files. No installation/download command has been executed here.

The existing normalized `/home/jagadish/mic-test-1m-whisper.wav` is appropriate
only if it is mono 16 kHz PCM16 as required by the runner. The original 48 kHz
stereo S32_LE recording must be normalized using the already verified **left**
channel, never averaged with its silent right channel. The tool refuses native
stereo input instead of silently changing channel mapping. Audio must be a
public, operator-approved recording; raw recognition is deliberately exported
for WER evaluation. Do not use private conversations or unattended microphone
capture. No raw audio is exported with reports.

For a separately approved trial, substitute actual existing paths (these paths
are placeholders, not claims about the Pi installation):

```bash
./venv/bin/python scripts/compare_offline_stt.py --run \
  --operator-approved-public-audio \
  --audio /home/jagadish/mic-test-1m-whisper.wav \
  --python /actual/isolated-stt-venv/bin/python \
  --whisper-bin /actual/whisper-cli \
  --whisper-model /actual/models/ggml-base.en.bin \
  --moonshine-model /actual/small-streaming-en/quantized_26_08_21 \
  --output "$HOME/stt-comparison-$(date +%Y%m%d-%H%M%S).json"
```

One invocation runs **one Whisper and at most one Moonshine trial**, sequentially,
without retries. Each worker requires below 50 C initially; if Whisper heats the
Pi above that, the candidate is refused. There is no automatic wait/retry loop.
Both use the same frozen normalized file snapshot and public reference (default
above). Its SHA256 is in the report. New paths/references can be supplied only
for separately approved public recordings via `--audio` / `--reference`.

Qwen must already be the sole resident model, matching known prefix
`d8b0f5e9760c`, full installed digest and context 4096. The benchmark only reads
Ollama metadata and never dispatches an LLM request, changes residency/keep-alive
or warms/unloads/restarts anything. Changes by another application are outside
the runner's control. Stop if another application is using inference/audio.
The lock serializes participating diagnostic tools.

Safety reuses the validated monitor/resource logic: fail closed on missing
required telemetry, current power/throttle flags or available RAM below 512 MiB;
abort at observed 70 C. Qwen residency is checked during each worker and after
successful completion. Each worker has a 120-second parent limit including
imports, fingerprinting, setup and recognition; Whisper also has a 90-second
subprocess bound. Two workers imply at most 240 seconds of nominal worker
budgets plus bounded metadata/telemetry/cleanup overhead, not a hard realtime
240-second guarantee. Sampling sleep is 0.5 s plus sensor/HTTP operation time.
Only fresh owned Linux process groups/descendants are terminated/reaped. Existing
Ollama is outside them. Failures preserve partial scalar/result reports and stop
the comparison. There are no native worker heartbeats beyond exit/stage timeout
checks. This is sampled protection, not prevention of every possible kernel OOM.

The candidate worker requires an isolated venv distinct from the baseline prefix.
It does not import Django; the parent's existing telemetry helper uses disposable
SQLite/media/cache bootstrap. No production data is opened. Python socket
connections in STT workers are blocked; explicit native local-file APIs are used.
This is **not** an OS network sandbox of arbitrary C code/executables: network-off
operation still needs operator observation/disconnection and trusted staged
binaries. No cloud adapter or model downloader is used. All native stdout/stderr
is suppressed from the terminal; temporary Whisper logs are removed.

Reports include raw public recognition, WER/S/D/I, audio/model fingerprints,
model-load timing if available, transcription time, worker-begin-to-final-text,
CPU seconds, max single-process RSS from Linux rusage, host CPU samples, available
RAM and sampled temperature peaks. Keep reports local; no automatic archive
retention is added. Audio is limited to 30 seconds/2 MB, text to 8192 characters,
reference/hypothesis scoring to 256 words, bounded child-result JSON to 32 KiB.
One failed worker does not authorize another attempt.

Timing interpretation:

- Moonshine load measures native constructor separately from feed/finalization.
  Audio is fed rapidly in 100 ms blocks with no realtime sleeping. Interim events
  are never dispatched; only the complete result returned by stop is scored.
  No listener is attached, so interim segments cannot create duplicate AI requests.
- Whisper uses four threads, no native VAD flag and no timestamps, recorded in
  controls metadata. This is a file-recognition control, not a claim to reproduce
  every production capture/STT-VAD option. Whisper CLI wall time **includes loading**. Native `load time` is reported if
  present, otherwise unknown; it is not inferred by subtracting unrelated stages.
  Native cold/warm OS file-cache differences and fixed trial order remain confounds.
- End-to-final-text starts at worker entry and includes preprocessing/import/
  fingerprinting/setup. It ends at final recognition readiness, excludes capture
  and parent launch, and is not live utterance-end latency or acoustically measured
  speech onset. Cleanup and scoring are part of worker wall time instead.
- CPU is worker process-time delta plus native child CPU time, not host CPU
  percent. RSS is the larger single-process peak across worker/native child, not
  summed concurrent process-tree peak or Qwen RAM. Host available RAM and Qwen
  metadata checks provide coexistence evidence, not a universal capacity guarantee.

After new-recording approval, use several public utterances at 1 metre: factual
questions, technical names/numbers, natural follow-ups, short pauses, and ordinary
room noise. Write references before recognition; score all examples, including
failures. Compare aggregate word-weighted WER, each semantic error, time-to-final,
resource pressure and repeatability against the same Whisper baseline. Agree
quality/latency acceptance limits with the operator before integrating Moonshine.
Do not infer production quality from one success. This tool never records audio.

## Capture VAD investigation remains independent

Direct recording working does not prove live VAD will detect speech. Existing VAD
uses left-channel RMS, a median calibration floor, start threshold
`max(.02, min(1, 2.5*noise+.002))`, confirmation, minimum voiced duration and
endpoint silence. Thresholds freeze after calibration; speaking during calibration
or room noise can set a threshold above later speech. Startup pipe buffering and
the distinction between LISTENING and the later capture-ready cue also matter.

There is no new measured RMS series in the supplied evidence sufficient to justify
another algorithm/threshold change. Existing deterministic fixtures already
reproduce quiet speech, noisy calibration, silence, transients, pauses, left-only
mapping and timeout behavior. The targeted diagnostic addition is an optional,
best-effort summary callback. Guarded voice reports now retain only noise_floor,
start_threshold, continue_threshold, max_wait_rms, voiced/capture durations,
elapsed time and stop_reason on success/failure. No frames or words are retained.
This exposes the measurements previously hidden by suppressed child console logs.
Callback exceptions do not alter capture, cleanup or cancellation.

On a future approved failure, compare measured max_wait_rms to start_threshold and
calibration noise; distinguish no PCM/wrong channel, low signal, contaminated
calibration and deadline from STT quality. A synthetic .05 floor gives .127 start;
.07 speech then times out. This is a reproduction, not the actual Pi diagnosis.
Wait for MICROPHONE READY and stay silent during calibration. Do not blindly lower
thresholds, enable the hands-free ceiling for normal capture, disable VAD or add
automatic retries. Any targeted threshold/calibration correction needs measured
evidence and separate physical validation first.

## Speaker profile implementation and rollback

Default `VOICE_PLAYBACK_PROFILE=legacy` retains the original 4x gain/0.98 limiter
and commands. New profiles apply only to the existing ALSA conversion path and
replace, rather than multiply, `VOICE_PLAYBACK_GAIN`:

| Opt-in | FFmpeg chain before unchanged stereo 48 kHz S32_LE output |
| --- | --- |
| `approved_loudness` | `loudnorm=I=-16:TP=-2:LRA=7,volume=1.10,alimiter=limit=0.95:level=false` |
| `bounded_gain` | `volume=1.10,alimiter=limit=0.95:level=false` |

Piper speech is normally mono. Do not apply the test recording's `pan=mono|c0=FL`
to it; the existing output conversion maps mono to the speaker's stereo transport.
No microphone/device routing or hardware changes are made. `bounded_gain` is a
lower-complexity comparison candidate, **not equivalent** to -16 LUFS normalization
and not a promise of the accepted test volume. Its fixed gain preserves relative
chunk amplitudes before limiting rather than normalizing each independently.

Both streaming PCM conversion and batch WAV conversion receive the opt-in profile.
They still use the same cancellable FFmpeg call, queues, first-sentence boundaries,
Piper overlap, output devices and player. There is no whole-answer/two-pass
normalization or new buffering stage. Single-pass loudnorm is applied per existing
chunk; differing chunk content/length may produce volume pumping, and processing
cost/internal buffering may delay first speech. No low-latency claim is made.
Upstream documents dynamic loudnorm's 192 kHz processing and limiter lookahead;
the output is still explicitly 48 kHz. [Official FFmpeg filter documentation](https://github.com/FFmpeg/FFmpeg/blob/master/doc/filters.texi).
No FFmpeg binary was available on this Windows test host, so command/streaming
contracts were mocked; rendered PCM peaks, real CPU cost, audible pumping,
first-word clarity and amplifier/speaker behavior remain physically unverified.

After explicit physical-trial approval, prefix the existing guarded single-cycle
voice command with `env VOICE_PLAYBACK_PROFILE=approved_loudness` (and optionally
the previously tested first-sentence setting). Do not edit `.env` or independently
increase gain. Check first word, complete speech, multiple chunk transitions,
distortion, actual speaker level and trace timing against the legacy control.
Stop on undesirable volume/distortion or guard refusal; do not automatically retry.
Choose bounded_gain only as a separately approved comparison, not as a silent
replacement for the accepted loudness target.

Rollback is immediate: omit/unset `VOICE_PLAYBACK_PROFILE` or use `legacy` for the
next process. No production setting is enabled by this commit. Ctrl+C during a
guarded trial terminates owned workers; it never restarts/unloads Qwen. Keep
Whisper selected. Code rollback should use a reviewed revert, never reset unrelated
work. No database/schema/firmware/systemd migration or package rollback is needed.

## Verification and changed files

Windows results: **245 focused tests, OK (6.016 s)**; complete suite
**1236 tests, OK, one platform skip (36.772 s)**. Tests use fake native APIs,
Whisper output, audio devices, models and timers. They verify WER, PCM validation,
offline refusal, reviewed architecture, final-only output, missing model/load
counters, hot-start refusal, failure/no-follow-on behavior, partial reports,
profile replacement of gain, unchanged format/cancellation, streaming overlap and
numeric VAD failure diagnostics. These tests are not Pi benchmarks or acoustic
acceptance.

Files: `scripts/compare_offline_stt.py`, `scripts/voice_loop.py`,
`scripts/validate_offline_voice.py`, `config/settings.py`,
`assistant/tests_stt_comparison_audio_profile.py`, this document,
`docs/stt-audio-focused-tests.log`, `docs/stt-audio-windows-tests.log`.
Whisper factory/default, Qwen, `.env`/`.env.example`, benchmarks/quality bank,
GPIO/ESP32, production data and unrelated files remain unchanged.


Verification commands (TEMP/TMP redirected to workspace `.test-tmp`):

```powershell
venv/Scripts/python.exe manage.py test assistant.tests_stt_comparison_audio_profile assistant.tests_capture_vad assistant.tests_offline_voice_validation assistant.tests_voice_loop assistant.tests_voice_stream assistant.tests_first_sentence_release --noinput
venv/Scripts/python.exe manage.py test --noinput
```

The default read-only CLI and `--help` were also exercised on Windows without a
Moonshine installation; no native inference or model download occurred.
