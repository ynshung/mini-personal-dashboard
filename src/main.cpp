#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>
#include <OneButtonTiny.h>
#include "NotoSans_Medium14.h"
#include "claude_logo.h"

const char *ssid     = WIFI_SSID;
const char *password = WIFI_PASSWORD;
const char *serverUrl = SERVER_URL;
const char *apiKey   = API_KEY;
const char *hostname = "esp32-dashboard";

TFT_eSPI tft = TFT_eSPI();
OneButtonTiny btn(19, false, false); // GPIO 19, active-high, no internal pull-up
OneButtonTiny btn2(21, false, false); // GPIO 21, active-high, no internal pull-up

const uint16_t COL_GREY      = 0x52AA;
const uint16_t COL_BAR_BG    = 0x39C7; // white at 25% opacity on black
const uint16_t COL_BAR_FILL  = 0xE71C; // white at 90% opacity on black
const uint16_t COL_BAR_PLAY  = 0x1CC4; // Spotify green #1DB954 in RGB565
const uint16_t COL_BAR_ERROR = 0xFD24; // orange
const uint16_t COL_RED       = 0xF800; // red
const unsigned long CC_POLL_INTERVAL_MS = 10000;

enum Screen { SPOTIFY, CC_USAGE };
Screen activeScreen = CC_USAGE;

struct CCUsage {
    float  five_hour_pct    = -1;
    String five_hour_resets = "";
    float  seven_day_pct    = -1;
    String seven_day_resets = "";
    String refreshed_ago    = "";
};
CCUsage ccUsage;
unsigned long lastCCPoll = 0;
bool ccNeedsFullRedraw = true;

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
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.loadFont(NotoSans_Medium14);
    tft.drawString(msg, CX, CX);
    tft.unloadFont();
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

uint16_t usageColor(float pct) {
    if (pct >= 100.0f) return COL_RED;
    if (pct >= 61.0f)  return COL_BAR_ERROR;
    return COL_BAR_FILL;
}

void drawCCBlock(int y, float pct, const char* label, const String& resets) {
    const int BAR_W = 170;
    const int BAR_H = 4;
    const int LEFT  = CX - BAR_W / 2;
    const int RIGHT = CX + BAR_W / 2;

    // Clear text rows to handle variable-width redraws (e.g. "100%" -> "5%")
    tft.fillRect(LEFT, y - 8,  BAR_W, 16, TFT_BLACK); // pct + label row
    tft.fillRect(LEFT, y + 21, BAR_W, 16, TFT_BLACK); // resets row

    tft.loadFont(NotoSans_Medium14);

    // Percentage (left) and label (right) on same row
    tft.setTextDatum(ML_DATUM);
    if (pct < 0) {
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString("--", LEFT, y);
    } else {
        tft.setTextColor(usageColor(pct), TFT_BLACK);
        char buf[8];
        snprintf(buf, sizeof(buf), "%d%%", (int)pct);
        tft.drawString(buf, LEFT, y);
    }

    tft.setTextDatum(MR_DATUM);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString(label, RIGHT, y);

    tft.unloadFont();

    // Progress bar
    int barY = y + 13;
    tft.fillRoundRect(LEFT, barY, BAR_W, BAR_H, BAR_H / 2, COL_BAR_BG);
    if (pct >= 0) {
        float clamped = pct > 100.0f ? 100.0f : pct;
        int fillW = (int)(clamped / 100.0f * BAR_W);
        if (fillW > 0)
            tft.fillRoundRect(LEFT, barY, fillW, BAR_H, BAR_H / 2, usageColor(clamped));
    }

    // Resets label
    if (resets.length() > 0) {
        tft.loadFont(NotoSans_Medium14);
        tft.setTextDatum(TL_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString(resets.c_str(), LEFT, barY + BAR_H + 4);
        tft.unloadFont();
    }
}

void updateCCUsage() {
    drawCCBlock(92, ccUsage.five_hour_pct, "5-HR",  ccUsage.five_hour_resets);
    drawCCBlock(144, ccUsage.seven_day_pct, "7-DAY", ccUsage.seven_day_resets);

    tft.fillRect(CX - 90, 199, 180, 16, TFT_BLACK);
    if (ccUsage.refreshed_ago.length() > 0) {
        tft.loadFont(NotoSans_Medium14);
        tft.setTextDatum(MC_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString(ccUsage.refreshed_ago.c_str(), CX, 207);
        tft.unloadFont();
    }
}

void drawCCUsage() {
    tft.fillScreen(TFT_BLACK);
    tft.pushImage(CX - CLAUDE_LOGO_W / 2, 46 - CLAUDE_LOGO_H / 2,
                  CLAUDE_LOGO_W, CLAUDE_LOGO_H, (uint16_t *)claude_logo);
    ccNeedsFullRedraw = false;
    updateCCUsage();
}

// --- WiFi ---

void initWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE, INADDR_NONE);
    WiFi.setHostname(hostname);
    WiFi.begin(ssid, password);
    tft.fillScreen(TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.loadFont(NotoSans_Medium14);
    tft.drawString("Connecting to", CX, CX - 20);
    tft.drawString(ssid, CX, CX + 20);
    tft.unloadFont();
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

void sendCommand(const char* path) {
    if (WiFi.status() != WL_CONNECTED) return;
    HTTPClient http;
    http.begin(String(serverUrl) + path);
    http.addHeader("X-API-Key", apiKey);
    int code = http.POST("");
    http.end();
    Serial.printf("sendCommand %s -> %d\n", path, code);
    if (code == 204) {
        lastPoll = millis() - POLL_INTERVAL_MS + 200;
    }
}

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
            if (hasArt)
                drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
            else
                drawStatus("Server unreachable");
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
            if (hasArt)
                drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
            else
                drawStatus("Server unreachable");
        }
        return;
    }

    bool wasFailedBefore = pollFailed;
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
        if (hasArt || track_changed || wasFailedBefore) drawIdle();
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

