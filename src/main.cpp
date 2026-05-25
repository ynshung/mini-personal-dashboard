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
OneButtonTiny btn(19, false, false); // GPIO 19, active-high, no internal pull-up
OneButtonTiny btn2(21, false, false); // GPIO 21, active-high, no internal pull-up

#define NTP_OFFSET_HOURS  8.0f       // UTC+8; supports fractional e.g. -5.5, 5.75
#define NTP_SERVER1       "pool.ntp.org"
#define NTP_SERVER2       "time.google.com"
const unsigned long CLOCK_TICK_MS  = 1000;
const unsigned long CLOCK_PING_MS  = 60000UL;

const uint16_t COL_GREY      = 0x52AA;
const uint16_t COL_BAR_BG    = 0x39C7; // white at 25% opacity on black
const uint16_t COL_BAR_FILL  = 0xE71C; // white at 90% opacity on black
const uint16_t COL_BAR_PLAY  = 0x1CC4; // Spotify green #1DB954 in RGB565
const uint16_t COL_BAR_ERROR = 0xF583; // orange #fab219
const uint16_t COL_RED       = 0xC9E7; // muted red #d03b3b
const unsigned long CC_POLL_INTERVAL_MS = 10000;
const unsigned long IDLE_TIMEOUT_MS    = 10UL * 60UL * 1000UL; // 10 minutes

enum Screen { CLOCK, SPOTIFY, CC_USAGE, RTSP };
Screen activeScreen = CLOCK;
Screen prevScreen   = CC_USAGE;
unsigned long serverUnreachableSince = 0;
bool clockFromIdle = false;
unsigned long lastClockTick = 0;
unsigned long lastClockPing = 0;

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
}

void updateClockTime(bool forceDate) {
    struct tm t;
    if (!getLocalTime(&t, 100)) {
        tft.setTextDatum(MC_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.loadFont(NotoSans_Medium14);
        tft.drawString("Syncing time...", CX, CX);
        tft.unloadFont();
        return;
    }

    if (forceDate) {
        const char* weekdays[] = {"Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"};
        tft.fillRect(0, 65, 240, 20, TFT_BLACK);
        tft.setTextDatum(MC_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.loadFont(NotoSans_Medium14);
        tft.drawString(weekdays[t.tm_wday], CX, 75);

        const char* months[] = {"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
        char dateBuf[16];
        snprintf(dateBuf, sizeof(dateBuf), "%d %s %d",
                 t.tm_mday, months[t.tm_mon], t.tm_year + 1900);
        tft.fillRect(0, 92, 240, 20, TFT_BLACK);
        tft.drawString(dateBuf, CX, 102);
        tft.unloadFont();
    }

    tft.fillRect(0, 118, 240, 20, TFT_BLACK);
    char timeBuf[12];
    snprintf(timeBuf, sizeof(timeBuf), "%02d:%02d:%02d",
             t.tm_hour, t.tm_min, t.tm_sec);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.loadFont(NotoSans_Medium14);
    tft.drawString(timeBuf, CX, 128);
    tft.unloadFont();
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

bool tft_output(int16_t x, int16_t y, uint16_t w, uint16_t h, uint16_t *bitmap) {
    tft.pushImage(x, y, w, h, bitmap);
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
    activeScreen = s;
    serverUnreachableSince = 0;
    pollFailed = false;
    if (s == CLOCK) {
        lastClockTick = millis();
        lastClockPing = millis();
        drawClockScreen();
        updateClockTime(true);
    } else if (s == CC_USAGE) {
        ccNeedsFullRedraw = true;
        drawCCUsage();
        fetchCCUsage();
        lastCCPoll = millis();
    } else if (s == SPOTIFY) {
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
        clockFromIdle = false;
        activateScreen(next);
    });
    btn2.attachDoubleClick([]() {
        // Backward cycle: CLOCK -> SPOTIFY -> RTSP -> CC_USAGE -> CLOCK
        Screen target;
        if      (activeScreen == CLOCK)    target = SPOTIFY;
        else if (activeScreen == SPOTIFY)  target = RTSP;
        else if (activeScreen == RTSP)     target = CC_USAGE;
        else                               target = CLOCK;
        clockFromIdle = false;
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
        prevScreen = activeScreen;
        clockFromIdle = true;
        activateScreen(CLOCK);
    }

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
    } else if (activeScreen == CLOCK) {
        if (now - lastClockTick >= CLOCK_TICK_MS) {
            lastClockTick = now;
            struct tm t;
            bool gotTime = getLocalTime(&t, 0);
            bool midnight = gotTime && t.tm_hour == 0 && t.tm_min == 0 && t.tm_sec == 0;
            updateClockTime(midnight);
        }
        if (clockFromIdle && WiFi.status() == WL_CONNECTED
                && (now - lastClockPing) >= CLOCK_PING_MS) {
            lastClockPing = now;
            HTTPClient http;
            http.begin(String(serverUrl) + "/v1/ping");
            http.addHeader("X-API-Key", apiKey);
            int code = http.GET();
            http.end();
            if (code == 200) {
                clockFromIdle = false;
                serverUnreachableSince = 0;
                activateScreen(prevScreen);
            }
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
