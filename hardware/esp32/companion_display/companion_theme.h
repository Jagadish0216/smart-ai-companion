#pragma once
#include <Arduino.h>
namespace CompanionTheme {
constexpr uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return static_cast<uint16_t>(((r & 0xF8U) << 8U) |
                             ((g & 0xFCU) << 3U) | (b >> 3U));
}
constexpr uint16_t BACKGROUND = rgb565(0, 0, 0);
constexpr uint16_t PANEL = rgb565(0, 0, 0);
constexpr uint16_t SEPARATOR = rgb565(0x17, 0x27, 0x30);
constexpr uint16_t PRIMARY_CYAN = rgb565(0x68, 0xD9, 0xF2);
constexpr uint16_t SOFT_WHITE = rgb565(0xF2, 0xF7, 0xF9);
constexpr uint16_t MUTED_GREY = rgb565(0x73, 0x80, 0x8A);
constexpr uint16_t CAUTION_ACCENT = rgb565(0xFF, 0xC8, 0x57);
constexpr uint16_t ERROR_ACCENT = rgb565(0xFF, 0x5D, 0x73);
constexpr uint16_t DIM_CYAN = rgb565(0x25, 0x9F, 0xAF);
constexpr int16_t NORMAL_EYE_WIDTH = 68;
constexpr int16_t NORMAL_EYE_HEIGHT = 54;
constexpr int16_t EYE_CORNER_RADIUS = 20;
constexpr uint32_t ACTIVE_FRAME_MS = 50;
constexpr uint32_t LEVEL_FRAME_MS = 100;
constexpr uint32_t IDLE_FRAME_MS = 180;
constexpr uint32_t ECO_FRAME_MS = 250;
constexpr uint32_t BLINK_FRAME_MS = 36;  // Time-indexed; skip frames under SPI load.
constexpr uint32_t READY_GAZE_INTERVAL_MS = 4800;
constexpr uint32_t READY_BLINK_INTERVALS_MS[] = {4800, 6400, 5700, 7200, 5300};
constexpr size_t READY_BLINK_INTERVAL_COUNT =
    sizeof(READY_BLINK_INTERVALS_MS) / sizeof(READY_BLINK_INTERVALS_MS[0]);
constexpr uint32_t DEMO_STATE_INTERVAL_MS = 4000;
}  // namespace CompanionTheme
