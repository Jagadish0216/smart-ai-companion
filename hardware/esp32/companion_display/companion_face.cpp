#include "companion_face.h"

#include <ctype.h>
#include <string.h>

#include "companion_theme.h"

namespace {

bool equalsIgnoreCase(const char* left, const char* right) {
  if (left == nullptr || right == nullptr) {
    return false;
  }
  while (*left != '\0' && *right != '\0') {
    if (toupper(static_cast<unsigned char>(*left)) !=
        toupper(static_cast<unsigned char>(*right))) {
      return false;
    }
    ++left;
    ++right;
  }
  return *left == '\0' && *right == '\0';
}

}  // namespace

CompanionFace::CompanionFace(TFT_eSPI& display)
    : tft_(display),
      state_(CompanionState::BOOTING),
      statusText_{0},
      stateEnteredAt_(0),
      lastAnimationAt_(0),
      nextBlinkAt_(0),
      animationStep_(0),
      blinkIntervalIndex_(0),
      blinkActive_(false) {}

void CompanionFace::begin() {
  tft_.init();
  tft_.setRotation(DisplayConfig::DISPLAY_ROTATION);
  tft_.fillScreen(CompanionTheme::BACKGROUND);
  drawBaseLayout();
  setState(CompanionState::BOOTING);
}

void CompanionFace::update() {
  const uint32_t now = millis();
  switch (state_) {
    case CompanionState::BOOTING:
      updateBooting(now);
      break;
    case CompanionState::CONNECTING:
      updateConnecting(now);
      break;
    case CompanionState::READY:
      updateReady(now);
      break;
    case CompanionState::LISTENING:
      updateListening(now);
      break;
    case CompanionState::THINKING:
      updateThinking(now);
      break;
    case CompanionState::SPEAKING:
      updateSpeaking(now);
      break;
    case CompanionState::SETUP:
    case CompanionState::OFFLINE:
    case CompanionState::ECO:
    case CompanionState::PROTECTIVE:
    case CompanionState::ERROR:
      break;
  }
}

void CompanionFace::setState(CompanionState state) {
  state_ = state;
  copyStatusText(defaultStatusText(state_));
  resetAnimation(millis());
  renderStateFace();
  drawStatusPanel();
  renderActivityIndicator();
}

void CompanionFace::setStatusText(const char* text) {
  if (text == nullptr || text[0] == '\0') {
    clearStatusText();
    return;
  }
  copyStatusText(text);
  drawStatusPanel();
  renderActivityIndicator();
}

void CompanionFace::clearStatusText() {
  copyStatusText(defaultStatusText(state_));
  drawStatusPanel();
  renderActivityIndicator();
}

CompanionState CompanionFace::getState() const { return state_; }

const char* CompanionFace::getStatusText() const { return statusText_; }

const char* CompanionFace::stateName(CompanionState state) {
  switch (state) {
    case CompanionState::BOOTING:
      return "BOOTING";
    case CompanionState::SETUP:
      return "SETUP";
    case CompanionState::CONNECTING:
      return "CONNECTING";
    case CompanionState::READY:
      return "READY";
    case CompanionState::LISTENING:
      return "LISTENING";
    case CompanionState::THINKING:
      return "THINKING";
    case CompanionState::SPEAKING:
      return "SPEAKING";
    case CompanionState::OFFLINE:
      return "OFFLINE";
    case CompanionState::ECO:
      return "ECO";
    case CompanionState::PROTECTIVE:
      return "PROTECTIVE";
    case CompanionState::ERROR:
      return "ERROR";
  }
  return "ERROR";
}

bool CompanionFace::parseStateName(const char* name, CompanionState& state) {
  const CompanionState states[] = {
      CompanionState::BOOTING,   CompanionState::SETUP,
      CompanionState::CONNECTING, CompanionState::READY,
      CompanionState::LISTENING, CompanionState::THINKING,
      CompanionState::SPEAKING,  CompanionState::OFFLINE,
      CompanionState::ECO,       CompanionState::PROTECTIVE,
      CompanionState::ERROR,
  };
  for (const CompanionState candidate : states) {
    if (equalsIgnoreCase(name, stateName(candidate))) {
      state = candidate;
      return true;
    }
  }
  return false;
}

void CompanionFace::drawBaseLayout() {
  tft_.fillRect(0, DisplayConfig::STATUS_TOP, DisplayConfig::SCREEN_WIDTH,
                DisplayConfig::STATUS_HEIGHT, CompanionTheme::PANEL);
  tft_.fillRect(0, DisplayConfig::DIVIDER_Y, DisplayConfig::SCREEN_WIDTH,
                DisplayConfig::DIVIDER_HEIGHT, CompanionTheme::MUTED_GREY);
}

