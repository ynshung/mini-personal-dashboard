#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>

const char *ssid     = WIFI_SSID;
const char *password = WIFI_PASSWORD;
const char *serverUrl = SERVER_URL;
const char *apiKey   = API_KEY;
const char *hostname = "esp32-dashboard";

TFT_eSPI tft = TFT_eSPI();

const uint16_t COL_GREY      = 0x52AA;
const uint16_t COL_BAR_BG    = 0x39C7; // white at 25% opacity on black
const uint16_t COL_BAR_FILL  = 0xE71C; // white at 90% opacity on black
const uint16_t COL_BAR_PLAY  = 0x1CC4; // Spotify green #1DB954 in RGB565
const uint16_t COL_BAR_ERROR = 0xFD24; // orange

const int CX    = 120;
const int BAR_W = 120;
const int BAR_X = (240 - BAR_W) / 2;
const int BAR_Y = 210;
const int BAR_H = 3;

struct TrackState {
    bool     is_playing  = false;
    String   track_id    = "";
    uint32_t progress_ms = 0;
    uint32_t duration_ms = 0;
};

TrackState current;

const unsigned long POLL_INTERVAL_MS = 5000;
const unsigned long TICK_INTERVAL_MS = 1000;
unsigned long lastPoll    = 0;
unsigned long lastTick    = 0;
unsigned long lastFetchMs = 0;
bool hasArt = false;
bool pollFailed = false;

// --- Display ---

void drawStatus(const char* msg) {
    tft.fillScreen(TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(2);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString(msg, CX, CX);
}

void drawIdle() {
    drawStatus("Not playing");
    hasArt = false;
}

void drawProgressBar(uint32_t progress_ms, uint32_t duration_ms, bool is_playing) {
    tft.fillRoundRect(BAR_X, BAR_Y, BAR_W, BAR_H, BAR_H / 2, COL_BAR_BG);
    if (duration_ms == 0) return;
    int fillW = (int)((float)progress_ms / duration_ms * BAR_W);
    if (fillW > BAR_W) fillW = BAR_W;
    uint16_t col = pollFailed ? COL_BAR_ERROR : (is_playing ? COL_BAR_PLAY : COL_BAR_FILL);
    if (fillW > 0)
        tft.fillRoundRect(BAR_X, BAR_Y, fillW, BAR_H, BAR_H / 2, col);
}

void drawTick() {
    if (pollFailed || !current.is_playing || current.duration_ms == 0) return;
    uint32_t estimated = current.progress_ms + (uint32_t)(millis() - lastFetchMs);
    if (estimated > current.duration_ms) estimated = current.duration_ms;
    drawProgressBar(estimated, current.duration_ms, true);
}

// --- WiFi ---

void initWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE, INADDR_NONE);
    WiFi.setHostname(hostname);
    WiFi.begin(ssid, password);
    tft.fillScreen(TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(2);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString("Connecting to", CX, CX - 8);
    tft.drawString(ssid, CX, CX + 8);
    Serial.print("Connecting to WiFi...");
    while (WiFi.status() != WL_CONNECTED) {
        Serial.print('.');
        delay(1000);
    }
    Serial.printf("\nConnected! IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("Hostname: %s\n", WiFi.getHostname());
    Serial.printf("RSSI: %d\n", WiFi.RSSI());
}

// --- Album art streaming ---

bool fetchAlbumArt() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/spotify/now-playing/art");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code == 204) {
        http.end();
        return false;
    }
    if (code != 200) {
        Serial.printf("Art fetch HTTP error: %d\n", code);
        http.end();
        return false;
    }

    int contentLength = http.getSize();
    if (contentLength != 240 * 240 * 2) {
        Serial.printf("Art unexpected size: %d\n", contentLength);
        http.end();
        return false;
    }

    WiFiClient *stream = http.getStreamPtr();
    uint16_t rowBuf[240];
    int y = 0;

    tft.startWrite();
    while (y < 240 && stream->connected()) {
        size_t avail = stream->available();
        if (avail < 480) {
            delay(1);
            continue;
        }
        stream->readBytes((uint8_t *)rowBuf, 480);
        tft.pushImage(0, y, 240, 1, rowBuf);
        y++;
    }
    tft.endWrite();

    http.end();

    if (y == 240) {
        Serial.println("Album art loaded");
        hasArt = true;
        return true;
    }

    Serial.printf("Art incomplete: %d/240 rows\n", y);
    return false;
}

// --- Networking ---

void fetchNowPlaying() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/spotify/now-playing");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code != 200) {
        Serial.printf("HTTP error: %d\n", code);
        http.end();
        if (!pollFailed) {
            pollFailed = true;
            drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
        }
        return;
    }

    String payload = http.getString();
    http.end();

    JsonDocument doc;
    if (deserializeJson(doc, payload)) {
        Serial.println("JSON parse error");
        if (!pollFailed) {
            pollFailed = true;
            drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
        }
        return;
    }

    pollFailed = false;

    TrackState next;
    next.is_playing  = doc["is_playing"]  | false;
    next.track_id    = doc["track_id"]    | "";
    next.progress_ms = doc["progress_ms"] | 0;
    next.duration_ms = doc["duration_ms"] | 0;

    bool track_changed = (next.track_id != current.track_id);
    bool play_state_changed = (next.is_playing != current.is_playing);

    current = next;
    lastFetchMs = millis();

    if (current.track_id.length() == 0) {
        if (hasArt || track_changed) drawIdle();
        return;
    }

    if (track_changed) {
        fetchAlbumArt();
        drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
    } else if (play_state_changed) {
        drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
    }

    Serial.printf("[%s] %s  [%u/%u ms]\n",
        current.is_playing ? "PLAY" : "PAUSE",
        current.track_id.c_str(),
        current.progress_ms,
        current.duration_ms);
}

// --- Arduino entry points ---

void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(0);
    initWiFi();
    drawStatus("Connecting to server...");
}

void loop() {
    unsigned long now = millis();

    // End-of-song poll: immediately check when estimated progress reaches duration
    if (current.is_playing && current.duration_ms > 0) {
        uint32_t estimated = current.progress_ms + (uint32_t)(now - lastFetchMs);
        if (estimated >= current.duration_ms) {
            if (WiFi.status() == WL_CONNECTED) {
                fetchNowPlaying();
                lastPoll = millis();
                lastTick = lastPoll;
            }
        }
    }

    if (now - lastPoll >= POLL_INTERVAL_MS) {
        lastPoll = now;
        if (WiFi.status() == WL_CONNECTED) {
            fetchNowPlaying();
            lastTick = now;
        } else {
            Serial.println("WiFi disconnected, reconnecting...");
            if (!hasArt) drawStatus("WiFi disconnected");
            initWiFi();
            drawStatus("Connecting to server...");
        }
    }

    if (current.is_playing && (now - lastTick >= TICK_INTERVAL_MS)) {
        lastTick = now;
        drawTick();
    }
}
