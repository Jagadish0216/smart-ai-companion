#include "companion_face.h"
#include <ctype.h>
#include <string.h>
#include <initializer_list>
#include "companion_theme.h"

namespace {
bool equalsIgnoreCase(const char* left, const char* right) {
  if (!left || !right) return false;
  while (*left && *right) {
    if (toupper(static_cast<unsigned char>(*left++)) !=
        toupper(static_cast<unsigned char>(*right++))) return false;
  }
  return *left == '\0' && *right == '\0';
}
// LEVEL smoothing is unchanged from V2: quarter-distance, 1/16 fixed point.
int16_t ease(int16_t current, int16_t target) {
  const int16_t delta = target - current;
  return current + (delta > 0 ? (delta + 3) / 4 : (delta - 3) / 4);
}
const int8_t GAZE_X[] = {0, 5, 0, -6, 0};
const int8_t GAZE_Y[] = {0, -4, 3, -2, 0};
const uint8_t MOUTH_SEQUENCE[] = {0, 18, 36, 55, 34, 12, 0, 25, 46, 22, 0, 0};
}  // namespace

CompanionFace::CompanionFace(TFT_eSPI& display)
    : tft_(display), eyeSprite_(&display), textSprite_(&display) {}

void CompanionFace::begin() {
  tft_.init();
  tft_.setRotation(DisplayConfig::DISPLAY_ROTATION);
  tft_.fillScreen(CompanionTheme::BACKGROUND);  // Initialization only.
  tft_.fillRect(0, DisplayConfig::BAND_Y, DisplayConfig::SCREEN_WIDTH,
                DisplayConfig::BAND_HEIGHT, CompanionTheme::PANEL);
  tft_.drawFastHLine(0, DisplayConfig::BAND_Y, DisplayConfig::SCREEN_WIDTH,
                    CompanionTheme::SEPARATOR);
  if (!spritesAttempted_) {
    spritesAttempted_ = true;
    eyeSprite_.setColorDepth(16);
    textSprite_.setColorDepth(16);
    eyeSpriteReady_ = eyeSprite_.createSprite(DisplayConfig::EYE_DIRTY_WIDTH,
                                             DisplayConfig::EYE_DIRTY_HEIGHT) != nullptr;
    textSpriteReady_ = textSprite_.createSprite(DisplayConfig::CONTENT_WIDTH,
                                               DisplayConfig::CONTENT_HEIGHT) != nullptr;
    if (!eyeSpriteReady_) Serial.println("Display: eye sprite unavailable; direct fallback");
    if (!textSpriteReady_) Serial.println("Display: text sprite unavailable; direct fallback");
  }
  setState(CompanionState::BOOTING);
  update();
}

void CompanionFace::setState(CompanionState state) {
  state_ = state;
  if (state != CompanionState::THINKING && state != CompanionState::SPEAKING) {
    query_[0] = '\0';
    mode_ = CompanionMode::NONE;
  }
  hasLevel_ = false;
  level_ = 0;
  smoothLevelQ_ = 0;
  copyText(statusText_, sizeof(statusText_), defaultStatusText(state));
  customText_ = false;
  stateEnteredAt_ = millis();
  lastLevelFrameAt_ = stateEnteredAt_ - CompanionTheme::ACTIVE_FRAME_MS;
  nextBlinkAt_ = stateEnteredAt_ + CompanionTheme::READY_BLINK_INTERVALS_MS[0] + relaxedBlinkExtra();
  blinking_ = secondBlinkPending_ = false;
  blinkCount_ = 0;
  textDirty_ = forceFrame_ = true;
  // Preserve component caches and gaze. No visible clear occurs in setState.
  // The next update replaces only changed eyes/mouth/header/text.
}
void CompanionFace::setStatusText(const char* text) {
  if (!text || !*text) { clearStatusText(); return; }
  char next[sizeof(statusText_)] = {};
  copyText(next, sizeof(next), text);
  if (strcmp(next, statusText_) != 0 || !customText_) textDirty_ = true;
  memcpy(statusText_, next, sizeof(statusText_));
  customText_ = true;
}
void CompanionFace::clearStatusText() {
  copyText(statusText_, sizeof(statusText_), defaultStatusText(state_));
  customText_ = false;
  textDirty_ = true;
}
void CompanionFace::setQuery(const char* text) {
  copyText(query_, sizeof(query_), text);
  if (state_ == CompanionState::THINKING && !customText_) textDirty_ = true;
}
void CompanionFace::clearQuery() {
  query_[0] = '\0';
  if (state_ == CompanionState::THINKING && !customText_) textDirty_ = true;
}
void CompanionFace::setMode(CompanionMode mode) { mode_ = mode; }
void CompanionFace::setLevel(uint8_t level) {
  level_ = level > 100 ? 100 : level;
  levelUpdatedAt_ = millis();
  hasLevel_ = true;
}