void CompanionFace::renderStateFace() {
  clearEyes();
  clearMouth();

  switch (state_) {
    case CompanionState::BOOTING:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH, 4,
                  CompanionTheme::PRIMARY_CYAN, 0, 0, false);
      break;
    case CompanionState::SETUP:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                  CompanionTheme::NORMAL_EYE_HEIGHT,
                  CompanionTheme::PRIMARY_CYAN, 0, 0, true);
      drawSmile();
      break;
    case CompanionState::CONNECTING:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                  CompanionTheme::NORMAL_EYE_HEIGHT,
                  CompanionTheme::PRIMARY_CYAN, 0, 0, true);
      break;
    case CompanionState::READY:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                  CompanionTheme::NORMAL_EYE_HEIGHT,
                  CompanionTheme::PRIMARY_CYAN, 0, 0, true);
      drawSmile();
      break;
    case CompanionState::LISTENING:
      drawEyePair(CompanionTheme::ATTENTIVE_EYE_WIDTH,
                  CompanionTheme::ATTENTIVE_EYE_HEIGHT,
                  CompanionTheme::PRIMARY_CYAN, 0, 0, true);
      break;
    case CompanionState::THINKING:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH, 46,
                  CompanionTheme::PRIMARY_CYAN, -8, 0, true);
      break;
    case CompanionState::SPEAKING:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                  CompanionTheme::NORMAL_EYE_HEIGHT,
                  CompanionTheme::PRIMARY_CYAN, 0, 0, true);
      drawSpeakingMouth(false);
      break;
    case CompanionState::OFFLINE:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                  CompanionTheme::SLEEPY_EYE_HEIGHT,
                  CompanionTheme::DIM_CYAN, 0, 0, false);
      break;
    case CompanionState::ECO:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                  CompanionTheme::RELAXED_EYE_HEIGHT,
                  CompanionTheme::DIM_CYAN, 0, 0, true);
      drawSmile();
      break;
    case CompanionState::PROTECTIVE:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH, 38,
                  CompanionTheme::PRIMARY_CYAN, 0, 3, true);
      drawConcernedBrows(false);
      drawConcernedMouth();
      break;
    case CompanionState::ERROR:
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH, 34,
                  CompanionTheme::PRIMARY_CYAN, 0, 4, true);
      drawConcernedBrows(true);
      drawConcernedMouth();
      break;
  }
}

void CompanionFace::renderActivityIndicator() {
  switch (state_) {
    case CompanionState::CONNECTING:
      drawConnectingIndicator(animationStep_);
      break;
    case CompanionState::LISTENING:
      drawListeningIndicator(animationStep_);
      break;
    case CompanionState::THINKING:
      drawThinkingIndicator(animationStep_);
      break;
    default:
      clearActivityIndicator();
      break;
  }
}

void CompanionFace::drawStatusPanel() {
  tft_.fillRect(0, DisplayConfig::STATUS_TOP, DisplayConfig::SCREEN_WIDTH,
                DisplayConfig::STATUS_HEIGHT, CompanionTheme::PANEL);
  tft_.fillCircle(DisplayConfig::STATUS_DOT_X, DisplayConfig::STATUS_DOT_Y,
                  CompanionTheme::STATUS_DOT_RADIUS, stateAccentColor());

  uint8_t font = 4;
  if (tft_.textWidth(statusText_, font) > 340) {
    font = 2;
  }
  tft_.setTextDatum(MC_DATUM);
  tft_.setTextColor(CompanionTheme::SOFT_WHITE, CompanionTheme::PANEL);
  tft_.drawString(statusText_, DisplayConfig::STATUS_CENTER_X,
                  DisplayConfig::STATUS_PRIMARY_Y, font);

  tft_.setTextColor(CompanionTheme::MUTED_GREY, CompanionTheme::PANEL);
  tft_.drawString(stateName(state_), DisplayConfig::STATUS_CENTER_X,
                  DisplayConfig::STATUS_STATE_Y, 2);
}

void CompanionFace::clearEyes() {
  tft_.fillRect(DisplayConfig::LEFT_EYE_DIRTY_X, DisplayConfig::EYE_DIRTY_Y,
                DisplayConfig::EYE_DIRTY_WIDTH,
                DisplayConfig::EYE_DIRTY_HEIGHT, CompanionTheme::BACKGROUND);
  tft_.fillRect(DisplayConfig::RIGHT_EYE_DIRTY_X, DisplayConfig::EYE_DIRTY_Y,
                DisplayConfig::EYE_DIRTY_WIDTH,
                DisplayConfig::EYE_DIRTY_HEIGHT, CompanionTheme::BACKGROUND);
}

