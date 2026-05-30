#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>
#include <TJpg_Decoder.h>
#include <OneButtonTiny.h>
#include "NotoSans_Medium14.h"
#include "claude_logo.h"
#include <time.h>

const char *ssid     = WIFI_SSID;
const char *password = WIFI_PASSWORD;
const char *serverUrl = SERVER_URL;
const char *apiKey   = API_KEY;
const char *hostname = "esp32-dashboard";

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite clockSprite(&tft);
OneButtonTiny btn(19, false, false); // GPIO 19, active-high, no internal pull-up
OneButtonTiny btn2(21, false, false); // GPIO 21, active-high, no internal pull-up

#define NTP_OFFSET_HOURS  8.0f       // UTC+8; supports fractional e.g. -5.5, 5.75
#define NTP_SERVER1       "pool.ntp.org"
#define NTP_SERVER2       "time.google.com"
const unsigned long CLOCK_TICK_MS  = 40;

const uint16_t COL_GREY      = 0x52AA;
const uint16_t COL_BAR_BG    = 0x39C7; // white at 25% opacity on black
const uint16_t COL_BAR_FILL  = 0xE71C; // white at 90% opacity on black
const uint16_t COL_BAR_PLAY  = 0x1CC4; // Spotify green #1DB954 in RGB565
const uint16_t COL_BAR_ERROR = 0xF583; // orange #fab219
const uint16_t COL_RED       = 0xC9E7; // muted red #d03b3b
const unsigned long CC_POLL_INTERVAL_MS = 10000;
const unsigned long IDLE_TIMEOUT_MS    = 2UL * 60UL * 1000UL; // 2 minutes

enum Screen { CLOCK, SPOTIFY, CC_USAGE, RTSP };
Screen activeScreen = CLOCK;
unsigned long serverUnreachableSince = 0;
unsigned long lastClockTick = 0;
char clockDateBuf[16] = "";

struct CCUsage {
    float  five_hour_pct      = -1;
    float  five_hour_time_pct = -1;
    String five_hour_resets   = "";
    float  seven_day_pct      = -1;
    float  seven_day_time_pct = -1;
    String seven_day_resets   = "";
    String refreshed_ago      = "";
};
CCUsage ccUsage;
unsigned long lastCCPoll = 0;
bool ccNeedsFullRedraw = true;

const int CX    = 120;
const int BAR_W = 120;
const int BAR_X = (240 - BAR_W) / 2;
const int BAR_Y = 210;
const int BAR_H = 3;
const int BAR_PAD = 3; // black clearance above and below the bar

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
bool lyricsMode = false;
uint32_t nextLyricFetchAt = 0;
static volatile int rtspIndex       = 0;
static volatile int rtspStreamCount = 1;

static uint8_t           rtspBuf[2][32768];
static int               rtspBufLen[2]       = {0, 0};
static volatile int      rtspWriteIdx        = 0;
static volatile int      rtspReadIdx         = 0;
static SemaphoreHandle_t rtspFreeSem         = nullptr;
static SemaphoreHandle_t rtspReadySem        = nullptr;
static TaskHandle_t      rtspNetTaskHandle   = nullptr;
static volatile bool     rtspFetchError      = false;
static bool              rtspErrorShown      = false;

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
    drawStatus("No playback");
    hasArt = false;
}

void drawClockScreen() {
    tft.fillScreen(TFT_BLACK);
    clockSprite.setColorDepth(8);
    void *p = clockSprite.createSprite(240, 240);
    Serial.printf("Clock sprite: %s (free heap: %u, largest block: %u)\n",
        p ? "OK" : "FAILED",
        ESP.getFreeHeap(), heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT));
    clockDateBuf[0] = '\0';
}

