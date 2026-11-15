"""Modelli di progetto (senza GUI). I pin sono segnaposto da verificare sulla propria scheda: nessun pin è dato per certo."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import pio_core as pc


@dataclass(frozen=True)
class Template:
    id: str
    nome: str
    descr: str
    main: str
    lib_deps: tuple[str, ...] = ()
    build_flags: tuple[str, ...] = ()
    extra_files: tuple[tuple[str, str], ...] = ()
    needs_wifi: bool = False
    chip_only: str = ""          # "" = qualunque ESP32; altrimenti prefisso dell'id scheda richiesto


BLINK = Template("blink", "Lampeggio + messaggio seriale", "Il progetto minimo (LED integrato e seriale).", "")

_WEB = '''#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>

// Server HTTP con API JSON: GET /info e GET /state, POST /out?n=0&v=1
// Modifica le credenziali e i pin PRIMA di caricare.
static const char *WIFI_SSID = "NOME_RETE";
static const char *WIFI_PASS = "PASSWORD";
static const int OUT_PINS[] = {-1, -1, -1};          // DA IMPOSTARE: pin delle uscite (-1 = non usata)
static const int N_OUT = sizeof(OUT_PINS) / sizeof(OUT_PINS[0]);
static const bool ACTIVE_HIGH = true;
static bool outState[N_OUT];

WebServer server(80);

static void setOut(int n, bool on) {
  if (n < 0 || n >= N_OUT) return;
  outState[n] = on;
  if (OUT_PINS[n] >= 0) digitalWrite(OUT_PINS[n], (on == ACTIVE_HIGH) ? HIGH : LOW);
}

static void sendJson(int code, const String &body) { server.send(code, "application/json", body); }

static void handleInfo() {
  String s = "{\\"chip\\":\\"" + String(ESP.getChipModel()) + "\\",\\"ip\\":\\"" + WiFi.localIP().toString() +
             "\\",\\"rssi\\":" + String(WiFi.RSSI()) + ",\\"uptime_s\\":" + String(millis() / 1000) + "}";
  sendJson(200, s);
}

static void handleState() {
  String s = "{\\"out\\":[";
  for (int i = 0; i < N_OUT; i++) s += String(outState[i] ? "true" : "false") + (i + 1 < N_OUT ? "," : "");
  sendJson(200, s + "]}");
}

static void handleOut() {
  if (!server.hasArg("n") || !server.hasArg("v")) { sendJson(400, "{\\"error\\":\\"servono n e v\\"}"); return; }
  int n = server.arg("n").toInt();
  if (n < 0 || n >= N_OUT) { sendJson(400, "{\\"error\\":\\"n fuori intervallo\\"}"); return; }
  setOut(n, server.arg("v") == "1");
  handleState();
}

static void wifiConnect() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(200);
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < N_OUT; i++) if (OUT_PINS[i] >= 0) { pinMode(OUT_PINS[i], OUTPUT); setOut(i, false); }
  wifiConnect();
  Serial.printf("IP: %s\\n", WiFi.localIP().toString().c_str());
  server.on("/info", HTTP_GET, handleInfo);
  server.on("/state", HTTP_GET, handleState);
  server.on("/out", HTTP_POST, handleOut);
  server.begin();
}

void loop() {
  wifiConnect();                       // riconnessione automatica
  server.handleClient();
}
'''

_OTA = '''#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoOTA.h>

// Base per aggiornare il firmware via rete: poi usa «Carica via OTA…» dell'IDE.
static const char *WIFI_SSID = "NOME_RETE";
static const char *WIFI_PASS = "PASSWORD";
static const char *OTA_NAME = "esp32-tornado";
static const char *OTA_PASS = "";                    // se la imposti, usala anche nel dialogo OTA dell'IDE

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(200);
  ArduinoOTA.setHostname(OTA_NAME);
  if (OTA_PASS[0]) ArduinoOTA.setPassword(OTA_PASS);
  ArduinoOTA.onStart([]() { Serial.println("OTA: inizio"); });
  ArduinoOTA.onEnd([]() { Serial.println("OTA: fine"); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("OTA errore %u\\n", e); });
  ArduinoOTA.begin();
  Serial.printf("Pronto: %s.local  %s\\n", OTA_NAME, WiFi.localIP().toString().c_str());
}

void loop() {
  ArduinoOTA.handle();
  // ... il tuo codice, senza delay() lunghi ...
}
'''

_TG = '''#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <UniversalTelegramBot.h>

// Invia un messaggio Telegram all'avvio e risponde a /stato. Crea il bot con @BotFather.
static const char *WIFI_SSID = "NOME_RETE";
static const char *WIFI_PASS = "PASSWORD";
static const char *BOT_TOKEN = "TOKEN_DEL_BOT";
static const char *CHAT_ID = "ID_CHAT";                // solo questa chat può comandare la scheda

WiFiClientSecure client;
UniversalTelegramBot bot(BOT_TOKEN, client);
static uint32_t lastPoll = 0;

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(300);
  client.setInsecure();                                 // niente verifica del certificato: accettabile per un test, non per produzione
  bot.sendMessage(CHAT_ID, "Scheda avviata", "");
}

void loop() {
  if (millis() - lastPoll > 2000) {
    lastPoll = millis();
    int n = bot.getUpdates(bot.last_message_received + 1);
    while (n) {
      for (int i = 0; i < n; i++) {
        if (String(bot.messages[i].chat_id) != CHAT_ID) continue;
        if (bot.messages[i].text == "/stato")
          bot.sendMessage(CHAT_ID, "Attiva da " + String(millis() / 1000) + " s, RSSI " + String(WiFi.RSSI()) + " dBm", "");
      }
      n = bot.getUpdates(bot.last_message_received + 1);
    }
  }
}
'''

_PLOT = '''#include <Arduino.h>
#include <math.h>

// Stampa due valori per riga ("seno,coseno"): aprili nel pannello «Grafico» dell'IDE.
void setup() { Serial.begin(115200); }

void loop() {
  static float t = 0;
  Serial.printf("seno:%.3f coseno:%.3f\\n", sinf(t), cosf(t));
  t += 0.1f;
  delay(50);
}
'''

_CYD = '''#include <Arduino.h>
#include <TFT_eSPI.h>

// Scheda "CYD" (ESP32-2432S028) con ILI9341: i pin nei build_flags vanno VERIFICATI sulla propria revisione.
TFT_eSPI tft;
void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(1);
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_ORANGE, TFT_BLACK);
  tft.drawString("Tornado Laboratori", 10, 10, 4);
}
void loop() {
  static uint32_t n = 0;
  tft.drawNumber(n++, 10, 60, 4);
  delay(500);
}
'''

TEMPLATES: tuple[Template, ...] = (
    BLINK,
    Template("web_json", "Server web con API JSON e uscite", "Wi-Fi, /info /state /out; pin da impostare.", _WEB, needs_wifi=True),
    Template("ota", "Aggiornamento OTA (ArduinoOTA)", "Base per caricare via rete.", _OTA, needs_wifi=True),
    Template("telegram", "Notifiche Telegram", "Messaggio all'avvio e comando /stato.", _TG,
             lib_deps=("witnessmenow/UniversalTelegramBot@^1.3.0", "bblanchon/ArduinoJson@^6.21.5"), needs_wifi=True),
    Template("serial_plot", "Dati seriali per il grafico", "Due serie sinusoidali da vedere nel grafico.", _PLOT),
    Template("cyd", "Display CYD ILI9341 (TFT_eSPI)", "Scheda ESP32-2432S028: verifica i pin.", _CYD, lib_deps=("bodmer/TFT_eSPI@^2.5.43",),
             build_flags=("-DUSER_SETUP_LOADED=1", "-DILI9341_2_DRIVER=1", "-DTFT_WIDTH=240", "-DTFT_HEIGHT=320", "-DTFT_MISO=12",
                          "-DTFT_MOSI=13", "-DTFT_SCLK=14", "-DTFT_CS=15", "-DTFT_DC=2", "-DTFT_RST=-1", "-DTFT_BL=21",
                          "-DLOAD_GLCD=1", "-DLOAD_FONT2=1", "-DLOAD_FONT4=1", "-DSPI_FREQUENCY=40000000")),
)


def by_id(tid: str) -> Template:
    for t in TEMPLATES:
        if t.id == tid:
            return t
    raise KeyError(tid)


def apply_template(root: str, tid: str, env: str) -> None:
    """Sovrascrive src/main.cpp e completa platformio.ini (lib_deps, build_flags) per il modello scelto."""
    t = by_id(tid)
    if not t.main:
        return
    (Path(root) / "src" / "main.cpp").write_text(t.main, encoding="utf-8", newline="\n")
    for rel, body in t.extra_files:
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
    ini = Path(root) / pc.INI_NAME
    text = ini.read_text(encoding="utf-8")
    sec = f"env:{env}"
    if t.lib_deps:
        text = pc.set_list(text, sec, "lib_deps", list(t.lib_deps))
    if t.build_flags:
        text = pc.set_list(text, sec, "build_flags", list(t.build_flags))
    ini.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")