void CompanionFace::clearMouth() {
  tft_.fillRect(DisplayConfig::MOUTH_DIRTY_X, DisplayConfig::MOUTH_DIRTY_Y,
                DisplayConfig::MOUTH_DIRTY_WIDTH,
                DisplayConfig::MOUTH_DIRTY_HEIGHT, CompanionTheme::BACKGROUND);
}

void CompanionFace::clearActivityIndicator() {
  tft_.fillRect(DisplayConfig::ACTIVITY_DIRTY_X,
                DisplayConfig::ACTIVITY_DIRTY_Y,
                DisplayConfig::ACTIVITY_DIRTY_SIZE,
                DisplayConfig::ACTIVITY_DIRTY_SIZE, CompanionTheme::PANEL);
}

void CompanionFace::drawEyePair(int16_t width, int16_t height,
                                uint16_t color, int16_t pupilOffsetX,
                                int16_t pupilOffsetY, bool drawPupils) {
  clearEyes();
  drawEye(DisplayConfig::LEFT_EYE_CENTER_X, DisplayConfig::EYE_CENTER_Y,
          width, height, color, pupilOffsetX, pupilOffsetY, drawPupils);
  drawEye(DisplayConfig::RIGHT_EYE_CENTER_X, DisplayConfig::EYE_CENTER_Y,
          width, height, color, pupilOffsetX, pupilOffsetY, drawPupils);
}

void CompanionFace::drawEye(int16_t centerX, int16_t centerY, int16_t width,
                            int16_t height, uint16_t color,
                            int16_t pupilOffsetX, int16_t pupilOffsetY,
                            bool drawPupil) {
  int16_t radius = CompanionTheme::EYE_CORNER_RADIUS;
  if (radius > height / 2) {
    radius = height / 2;
  }
  tft_.fillRoundRect(centerX - width / 2, centerY - height / 2, width,
                     height, radius, color);
  if (!drawPupil || height < CompanionTheme::PUPIL_RADIUS * 3) {
    return;
  }

  const int16_t pupilX = centerX + pupilOffsetX;
  const int16_t pupilY = centerY + pupilOffsetY;
  tft_.fillCircle(pupilX, pupilY, CompanionTheme::PUPIL_RADIUS,
                  CompanionTheme::BACKGROUND);
  tft_.fillCircle(pupilX - 2, pupilY - 2, CompanionTheme::HIGHLIGHT_RADIUS,
                  CompanionTheme::SOFT_WHITE);
}

void CompanionFace::drawSmile() {
  clearMouth();
  const int16_t x = DisplayConfig::MOUTH_CENTER_X;
  const int16_t y = DisplayConfig::MOUTH_CENTER_Y;
  tft_.drawLine(x - 22, y - 3, x - 10, y + 5,
                CompanionTheme::PRIMARY_CYAN);
  tft_.drawFastHLine(x - 10, y + 5, 20, CompanionTheme::PRIMARY_CYAN);
  tft_.drawLine(x + 10, y + 5, x + 22, y - 3,
                CompanionTheme::PRIMARY_CYAN);
}

void CompanionFace::drawConcernedMouth() {
  clearMouth();
  const int16_t x = DisplayConfig::MOUTH_CENTER_X;
  const int16_t y = DisplayConfig::MOUTH_CENTER_Y + 5;
  tft_.drawLine(x - 20, y + 4, x - 9, y - 3,
                CompanionTheme::PRIMARY_CYAN);
  tft_.drawFastHLine(x - 9, y - 3, 18, CompanionTheme::PRIMARY_CYAN);
  tft_.drawLine(x + 9, y - 3, x + 20, y + 4,
                CompanionTheme::PRIMARY_CYAN);
}

void CompanionFace::drawSpeakingMouth(bool open) {
  clearMouth();
  const int16_t x = DisplayConfig::MOUTH_CENTER_X;
  const int16_t y = DisplayConfig::MOUTH_CENTER_Y;
  if (open) {
    tft_.fillRoundRect(x - 24, y - 10, 48, 22, 10,
                       CompanionTheme::PRIMARY_CYAN);
    tft_.fillRoundRect(x - 17, y - 5, 34, 12, 6,
                       CompanionTheme::BACKGROUND);
  } else {
    tft_.fillRoundRect(x - 22, y - 2, 44, 5, 2,
                       CompanionTheme::PRIMARY_CYAN);
  }
}