void updateClockTime(bool forceDate) {
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    struct tm t;
    localtime_r(&tv.tv_sec, &t);
    if (t.tm_year < 100) {
        clockSprite.fillSprite(TFT_BLACK);
        clockSprite.setTextDatum(MC_DATUM);
        clockSprite.setTextColor(COL_GREY, TFT_BLACK);
        clockSprite.loadFont(NotoSans_Medium14);
        clockSprite.drawString("Syncing time...", CX, CX);
        clockSprite.unloadFont();
        clockSprite.pushSprite(0, 0);
        return;
    }

    if (forceDate) {
        const char* wdays[] = {"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};
        snprintf(clockDateBuf, sizeof(clockDateBuf), "%s %d",
                 wdays[t.tm_wday], t.tm_mday);
    }

    clockSprite.fillSprite(TFT_BLACK);

    const float outerR = 108.0f;
    const float innerR = 94.0f;
    for (int i = 0; i < 12; i++) {
        float angle = i * 30.0f * DEG_TO_RAD;
        float sinA = sinf(angle);
        float cosA = cosf(angle);
        float ox = CX + sinA * outerR;
        float oy = CX - cosA * outerR;
        float ix = CX + sinA * innerR;
        float iy = CX - cosA * innerR;
        clockSprite.drawWideLine(ix, iy, ox, oy, 2.0f, COL_GREY, TFT_BLACK);
    }

    if (clockDateBuf[0] != '\0') {
        clockSprite.setTextDatum(MC_DATUM);
        clockSprite.setTextColor(COL_GREY, TFT_BLACK);
        clockSprite.loadFont(NotoSans_Medium14);
        clockSprite.drawString(clockDateBuf, CX, 165);
        clockSprite.unloadFont();
    }

    float sec  = t.tm_sec + tv.tv_usec / 1000000.0f;
    float min  = t.tm_min + sec / 60.0f;
    float hour = (t.tm_hour % 12) + min / 60.0f;

    float secAngle  = sec  * 6.0f   * DEG_TO_RAD;
    float minAngle  = min  * 6.0f   * DEG_TO_RAD;
    float hourAngle = hour * 30.0f  * DEG_TO_RAD;

    float hx = CX + sinf(hourAngle) * 55.0f;
    float hy = CX - cosf(hourAngle) * 55.0f;
    clockSprite.drawWideLine(CX, CX, hx, hy, 4.0f, TFT_WHITE, TFT_BLACK);

    float mx = CX + sinf(minAngle) * 80.0f;
    float my = CX - cosf(minAngle) * 80.0f;
    clockSprite.drawWideLine(CX, CX, mx, my, 3.0f, TFT_WHITE, TFT_BLACK);

    float sx = CX + sinf(secAngle) * 90.0f;
    float sy = CX - cosf(secAngle) * 90.0f;
    float tx = CX - sinf(secAngle) * 15.0f;
    float ty = CX + cosf(secAngle) * 15.0f;
    clockSprite.drawWideLine(tx, ty, sx, sy, 2.0f, COL_RED, TFT_BLACK);

    clockSprite.fillSmoothCircle(CX, CX, 5, TFT_WHITE, TFT_BLACK);

    clockSprite.pushSprite(0, 0);
}


void rtspNetTask(void *) {
    HTTPClient http;
    String connectedUrl = "";

    for (;;) {
        xSemaphoreTake(rtspFreeSem, portMAX_DELAY);

        if (WiFi.status() != WL_CONNECTED) {
            if (connectedUrl.length() > 0) { http.end(); connectedUrl = ""; }
            xSemaphoreGive(rtspFreeSem);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        String url = String(serverUrl) + "/v1/rtsp/frame?index=" + String(rtspIndex);
        if (url != connectedUrl) {
            if (connectedUrl.length() > 0) http.end();
            http.begin(url);
            http.addHeader("X-API-Key", apiKey);
            const char *headerKeys[] = {"X-Stream-Count"};
            http.collectHeaders(headerKeys, 1);
            connectedUrl = url;
        }

        int code = http.GET();
        if (code != 200) {
            Serial.printf("RTSP HTTP error: %d\n", code);
            http.end();
            connectedUrl = "";
            if (serverUnreachableSince == 0) serverUnreachableSince = millis();
            rtspFetchError = true;
            xSemaphoreGive(rtspFreeSem);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        String countStr = http.header("X-Stream-Count");
        if (countStr.length() > 0) rtspStreamCount = countStr.toInt();

        int contentLength = http.getSize();
        if (contentLength <= 0 || contentLength > (int)sizeof(rtspBuf[0])) {
            Serial.printf("RTSP unexpected size: %d\n", contentLength);
            http.end();
            connectedUrl = "";
            xSemaphoreGive(rtspFreeSem);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        WiFiClient *stream = http.getStreamPtr();
        int received = 0;
        while (received < contentLength && stream->connected()) {
            int avail = stream->available();
            if (avail > 0) {
                int toRead = min(avail, contentLength - received);
                stream->readBytes(rtspBuf[rtspWriteIdx] + received, toRead);
                received += toRead;
            } else {
                taskYIELD();
            }
        }

        if (received != contentLength) {
            Serial.printf("RTSP incomplete: %d/%d\n", received, contentLength);
            http.end();
            connectedUrl = "";
            xSemaphoreGive(rtspFreeSem);
            continue;
        }

        rtspBufLen[rtspWriteIdx] = received;
        serverUnreachableSince = 0;
        rtspFetchError = false;
        rtspWriteIdx ^= 1;
        xSemaphoreGive(rtspReadySem);
    }
}

void drawProgressBar(uint32_t progress_ms, uint32_t duration_ms, bool is_playing) {
    tft.fillRect(0, BAR_Y - BAR_PAD, 240, BAR_H + 2 * BAR_PAD, TFT_BLACK);
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

uint16_t lerpRGB565(uint16_t c1, uint16_t c2, float t) {
    int r1 = (c1 >> 11) & 0x1F, r2 = (c2 >> 11) & 0x1F;
    int g1 = (c1 >>  5) & 0x3F, g2 = (c2 >>  5) & 0x3F;
    int b1 =  c1        & 0x1F, b2 =  c2        & 0x1F;
    int r = (int)(r1 + (r2 - r1) * t + 0.5f);
    int g = (int)(g1 + (g2 - g1) * t + 0.5f);
    int b = (int)(b1 + (b2 - b1) * t + 0.5f);
    return ((uint16_t)r << 11) | ((uint16_t)g << 5) | (uint16_t)b;
}

uint16_t usageColor(float pct) {
    if (pct >= 95.0f)  return COL_RED;
    if (pct >= 80.0f)  return lerpRGB565(COL_BAR_ERROR, COL_RED, (pct - 80.0f) / 15.0f);
    if (pct >  50.0f)  return lerpRGB565(COL_BAR_FILL, COL_BAR_ERROR, (pct - 50.0f) / 30.0f);
    return COL_BAR_FILL;
}

void drawCCBlock(int y, float pct, float time_pct, const char* label, const String& resets) {
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
    tft.fillRect(LEFT - 2, barY - 6, BAR_W + 4, 6, TFT_BLACK); // clear tick area above bar
    tft.fillRoundRect(LEFT, barY, BAR_W, BAR_H, BAR_H / 2, COL_BAR_BG);
    if (pct >= 0) {
        float clamped = pct > 100.0f ? 100.0f : pct;
        int fillW = (int)(clamped / 100.0f * BAR_W);
        if (fillW > 0)
            tft.fillRoundRect(LEFT, barY, fillW, BAR_H, BAR_H / 2, usageColor(clamped));
    }
    if (time_pct >= 0 && time_pct <= 100.0f) {
        int markerX = LEFT + (int)(time_pct / 100.0f * BAR_W);
        if (markerX >= LEFT && markerX < LEFT + BAR_W)
            tft.fillTriangle(markerX - 2, barY - 6, markerX + 2, barY - 6, markerX, barY - 3, TFT_WHITE);
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
    drawCCBlock(92,  ccUsage.five_hour_pct, ccUsage.five_hour_time_pct, "5-HR",  ccUsage.five_hour_resets);
    drawCCBlock(144, ccUsage.seven_day_pct, ccUsage.seven_day_time_pct, "7-DAY", ccUsage.seven_day_resets);

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
    tft.pushImage(CX - CLAUDE_LOGO_W / 2, 52 - CLAUDE_LOGO_H / 2,
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
    configTime((long)(NTP_OFFSET_HOURS * 3600), 0, NTP_SERVER1, NTP_SERVER2);
    Serial.println("NTP sync started");
    {
        struct tm tmp;
        int tries = 0;
        while (!getLocalTime(&tmp, 1000) && tries++ < 10)
            Serial.print(".");
        Serial.println(getLocalTime(&tmp, 0) ? "\nNTP synced" : "\nNTP timeout");
    }
}

// --- Album art streaming ---

static bool skipBarRows = false;

bool tft_output(int16_t x, int16_t y, uint16_t w, uint16_t h, uint16_t *bitmap) {
    if (skipBarRows && y + h > BAR_Y - BAR_PAD && y < BAR_Y + BAR_H + BAR_PAD) {
        // Render the portion above the cleared band
        int16_t above_h = (BAR_Y - BAR_PAD) - y;
        if (above_h > 0)
            tft.pushImage(x, y, w, above_h, bitmap);
        // Render the portion below the cleared band
        int16_t below_y = BAR_Y + BAR_H + BAR_PAD;
        int16_t below_h = (y + h) - below_y;
        if (below_h > 0)
            tft.pushImage(x, below_y, w, below_h, bitmap + (below_y - y) * w);
    } else {
        tft.pushImage(x, y, w, h, bitmap);
    }
    return true;
}

bool fetchAlbumArt() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/spotify/now-playing/art/jpeg");
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
    if (contentLength <= 0 || contentLength > 100000) {
        Serial.printf("Art unexpected size: %d\n", contentLength);
        http.end();
        return false;
    }

    uint8_t *buf = (uint8_t *)malloc(contentLength);
    if (!buf) {
        Serial.println("Art malloc failed");
        http.end();
        return false;
    }

    WiFiClient *stream = http.getStreamPtr();
    int received = 0;
    while (received < contentLength && stream->connected()) {
        int avail = stream->available();
        if (avail > 0) {
            int toRead = min(avail, contentLength - received);
            stream->readBytes(buf + received, toRead);
            received += toRead;
        } else {
            delay(1);
        }
    }
    http.end();

    if (received != contentLength) {
        Serial.printf("Art incomplete: %d/%d bytes\n", received, contentLength);
        free(buf);
        return false;
    }

    tft.startWrite();
    tft.setSwapBytes(true);
    TJpgDec.drawJpg(0, 0, buf, contentLength);
    tft.setSwapBytes(false);
    tft.endWrite();

    free(buf);
    Serial.println("Album art loaded");
    hasArt = true;
    return true;
}

void fetchLyricsFrame() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/spotify/lyrics/frame");
    http.addHeader("X-API-Key", apiKey);
    const char *headerKeys[] = {"X-Next-Lyric-Ms"};
    http.collectHeaders(headerKeys, 1);

    int code = http.GET();
    if (code == 204) {
        http.end();
        nextLyricFetchAt = millis() + 1000;
        return;
    }
    if (code == 404) {
        // Server has no lyrics for this track; fall back to album art
        http.end();
        lyricsMode = false;
        fetchAlbumArt();
        return;
    }
    if (code != 200) {
        Serial.printf("Lyrics frame HTTP error: %d\n", code);
        http.end();
        nextLyricFetchAt = millis() + 1000;
        return;
    }

    String nextMsStr = http.header("X-Next-Lyric-Ms");
    uint32_t nextMs = nextMsStr.length() > 0 ? (uint32_t)nextMsStr.toInt() : 1000;
    if (nextMs < 500) nextMs = 500;

    int contentLength = http.getSize();
    if (contentLength <= 0 || contentLength > 100000) {
        Serial.printf("Lyrics frame unexpected size: %d\n", contentLength);
        http.end();
        nextLyricFetchAt = millis() + 1000;
        return;
    }

    uint8_t *buf = (uint8_t *)malloc(contentLength);
    if (!buf) {
        Serial.println("Lyrics frame malloc failed");
        http.end();
        nextLyricFetchAt = millis() + 1000;
        return;
    }

    WiFiClient *stream = http.getStreamPtr();
    int received = 0;
    while (received < contentLength && stream->connected()) {
        int avail = stream->available();
        if (avail > 0) {
            int toRead = min(avail, contentLength - received);
            stream->readBytes(buf + received, toRead);
            received += toRead;
        } else {
            delay(1);
        }
    }
    http.end();

    if (received != contentLength) {
        Serial.printf("Lyrics frame incomplete: %d/%d\n", received, contentLength);
        free(buf);
        nextLyricFetchAt = millis() + 1000;
        return;
    }

    tft.startWrite();
    tft.setSwapBytes(true);
    skipBarRows = true;
    TJpgDec.drawJpg(0, 0, buf, contentLength);
    skipBarRows = false;
    tft.setSwapBytes(false);
    tft.endWrite();
    free(buf);

    uint32_t display_progress = current.is_playing
        ? current.progress_ms + (uint32_t)(millis() - lastFetchMs)
        : current.progress_ms;
    drawProgressBar(display_progress, current.duration_ms, current.is_playing);

    nextLyricFetchAt = millis() + nextMs;
    Serial.printf("Lyrics frame ok, next in %u ms\n", nextMs);
}

// --- Networking ---

void sendCommand(const char* path) {
    if (WiFi.status() != WL_CONNECTED) return;
    HTTPClient http;
    http.begin(String(serverUrl) + path);
    http.addHeader("X-API-Key", apiKey);
    int code = http.POST("");
    if (code == 204) {
        lastPoll = millis() - POLL_INTERVAL_MS + 200;
    } else {
        Serial.printf("sendCommand %s -> %d: %s\n", path, code, http.getString().c_str());
    }
    http.end();
}

void fetchNowPlaying() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/spotify/now-playing");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code != 200) {
        Serial.printf("HTTP error: %d\n", code);
        http.end();
        if (serverUnreachableSince == 0) serverUnreachableSince = millis();
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
        if (serverUnreachableSince == 0) serverUnreachableSince = millis();
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
    serverUnreachableSince = 0;

    TrackState next;
    next.is_playing  = doc["is_playing"]  | false;
    next.track_id    = doc["track_id"]    | "";
    next.progress_ms = doc["progress_ms"] | 0;
    next.duration_ms = doc["duration_ms"] | 0;

    bool track_changed = (next.track_id != current.track_id);
    bool play_state_changed = (next.is_playing != current.is_playing);

    // Detect seek: progress differs from local estimate by more than 3 s
    bool seeked = false;
    if (!track_changed && current.is_playing) {
        uint32_t estimated = current.progress_ms + (uint32_t)(millis() - lastFetchMs);
        int32_t drift = (int32_t)next.progress_ms - (int32_t)estimated;
        if (drift < -3000 || drift > 3000) seeked = true;
    }

    current = next;
    lastFetchMs = millis();

    bool has_lyrics = doc["has_lyrics"] | false;

    if (current.track_id.length() == 0) {
        lyricsMode = false;
        if (hasArt || track_changed || wasFailedBefore) drawIdle();
        return;
    }

    if (track_changed) {
        lyricsMode = has_lyrics;
        nextLyricFetchAt = 0;
        if (!lyricsMode) {
            fetchAlbumArt();
        }
        drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
    } else if (play_state_changed || seeked) {
        if (lyricsMode) nextLyricFetchAt = 0;
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
        if (serverUnreachableSince == 0) serverUnreachableSince = millis();
        ccNeedsFullRedraw = true;
        drawStatus("CC usage unavailable");
        return;
    }

    String payload = http.getString();
    http.end();

    JsonDocument doc;
    if (deserializeJson(doc, payload)) {
        Serial.println("CC usage JSON parse error");
        if (serverUnreachableSince == 0) serverUnreachableSince = millis();
        ccNeedsFullRedraw = true;
        drawStatus("CC usage unavailable");
        return;
    }

    JsonVariant fh = doc["five_hour"];
    if (fh.isNull() || fh["utilization"].isNull()) {
        ccUsage.five_hour_pct      = -1;
        ccUsage.five_hour_time_pct = -1;
        ccUsage.five_hour_resets   = "";
    } else {
        ccUsage.five_hour_pct      = fh["utilization"].as<float>();
        ccUsage.five_hour_time_pct = fh["time_pct"].isNull() ? -1.0f : fh["time_pct"].as<float>();
        ccUsage.five_hour_resets   = fh["resets_at"] | "";
    }

    JsonVariant sd = doc["seven_day"];
    if (sd.isNull() || sd["utilization"].isNull()) {
        ccUsage.seven_day_pct      = -1;
        ccUsage.seven_day_time_pct = -1;
        ccUsage.seven_day_resets   = "";
    } else {
        ccUsage.seven_day_pct      = sd["utilization"].as<float>();
        ccUsage.seven_day_time_pct = sd["time_pct"].isNull() ? -1.0f : sd["time_pct"].as<float>();
        ccUsage.seven_day_resets   = sd["resets_at"] | "";
    }

    ccUsage.refreshed_ago = doc["refreshed_ago"] | "";

    serverUnreachableSince = 0;
    Serial.printf("CC usage: 5h=%.1f%% 7d=%.1f%%\n",
        ccUsage.five_hour_pct, ccUsage.seven_day_pct);
    if (ccNeedsFullRedraw) drawCCUsage(); else updateCCUsage();
}

// --- Arduino entry points ---

void activateScreen(Screen s) {
    if (activeScreen == RTSP && s != RTSP && rtspNetTaskHandle != nullptr)
        vTaskSuspend(rtspNetTaskHandle);
    if (activeScreen == CLOCK && s != CLOCK)
        clockSprite.deleteSprite();
    activeScreen = s;
    serverUnreachableSince = 0;
    pollFailed = false;
    if (s == CLOCK) {
        lastClockTick = millis();
        drawClockScreen();
        updateClockTime(true);
    } else if (s == CC_USAGE) {
        ccNeedsFullRedraw = true;
        drawCCUsage();
        fetchCCUsage();
        lastCCPoll = millis();
    } else if (s == SPOTIFY) {
        lyricsMode = false;
        nextLyricFetchAt = 0;
        hasArt = false;
        current.track_id = "\x01";
        drawStatus("Loading...");
        fetchNowPlaying();
        lastPoll = millis();
        lastTick = lastPoll;
    } else if (s == RTSP) {
        // drain any stale semaphore counts, then reset to initial state
        while (xSemaphoreTake(rtspReadySem, 0) == pdTRUE) {}
        while (xSemaphoreTake(rtspFreeSem, 0) == pdTRUE) {}
        xSemaphoreGive(rtspFreeSem);
        xSemaphoreGive(rtspFreeSem);
        rtspWriteIdx   = 0;
        rtspReadIdx    = 0;
        rtspFetchError = false;
        rtspErrorShown = false;
        drawStatus("Loading...");
        vTaskResume(rtspNetTaskHandle);
    }
}


void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(0);
    TJpgDec.setJpgScale(1);
    TJpgDec.setCallback(tft_output);
    initWiFi();
    rtspFreeSem  = xSemaphoreCreateCounting(2, 2);
    rtspReadySem = xSemaphoreCreateCounting(2, 0);
    xTaskCreatePinnedToCore(rtspNetTask, "rtspNet", 8192, nullptr, 1, &rtspNetTaskHandle, 0);
    vTaskSuspend(rtspNetTaskHandle);
    activateScreen(CLOCK);
    btn.attachClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex + 1) % rtspStreamCount;
            return;
        }
        sendCommand("/v1/spotify/toggle");
    });
    btn.attachDoubleClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount;
            return;
        }
        sendCommand("/v1/spotify/next");
    });
    btn.attachLongPressStart([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) return;
        sendCommand("/v1/spotify/previous");
    });
    btn2.attachClick([]() {
        // Forward cycle: CLOCK -> CC_USAGE -> RTSP -> SPOTIFY -> CLOCK
        Screen next;
        if      (activeScreen == CLOCK)    next = CC_USAGE;
        else if (activeScreen == CC_USAGE) next = RTSP;
        else if (activeScreen == RTSP)     next = SPOTIFY;
        else                               next = CLOCK;
        activateScreen(next);
    });
    btn2.attachDoubleClick([]() {
        // Backward cycle: CLOCK -> SPOTIFY -> RTSP -> CC_USAGE -> CLOCK
        Screen target;
        if      (activeScreen == CLOCK)    target = SPOTIFY;
        else if (activeScreen == SPOTIFY)  target = RTSP;
        else if (activeScreen == RTSP)     target = CC_USAGE;
        else                               target = CLOCK;
        activateScreen(target);
    });
    btn2.attachLongPressStart([]() {
        ESP.restart();
    });
}

