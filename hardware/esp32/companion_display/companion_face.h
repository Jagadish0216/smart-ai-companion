#pragma once

#include <Arduino.h>
#include <TFT_eSPI.h>

#include "display_config.h"

enum class CompanionState : uint8_t {
  BOOTING,
  SETUP,
  CONNECTING,
  READY,
  LISTENING,
  THINKING,
  SPEAKING,
  OFFLINE,
  ECO,
  PROTECTIVE,
  ERROR,
};

class CompanionFace {
 public:
  explicit CompanionFace(TFT_eSPI& display);

  void begin();
  void update();
  void setState(CompanionState state);
  void setStatusText(const char* text);
  void clearStatusText();

  CompanionState getState() const;
  const char* getStatusText() const;

  static const char* stateName(CompanionState state);
  static bool parseStateName(const char* name, CompanionState& state);

 private:
  TFT_eSPI& tft_;
  CompanionState state_;
  char statusText_[DisplayConfig::MAX_STATUS_TEXT_LENGTH + 1];

  uint32_t stateEnteredAt_;
  uint32_t lastAnimationAt_;
  uint32_t nextBlinkAt_;
  uint8_t animationStep_;
  uint8_t blinkIntervalIndex_;
  bool blinkActive_;

  void drawBaseLayout();
  void renderStateFace();
  void renderActivityIndicator();
  void drawStatusPanel();

  void clearEyes();
  void clearMouth();
  void clearActivityIndicator();
  void drawEyePair(int16_t width, int16_t height, uint16_t color,
                   int16_t pupilOffsetX = 0, int16_t pupilOffsetY = 0,
                   bool drawPupils = true);
  void drawEye(int16_t centerX, int16_t centerY, int16_t width,
               int16_t height, uint16_t color, int16_t pupilOffsetX,
               int16_t pupilOffsetY, bool drawPupil);
  void drawSmile();
  void drawConcernedMouth();
  void drawSpeakingMouth(bool open);
  void drawConcernedBrows(bool errorStyle);
  void drawConnectingIndicator(uint8_t step);
  void drawThinkingIndicator(uint8_t step);
  void drawListeningIndicator(uint8_t step);

  void updateBooting(uint32_t now);
  void updateReady(uint32_t now);
  void updateConnecting(uint32_t now);
  void updateListening(uint32_t now);
  void updateThinking(uint32_t now);
  void updateSpeaking(uint32_t now);

  uint16_t stateAccentColor() const;
  const char* defaultStatusText(CompanionState state) const;
  void resetAnimation(uint32_t now);
  void copyStatusText(const char* text);
  static bool timeReached(uint32_t now, uint32_t target);
};