void CompanionFace::update() {
  const uint32_t now = millis();
  uint32_t interval = CompanionTheme::ACTIVE_FRAME_MS;
  if (state_ == CompanionState::READY || state_ == CompanionState::OFFLINE ||
      state_ == CompanionState::SETUP) interval = CompanionTheme::IDLE_FRAME_MS;
  if (state_ == CompanionState::ECO) interval = CompanionTheme::ECO_FRAME_MS;
  if (blinking_) interval = CompanionTheme::BLINK_FRAME_MS;
  if (forceFrame_ || now - lastFrameAt_ >= interval) {
    forceFrame_ = false;
    lastFrameAt_ = now;
    renderExpression(now);
  }
  renderIndicators(now);
  if (textDirty_) {
    drawInteractionText();
    textDirty_ = false;
  }
  if (state_ == CompanionState::LISTENING) renderMeter(smoothLevelQ_ / 16);
}

uint32_t CompanionFace::relaxedBlinkExtra() const {
  return state_ == CompanionState::ECO ? 2400 : state_ == CompanionState::OFFLINE ? 1600 : 0;
}
uint8_t CompanionFace::blinkCoverage(uint32_t now) {
  if (!blinking_ && timeReached(now, nextBlinkAt_)) {
    blinking_ = true;
    blinkStartedAt_ = now;
  }
  if (!blinking_) return 0;
  const uint8_t coverage[] = {0, 35, 80, 96, 80, 35, 0};
  const uint32_t frame = (now - blinkStartedAt_) / CompanionTheme::BLINK_FRAME_MS;
  if (frame < sizeof(coverage)) return coverage[frame];
  blinking_ = false;
  if (secondBlinkPending_) {
    secondBlinkPending_ = false;
    nextBlinkAt_ = now + CompanionTheme::READY_BLINK_INTERVALS_MS[
        blinkCount_ % CompanionTheme::READY_BLINK_INTERVAL_COUNT] + relaxedBlinkExtra();
  } else {
    ++blinkCount_;
    secondBlinkPending_ = blinkCount_ % 5 == 0;
    nextBlinkAt_ = now + (secondBlinkPending_ ? 180 :
        CompanionTheme::READY_BLINK_INTERVALS_MS[
            blinkCount_ % CompanionTheme::READY_BLINK_INTERVAL_COUNT] + relaxedBlinkExtra());
  }
  return 0;
}

