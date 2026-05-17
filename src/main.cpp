#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char *ssid = WIFI_SSID;
const char *password = WIFI_PASSWORD;
const char *serverUrl = SERVER_URL;
const char *apiKey = API_KEY;
const char *hostname = "esp32-dashboard";

const int LED_PIN = 2;
const unsigned long POLL_INTERVAL_MS = 5000;

bool isPlaying = false;
String lastTrack = "";
unsigned long lastPoll = 0;
unsigned long lastBlink = 0;
bool ledState = false;

void initWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE, INADDR_NONE);
    WiFi.setHostname(hostname);

    WiFi.begin(ssid, password);
    Serial.print("Connecting to WiFi...");
    while (WiFi.status() != WL_CONNECTED) {
        Serial.print('.');
        delay(1000);
    }

    Serial.printf("\nConnected! IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("Hostname: %s\n", WiFi.getHostname());
    Serial.printf("RSSI: %d\n", WiFi.RSSI());
}

void fetchNowPlaying() {
    HTTPClient http;
    String url = String(serverUrl) + "/v1/spotify/now-playing";

    http.begin(url);
    http.addHeader("X-API-Key", apiKey);

    int httpCode = http.GET();

    if (httpCode == 200) {
        String payload = http.getString();
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, payload);
        if (err) {
            Serial.printf("JSON parse error: %s\n", err.c_str());
            isPlaying = false;
        } else {
            isPlaying = doc["is_playing"] | false;
            if (isPlaying) {
                const char *track = doc["track"];
                const char *artist = doc["artist"];
                String currentTrack = String(artist) + " - " + String(track);
                if (currentTrack != lastTrack) {
                    lastTrack = currentTrack;
                    Serial.printf("Playing: %s\n", currentTrack.c_str());
                }
            } else if (!lastTrack.isEmpty()) {
                lastTrack = "";
                Serial.println("Nothing playing");
            }
        }
    } else {
        Serial.printf("HTTP error: %d\n", httpCode);
        isPlaying = false;
    }

    http.end();
}

void setup() {
    Serial.begin(115200);
    pinMode(LED_PIN, OUTPUT);
    initWiFi();
}

void loop() {
    unsigned long now = millis();

    if (now - lastPoll >= POLL_INTERVAL_MS) {
        lastPoll = now;
        if (WiFi.status() == WL_CONNECTED) {
            fetchNowPlaying();
        } else {
            Serial.println("WiFi disconnected, reconnecting...");
            initWiFi();
        }
    }

    if (isPlaying) {
        if (now - lastBlink >= 2000) {
            lastBlink = now;
            ledState = !ledState;
            digitalWrite(LED_PIN, ledState ? HIGH : LOW);
        }
    } else {
        digitalWrite(LED_PIN, LOW);
        ledState = false;
    }
}
