# Step 65: Whisper input variants and offline VAD replay

Baseline `a3384578d2ebb7ff3a613506a90f585e5c389dcd`, branch
`codex/offline-model-selection`. No Pi inference, recording, installation,
service/model restart, production-data write or configuration change ran here.
The production voice path, Whisper factory, Moonshine installation and approved
speaker profile are unchanged. These diagnostics do not enable an optimization
in production or establish a physical accuracy improvement.

## Evidence and limits

Operator-supplied raw recording: 8 seconds, 48 kHz stereo S32_LE, active left and
silent right; left whole-file RMS approximately -31.9 dBFS and peak 0.3232.
Calculated from those supplied values, RMS is about 0.0254, crest ratio about
12.72 (22.1 dB), and a peak target of 0.5 implies gain about 1.54703 (+3.79 dB).
These are derived quantities, not a new analysis of the Pi file. Whole-file RMS
includes silence and does not identify speech-only loudness, noise or SNR.
The available recording is not attached in this Windows workspace; its frame
distribution and true speech/noise windows have not been measured here.

Historical Whisper base.en recognition took 2.33 s, small.en 12.09 s, and a guarded
base.en rerun 3.55 s. Reported latest WER is 45.45% for Whisper and 54.55% for
Moonshine, from separate runs. These are non-contemporaneous observations, not
controlled evidence of a universal engine difference. No new model or provider
integration is justified. Low microphone level is not a proven cause of errors.

## Audio analysis and exact variants

New opt-in tool: `scripts/whisper_audio_diagnostic.py`. It accepts only the existing
native 48 kHz stereo PCM32 WAV or normalized 16 kHz mono PCM16 WAV, limited to
30 seconds/20 MB. Channel zero is the verified left channel; stereo is never
averaged. Source files are read, fingerprinted and preserved. Generated WAVs are
mono 16 kHz PCM16, validated before Whisper. No microphone is opened.

For native stereo source, the exact chains are:

| Variant | FFmpeg filter chain |
| --- | --- |
| A: `baseline` | `pan=mono|c0=c0,aresample=16000` |
| B: `normalize` | `pan=mono|c0=c0,aresample=16000,volume=G,alimiter=limit=0.95:level=false` |
| C: `highpass` | `pan=mono|c0=c0,aresample=16000,highpass=f=80:p=2,alimiter=limit=0.95:level=false` |
| D: noise reduction | Skipped: no confirmed noise-only segment/stationary-noise evidence. No denoising filter is fabricated or enabled. |

For already normalized mono input, omit the pan term. `G` is formatted to nine
decimal places from `min(4, 0.5/source_peak)`; silence uses 1. For the supplied
peak, `volume=1.547029703`. This is bounded **peak-based amplitude normalization**,
not a target derived from whole-file RMS or an assumption about microphone
sensitivity. It can attenuate a source above the target. The 0.5 target reserves
resampling headroom; the 0.95 limiter protects B/C and their actual output peak is
checked (allowance 0.001 for PCM quantization). Existing source clipping cannot
be repaired by gain. No speaker loudnorm chain or legacy 4x speaker gain is used
on microphone input.

