#pragma once

#include <Arduino.h>

namespace CompanionTheme {

constexpr uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return static_cast<uint16_t>(((r & 0xF8U) << 8U) |
                               ((g & 0xFCU) << 3U) | (b >> 3U));
}

constexpr uint16_t BACKGROUND = rgb565(0x05, 0x08, 0x0A);
constexpr uint16_t PANEL = rgb565(0x0B, 0x11, 0x16);
constexpr uint16_t PRIMARY_CYAN = rgb565(0x37, 0xE7, 0xFF);
constexpr uint16_t SOFT_WHITE = rgb565(0xF2, 0xF7, 0xF9);
constexpr uint16_t MUTED_GREY = rgb565(0x73, 0x80, 0x8A);
constexpr uint16_t SUCCESS_ACCENT = rgb565(0x56, 0xF3, 0x9A);
constexpr uint16_t CAUTION_ACCENT = rgb565(0xFF, 0xC8, 0x57);
constexpr uint16_t ERROR_ACCENT = rgb565(0xFF, 0x5D, 0x73);
constexpr uint16_t DIM_CYAN = rgb565(0x25, 0x9F, 0xAF);

constexpr int16_t NORMAL_EYE_WIDTH = 94;
constexpr int16_t NORMAL_EYE_HEIGHT = 50;
constexpr int16_t ATTENTIVE_EYE_WIDTH = 98;
constexpr int16_t ATTENTIVE_EYE_HEIGHT = 58;
constexpr int16_t RELAXED_EYE_HEIGHT = 36;
constexpr int16_t SLEEPY_EYE_HEIGHT = 20;
constexpr int16_t EYE_CORNER_RADIUS = 20;
constexpr int16_t PUPIL_RADIUS = 7;
constexpr int16_t HIGHLIGHT_RADIUS = 3;
constexpr int16_t STATUS_DOT_RADIUS = 5;

constexpr uint32_t BOOT_FRAME_INTERVAL_MS = 90;
constexpr uint8_t BOOT_FRAME_COUNT = 9;

constexpr uint32_t BLINK_DURATION_MS = 170;
constexpr uint32_t READY_EYE_MOVEMENT_INTERVAL_MS = 1800;
constexpr uint32_t READY_BLINK_INTERVALS_MS[] = {
    3200, 4700, 5800, 3900, 5200,
};
constexpr size_t READY_BLINK_INTERVAL_COUNT =
    sizeof(READY_BLINK_INTERVALS_MS) /
    sizeof(READY_BLINK_INTERVALS_MS[0]);

constexpr uint32_t LISTENING_PULSE_INTERVAL_MS = 300;
constexpr uint32_t THINKING_STEP_INTERVAL_MS = 450;
constexpr uint32_t SPEAKING_MOUTH_INTERVAL_MS = 140;
constexpr uint32_t CONNECTING_STEP_INTERVAL_MS = 350;
constexpr uint32_t DEMO_STATE_INTERVAL_MS = 4000;

}  // namespace CompanionTheme