void CompanionFace::renderExpression(uint32_t now) {
  const uint32_t elapsed = now - stateEnteredAt_;
  const bool fresh = hasLevel_ && now - levelUpdatedAt_ < DisplayConfig::LEVEL_STALE_MS;
  uint8_t targetLevel = 0;
  if (fresh) targetLevel = level_;
  else if (state_ == CompanionState::SPEAKING)
    targetLevel = MOUTH_SEQUENCE[(elapsed / 200) % sizeof(MOUTH_SEQUENCE)];
  else if (state_ == CompanionState::LISTENING) {
    const uint8_t pulse[] = {8, 12, 18, 22, 18, 12};
    targetLevel = pulse[(elapsed / 300) % sizeof(pulse)];
  }
  // Brief faster eyelid frames must not accelerate the verified LEVEL filter.
  if (now - lastLevelFrameAt_ >= CompanionTheme::ACTIVE_FRAME_MS) {
    lastLevelFrameAt_ = now;
    smoothLevelQ_ = ease(smoothLevelQ_, targetLevel * 16);
  }
  const uint8_t smoothLevel = smoothLevelQ_ / 16;
  uint16_t color = CompanionTheme::PRIMARY_CYAN;
  int16_t targetX = 0, targetY = 0, upper = 0, lower = 0;
  bool allowBlink = true;
  switch (state_) {
    case CompanionState::BOOTING: {
      allowBlink = false;
      const uint32_t p = elapsed > 900 ? 900 : elapsed;
      const uint32_t opened = p * p * (2700 - 2 * p) / 810000;
      upper = (CompanionTheme::NORMAL_EYE_HEIGHT - 2) * (900 - opened) / 900;
      if (elapsed >= 900) drawSmile();
      else if (mouthLevel_ != 6) { clearMouth(); mouthLevel_ = 6; }
      break;
    }
    case CompanionState::READY:
    case CompanionState::OFFLINE:
    case CompanionState::ECO: {
      const uint32_t period = state_ == CompanionState::ECO ? 7200 :
                              state_ == CompanionState::OFFLINE ? 6200 :
                              CompanionTheme::READY_GAZE_INTERVAL_MS;
      const uint8_t step = (elapsed / period) % 5;
      targetX = GAZE_X[step]; targetY = GAZE_Y[step];
      if (state_ != CompanionState::READY) color = CompanionTheme::DIM_CYAN;
      if (state_ == CompanionState::ECO) { upper = 10; lower = 2; }
      if (state_ == CompanionState::OFFLINE) upper = 4;
      drawSmile();
      break;
    }
    case CompanionState::SETUP:
      upper = 2; drawSmile(); break;
    case CompanionState::CONNECTING:
      targetY = -3; renderMouth(0); break;
    case CompanionState::LISTENING:
      upper = 4 - smoothLevel / 35;
      targetY = -2 - smoothLevel / 35;
      renderMouth(0);
      break;
    case CompanionState::THINKING: {
      const int8_t x[] = {0, 6, 6, 0, -6, -6, 0};
      const int8_t y[] = {-2, -5, -5, -2, -5, -5, -2};
      const uint8_t step = (elapsed / 500) % 7;
      targetX = x[step]; targetY = y[step];
      upper = 6;
      renderMouth(0);
      break;
    }
    case CompanionState::SPEAKING: {
      const int8_t x[] = {0, 2, 0, -2, 0};
      const int8_t y[] = {-1, 0, 1, 0, 0};
      const uint8_t step = (elapsed / 4500) % 5;
      targetX = x[step]; targetY = y[step];
      renderMouth(smoothLevel < 12 ? 0 : smoothLevel < 32 ? 1 : smoothLevel < 62 ? 2 : 3);
      break;
    }
    case CompanionState::PROTECTIVE:
      allowBlink = false; upper = 18; lower = 4; targetY = 2;
      drawSmile(true);
      break;
    case CompanionState::ERROR:
      allowBlink = false; upper = 22; lower = 6; targetY = 3;
      drawSmile(true);
      break;
  }
  gazeXQ_ = ease(gazeXQ_, targetX * 16);
  gazeYQ_ = ease(gazeYQ_, targetY * 16);
  if (allowBlink) {
    const uint8_t coverage = blinkCoverage(now);
    const int16_t visible = CompanionTheme::NORMAL_EYE_HEIGHT - upper - lower;
    // The shape never compresses: upper/lower masks cover 78% / 22% of the blink.
    upper += visible * coverage * 78 / 10000;
    lower += visible * coverage * 22 / 10000;
  }
  renderEyes(color, gazeXQ_ / 16, gazeYQ_ / 16, upper, lower);
}

