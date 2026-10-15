#include <Arduino.h>
#include <TFT_eSPI.h>
#include <string.h>

#include "companion_face.h"
#include "companion_theme.h"
#include "display_config.h"

#define COMPANION_DISPLAY_DEMO_MODE 0

TFT_eSPI tft = TFT_eSPI();
CompanionFace companionFace(tft);

namespace {

char serialLine[DisplayConfig::SERIAL_LINE_BUFFER_SIZE];
size_t serialLineLength = 0;
bool serialLineOverflow = false;
bool serialLineInvalid = false;
bool serialCarriageReturn = false;

char* trimWhitespace(char* text) {
  while (*text == ' ' || *text == '\t') {
    ++text;
  }

  size_t length = strlen(text);
  while (length > 0 &&
         (text[length - 1] == ' ' || text[length - 1] == '\t')) {
    text[--length] = '\0';
  }
  return text;
}

void printHelp() {
  Serial.println("Commands:");
  Serial.println("  STATE BOOTING|SETUP|CONNECTING|READY|LISTENING");
  Serial.println("  STATE THINKING|SPEAKING|OFFLINE|ECO|PROTECTIVE|ERROR");
  Serial.println("  TEXT <short text>");
  Serial.println("  CLEAR TEXT");
  Serial.println("  QUERY <text, up to 80 bytes> | CLEAR QUERY");
  Serial.println("  MODE LOCAL|KNOWLEDGE|WEB | CLEAR MODE");
  Serial.println("  LEVEL <integer 0-100>");
  Serial.println("  HELP");
}

void handleCommand(char* line) {
  char* command = trimWhitespace(line);
  if (command[0] == '\0') {
    return;
  }

  if (strcmp(command, "HELP") == 0) {
    printHelp();
    return;
  }

  if (strcmp(command, "CLEAR TEXT") == 0) {
    companionFace.clearStatusText();
    Serial.print("TEXT: ");
    Serial.println(companionFace.getStatusText());
    return;
  }

  if (strcmp(command, "CLEAR QUERY") == 0) {
    companionFace.clearQuery();
    Serial.println("QUERY: cleared");
    return;
  }
  if (strcmp(command, "CLEAR MODE") == 0) {
    companionFace.setMode(CompanionMode::NONE);
    Serial.println("MODE: cleared");
    return;
  }
  if (strcmp(command, "QUERY") == 0) {
    Serial.println("ERR: query is empty");
    return;
  }
  if (strncmp(command, "QUERY ", 6) == 0) {
    char* text = trimWhitespace(command + 6);
    if (!*text) { Serial.println("ERR: query is empty"); return; }
    if (strlen(text) > DisplayConfig::MAX_QUERY_TEXT_LENGTH)
      Serial.println("WARN: query truncated");
    companionFace.setQuery(text);
    Serial.println("QUERY: OK");
    return;
  }
  if (strncmp(command, "MODE ", 5) == 0) {
    CompanionMode mode;
    if (!CompanionFace::parseModeName(trimWhitespace(command + 5), mode)) {
      Serial.println("ERR: unknown mode");
      return;
    }
    companionFace.setMode(mode);
    Serial.print("MODE: ");
    Serial.println(CompanionFace::modeName(mode));
    return;
  }
  if (strcmp(command, "LEVEL") == 0 || strncmp(command, "LEVEL ", 6) == 0) {
    const char* text = strcmp(command, "LEVEL") == 0 ? "" : trimWhitespace(command + 6);
    if (!*text) { Serial.println("ERR: invalid level"); return; }
    uint16_t level = 0;
    for (const char* digit = text; *digit; ++digit) {
      if (*digit < '0' || *digit > '9') { Serial.println("ERR: invalid level"); return; }
      // Saturate the parser accumulator, never wrap on arbitrarily many digits.
      level = level > 100 ? 101 : level * 10 + (*digit - '0');
    }
    if (level > 100) { Serial.println("ERR: level range 0-100"); return; }
    companionFace.setLevel(static_cast<uint8_t>(level));
    Serial.print("LEVEL: ");
    Serial.println(level);
    return;
  }

  if (strncmp(command, "STATE ", 6) == 0) {
    char* requestedState = trimWhitespace(command + 6);
    CompanionState state;
    if (!CompanionFace::parseStateName(requestedState, state)) {
      Serial.println("ERR: unknown state");
      return;
    }
    companionFace.setState(state);
    Serial.print("STATE: ");
    Serial.println(CompanionFace::stateName(companionFace.getState()));
    return;
  }

  if (strcmp(command, "TEXT") == 0) {
    Serial.println("ERR: text is empty");
    return;
  }
  if (strncmp(command, "TEXT ", 5) == 0) {
    char* requestedText = trimWhitespace(command + 5);
    if (requestedText[0] == '\0') {
      Serial.println("ERR: text is empty");
      return;
    }
    if (strlen(requestedText) > DisplayConfig::MAX_STATUS_TEXT_LENGTH) {
      Serial.println("WARN: text truncated");
    }
    companionFace.setStatusText(requestedText);
    Serial.print("TEXT: ");
    Serial.println(companionFace.getStatusText());
    return;
  }

  Serial.println("ERR: unknown command");
}

void processSerialInput() {
  // Bound work under a sustained serial stream so animation gets loop time.
  size_t consumed = 0;
  while (Serial.available() > 0 && consumed++ < DisplayConfig::SERIAL_BYTES_PER_LOOP) {
    const char incoming = static_cast<char>(Serial.read());
    if (incoming == '\r') {
      if (serialCarriageReturn) serialLineInvalid = true;
      serialCarriageReturn = true;
      continue;
    }
    if (incoming == '\n') {
      if (serialLineOverflow) {
        Serial.println("ERR: command too long");
      } else if (serialLineInvalid) {
        Serial.println("ERR: invalid command bytes");
      } else {
        serialLine[serialLineLength] = '\0';
        handleCommand(serialLine);
      }
      serialLineLength = 0;
      serialLineOverflow = false;
      serialLineInvalid = false;
      serialCarriageReturn = false;
      continue;
    }

    if (serialCarriageReturn) serialLineInvalid = true;  // Only terminal CRLF is valid.

    if (serialLineOverflow || serialLineInvalid) {
      continue;
    }
    const uint8_t byte = static_cast<uint8_t>(incoming);
    if ((byte < 32 && incoming != '\t') || byte > 126) {
      serialLineInvalid = true;  // NUL must not turn a malformed line into a valid prefix.
      continue;
    }
    if (serialLineLength + 1 >= DisplayConfig::SERIAL_LINE_BUFFER_SIZE) {
      serialLineOverflow = true;
      continue;
    }
    serialLine[serialLineLength++] = incoming;
  }
}

#if COMPANION_DISPLAY_DEMO_MODE
const CompanionState DEMO_STATES[] = {
    CompanionState::SETUP,      CompanionState::CONNECTING,
    CompanionState::READY,      CompanionState::LISTENING,
    CompanionState::THINKING,   CompanionState::SPEAKING,
    CompanionState::OFFLINE,    CompanionState::ECO,
    CompanionState::PROTECTIVE, CompanionState::ERROR,
};
constexpr size_t DEMO_STATE_COUNT =
    sizeof(DEMO_STATES) / sizeof(DEMO_STATES[0]);
size_t demoStateIndex = 0;
uint32_t lastDemoChangeAt = 0;

void updateDemo() {
  const uint32_t now = millis();
  if (now - lastDemoChangeAt < CompanionTheme::DEMO_STATE_INTERVAL_MS) {
    return;
  }
  lastDemoChangeAt = now;
  companionFace.setState(DEMO_STATES[demoStateIndex]);
  Serial.print("DEMO STATE: ");
  Serial.println(CompanionFace::stateName(DEMO_STATES[demoStateIndex]));
  demoStateIndex = (demoStateIndex + 1) % DEMO_STATE_COUNT;
}
#endif

}  // namespace

void setup() {
  Serial.begin(115200);
  companionFace.begin();

  Serial.println("Smart AI Companion Display Firmware V2.1");
  Serial.print("Display: ");
  Serial.print(tft.width());
  Serial.print(" x ");
  Serial.println(tft.height());
  Serial.println("State: BOOTING");
}

void loop() {
  processSerialInput();
  companionFace.update();

#if COMPANION_DISPLAY_DEMO_MODE
  updateDemo();
#endif
}
