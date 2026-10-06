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
  while (Serial.available() > 0) {
    const char incoming = static_cast<char>(Serial.read());
    if (incoming == '\r') {
      continue;
    }
    if (incoming == '\n') {
      if (serialLineOverflow) {
        Serial.println("ERR: command too long");
      } else {
        serialLine[serialLineLength] = '\0';
        handleCommand(serialLine);
      }
      serialLineLength = 0;
      serialLineOverflow = false;
      continue;
    }

    if (serialLineOverflow) {
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

  Serial.println("Smart AI Companion Display Firmware V1");
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
