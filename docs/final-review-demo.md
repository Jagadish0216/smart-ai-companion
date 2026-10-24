# Offline final review demonstration

The primary review input is **hold-to-record push-to-talk**, selected by the
new physical voice service. Release starts processing without a 10-second
recording window or a VAD endpoint wait. The existing Enter-to-record and Hey
Jarvis paths remain available. Experimental acoustic interruption and adaptive
capture do not run in PTT mode. Complete-first-sentence streaming (`--responsive`)
and bounded speech-delivery refinements (`--voice-refinements`) are enabled:
earlier complete later sentences, punctuation-only silence and at most 0.8 seconds
of initial lookahead. These preserve one Piper preparer and ordered playback;
they cannot guarantee zero gaps when model production is slower than speech.
No Pi hardware acceptance is claimed
from Windows tests.

## Button wiring and safe attachment

The checked-in `hardware/enclosure/WIRING.md` and its optimized copy assign
BCM18/19/20/21 (physical 12/35/38/40) to I2S audio and reserve BCM14/15
(physical 8/10) for a possible serial harness. The current TFT uses the separate
ESP32 mapping; its pin numbers are not Raspberry Pi header allocations. No
repository assignment uses **BCM17, physical header pin 11**. The active cooler
uses the Pi 5 fan connector. Actual added wires/HATs must still be checked locally.

1. On the existing powered Pi, inspect `pinctrl get 17` and the enclosure wiring.
   It must be an unused input, not a peripheral alternate function or another
   switch/output. The deployment preflight also discovers the RP1 GPIO chip by
   label and refuses an already claimed BCM17. If either check disagrees, stop;
   do not silently select another pin or disconnect an existing peripheral.
2. Shut down with `sudo systemctl poweroff`. Disconnect Pi power and any ESP32
   USB/power source that could back-power the enclosure.
3. Connect a normally-open momentary button between **Pi physical pin 11
   (BCM17)** and **Pi physical pin 9 (GND)**. These are the only two button wires.
   Internal pull-up makes the input active-low. Never connect it to 5V or 3.3V.
   A four-leg tactile switch has permanently connected leg pairs: choose contacts
   that are open until pressed, confirmed with an unpowered continuity test.
4. Secure and insulate both wires, then reconnect power. Do not change I2S,
   speaker, fan or ESP32 wiring.

Press and hold, wait for **LISTENING**, speak, then release after the last word.
The adapter requires a stable 40-ms press and stable release before rearming;
release during capture is checked without debounce delay (<=10-ms selector
polling). It retains native audio from the first captured frame, including the
last complete frame. Presses shorter than 120 ms of audio are ignored. A stuck
button has a 20-second capture cap and must be released before another request.
Presses/releases while an answer is busy are discarded. Shutdown closes/reaps
capture. A shared Linux per-user lock prevents this project's live CLI modes
and service from claiming the microphone simultaneously. Other unrelated audio
owners must also be stopped; a failed device claim fails closed.

## Genuine wake-word status