void fetchCCUsage() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/cc-usage");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code != 200) {
        Serial.printf("CC usage HTTP error: %d\n", code);
        http.end();
        ccNeedsFullRedraw = true;
        drawStatus("CC usage unavailable");
        return;
    }

    String payload = http.getString();
    http.end();

    JsonDocument doc;
    if (deserializeJson(doc, payload)) {
        Serial.println("CC usage JSON parse error");
        ccNeedsFullRedraw = true;
        drawStatus("CC usage unavailable");
        return;
    }

    JsonVariant fh = doc["five_hour"];
    if (fh.isNull() || fh["utilization"].isNull()) {
        ccUsage.five_hour_pct    = -1;
        ccUsage.five_hour_resets = "";
    } else {
        ccUsage.five_hour_pct    = fh["utilization"].as<float>();
        ccUsage.five_hour_resets = fh["resets_at"] | "";
    }

    JsonVariant sd = doc["seven_day"];
    if (sd.isNull() || sd["utilization"].isNull()) {
        ccUsage.seven_day_pct    = -1;
        ccUsage.seven_day_resets = "";
    } else {
        ccUsage.seven_day_pct    = sd["utilization"].as<float>();
        ccUsage.seven_day_resets = sd["resets_at"] | "";
    }

    ccUsage.refreshed_ago = doc["refreshed_ago"] | "";

    Serial.printf("CC usage: 5h=%.1f%% 7d=%.1f%%\n",
        ccUsage.five_hour_pct, ccUsage.seven_day_pct);
    if (ccNeedsFullRedraw) drawCCUsage(); else updateCCUsage();
}

// --- Arduino entry points ---

void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(0);
    initWiFi();
    drawStatus("Connecting to server...");
    btn.attachClick([]() { sendCommand("/v1/spotify/toggle"); });
    btn.attachDoubleClick([]() { sendCommand("/v1/spotify/next"); });
    btn.attachLongPressStart([]() { sendCommand("/v1/spotify/previous"); });
    btn2.attachClick([]() {
        activeScreen = (activeScreen == SPOTIFY) ? CC_USAGE : SPOTIFY;
        if (activeScreen == CC_USAGE) {
            drawCCUsage();
            fetchCCUsage();
            lastCCPoll = millis();
        } else {
            // Switching back to Spotify: the screen was wiped by CC usage.
            // Set a sentinel track_id so fetchNowPlaying() always sees a track
            // change — triggering art re-fetch if playing, or drawIdle() if not.
            hasArt = false;
            current.track_id = "\x01";
            drawStatus("Loading...");
            fetchNowPlaying();
            lastPoll = millis();
            lastTick = lastPoll;
        }
    });
}

void loop() {
    btn.tick();
    btn2.tick();
    unsigned long now = millis();

    if (activeScreen == SPOTIFY) {
        // End-of-song poll
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
    } else if (activeScreen == CC_USAGE) {
        if (now - lastCCPoll >= CC_POLL_INTERVAL_MS) {
            lastCCPoll = now;
            if (WiFi.status() == WL_CONNECTED)
                fetchCCUsage();
        }
    }
}