The 80 Hz two-pole high-pass candidate removes low-frequency content conservatively
compared with a telephone-style speech band. It may also affect low voice energy;
rumble reduction or accuracy benefit is a hypothesis until measured. B and C are
independent variants, not a combined gain/high-pass optimization. Their limiter is
included explicitly so amplitude effects can be reviewed as a confound. FFmpeg's
[official highpass/filter definitions](https://github.com/FFmpeg/FFmpeg/blob/master/doc/filters.texi)
document `f` in Hz and `p` as pole count. Missing/unsupported Pi filters fail closed;
the diagnostic does not install/upgrade tooling or silently substitute filters.

Every conversion uses `-nostdin -n`, `-ar 16000 -ac 1 -c:a pcm_s16le` and a 25 s
subprocess timeout. Existing outputs/source aliases are refused. Partial owned
variant files are removed on conversion/validation failure; source data is not
deleted. No WAV is played. B/C report peak-limit failure before inference.

The analysis JSON reports duration, source rate/channels/width, SHA256/bytes,
peak/RMS/dBFS, crest ratio, fraction of samples touching integer full scale, and
20 ms frame RMS p10/p50/p90/p99. Full-scale sample percentage is a clipping
indicator, not proof of a sustained clipped waveform. Individual frames/samples
are not exported.

The lower-decile frame RMS is labeled a **background-floor proxy**, not verified
noise. Frames at least 10 dB above that proxy (strictly above the threshold, with
a 1e-6 amplitude floor) supply an active-level estimate, weighted by sample count.
This is not linguistic speech identification; no selected frames means unknown.
No credible noise-only interval has been established, so SNR is explicitly null
and noise reduction is skipped. Do not interpret crest ratio or low-decile RMS as
SNR. Human annotation of a public noise-only interval would be additional evidence
for a later, separately reviewed denoising/SNR diagnostic.

## Read-only Pi analysis and replay

From the updated feature checkout, the following reads the existing public file
and prints numeric analysis/replay JSON. It does not infer, open devices, convert
audio, write a report/audio/configuration/database, or contact Ollama/network:

```bash
./venv/bin/python scripts/whisper_audio_diagnostic.py \
  --operator-approved-public-audio --audio "$HOME/mic-test-1m.wav"
```

Replay instantiates the **actual** `AdaptiveCaptureVAD`, with numeric values read
from current `config.settings` without django.setup, provider construction or
database connections. It uses 20 ms left RMS frames, records start/reset/stop
events, acceptance, floor, thresholds, max waiting RMS, durations and stop reason.
The returned configuration distinguishes actual capture-VAD enablement from this
hypothetical replay: replay can simulate the gate even when production fixed-length
capture is configured. The first file frame starts calibration at time zero.
Warm-up sleep, device initialization, ALSA/pipe buffering, wall-clock ready cue and
live room timing are **not simulated**. A file ending before a stop is incomplete,
not a successful endpoint. This replay cannot prove how live ALSA behaved.

Repository defaults currently remain: start .02, adaptive true, .5 s calibration,
60 ms confirmation, 300 ms minimum voiced time, 5 s start timeout including
calibration, .8 s end silence, 10 s maximum speech capture and 1 s warm-up.
Actual Pi environment overrides may differ; inspect the emitted options rather
than treating these defaults as measured Pi configuration. Production capture-VAD
enablement defaults false and is reported separately.

The production RMS path uses native LEFT 32-bit samples, and normalization also
selects left. Start threshold is `max(configured, min(1, 2.5*median_noise+.002))`.
Continuation uses a lower hysteresis threshold; thresholds freeze after
calibration. Speech/noise in the calibration window can raise the start threshold;
isolated spikes are rejected by median/onset confirmation. Consecutive above-start
frames are required; a short burst may reset and the original start deadline still
applies. Endpoint silence and maximum capture duration remain bounded.

Production opens arecord, sleeps during warm-up, then reads its pipe; buffering
means initial PCM and wall-clock room time need not coincide. The guarded parent
receives the ready cue through polling after calibration. Its delivery consumes
some of the existing start window. No buffer flush, routing or deadline change is
made without physical evidence. Compare Step 64 numeric `capture_vad_summary`:
noise_floor, start_threshold, continue_threshold, max_wait_rms and stop_reason.
If max_wait_rms is below threshold, inspect channel, calibration and speech level;
if above, inspect sustained confirmation, resets and the remaining start budget.

`speech_detected=True` alone does not mean minimum speech/endpoint was accepted.
Nor does it mean Whisper matched the public fixture. The separate categorical
`transcription_did_not_match_public_fixture` means STT returned unapproved text
after capture; it is not a VAD start timeout. The existing strict live fixture
gate remains unchanged. File-based WER diagnostics intentionally report approved
public-file recognition, but never dispatch it to Qwen or a voice conversation.
No production threshold/calibration correction is justified from aggregate RMS
and historical failures alone; none is implemented.

## One guarded Whisper variant per invocation

The new diagnostic calls the existing STT benchmark parent through a narrow
Whisper-variant entry point. It launches exactly one Whisper worker and no
Moonshine worker; Step 64's public CLI and default two-engine comparison remain
unchanged. Conversion occurs **inside** the monitored owned child before model
execution. Failure to prepare/validate the variant reports `audio_preparation_failed`
with available filter/source metadata and never calls Whisper. Unknown execution
errors retain categorical failure reporting without exporting native stderr.

All existing inference protections remain: below-50 C worker start, sampled abort
at >=70 C, current power/throttle checks, >=512 MiB available RAM, fail closed on
missing required telemetry, exclusive diagnostic lock, sole exact resident Qwen
tag/full installed digest/context 4096 before/during/after successful inference,
120 s overall worker bound, owned-group cleanup and no retries. Qwen is not warmed,
reloaded, unloaded or used for generation. Telemetry polls sleep approximately .5 s
plus sensor/metadata operation time; these are sampled guards, not hard realtime
or kernel OOM guarantees. Disposable state is used for existing resource helpers.

Whisper must be the existing `ggml-base.en.bin` and existing CLI. Four threads,
no native Whisper VAD flag and no timestamps match the established file benchmark
controls; this does not replace the production capture/VAD configuration. The
model command retains its 90 s subprocess bound inside the overall 120 s child
bound, which also covers preparation/fingerprinting. No new packages/models are
needed. Optional `--prepare --variant NAME --output-dir NEW_DIRECTORY` prepares
only that selected file without STT (25 s conversion bound); it is an explicit
processing operation, not the read-only mode or an unguarded inference shortcut.
For the first evaluation use the guarded command below rather than separate
preprocessing. Multiple/repeated/`all` variant selectors are rejected.

The guarded JSON includes source/processed analysis and fingerprints, exact filter
chain, model fingerprint, final recognition, WER/S/D/I, model timing, worker CPU/RSS,
sampled peak temperature and minimum available RAM. Completion status is
`whisper_variant_complete_quality_pending`, mode `whisper_audio_variant`.
Source top-level SHA is the frozen original snapshot; processed SHA lives under
the child `audio_processing` metadata. Historical 45.45%/3.55 s Whisper results
are explicitly non-contemporaneous, with scoring/audio identity unverified.

Whisper CLI wall time includes model load; native load counters remain unknown if
absent. End-to-final-text includes preparation/setup, not live utterance endpoint
latency. CPU/RSS/temperature interpretation is unchanged from the existing
[benchmark methodology](offline-stt-audio-profile.md). No Windows test time is a Pi
speed improvement. Review actual words and factual intent, not WER alone. Repeat
measurements/new public utterances need a new operator decision; never automatically
run all variants or retry after heating/failure.

## Operator commands (not executed here)

Update only a clean tracked feature checkout, preserving untracked/unrelated work:

```bash
cd "$HOME/apps/smart-ai-companion" &&
test "$(git branch --show-current)" = codex/offline-model-selection &&
git diff --quiet && git diff --cached --quiet &&
git fetch origin codex/offline-model-selection &&
git merge --ff-only origin/codex/offline-model-selection
```

Verify HEAD against the delivered SHA. First run the read-only analysis above.
For a separately supervised first **baseline** trial, naturally cool below 50 C,
ensure Qwen is already correctly resident, then run this once from the checkout:

```bash
./venv/bin/python scripts/whisper_audio_diagnostic.py --run --variant baseline \
  --operator-approved-public-audio --audio "$HOME/mic-test-1m.wav" \
  --whisper-bin "$(./venv/bin/python -c 'from config import settings; print(settings.STT_WHISPER_BIN)')" \
  --whisper-model "$(./venv/bin/python -c 'from config import settings; print(settings.STT_WHISPER_MODEL)')" \
  --reference "Hello mic testing one two three this is smart AI companion" \
  --output "$HOME/whisper-baseline-$(date +%Y%m%d-%H%M%S).json"
```

The two command substitutions read only the configured executable/model paths;
they do not transcribe, modify settings or access application data. An unexpected
model path/name refuses the trial. No guessed installation path or new model is
substituted. After reviewing a completed baseline and cooling, an operator may
explicitly authorize a separate invocation changing only `--variant` to normalize
or highpass and choosing a new report name. Do not paste this in a loop. A gate
refusal is not authorization to unload Qwen, restart services or change safety limits.

Remaining physical work: obtain actual analysis/replay output; verify required
FFmpeg filters on the installed Pi version; review prepared input audio for
clipping/consonant loss with separate playback approval; compare final words/WER,
semantic correctness and thermal/resource behavior one variant at a time. New
1-metre recordings require explicit operator approval. Live VAD calibration,
ready-cue timing and endpoint behavior still need supervised physical validation.
Piper speaker-profile acceptance remains separate and unperformed here.

Rollback: leave the diagnostic unused; production settings were never enabled.
Existing benchmark modes continue to work. Ctrl+C reaps only owned guarded workers;
do not kill unrelated services. Remove only explicitly owned diagnostic reports/
generated files when no longer needed, preserving original WAVs. A reviewed revert
of this commit is the code rollback; do not reset a checkout with unrelated work.
No `.env`, database, model, package, GPIO/ESP32, audio-device or speaker-profile
rollback is needed.

## Verification and files

Windows: **117 focused tests, OK (1.540 s)**; full suite **1257 tests, OK, one
platform skip (39.299 s)**. Deterministic mocks cover left-only native input,
frame/level calculations, full-scale indicator, gain bound/limiter validation,
source preservation, mono16 output validation, filter/preparation failures,
single-variant routing, child transcript/WER reports, production VAD consistency,
temperature/RAM/power/residency refusal and no retries. Existing tests cover
monitored thermal/deadline abort, worker ownership, VAD silence/transients/onset/
endpoints and unchanged speaker/provider behavior. FFmpeg is unavailable on this
Windows host: conversions/native inference were mocked, so rendered Pi PCM and
accuracy are not newly verified.

Commands (TEMP/TMP pointed to workspace `.test-tmp`):

```powershell
venv/Scripts/python.exe manage.py test assistant.tests_whisper_audio_diagnostic assistant.tests_moonshine_only assistant.tests_stt_comparison_audio_profile assistant.tests_capture_vad assistant.tests_offline_voice_validation --noinput
venv/Scripts/python.exe manage.py test --noinput
```

Files changed: `scripts/whisper_audio_diagnostic.py`,
`scripts/compare_offline_stt.py`, `assistant/tests_whisper_audio_diagnostic.py`,
this document, `docs/whisper-audio-focused-tests.log`,
`docs/whisper-audio-windows-tests.log`. No production voice/configuration/factory
or approved speaker-profile file changes.
