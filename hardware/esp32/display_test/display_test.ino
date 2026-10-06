#include <TFT_eSPI.h>

TFT_eSPI tft = TFT_eSPI();

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("Starting TFT test");

  tft.init();
  tft.setRotation(1);

  tft.fillScreen(TFT_RED);
  delay(1000);

  tft.fillScreen(TFT_GREEN);
  delay(1000);

  tft.fillScreen(TFT_BLUE);
  delay(1000);

  tft.fillScreen(TFT_WHITE);
  delay(1000);

  tft.fillScreen(TFT_BLACK);

  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.drawString("DISPLAY OK", 240, 160, 4);

  Serial.println("Finished");
}

void loop() {
}