#pragma once
#include <Arduino.h>
namespace DisplayConfig {
constexpr int16_t SCREEN_WIDTH = 480;
constexpr int16_t SCREEN_HEIGHT = 320;
constexpr uint8_t DISPLAY_ROTATION = 1;
constexpr int16_t LEFT_EYE_CENTER_X = 166;
constexpr int16_t RIGHT_EYE_CENTER_X = 314;
constexpr int16_t EYE_CENTER_Y = 108;
constexpr int16_t EYE_DIRTY_WIDTH = 84;
constexpr int16_t EYE_DIRTY_HEIGHT = 80;
constexpr int16_t EYE_DIRTY_Y = 64;
constexpr int16_t MOUTH_DIRTY_X = 186;
constexpr int16_t MOUTH_DIRTY_Y = 157;
constexpr int16_t MOUTH_DIRTY_WIDTH = 108;
constexpr int16_t MOUTH_DIRTY_HEIGHT = 48;
constexpr int16_t BAND_Y = 220;
constexpr int16_t BAND_HEIGHT = 100;
constexpr int16_t CONTENT_X = 24;
constexpr int16_t CONTENT_Y = 250;
constexpr int16_t CONTENT_WIDTH = 432;
constexpr int16_t CONTENT_HEIGHT = 62;
constexpr int16_t TEXT_INSET = 2;
constexpr int16_t LISTENING_TEXT_WIDTH = 260;
constexpr int16_t MODE_X = 336;
constexpr int16_t MODE_Y = 228;
constexpr int16_t MODE_WIDTH = 120;
constexpr int16_t MODE_HEIGHT = 18;
constexpr int16_t INDICATOR_X = 24;
constexpr int16_t INDICATOR_Y = 228;
constexpr int16_t INDICATOR_WIDTH = 64;
constexpr int16_t INDICATOR_HEIGHT = 18;
constexpr int16_t METER_X = 306;
constexpr int16_t METER_Y = 254;
constexpr int16_t METER_WIDTH = 140;
constexpr int16_t METER_HEIGHT = 44;
constexpr uint8_t METER_BARS = 7;
constexpr size_t MAX_STATUS_TEXT_LENGTH = 40;
constexpr size_t MAX_QUERY_TEXT_LENGTH = 80;
constexpr size_t SERIAL_LINE_BUFFER_SIZE = 96;
constexpr uint32_t LEVEL_STALE_MS = 1200;
constexpr size_t SERIAL_BYTES_PER_LOOP = 64;
static_assert(MAX_QUERY_TEXT_LENGTH + 7 <= SERIAL_LINE_BUFFER_SIZE,
              "Query command must fit the bounded serial line");
static_assert(EYE_DIRTY_Y + EYE_DIRTY_HEIGHT <= MOUTH_DIRTY_Y,
              "Eye and mouth replacements must not overlap");
static_assert(MOUTH_DIRTY_Y + MOUTH_DIRTY_HEIGHT <= BAND_Y,
              "Face must stay above interaction text");
}  // namespace DisplayConfig