void CompanionFace::composeEye(TFT_eSPI& canvas, uint16_t color, int16_t x, int16_t y,
                                int16_t upper, int16_t lower) {
  canvas.fillRect(0, 0, DisplayConfig::EYE_DIRTY_WIDTH, DisplayConfig::EYE_DIRTY_HEIGHT,
                   CompanionTheme::BACKGROUND);
  const int16_t cx = DisplayConfig::EYE_DIRTY_WIDTH / 2;
  const int16_t cy = DisplayConfig::EYE_CENTER_Y - DisplayConfig::EYE_DIRTY_Y;
  const int16_t width = CompanionTheme::NORMAL_EYE_WIDTH;
  const int16_t height = CompanionTheme::NORMAL_EYE_HEIGHT;
  const int16_t left = cx - width / 2, top = cy - height / 2;
  canvas.fillRoundRect(left, top, width, height, CompanionTheme::EYE_CORNER_RADIUS, color);
  canvas.fillCircle(cx + x, cy + y, CompanionTheme::PUPIL_RADIUS, CompanionTheme::BACKGROUND);
  canvas.fillCircle(cx + x - 2, cy + y - 2, 2, CompanionTheme::SOFT_WHITE);
  if (upper > 0) canvas.fillRect(left, top, width, upper, CompanionTheme::BACKGROUND);
  if (lower > 0) canvas.fillRect(left, top + height - lower, width, lower, CompanionTheme::BACKGROUND);
}
void CompanionFace::renderEyes(uint16_t color, int16_t x, int16_t y, int16_t upper, int16_t lower) {
  if (eyeValid_ && color == eyeColor_ && x == pupilX_ && y == pupilY_ &&
      upper == upperLid_ && lower == lowerLid_) return;
  // One sprite is composed once and reused for both symmetric eyes.
  if (eyeSpriteReady_) {
    composeEye(eyeSprite_, color, x, y, upper, lower);
    eyeSprite_.pushSprite(DisplayConfig::LEFT_EYE_CENTER_X - DisplayConfig::EYE_DIRTY_WIDTH / 2,
                           DisplayConfig::EYE_DIRTY_Y);
    eyeSprite_.pushSprite(DisplayConfig::RIGHT_EYE_CENTER_X - DisplayConfig::EYE_DIRTY_WIDTH / 2,
                           DisplayConfig::EYE_DIRTY_Y);
  } else {
    // Allocation failure: clear and replace each bounded eye immediately.
    for (int16_t center : {DisplayConfig::LEFT_EYE_CENTER_X, DisplayConfig::RIGHT_EYE_CENTER_X}) {
      tft_.setViewport(center - DisplayConfig::EYE_DIRTY_WIDTH / 2, DisplayConfig::EYE_DIRTY_Y,
                        DisplayConfig::EYE_DIRTY_WIDTH, DisplayConfig::EYE_DIRTY_HEIGHT);
      composeEye(tft_, color, x, y, upper, lower);
      tft_.resetViewport();
    }
  }
  eyeValid_ = true; eyeColor_ = color; pupilX_ = x; pupilY_ = y;
  upperLid_ = upper; lowerLid_ = lower;
}

