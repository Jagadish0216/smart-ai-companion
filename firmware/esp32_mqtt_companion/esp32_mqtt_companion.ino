#include <ArduinoJson.h>
#include <DHT.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <ctype.h>
#include <math.h>

#include "secrets.h"

// Hardware configuration: adjust these for the actual board and wiring.
// GPIO 2 is intentionally not assumed because onboard LEDs vary by board.
constexpr uint8_t LED_PIN = 5;
constexpr bool LED_ACTIVE_HIGH = true;
constexpr uint8_t DHT_PIN = 4;
constexpr uint8_t DHT_TYPE = DHT11;

constexpr int PROTOCOL_VERSION = 1;
constexpr size_t MAX_COMMAND_BYTES = 512;
constexpr unsigned long RECONNECT_INTERVAL_MS = 3000;
constexpr float MIN_TEMPERATURE_C = 0.0F;
constexpr float MAX_TEMPERATURE_C = 50.0F;

WiFiClient networkClient;
PubSubClient mqttClient(networkClient);
DHT dht(DHT_PIN, DHT_TYPE);

char commandTopic[128];
char responseTopic[128];
unsigned long lastReconnectAttempt = 0;
unsigned long lastWiFiAttempt = 0;
bool wifiAttempted = false;

void setLed(bool enabled) {
  bool pinHigh = LED_ACTIVE_HIGH ? enabled : !enabled;
  digitalWrite(LED_PIN, pinHigh ? HIGH : LOW);
}

bool validRequestId(const char* requestId) {
  if (requestId == nullptr) {
    return false;
  }
  size_t length = strlen(requestId);
  if (length == 0 || length > 64) {
    return false;
  }
  for (size_t index = 0; index < length; ++index) {
    char character = requestId[index];
    if (!isalnum(character) && character != '-' && character != '_') {
      return false;
    }
  }
  return true;
}

void publishError(const char* requestId, const char* action, const char* error) {
  StaticJsonDocument<384> response;
  response["version"] = PROTOCOL_VERSION;
  response["request_id"] = requestId;
  response["ok"] = false;
  response["action"] = action;
  response["error"] = error;

  char payload[384];
  size_t length = serializeJson(response, payload, sizeof(payload));
  if (length > 0 && length < sizeof(payload)) {
    mqttClient.publish(responseTopic, payload, false);
  }
}

void handleSetLed(JsonDocument& command, const char* requestId) {
  JsonVariant stateValue = command["params"]["state"];
  if (!stateValue.is<bool>()) {
    publishError(requestId, "set_led", "params.state must be boolean");
    return;
  }

  bool state = stateValue.as<bool>();
  setLed(state);

  StaticJsonDocument<384> response;
  response["version"] = PROTOCOL_VERSION;
  response["request_id"] = requestId;
  response["ok"] = true;
  response["action"] = "set_led";
  response["result"]["state"] = state;

  char payload[384];
  size_t length = serializeJson(response, payload, sizeof(payload));
  if (length > 0 && length < sizeof(payload)) {
    mqttClient.publish(responseTopic, payload, false);
  }
}

void handleReadTemperature(const char* requestId) {
  float temperature = dht.readTemperature();
  if (!isfinite(temperature) || temperature < MIN_TEMPERATURE_C ||
      temperature > MAX_TEMPERATURE_C) {
    publishError(requestId, "read_temperature", "temperature reading unavailable");
    return;
  }

  StaticJsonDocument<384> response;
  response["version"] = PROTOCOL_VERSION;
  response["request_id"] = requestId;
  response["ok"] = true;
  response["action"] = "read_temperature";
  response["result"]["temperature_c"] = temperature;

  char payload[384];
  size_t length = serializeJson(response, payload, sizeof(payload));
  if (length > 0 && length < sizeof(payload)) {
    mqttClient.publish(responseTopic, payload, false);
  }
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  if (strcmp(topic, commandTopic) != 0 || length == 0 ||
      length > MAX_COMMAND_BYTES) {
    return;
  }

  StaticJsonDocument<512> command;
  DeserializationError error = deserializeJson(command, payload, length);
  if (error || !command["version"].is<int>() ||
      command["version"].as<int>() != PROTOCOL_VERSION ||
      !command["request_id"].is<const char*>() ||
      !command["action"].is<const char*>()) {
    return;
  }

  const char* requestId = command["request_id"];
  const char* action = command["action"];
  if (!validRequestId(requestId)) {
    return;
  }

  if (strcmp(action, "set_led") == 0) {
    handleSetLed(command, requestId);
  } else if (strcmp(action, "read_temperature") == 0) {
    handleReadTemperature(requestId);
  } else {
    publishError(requestId, action, "unsupported action");
  }
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  unsigned long now = millis();
  if (wifiAttempted && now - lastWiFiAttempt < RECONNECT_INTERVAL_MS) {
    return;
  }
  wifiAttempted = true;
  lastWiFiAttempt = now;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void connectMqttIfNeeded() {
  if (WiFi.status() != WL_CONNECTED || mqttClient.connected()) {
    return;
  }
  unsigned long now = millis();
  if (now - lastReconnectAttempt < RECONNECT_INTERVAL_MS) {
    return;
  }
  lastReconnectAttempt = now;

  char clientId[96];
  snprintf(clientId, sizeof(clientId), "%s-firmware", ESP32_DEVICE_ID);
  if (mqttClient.connect(clientId)) {
    mqttClient.subscribe(commandTopic, 1);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  setLed(false);
  dht.begin();

  snprintf(commandTopic, sizeof(commandTopic),
           "%s/%s/command", MQTT_TOPIC_PREFIX, ESP32_DEVICE_ID);
  snprintf(responseTopic, sizeof(responseTopic),
           "%s/%s/response", MQTT_TOPIC_PREFIX, ESP32_DEVICE_ID);

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(onMqttMessage);
  mqttClient.setBufferSize(MAX_COMMAND_BYTES);
  connectWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }
  connectMqttIfNeeded();
  mqttClient.loop();
  delay(10);
}