No compatible, validated **Hey Companion** model is present in this repository
or its checked project assets. The official OpenWakeWord model list does not
provide that phrase. Its custom training workflow requires new positive and
negative training data plus validation; changing a label is not training.
Preparing a credible acoustically validated model is outside today's verified
assets, so no synthetic placeholder or relabelled Jarvis model is installed.
See the [official training configuration](https://github.com/dscripka/openWakeWord/blob/main/examples/custom_model.yml).

PTT never imports/loads wake models. A missing optional wake model therefore
cannot prevent the primary demo from booting. The existing Hey Jarvis provider,
custom ONNX/TFLite path support and local-asset checks remain intact for rollback.
Do not announce Hey Companion recognition or demonstrate it until a real model
has been trained, installed locally and physically accepted. Do not run a second
wake/voice loop alongside the physical service.

## Boot and offline behavior

The existing NetworkManager boot unit now selects `DEMO_AP` when the root-owned
demo override is enabled. Every boot chooses the separate **SmartCompanion Demo**
profile before saved client networks; no saved profile is deleted. Its SSID is
**SmartAI-Companion**, WPA2/RSN with a private on-device password, shared IPv4/DHCP,
and configured gateway **10.77.0.1/24**. Setup provisioning remains separate and
retains its original isolation. The demo dashboard and APIs require a Django
login; staff restrictions and CSRF remain. Browser radio mutations are blocked
during demo mode so they cannot accidentally drop the presentation network.
There is no password in Git, the systemd units or command history. NetworkManager
stores the interactively entered PSK in its protected profile, not the database.

The existing Gunicorn unit remains the only production Django server on port
8000. Model warm-up reuses the approved resident Qwen3.5 4B Q4_K_M/context 4096;
it does not pull or replace a model. A new loopback-only `whisper-server` service
loads the **existing base.en model once** and accepts bounded normalized WAV
requests at 127.0.0.1:8178. It is not exposed to hotspot clients. The adapter
disables environment proxies and redirects, bounds upload/response sizes and
does not spawn Whisper per question. Upstream whisper.cpp serializes inference
with a model mutex; the application also rejects overlapping STT calls within
one provider. See [upstream server implementation](https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/server.cpp).

The physical service waits for resident STT and successful model warm-up, warms
Piper, claims BCM17 and then shows READY. It has one microphone owner and uses
the existing AssistantService, SQLite context, lexical document retrieval,
approved ALSA loudness and TFT states. Piper/providers are reused. GPU/model
configuration, thermal/resource guards, firmware and production `.env` remain
unchanged. Private conversational stdout is not sent to the service journal.
Startup/restart limits are bounded; repeated failure stops the service rather
than retrying forever. Failed AP configuration never creates an open hotspot;
authenticated Ethernet/console recovery remains possible.

The dashboard header says **LOCAL MODE**, not that the hotspot has WAN access.
Core local speech, chat, stored context and indexed document retrieval work
without WAN; optional online tools remain disabled and are not claimed offline.

## One-time Pi deployment

Perform this while Internet is still available for Git and the one optional
GPIO dependency. Stop the old foreground voice loop with Ctrl+C first. Do not
run the older all-in-one `pi_setup.sh`, which can install/pull models. Use user
`jagadish` and the existing `/home/jagadish/apps/smart-ai-companion` checkout.

Use the exact commit SHA supplied in the completion message:

```sh
DEMO_COMMIT=PASTE_THE_FULL_COMMIT_SHA_FROM_COMPLETION
cd /home/jagadish/apps/smart-ai-companion
test "$(git branch --show-current)" = codex/offline-model-selection &&
git diff --quiet && git diff --cached --quiet &&
git fetch origin codex/offline-model-selection &&
git merge --ff-only "$DEMO_COMMIT" &&
bash scripts/final_review_demo.sh prepare "$DEMO_COMMIT"
```

Preparation backs up SQLite using its backup API, production `.env`, and prior
systemd units/drop-ins under a private per-commit directory in
`~/.cache/smart-ai-companion/`; it does not overwrite the original backup on a
retry. The original database contents and `.env` are retained. It installs only
`lgpio` if not already importable in the venv. It verifies BCM17 ownership,
existing model/voice assets, approved Qwen/context and a private Django key.
If a `whisper-server` executable is not already built beside the configured CLI,
it may build **only that target**, with two compile jobs and a 180-second bound,
from the existing local CMake build. No source/model download or reinstall is
performed. A missing/incompatible local build stops preparation without replacing
the working CLI. This is a real device prerequisite, not an assertion that the
server is already installed on the unseen Pi.

Preparation installs root-owned overrides and units, runs additive migrations,
collects local static assets and checks Django. It uses an existing active staff
account or prompts for `createsuperuser` if none exists. Enter a private review
account password only at that on-device prompt. It separately prompts twice for
a unique WPA2 hotspot password (16+ characters recommended), without echoing it.
Do not enter a sample password or publish it. The prepared profile is not
activated, so this step does not switch the Wi-Fi management connection.

**The next action can disconnect Wi-Fi SSH.** Move to a local console or verified
wired Ethernet management connection before continuing. The script requires an
interactive `DEMO-AP` acknowledgement:

```sh
bash scripts/final_review_demo.sh activate "$DEMO_COMMIT"
```

It stops the old web process before switching the radio, so old middleware cannot
briefly expose unauthenticated routes on the new AP. It starts the protected AP
and updated services, checks the active profile and interface
address, and checks the login page locally. Do not claim hotspot connectivity
until a real phone/laptop connects. No automatic reboot or hardware voice test
is performed by the script.
If activation fails, use the console/Ethernet rollback command below; do not
repeat radio changes from the lost Wi-Fi session.

Determine the numerical address from the actual profile and active interface:

```sh
nmcli -g ipv4.addresses connection show 'SmartCompanion Demo'
nmcli -g GENERAL.CONNECTION,IP4.ADDRESS device show wlan0
```

Require `SmartCompanion Demo` and `10.77.0.1/24` to agree. The address is explicitly
configured and checked, rather than guessed from NetworkManager's shared default.
Then use **http://10.77.0.1:8000/**. Also use
**http://smart-ai-companion.local:8000/** when mDNS resolves. The existing Pi
hostname must be `smart-ai-companion` for that optional name; the numeric address
works even on phones without mDNS. No wildcard host, DEBUG mode, development
server or disabled security is required.

## Supervised acceptance, reboot and college checklist

With temperature below 50 C, healthy power/throttle/RAM status and the existing
70 C operator cutoff, perform one supervised session:

1. Join SmartAI-Companion from a phone/laptop, sign in, and confirm local static
   assets and live status load. Disable phone cellular data for the demonstration
   and unplug the Pi's Ethernet after keeping local-console recovery available.
2. Wait for READY. Hold the button, wait for LISTENING, ask “What is Smart AI
   Companion?”, finish the last word, then release. Require prompt THINKING,
   complete speech, SPEAKING and READY. Confirm no post-release 10-second/VAD wait.
3. Ask a factual follow-up; confirm conversation context/history. Hold through
   a natural mid-question pause. Test one short accidental tap, a silent press,
   a press while busy and another normal question. No stale/duplicate request,
   overlapping audio, dropped first/last word or stuck microphone is acceptable.
4. Ask a longer public question; listen for multi-second gaps, repetition or
   truncation. Show local document retrieval only from already indexed material.
   Explain that online tools are separate. Skip Hey Companion because no validated
   model is installed. Stop on device/thermal failure; do not repeatedly restart.
5. Verify service state and successful local login, then perform the reboot test:

```sh
sudo systemctl --no-pager status smart-ai-companion-network-mode.service smart-ai-companion.service smart-ai-companion-stt.service smart-ai-companion-voice.service
sudo systemctl reboot
```

After reboot, repeat hotspot join, login and two hold/release questions without
terminal setup. Only then claim boot recovery/physical PTT/hotspot acceptance.
For college: power on → wait READY → join the configured SSID → sign in at the
verified numerical URL → hold, ask, release → show speech/TFT and follow-up →
show indexed local knowledge → explain that processing stays on the Pi.

## One rollback procedure

On local console or wired Ethernet, stop the demo and restore the prior service
overrides/client setup without deleting any database or Wi-Fi profile:

```sh
bash scripts/final_review_demo.sh rollback "$DEMO_COMMIT"
git diff --quiet && git diff --cached --quiet &&
git switch --detach 79a8c65966bec878e0097b124892e1c6c063c975 &&
sudo systemctl restart smart-ai-companion.service
```

This disables the new voice/STT services, restores backed-up units/drop-ins,
deactivates (retains) the demo profile, and asks the existing provisioning system
to restore client/setup mode. Production `.env` and all current conversation
records remain. Run the previously working manual hands-free/Enter command from
that baseline; do not start two microphone owners. Returning to the feature
checkout uses `git switch codex/offline-model-selection`. A database snapshot
exists for recovery but rollback does not overwrite newer conversation records.

## Verification scope

Automated tests cover hold/release retention, short/long presses, shutdown and
reader cleanup, busy-press handling/context reuse, RP1 selection/claimed-line
rejection, resident STT reuse/bounds, authenticated demo access, CSRF, AP-first
boot selection, protected profile setup, verified gateway mismatch rejection,
and service/deployment guards. Existing suites cover the original voice,
streaming, cancellation, retrieval, resource and provisioning behavior. Exact
test counts and commit SHA are reported in the completion message. Pi GPIO,
RF/DHCP/mDNS, real speech, timing and reboot results require the supervised checks.

Final automated result: **193 focused tests, OK; 1,388 full-suite tests, OK
(one existing skip)**. Django system checks, migration consistency, Python AST,
Bash syntax and Git whitespace validation passed on the development host.