void CompanionFace::clearMouth() {
  tft_.fillRect(DisplayConfig::MOUTH_DIRTY_X, DisplayConfig::MOUTH_DIRTY_Y,
                DisplayConfig::MOUTH_DIRTY_WIDTH, DisplayConfig::MOUTH_DIRTY_HEIGHT, CompanionTheme::BACKGROUND);
}
void CompanionFace::drawSmile(bool concerned) {
  const int8_t expression = concerned ? 5 : 4;
  if (mouthLevel_ == expression) return;
  mouthLevel_ = expression;
  clearMouth();
  const int16_t y = 181;
  tft_.drawLine(218, y + (concerned ? 5 : -3), 230, y + (concerned ? -3 : 5), CompanionTheme::PRIMARY_CYAN);
  tft_.drawFastHLine(230, y + (concerned ? -3 : 5), 20, CompanionTheme::PRIMARY_CYAN);
  tft_.drawLine(250, y + (concerned ? -3 : 5), 262, y + (concerned ? 5 : -3), CompanionTheme::PRIMARY_CYAN);
}
void CompanionFace::renderMouth(uint8_t level) {
  if (mouthLevel_ == level) return;
  mouthLevel_ = level;
  clearMouth();
  // Verified V2 mouth geometry and categories are deliberately unchanged.
  const int16_t widths[] = {38, 34, 46, 58}, heights[] = {4, 12, 22, 32};
  const int16_t width = widths[level], height = heights[level];
  tft_.fillRoundRect(240 - width / 2, 181 - height / 2, width, height, height / 2, CompanionTheme::PRIMARY_CYAN);
  if (level) tft_.fillRoundRect(240 - (width - 10) / 2, 181 - (height - 6) / 2,
                                width - 10, height - 6, (height - 6) / 2, CompanionTheme::BACKGROUND);
}

void CompanionFace::renderIndicators(uint32_t now) {
  const CompanionMode visible = state_ == CompanionState::THINKING || state_ == CompanionState::SPEAKING
                                ? mode_ : CompanionMode::NONE;
  if (visible != displayedMode_) {
    tft_.fillRect(DisplayConfig::MODE_X, DisplayConfig::MODE_Y,
                  DisplayConfig::MODE_WIDTH, DisplayConfig::MODE_HEIGHT, CompanionTheme::PANEL);
    if (visible != CompanionMode::NONE) {
      tft_.setFreeFont(nullptr);
      tft_.setTextSize(1);
      tft_.setTextDatum(TR_DATUM);
      tft_.setTextColor(CompanionTheme::MUTED_GREY, CompanionTheme::PANEL);
      tft_.drawString(modeName(visible), DisplayConfig::CONTENT_X + DisplayConfig::CONTENT_WIDTH,
                      DisplayConfig::MODE_Y, 2);
    }
    displayedMode_ = visible;
  }
  const uint32_t elapsed = now - stateEnteredAt_;
  const bool processing = state_ == CompanionState::THINKING || state_ == CompanionState::CONNECTING ||
                          (state_ == CompanionState::BOOTING && elapsed < 900);
  const int8_t type = processing ? 1 : state_ == CompanionState::PROTECTIVE ? 2 :
                                      state_ == CompanionState::ERROR ? 3 : 0;
  const int8_t step = processing ? (elapsed / 350) % 3 : 0;
  if (type == indicatorType_ && step == indicatorStep_) return;
  tft_.fillRect(DisplayConfig::INDICATOR_X, DisplayConfig::INDICATOR_Y,
                DisplayConfig::INDICATOR_WIDTH, DisplayConfig::INDICATOR_HEIGHT, CompanionTheme::PANEL);
  if (processing) {
    for (uint8_t index = 0; index < 3; ++index)
      tft_.fillCircle(30 + 12 * index, 237, 2,
                      index == step ? CompanionTheme::PRIMARY_CYAN : CompanionTheme::DIM_CYAN);
  } else if (type == 2 || type == 3) {
    const uint16_t color = type == 2 ? CompanionTheme::CAUTION_ACCENT : CompanionTheme::ERROR_ACCENT;
    tft_.fillRect(24, 237, 18, 2, color);  // Steady small accent; never a pulse.
  }
  indicatorType_ = type; indicatorStep_ = step;
}
void CompanionFace::renderMeter(uint8_t level) {
  const uint8_t weights[] = {42, 65, 85, 100, 85, 65, 42};
  for (uint8_t index = 0; index < DisplayConfig::METER_BARS; ++index) {
    const int16_t height = 4 + level * weights[index] * 34 / 10000;
    if (height == meterHeights_[index]) continue;
    const int16_t x = DisplayConfig::METER_X + 14 + index * 16;
    tft_.fillRect(x, DisplayConfig::METER_Y, 8, DisplayConfig::METER_HEIGHT, CompanionTheme::PANEL);
    tft_.fillRoundRect(x, DisplayConfig::METER_Y + DisplayConfig::METER_HEIGHT - height,
                       8, height, height > 5 ? 3 : 2, CompanionTheme::PRIMARY_CYAN);
    meterHeights_[index] = height;
  }
}