void loop() {
    btn.tick();
    btn2.tick();
    unsigned long now = millis();

    if (serverUnreachableSince > 0 && activeScreen != CLOCK
            && (now - serverUnreachableSince) >= IDLE_TIMEOUT_MS) {
        activateScreen(CLOCK);
    }

    if (activeScreen == SPOTIFY) {
        // Lyrics frame fetch
        if (lyricsMode && now >= nextLyricFetchAt && WiFi.status() == WL_CONNECTED) {
            fetchLyricsFrame();
        }

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
    } else if (activeScreen == CLOCK) {
        if (now - lastClockTick >= CLOCK_TICK_MS) {
            lastClockTick = now;
            struct tm t;
            bool gotTime = getLocalTime(&t, 0);
            bool midnight = gotTime && t.tm_hour == 0 && t.tm_min == 0 && t.tm_sec == 0;
            updateClockTime(midnight);
        }
    } else if (activeScreen == CC_USAGE) {
        if (now - lastCCPoll >= CC_POLL_INTERVAL_MS) {
            lastCCPoll = now;
            if (WiFi.status() == WL_CONNECTED)
                fetchCCUsage();
        }
    } else if (activeScreen == RTSP) {
        if (xSemaphoreTake(rtspReadySem, 0) == pdTRUE) {
            int idx = rtspReadIdx;
            tft.startWrite();
            tft.setSwapBytes(true);
            TJpgDec.drawJpg(0, 0, rtspBuf[idx], rtspBufLen[idx]);
            tft.setSwapBytes(false);
            tft.endWrite();
            rtspReadIdx ^= 1;
            xSemaphoreGive(rtspFreeSem);
            rtspErrorShown = false;
        } else if (rtspFetchError && !rtspErrorShown) {
            drawStatus("Stream unavailable");
            rtspErrorShown = true;
        }
    }
}
