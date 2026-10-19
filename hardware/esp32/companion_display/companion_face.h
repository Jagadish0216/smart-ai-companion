#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>
#include "display_config.h"

enum class CompanionState : uint8_t {
  BOOTING, SETUP, CONNECTING, READY, LISTENING, THINKING,
  SPEAKING, OFFLINE, ECO, PROTECTIVE, ERROR,
};
enum class CompanionMode : uint8_t { NONE, LOCAL, KNOWLEDGE, WEB };

class CompanionFace {
 public:
  explicit CompanionFace(TFT_eSPI& display);
  void begin();
  void update();
  void setState(CompanionState state);
  void setStatusText(const char* text);
  void clearStatusText();
  void setQuery(const char* text);
  void clearQuery();
  void setMode(CompanionMode mode);
  void setLevel(uint8_t level);
  CompanionState getState() const { return state_; }
  const char* getStatusText() const { return statusText_; }
  static const char* stateName(CompanionState state);
  static bool parseStateName(const char* name, CompanionState& state);
  static const char* modeName(CompanionMode mode);
  static bool parseModeName(const char* name, CompanionMode& mode);
 private:
  TFT_eSPI& tft_;
  TFT_eSprite eyeSprite_;
  TFT_eSprite textSprite_;
  TFT_eSprite mouthSprite_;
  bool spritesAttempted_ = false, eyeSpriteReady_ = false, textSpriteReady_ = false;
  bool mouthSpriteReady_ = false;
  CompanionState state_ = CompanionState::BOOTING;
  CompanionMode mode_ = CompanionMode::NONE, displayedMode_ = CompanionMode::NONE;
  char statusText_[DisplayConfig::MAX_STATUS_TEXT_LENGTH + 1] = {};
  char query_[DisplayConfig::MAX_QUERY_TEXT_LENGTH + 1] = {};
  bool customText_ = false, textDirty_ = true, forceFrame_ = true;
  bool lastTextWasListening_ = false;
  uint32_t stateEnteredAt_ = 0, lastFrameAt_ = 0, nextBlinkAt_ = 0, blinkStartedAt_ = 0;
  uint32_t lastLevelFrameAt_ = 0;
  uint8_t blinkCount_ = 0;
  bool blinking_ = false, secondBlinkPending_ = false;
  uint8_t level_ = 0;
  int16_t smoothLevelQ_ = 0;
  uint32_t levelUpdatedAt_ = 0;
  bool hasLevel_ = false;
  int16_t gazeXQ_ = 0, gazeYQ_ = 0;
  bool eyeValid_ = false;
  uint16_t eyeColor_ = 0;
  int16_t eyeX_ = 0, eyeY_ = 0, upperLid_ = -1, lowerLid_ = -1;
  int16_t mouthLevel_ = -1;
  int8_t indicatorType_ = -1, indicatorStep_ = -1;
  int16_t meterHeights_[DisplayConfig::METER_BARS] = {};
  void renderExpression(uint32_t now);
  void renderEyes(uint16_t color, int16_t x, int16_t y, int16_t upper, int16_t lower);
  void composeEye(TFT_eSPI& canvas, uint16_t color, int16_t x, int16_t y,
                  int16_t upper, int16_t lower);
  void renderMouth(uint8_t level);
  void beginMouth();
  void endMouth();
  TFT_eSPI& mouthCanvas();
  void renderThinking(uint32_t elapsed);
  void drawSmile(bool concerned = false);
  void clearMouth();
  void renderIndicators(uint32_t now);
  void renderMeter(uint8_t level);
  void drawInteractionText();
  bool wrapText(TFT_eSPI& canvas, const char* text,
                char lines[2][DisplayConfig::MAX_QUERY_TEXT_LENGTH + 1],
                uint8_t font, int16_t width);
  uint8_t blinkCoverage(uint32_t now);
  uint32_t relaxedBlinkExtra() const;
  static const char* defaultStatusText(CompanionState state);
  static void copyText(char* output, size_t capacity, const char* input);
  static bool timeReached(uint32_t now, uint32_t target);
};