void CompanionFace::drawInteractionText() {
  const bool listening = state_ == CompanionState::LISTENING;
  const int16_t regionWidth = listening ? DisplayConfig::LISTENING_TEXT_WIDTH : DisplayConfig::CONTENT_WIDTH;
  if (listening && !lastTextWasListening_) {
    // Remove only the previous caption's right-hand footprint, then draw the meter.
    tft_.fillRect(DisplayConfig::CONTENT_X + regionWidth, DisplayConfig::CONTENT_Y,
                  DisplayConfig::CONTENT_WIDTH - regionWidth, DisplayConfig::CONTENT_HEIGHT, CompanionTheme::PANEL);
    for (int16_t& height : meterHeights_) height = -1;
  }
  TFT_eSPI& canvas = textSpriteReady_ ? static_cast<TFT_eSPI&>(textSprite_) : tft_;
  if (!textSpriteReady_)
    tft_.setViewport(DisplayConfig::CONTENT_X, DisplayConfig::CONTENT_Y,
                      regionWidth, DisplayConfig::CONTENT_HEIGHT);
  canvas.fillRect(0, 0, textSpriteReady_ ? DisplayConfig::CONTENT_WIDTH : regionWidth,
                   DisplayConfig::CONTENT_HEIGHT, CompanionTheme::PANEL);
  canvas.setTextSize(1);
  canvas.setFreeFont(&FreeSans12pt7b);  // Already provided by LOAD_GFXFF / TFT_eSPI.
  const char* text = statusText_;
  if (state_ == CompanionState::THINKING && query_[0] && !customText_) text = query_;
  char lines[2][DisplayConfig::MAX_QUERY_TEXT_LENGTH + 1] = {};
  uint8_t font = 1;
  const int16_t width = regionWidth - 2 * DisplayConfig::TEXT_INSET;
  if (!wrapText(canvas, text, lines, font, width)) {
    // Only unusually wide/long text needs the smaller existing font.
    font = 2;
    canvas.setFreeFont(nullptr);
    wrapText(canvas, text, lines, font, width);
  }
  canvas.setTextDatum(L_BASELINE);
  canvas.setTextColor(CompanionTheme::SOFT_WHITE, CompanionTheme::PANEL);
  const int16_t baseline = lines[1][0] ? 20 : 37;
  const int16_t stride = font == 1 ? 29 : 20;
  canvas.drawString(lines[0], DisplayConfig::TEXT_INSET, baseline, font);
  if (lines[1][0]) canvas.drawString(lines[1], DisplayConfig::TEXT_INSET, baseline + stride, font);
  if (textSpriteReady_) {
    textSprite_.pushSprite(DisplayConfig::CONTENT_X, DisplayConfig::CONTENT_Y,
                            0, 0, regionWidth, DisplayConfig::CONTENT_HEIGHT);
  } else {
    tft_.setFreeFont(nullptr);
    tft_.resetViewport();
  }
  lastTextWasListening_ = listening;
}

