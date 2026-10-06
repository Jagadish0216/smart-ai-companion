#pragma once

#include <Arduino.h>

namespace DisplayConfig {

constexpr int16_t SCREEN_WIDTH = 480;
constexpr int16_t SCREEN_HEIGHT = 320;
constexpr uint8_t DISPLAY_ROTATION = 1;

constexpr int16_t FACE_TOP = 0;
constexpr int16_t FACE_BOTTOM = 224;
constexpr int16_t FACE_HEIGHT = FACE_BOTTOM - FACE_TOP + 1;

constexpr int16_t DIVIDER_Y = 229;
constexpr int16_t DIVIDER_HEIGHT = 2;

constexpr int16_t STATUS_TOP = 230;
constexpr int16_t STATUS_BOTTOM = 319;
constexpr int16_t STATUS_HEIGHT = STATUS_BOTTOM - STATUS_TOP + 1;
constexpr int16_t STATUS_CENTER_X = SCREEN_WIDTH / 2;
constexpr int16_t STATUS_PRIMARY_Y = 266;
constexpr int16_t STATUS_STATE_Y = 301;

constexpr int16_t LEFT_EYE_CENTER_X = 145;
constexpr int16_t RIGHT_EYE_CENTER_X = 335;
constexpr int16_t EYE_CENTER_Y = 108;

constexpr int16_t EYE_DIRTY_WIDTH = 126;
constexpr int16_t EYE_DIRTY_HEIGHT = 86;
constexpr int16_t EYE_DIRTY_Y = EYE_CENTER_Y - (EYE_DIRTY_HEIGHT / 2);
constexpr int16_t LEFT_EYE_DIRTY_X =
    LEFT_EYE_CENTER_X - (EYE_DIRTY_WIDTH / 2);
constexpr int16_t RIGHT_EYE_DIRTY_X =
    RIGHT_EYE_CENTER_X - (EYE_DIRTY_WIDTH / 2);

constexpr int16_t MOUTH_CENTER_X = STATUS_CENTER_X;
constexpr int16_t MOUTH_CENTER_Y = 181;
constexpr int16_t MOUTH_DIRTY_X = 190;
constexpr int16_t MOUTH_DIRTY_Y = 158;
constexpr int16_t MOUTH_DIRTY_WIDTH = 100;
constexpr int16_t MOUTH_DIRTY_HEIGHT = 48;

constexpr int16_t STATUS_DOT_X = 28;
constexpr int16_t STATUS_DOT_Y = 268;
constexpr int16_t ACTIVITY_INDICATOR_X = 448;
constexpr int16_t ACTIVITY_INDICATOR_Y = 268;
constexpr int16_t ACTIVITY_DIRTY_X = 424;
constexpr int16_t ACTIVITY_DIRTY_Y = 244;
constexpr int16_t ACTIVITY_DIRTY_SIZE = 48;

constexpr size_t MAX_STATUS_TEXT_LENGTH = 40;
constexpr size_t SERIAL_LINE_BUFFER_SIZE = 96;

}  // namespace DisplayConfig
