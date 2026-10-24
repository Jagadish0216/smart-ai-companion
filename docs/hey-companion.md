# Optional offline Hey Companion

Status: integration only. **No trained or validated Hey Companion model is
included. Activation remains disabled.** GPIO17 push-to-talk and its existing
boot service remain the default. No service, production environment, network,
authentication, model weights, database or firmware is changed by this feature.

## Obtaining a genuine model

The legitimate route is a custom openWakeWord binary classifier trained on the
exact phrase **Hey Companion**, using its shared embedding/melspectrogram
backbone. The upstream built-in model list does not supply this phrase. A model
from another engine (including microWakeWord) is not automatically compatible.
Renaming `hey_jarvis` does not change its acoustic target. The local validation
also rejects an exact copy of any installed built-in model by hash; that check
cannot prove the provenance of every possible file from the Internet.

Use the upstream [automatic training notebook](https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb)
or its linked [Colab workflow](https://github.com/dscripka/openWakeWord#training-new-models).
The [complete training configuration](https://github.com/dscripka/openWakeWord/blob/main/examples/custom_model.yml)
and [trainer](https://github.com/dscripka/openWakeWord/blob/main/openwakeword/train.py)
are the authoritative references. Record the actual training repository commit,
environment/package versions, data licenses, export settings and output hash.
Use a backbone compatible with the Pi's pinned openWakeWord 0.6.0 ONNX pipeline;
do not upgrade the working Pi environment or replace its backbone to fit a
candidate. A mismatched export must be retrained/exported appropriately.

Training belongs on a separate workstation/environment: PyTorch, synthetic
Piper sample generation, augmentation, room impulse responses, background noise,
confusable phrases and substantial negative speech/music features are required.
A GPU workstation or Colab is practical for generating many examples and
training; cloud services are optional, not an inference dependency. CPU training
is possible but can be slow. The supplied upstream configuration recommends at
least 20,000 synthetic positives, with held-out synthetic validation, and provides
paths for large negative feature datasets. Provision storage, datasets and
training dependencies there. This task did not provision that infrastructure,
download datasets, run training or produce an acoustic model. All Pi inference
and evaluation below run locally without an API key or Internet connection once
the existing runtime, shared assets and genuine model are installed.

In the upstream notebook/full YAML set these values (retain and populate all the
other required data, augmentation and training fields):

```yaml
model_name: hey_companion
target_phrase:
  - "Hey Companion"
custom_negative_phrases:
  - "hey computer"
  - "hey company"
  - "companion"
  - "hey Jarvis"
n_samples: 20000
n_samples_val: 2000
```

After setting up that notebook's training environment and the full configuration,
its trainer accepts these stages; these are workstation commands, not Pi setup:

```sh
python openwakeword/train.py --training_config hey_companion.yml --generate_clips
python openwakeword/train.py --training_config hey_companion.yml --augment_clips
python openwakeword/train.py --training_config hey_companion.yml --train_model
```

Transfer the resulting genuine `hey_companion.onnx` over trusted removable media
or an authenticated local connection. Check its source/license/hash. Keep model
artifacts and recordings **outside the Git checkout**. No example ONNX binary or
passing report is supplied. A filename, JSON claim or successful load alone is
not proof that a file recognizes the intended phrase.

## Pi history and code update

The Pi has local merge commit `e4a3b33`. Do not assume a fast-forward, reset,
force-push, run `git clean`, or delete its untracked `knowledge/`. Before code
updates, stop any foreground voice loop with Ctrl+C. Inspect both histories:

```sh
cd /home/jagadish/apps/smart-ai-companion
git status --short
git branch --show-current
git log -5 --oneline
git fetch origin codex/offline-model-selection
git log --oneline --left-right HEAD...origin/codex/offline-model-selection
```

Resolve any existing tracked local modifications before merging. Preserve them
explicitly; do not automatically stash or discard them. With a clean tracked
checkout on the feature branch, integrate the remote while retaining local history:

```sh
test "$(git branch --show-current)" = codex/offline-model-selection &&
git diff --quiet && git diff --cached --quiet &&
git merge --no-edit origin/codex/offline-model-selection
```

If there is a conflict, stop and resolve it deliberately; do not run deployment
commands against an unresolved merge. Record `git rev-parse HEAD` after the merge.
No dependency reinstall, model pull, database migration, network switch or change
to an installed unit is required for the code integration itself.

## Candidate evaluation (offline, no microphone owner)

Prerequisites: the existing venv can import `openwakeword`, `onnxruntime`, `numpy`
and `scipy`; its feature ONNX files and Silero VAD asset are already local. Debian
13/Python >=3.12 needs the documented ONNX-only openWakeWord installation in the
repository README. Do not install a training stack on the Pi. If shared runtime
assets are missing, stage the official release assets separately while online
using the existing `prepare_wake_word_model` process, then repeat the offline
check. Runtime and validation never invoke its downloader.

Use a private directory such as `/home/jagadish/wake-validation`. Put the genuine
model there and held-out WAVs under `corpus/positive/` and `corpus/negative/`.
Collect consented real INMP441 LEFT-channel samples in the assembled Pi enclosure;
normalize to mono 16 kHz PCM16, using the existing normalizer. Stop the production
voice service before making any microphone recording; never open another reader
beside it. Store recordings privately, outside the repository, without logging
transcripts or passwords. Do not reuse training clips, duplicate/loop negative
recordings to inflate hours, or use synthetic-only evaluation.

The project acceptance policy requires at least 40 independent exact-phrase
positive clips across intended speakers, accents, distances and realistic fan
noise, at least 90% recall, at least five hours of representative independent
negative audio, at most 0.2 false activations/hour, and p95 inference per 80-ms
frame at most 80 ms **on this Pi**. Include ordinary speech, near phrases, silence,
fan noise, room noise and music among negatives. These are project trial gates,
not a statistical guarantee of accuracy. Calibration and final holdout sets must
be separate; do not tune the threshold on the final test set.

The evaluator resets state between WAVs, requires two consecutive scores,
applies the same VAD/threshold as runtime and counts negative activations with
a 1.5-second refractory interval. Negative evaluation estimates standby false
activations; it does not simulate a whole answer after each false wake. It reports
only aggregates/hashes, not recording names or audio. The operator is responsible
for correct labels and provenance. Do not use a renamed built-in as a candidate.

Create `/home/jagadish/wake-validation/training.json` with the following keys;
fill the hash/source from the actual completed training run, not example values:

```json
{
  "phrase": "Hey Companion",
  "model_sha256": "THE_ACTUAL_SHA256_OF_THE_TRAINED_MODEL",
  "training_source": "actual training repository revision and private run identifier",
  "held_out_real_recordings": true
}
```

The following is an offline evaluation, not activation. It opens no microphone
and can run while PTT remains available, subject to the usual resource/thermal
limits. Use a new report filename for each run; existing reports are not overwritten.

```sh
cd /home/jagadish/apps/smart-ai-companion &&
./venv/bin/python manage.py validate_companion_wake \
  --model /home/jagadish/wake-validation/hey_companion.onnx \
  --corpus /home/jagadish/wake-validation/corpus \
  --training-record /home/jagadish/wake-validation/training.json \
  --threshold 0.6 \
  --report /home/jagadish/wake-validation/candidate.json
```

The candidate report is always unapproved without `--accept-pi`. After reviewing
the measured results, checking the recordings were held out and captured on this
Pi, and verifying the existing PTT pipeline's audio cleanup, playback, Ctrl+C,
service stop/start and fallback, repeat the same evaluation with explicit Pi
acceptance. This is an operator attestation, not automatic proof of those checks:

```sh
./venv/bin/python manage.py validate_companion_wake \
  --model /home/jagadish/wake-validation/hey_companion.onnx \
  --corpus /home/jagadish/wake-validation/corpus \
  --training-record /home/jagadish/wake-validation/training.json \
  --threshold 0.6 \
  --report /home/jagadish/wake-validation/approved.json \
  --accept-pi
```

Even with this option, failing metrics leave `accepted_on_pi` false. The model,
shared assets, runtime versions, phrase, threshold and policy profile must match
at every startup. Changed assets require a new holdout evaluation. Missing,
incompatible or unapproved assets fail closed before ALSA is opened; nothing
downloads or silently substitutes Jarvis. Keep the report private and local.

## Explicit supervised activation and fallback

Until a genuine candidate passes evaluation, **stop here and keep using GPIO17**.
No boot-time enablement is installed. After approval, stop the existing physical
voice service, confirm it stopped, and run the optional mode in the foreground:

```sh
sudo systemctl stop smart-ai-companion-voice.service &&
test "$(systemctl is-active smart-ai-companion-voice.service)" = inactive &&
cd /home/jagadish/apps/smart-ai-companion &&
./venv/bin/python scripts/voice_loop.py \
  --hey-companion \
  --companion-model /home/jagadish/wake-validation/hey_companion.onnx \
  --companion-validation /home/jagadish/wake-validation/approved.json \
  --responsive --voice-refinements
```

This is a separate explicit input mode; it cannot be combined with PTT or
concurrent voice barge-in. It uses the existing wake provider, LEFT-channel
converter, retained post-wake capture, Whisper, AssistantService/Ollama, Piper,
conversation context and TFT states. It never changes Whisper/Ollama/Piper units.
The current shell must have the same speech settings as the working voice loop.
If the default voice service uses `/etc/smart-ai-companion/demo.env`, load that
existing non-secret override first in the same shell with
`set -a; . /etc/smart-ai-companion/demo.env; set +a`. Do not print/source production
`.env` as shell code; Django loads it through its existing mechanism.

Say the phrase, wait for LISTENING, then ask the request. Wake-only transcription
returns to READY without calling the LLM. Test a factual request, follow-up,
background noise/near phrases, and a long spoken response. The wake reader is
closed before request capture and remains closed throughout STT/LLM/playback.
After playback drains, a 1.5-second cooldown and model/VAD reset precede standby.
This suppresses this application's own speaker output, without claiming echo
cancellation or immunity to another nearby TV/person saying the actual phrase.
Do not enable experimental concurrent voice barge-in for this mode.

Test Ctrl+C during standby, request capture and playback; confirm all owned
arecord/aplay processes stop and the mic can be reclaimed. Relaunch the exact
foreground command to check clean restart and persistent model reuse within a
run. SIGTERM uses the same existing cleanup path. Any uncertain capture cleanup
is fatal; the loop must not reopen the microphone or start PTT automatically.
Do not repeatedly restart after hardware/thermal/cleanup failures.

Restore the reliable fallback only after the foreground process has exited and
its audio children are gone. If cleanup was uncertain, inspect `pgrep -a arecord`
and `pgrep -a aplay`; do not kill unrelated processes or start a second owner.

```sh
sudo systemctl reset-failed smart-ai-companion-voice.service &&
sudo systemctl start smart-ai-companion-voice.service &&
sudo systemctl --no-pager status smart-ai-companion-voice.service
```

Then test GPIO17 hold/release and a follow-up request. Repeat stop/foreground
restart/fallback and a normal reboot (with local recovery available); the unchanged
boot service should return to PTT. If the live wake trial fails, revoke the local
approval by moving `approved.json` aside and use PTT while retraining. No edits to
Wi-Fi, authentication, secrets or systemd units are needed. The per-user Linux
microphone flock excludes other project CLI modes under the same service user;
unrelated applications/users still require normal ALSA ownership discipline.

## Development tests and limits

```sh
node --test scripts/setup_handoff_check.cjs
./venv/bin/python manage.py test assistant.tests_companion_wake assistant.tests_wakeword assistant.tests_hands_free assistant.tests_push_to_talk
```

Tests use clearly synthetic model/score fixtures and mocked devices. They cover
approval rejection, hash/runtime changes, output compatibility, two-score
detection, state reset, capture cleanup/cancellation, serialized playback,
microphone exclusion and PTT fallback. They do not establish acoustic accuracy,
Pi performance, real GPIO/ALSA release or successful reboot. Those remain pending
until a real trained model and supervised Pi validation are available.