void CompanionFace::drawConcernedBrows(bool errorStyle) {
  const int16_t y = DisplayConfig::EYE_CENTER_Y - 39;
  const int16_t drop = errorStyle ? 8 : 5;
  for (int16_t offset = 0; offset < 3; ++offset) {
    tft_.drawLine(DisplayConfig::LEFT_EYE_CENTER_X - 35, y + offset,
                  DisplayConfig::LEFT_EYE_CENTER_X + 35, y + drop + offset,
                  CompanionTheme::PRIMARY_CYAN);
    tft_.drawLine(DisplayConfig::RIGHT_EYE_CENTER_X - 35,
                  y + drop + offset,
                  DisplayConfig::RIGHT_EYE_CENTER_X + 35, y + offset,
                  CompanionTheme::PRIMARY_CYAN);
  }
}

void CompanionFace::drawConnectingIndicator(uint8_t step) {
  clearActivityIndicator();
  const int16_t startX = DisplayConfig::ACTIVITY_INDICATOR_X - 12;
  for (uint8_t index = 0; index < 3; ++index) {
    const uint16_t color =
        index <= (step % 3) ? CompanionTheme::PRIMARY_CYAN
                            : CompanionTheme::MUTED_GREY;
    tft_.fillCircle(startX + static_cast<int16_t>(index) * 12,
                    DisplayConfig::ACTIVITY_INDICATOR_Y, 3, color);
  }
}

void CompanionFace::drawThinkingIndicator(uint8_t step) {
  clearActivityIndicator();
  const int16_t startX = DisplayConfig::ACTIVITY_INDICATOR_X - 12;
  for (uint8_t index = 0; index < 3; ++index) {
    const int16_t lift = index == (step % 3) ? 4 : 0;
    tft_.fillCircle(startX + static_cast<int16_t>(index) * 12,
                    DisplayConfig::ACTIVITY_INDICATOR_Y - lift, 3,
                    CompanionTheme::PRIMARY_CYAN);
  }
}

void CompanionFace::drawListeningIndicator(uint8_t step) {
  clearActivityIndicator();
  const uint8_t radii[] = {4, 7, 10, 7};
  const uint8_t radius = radii[step % 4];
  tft_.drawCircle(DisplayConfig::ACTIVITY_INDICATOR_X,
                  DisplayConfig::ACTIVITY_INDICATOR_Y, radius,
                  CompanionTheme::PRIMARY_CYAN);
  tft_.fillCircle(DisplayConfig::ACTIVITY_INDICATOR_X,
                  DisplayConfig::ACTIVITY_INDICATOR_Y, 2,
                  CompanionTheme::PRIMARY_CYAN);
}

void CompanionFace::updateBooting(uint32_t now) {
  const uint32_t elapsed = now - stateEnteredAt_;
  uint8_t step = static_cast<uint8_t>(elapsed /
                                      CompanionTheme::BOOT_FRAME_INTERVAL_MS);
  if (step >= CompanionTheme::BOOT_FRAME_COUNT) {
    step = CompanionTheme::BOOT_FRAME_COUNT - 1;
  }
  if (step == animationStep_) {
    return;
  }
  animationStep_ = step;
  const int16_t height = 4 +
      ((CompanionTheme::NORMAL_EYE_HEIGHT - 4) * step /
       (CompanionTheme::BOOT_FRAME_COUNT - 1));
  drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH, height,
              CompanionTheme::PRIMARY_CYAN, 0, 0,
              step == CompanionTheme::BOOT_FRAME_COUNT - 1);
}

void CompanionFace::updateReady(uint32_t now) {
  if (blinkActive_) {
    if (now - lastAnimationAt_ >= CompanionTheme::BLINK_DURATION_MS) {
      blinkActive_ = false;
      lastAnimationAt_ = now;
      drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                  CompanionTheme::NORMAL_EYE_HEIGHT,
                  CompanionTheme::PRIMARY_CYAN, 0, 0, true);
      nextBlinkAt_ = now + CompanionTheme::READY_BLINK_INTERVALS_MS[
                               blinkIntervalIndex_ %
                               CompanionTheme::READY_BLINK_INTERVAL_COUNT];
      ++blinkIntervalIndex_;
    }
    return;
  }

  if (timeReached(now, nextBlinkAt_)) {
    blinkActive_ = true;
    lastAnimationAt_ = now;
    drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH, 4,
                CompanionTheme::PRIMARY_CYAN, 0, 0, false);
    return;
  }

  if (now - lastAnimationAt_ >=
      CompanionTheme::READY_EYE_MOVEMENT_INTERVAL_MS) {
    const int16_t offsets[] = {-3, 0, 3, 0};
    animationStep_ = static_cast<uint8_t>((animationStep_ + 1) % 4);
    lastAnimationAt_ = now;
    drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH,
                CompanionTheme::NORMAL_EYE_HEIGHT,
                CompanionTheme::PRIMARY_CYAN, offsets[animationStep_], 0,
                true);
  }
}

