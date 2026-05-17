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

// Color constants (RGB565)
const uint16_t COL_SPOTIFY_GREEN = 0x1DCA; // #1DB954
const uint16_t COL_GREY          = 0x52AA; // #555555
const uint16_t COL_DARK_GREY     = 0x2104; // #222222
const uint16_t COL_PAUSED_ART    = 0x3186; // #303030
const uint16_t COL_DIM_WHITE     = 0xAD55; // ~#AAAAAA

// Display geometry
const int CX      = 120;
const int ART_X   = 72;   // (240 - 96) / 2
const int ART_Y   = 36;
const int ART_SIZE = 96;

// Progress bar (sits at bottom of visible circle area)
const int BAR_X = 40;
const int BAR_Y = 208;
const int BAR_W = 160;
const int BAR_H = 3;

// Accent color palette for art placeholder
const uint16_t ACCENT_COLORS[] = {
    0x035F, // deep blue
    0x780F, // purple
    0xD340, // orange
    0x07E4, // teal green
    0xB882, // muted red
};
const int ACCENT_COUNT = 5;

struct TrackState {
    bool     is_playing  = false;
    bool     has_track   = false;
    String   track       = "";
    String   artist      = "";
    uint32_t progress_ms = 0;
    uint32_t duration_ms = 0;
};

TrackState current;

const unsigned long POLL_INTERVAL_MS = 5000;
const unsigned long TICK_INTERVAL_MS = 250;
unsigned long lastPoll    = 0;
unsigned long lastTick    = 0;
unsigned long lastFetchMs = 0;

// --- Helpers ---

String msToTime(uint32_t ms) {
    uint32_t secs = ms / 1000;
    uint32_t m    = secs / 60;
    uint32_t s    = secs % 60;
    return String(m) + ":" + (s < 10 ? "0" : "") + String(s);
}

uint8_t accentIndex(const String &track) {
    uint32_t h = 0;
    for (char c : track) h += (uint8_t)c;
    return h % ACCENT_COUNT;
}

String truncate(const String &text, uint8_t font, int maxPx) {
    if (tft.textWidth(text, font) <= maxPx) return text;
    String t = text;
    while (t.length() > 0 && tft.textWidth(t + "...", font) > maxPx)
        t = t.substring(0, t.length() - 1);
    return t + "...";
}

// --- WiFi ---

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

// --- Display ---

void drawProgressBar(uint32_t progress_ms, uint32_t duration_ms, bool is_playing) {
    tft.fillRect(BAR_X, BAR_Y, BAR_W, BAR_H, COL_DARK_GREY);
    if (duration_ms == 0) return;
    int fillW = (int)((float)progress_ms / duration_ms * BAR_W);
    if (fillW > BAR_W) fillW = BAR_W;
    if (fillW > 0)
        tft.fillRect(BAR_X, BAR_Y, fillW, BAR_H, is_playing ? COL_SPOTIFY_GREEN : COL_GREY);
}

void drawTick() {
    if (!current.is_playing || current.duration_ms == 0) return;
    uint32_t estimated = current.progress_ms + (uint32_t)(millis() - lastFetchMs);
    if (estimated > current.duration_ms) estimated = current.duration_ms;

    drawProgressBar(estimated, current.duration_ms, true);

    tft.setTextDatum(TC_DATUM);
    tft.setTextFont(1);
    tft.setTextColor(COL_DARK_GREY, TFT_BLACK);
    tft.drawString(msToTime(estimated) + " / " + msToTime(current.duration_ms), CX, 196);
}

void drawNowPlaying(const TrackState &state) {
    tft.fillScreen(TFT_BLACK);

    // Album art placeholder
    uint16_t artCol;
    if (!state.has_track) {
        artCol = 0x1082;
    } else if (!state.is_playing) {
        artCol = COL_PAUSED_ART;
    } else {
        artCol = ACCENT_COLORS[accentIndex(state.track)];
    }
    tft.fillRoundRect(ART_X, ART_Y, ART_SIZE, ART_SIZE, 8, artCol);

    tft.setTextDatum(TC_DATUM);

    if (!state.has_track) {
        tft.setTextFont(2);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString("Not playing", CX, 152);
        return;
    }

    uint16_t trackCol = state.is_playing ? TFT_WHITE : COL_DIM_WHITE;

    tft.setTextFont(2);
    tft.setTextColor(trackCol, TFT_BLACK);
    tft.drawString(truncate(state.track, 2, 180), CX, 148);

    tft.setTextFont(1);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString(truncate(state.artist, 1, 180), CX, 168);

    tft.setTextFont(1);
    tft.setTextColor(COL_DARK_GREY, TFT_BLACK);
    tft.drawString(msToTime(state.progress_ms) + " / " + msToTime(state.duration_ms), CX, 196);

    drawProgressBar(state.progress_ms, state.duration_ms, state.is_playing);
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
        return;
    }

    String payload = http.getString();
    http.end();

    JsonDocument doc;
    if (deserializeJson(doc, payload)) {
        Serial.println("JSON parse error");
        return;
    }

    TrackState next;
    next.is_playing  = doc["is_playing"]  | false;
    next.track       = doc["track"]       | "";
    next.artist      = doc["artist"]      | "";
    next.progress_ms = doc["progress_ms"] | 0;
    next.duration_ms = doc["duration_ms"] | 0;
    next.has_track   = next.track.length() > 0;

    bool identity_changed = (next.track != current.track) ||
                            (next.is_playing != current.is_playing);
    current = next;
    lastFetchMs = millis();

    if (identity_changed)
        drawNowPlaying(current);

    if (current.has_track) {
        Serial.printf("%s - %s  [%s / %s]  %s\n",
            current.artist.c_str(),
            current.track.c_str(),
            msToTime(current.progress_ms).c_str(),
            msToTime(current.duration_ms).c_str(),
            current.is_playing ? "playing" : "paused");
    } else {
        Serial.println("Not playing");
    }
}

// --- Arduino entry points ---

void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(0);
    drawNowPlaying(current);
    initWiFi();
}

void loop() {
    unsigned long now = millis();
    if (now - lastPoll >= POLL_INTERVAL_MS) {
        lastPoll = now;
        if (WiFi.status() == WL_CONNECTED) {
            fetchNowPlaying();
            lastTick = now;
        } else {
            Serial.println("WiFi disconnected, reconnecting...");
            initWiFi();
        }
    }

    if (current.is_playing && (now - lastTick >= TICK_INTERVAL_MS)) {
        lastTick = now;
        drawTick();
    }
}