bool CompanionFace::wrapText(TFT_eSPI& canvas, const char* text,
                              char lines[2][DisplayConfig::MAX_QUERY_TEXT_LENGTH + 1],
                              uint8_t font, int16_t width) {
  const char* remaining = text;
  for (uint8_t row = 0; row < 2; ++row) {
    while (*remaining == ' ') ++remaining;
    size_t count = 0, boundary = 0;
    lines[row][0] = '\0';
    while (remaining[count] && count < DisplayConfig::MAX_QUERY_TEXT_LENGTH) {
      lines[row][count] = remaining[count];
      lines[row][count + 1] = '\0';
      if (canvas.textWidth(lines[row], font) > width) { lines[row][count] = '\0'; break; }
      if (remaining[count] == ' ') boundary = count;
      ++count;
    }
    if (remaining[count] && boundary) count = boundary;
    lines[row][count] = '\0';
    remaining += count;
  }
  while (*remaining == ' ') ++remaining;
  if (!*remaining) return true;
  size_t count = strlen(lines[1]);
  while (count > 0) {
    lines[1][--count] = '\0';
    if (count + 3 <= DisplayConfig::MAX_QUERY_TEXT_LENGTH) {
      strcpy(lines[1] + count, "...");
      if (canvas.textWidth(lines[1], font) <= width) break;
    }
  }
  return false;
}

const char* CompanionFace::defaultStatusText(CompanionState state) {
  switch (state) {
    case CompanionState::BOOTING: return "Starting...";
    case CompanionState::SETUP: return "Ready for setup";
    case CompanionState::CONNECTING: return "Connecting...";
    case CompanionState::READY: return "Ready";
    case CompanionState::LISTENING: return "Listening...";
    case CompanionState::THINKING: return "Thinking...";
    case CompanionState::SPEAKING: return "...";
    case CompanionState::OFFLINE: return "Local mode";
    case CompanionState::ECO: return "Eco mode";
    case CompanionState::PROTECTIVE: return "Protecting resources";
    case CompanionState::ERROR: return "Let's try again";
  }
  return "Let's try again";
}
const char* CompanionFace::stateName(CompanionState state) {
  const char* names[] = {"BOOTING", "SETUP", "CONNECTING", "READY", "LISTENING", "THINKING",
                         "SPEAKING", "OFFLINE", "ECO", "PROTECTIVE", "ERROR"};
  const uint8_t index = static_cast<uint8_t>(state);
  return index < sizeof(names) / sizeof(names[0]) ? names[index] : "ERROR";
}
bool CompanionFace::parseStateName(const char* name, CompanionState& state) {
  for (uint8_t index = 0; index <= static_cast<uint8_t>(CompanionState::ERROR); ++index) {
    if (equalsIgnoreCase(name, stateName(static_cast<CompanionState>(index)))) {
      state = static_cast<CompanionState>(index); return true;
    }
  }
  return false;
}
const char* CompanionFace::modeName(CompanionMode mode) {
  switch (mode) {
    case CompanionMode::LOCAL: return "LOCAL";
    case CompanionMode::KNOWLEDGE: return "KNOWLEDGE";
    case CompanionMode::WEB: return "WEB";
    default: return "";
  }
}
bool CompanionFace::parseModeName(const char* name, CompanionMode& mode) {
  for (CompanionMode candidate : {CompanionMode::LOCAL, CompanionMode::KNOWLEDGE, CompanionMode::WEB}) {
    if (strcmp(name, modeName(candidate)) == 0) { mode = candidate; return true; }
  }
  return false;
}
void CompanionFace::copyText(char* output, size_t capacity, const char* input) {
  size_t count = 0;
  bool space = false;
  if (input) {
    for (; *input && count + 1 < capacity; ++input) {
      const unsigned char value = static_cast<unsigned char>(*input);
      if (isspace(value)) { space = count > 0; continue; }
      if (space) {
        if (count + 2 >= capacity) break;
        output[count++] = ' ';
      }
      space = false;
      output[count++] = value >= 32 && value <= 126 ? *input : '?';
    }
  }
  output[count] = '\0';
}
bool CompanionFace::timeReached(uint32_t now, uint32_t target) {
  return static_cast<int32_t>(now - target) >= 0;
}