void CompanionFace::updateConnecting(uint32_t now) {
  if (now - lastAnimationAt_ <
      CompanionTheme::CONNECTING_STEP_INTERVAL_MS) {
    return;
  }
  lastAnimationAt_ = now;
  animationStep_ = static_cast<uint8_t>((animationStep_ + 1) % 3);
  drawConnectingIndicator(animationStep_);
}

void CompanionFace::updateListening(uint32_t now) {
  if (now - lastAnimationAt_ <
      CompanionTheme::LISTENING_PULSE_INTERVAL_MS) {
    return;
  }
  lastAnimationAt_ = now;
  animationStep_ = static_cast<uint8_t>((animationStep_ + 1) % 4);
  drawListeningIndicator(animationStep_);
}

void CompanionFace::updateThinking(uint32_t now) {
  if (now - lastAnimationAt_ < CompanionTheme::THINKING_STEP_INTERVAL_MS) {
    return;
  }
  const int16_t offsets[] = {-8, 0, 8, 0};
  lastAnimationAt_ = now;
  animationStep_ = static_cast<uint8_t>((animationStep_ + 1) % 4);
  drawEyePair(CompanionTheme::NORMAL_EYE_WIDTH, 46,
              CompanionTheme::PRIMARY_CYAN, offsets[animationStep_], 0,
              true);
  drawThinkingIndicator(animationStep_);
}

void CompanionFace::updateSpeaking(uint32_t now) {
  if (now - lastAnimationAt_ < CompanionTheme::SPEAKING_MOUTH_INTERVAL_MS) {
    return;
  }
  lastAnimationAt_ = now;
  animationStep_ = static_cast<uint8_t>((animationStep_ + 1) % 2);
  drawSpeakingMouth(animationStep_ != 0);
}

uint16_t CompanionFace::stateAccentColor() const {
  switch (state_) {
    case CompanionState::READY:
    case CompanionState::ECO:
      return CompanionTheme::SUCCESS_ACCENT;
    case CompanionState::OFFLINE:
    case CompanionState::PROTECTIVE:
      return CompanionTheme::CAUTION_ACCENT;
    case CompanionState::ERROR:
      return CompanionTheme::ERROR_ACCENT;
    case CompanionState::BOOTING:
      return CompanionTheme::MUTED_GREY;
    case CompanionState::SETUP:
    case CompanionState::CONNECTING:
    case CompanionState::LISTENING:
    case CompanionState::THINKING:
    case CompanionState::SPEAKING:
      return CompanionTheme::PRIMARY_CYAN;
  }
  return CompanionTheme::MUTED_GREY;
}

const char* CompanionFace::defaultStatusText(CompanionState state) const {
  switch (state) {
    case CompanionState::BOOTING:
      return "Starting...";
    case CompanionState::SETUP:
      return "Setup Wi-Fi";
    case CompanionState::CONNECTING:
      return "Connecting...";
    case CompanionState::READY:
      return "Ready";
    case CompanionState::LISTENING:
      return "Listening...";
    case CompanionState::THINKING:
      return "Thinking...";
    case CompanionState::SPEAKING:
      return "Speaking...";
    case CompanionState::OFFLINE:
      return "Offline mode";
    case CompanionState::ECO:
      return "Eco mode";
    case CompanionState::PROTECTIVE:
      return "Protective mode";
    case CompanionState::ERROR:
      return "Error";
  }
  return "Error";
}

void CompanionFace::resetAnimation(uint32_t now) {
  stateEnteredAt_ = now;
  lastAnimationAt_ = now;
  animationStep_ = 0;
  blinkActive_ = false;
  nextBlinkAt_ = now + CompanionTheme::READY_BLINK_INTERVALS_MS[
                           blinkIntervalIndex_ %
                           CompanionTheme::READY_BLINK_INTERVAL_COUNT];
}

void CompanionFace::copyStatusText(const char* text) {
  if (text == nullptr) {
    statusText_[0] = '\0';
    return;
  }
  strncpy(statusText_, text, DisplayConfig::MAX_STATUS_TEXT_LENGTH);
  statusText_[DisplayConfig::MAX_STATUS_TEXT_LENGTH] = '\0';
}

bool CompanionFace::timeReached(uint32_t now, uint32_t target) {
  return static_cast<int32_t>(now - target) >= 0;
}
