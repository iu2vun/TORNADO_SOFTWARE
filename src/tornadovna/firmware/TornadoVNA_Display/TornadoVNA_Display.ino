/*
 * TornadoVNA Display  -  Waveshare ESP32-S3-Touch-LCD-4.3 (800x480, RGB, GT911, CH422G)
 * Tornado Laboratori
 *
 * Mostra lo stato che TornadoVNA legge dal LibreVNA e invia comandi a TornadoVNA via USB-seriale.
 * Nessun sensore aggiunto: tutto cio' che si vede arriva dal PC. L'ESP32 non parla col VNA.
 *
 * ARDUINO IDE (Ide 2.x)
 *   Board manager : "esp32" by Espressif, versione 3.x (testato sulla logica, vedi note)
 *   Librerie      : "GFX Library for Arduino" (moononournation) >= 1.5.0
 *                   "ArduinoJson" (Benoit Blanchon) >= 7.0
 *   Scheda        : ESP32S3 Dev Module
 *   Impostazioni  : Flash Size 8MB (o 16MB come da modello) | PSRAM: "OPI PSRAM" | USB CDC On Boot: Disabled
 *                   Partition Scheme: Huge APP o default | Upload Speed 921600
 *   Porta         : connettore USB marcato "UART" (CH343, COM dedicata). Stessa porta usata da TornadoVNA.
 *
 * !! Pinout, CH422G e timing RGB sono presi dai dati Waveshare ricordati a memoria: NON verificati su hardware.
 *    Se lo schermo resta nero/sfarfalla, vedi le costanti RGB_* e CH422G_* qui sotto.
 *
 * Protocollo (una riga JSON per messaggio, 115200 8N1)  -  vedi benchvna/esp_link.py
 *   PC -> ESP : {"t":"s",conn,run,rate,start,stop,pts,ifbw,lvl,avg,tsrc,tlo,tmcu,src,lo,ovl,unl,fpga,xref,vs,isup,drift}
 *               {"t":"ev","m":"testo evento"}
 *   ESP -> PC : {"c":"hello"} {"c":"run","v":0|1} {"c":"single"} {"c":"preset"} {"c":"calref"}
 *               {"c":"set","k":"start|stop|pts|ifbw|lvl|avg","v":n}
 */
#include <Arduino.h>
#include <Wire.h>
#include <Arduino_GFX_Library.h>
#include <ArduinoJson.h>

// (definita qui in cima: il preprocessore Arduino genera i prototipi prima del primo codice)
struct Btn { int x, y, w, h; };
struct Unit { const char *n; double m; };

// ------------------------------------------------------------------ hardware
#define I2C_SDA 8
#define I2C_SCL 9
#define TOUCH_INT 4
#define CH422G_MODE 0x24        // registro modo: 0x01 = uscite push-pull EXIO0..7
#define CH422G_OUT  0x38        // registro uscite EXIO0..7
#define EXIO_TP_RST  (1 << 1)   // EXIO1 = bit1 (bit0 non usato)
#define EXIO_BL      (1 << 2)   // EXIO2 backlight (1 = acceso)
#define EXIO_LCD_RST (1 << 3)   // EXIO3
#define EXIO_SD_CS   (1 << 4)   // EXIO4

Arduino_ESP32RGBPanel *rgbpanel = new Arduino_ESP32RGBPanel(
    5 /*DE*/, 3 /*VSYNC*/, 46 /*HSYNC*/, 7 /*PCLK*/,
    1, 2, 42, 41, 40 /*R0..R4*/,
    39, 0, 45, 48, 47, 21 /*G0..G5*/,
    14, 38, 18, 17, 10 /*B0..B4*/,
    0 /*hsync_pol*/, 8 /*hfp*/, 4 /*hpw*/, 8 /*hbp*/,
    0 /*vsync_pol*/, 8 /*vfp*/, 4 /*vpw*/, 8 /*vbp*/,
    1 /*pclk_active_neg*/, 16000000 /*pclk Hz*/);
Arduino_RGB_Display *gfx = new Arduino_RGB_Display(800, 480, rgbpanel, 0, true);
Arduino_Canvas *canvas = new Arduino_Canvas(800, 480, NULL);   // backbuffer in PSRAM
Arduino_GFX *cv = canvas;

// ------------------------------------------------------------------ colori Tornado
#define RGB(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))
const uint16_t C_BG = RGB(0x12, 0x14, 0x17), C_PANEL = RGB(0x1a, 0x1d, 0x22), C_BORDER = RGB(0x2c, 0x31, 0x3a);
const uint16_t C_TEXT = RGB(0xd9, 0xdc, 0xe2), C_DIM = RGB(0x7d, 0x84, 0x90), C_ACC = RGB(0xff, 0x8a, 0x1f);
const uint16_t C_OK = RGB(0x5f, 0xe3, 0xa1), C_ERR = RGB(0xff, 0x4d, 0x4d), C_ONACC = RGB(0x15, 0x11, 0x0c);

// ------------------------------------------------------------------ stato ricevuto
struct State {
  bool valid = false;
  int conn = 0, run = 0;
  float rate = 0;
  double start = 0, stop = 0;
  int pts = 0, ifbw = 0, lvl = 0, avg = 0;
  float tsrc = NAN, tlo = NAN, tmcu = NAN, vs = NAN, isup = NAN, drift = NAN;
  int src = -1, lo = -1, ovl = -1, unl = -1, xav = -1, xref = -1;
} S;
uint32_t lastRx = 0;
char lastEv[96] = "";
bool dirty = true;

static float jf(JsonVariantConst v) { return v.isNull() ? NAN : v.as<float>(); }
static int ji(JsonVariantConst v) { return v.isNull() ? -1 : v.as<int>(); }

void handleLine(const char *line) {
  JsonDocument d;
  if (deserializeJson(d, line)) return;
  const char *t = d["t"] | "";
  if (!strcmp(t, "s")) {
    S.valid = true;
    S.conn = d["conn"] | 0; S.run = d["run"] | 0; S.rate = d["rate"] | 0.0f;
    S.start = d["start"] | 0.0; S.stop = d["stop"] | 0.0;
    S.pts = d["pts"] | 0; S.ifbw = d["ifbw"] | 0; S.lvl = d["lvl"] | 0; S.avg = d["avg"] | 0;
    S.tsrc = jf(d["tsrc"]); S.tlo = jf(d["tlo"]); S.tmcu = jf(d["tmcu"]);
    S.vs = jf(d["vs"]); S.isup = jf(d["isup"]); S.drift = jf(d["drift"]);
    S.src = ji(d["src"]); S.lo = ji(d["lo"]); S.ovl = ji(d["ovl"]); S.unl = ji(d["unl"]);
    S.xav = ji(d["xav"]); S.xref = ji(d["xref"]);
    lastRx = millis();
    char sig[220];
    snprintf(sig, sizeof sig, "%d %d %.1f %.0f %.0f %d %d %d %d %.0f %.0f %.0f %.0f %.1f %.0f %.1f %d %d %d %d %d %d",
             S.conn, S.run, S.rate, S.start, S.stop, S.pts, S.ifbw, S.lvl, S.avg, S.tsrc, S.tlo, S.tmcu,
             S.vs, S.isup, S.drift, S.drift, S.src, S.lo, S.ovl, S.unl, S.xav, S.xref);
    static char prev[220] = "";
    if (strcmp(sig, prev)) { strlcpy(prev, sig, sizeof prev); dirty = true; }
  } else if (!strcmp(t, "ev")) {
    strlcpy(lastEv, d["m"] | "", sizeof lastEv);
    dirty = true;
  }
}

// ------------------------------------------------------------------ comandi verso il PC
void sendCmd(const char *c) { Serial.printf("{\"c\":\"%s\"}\n", c); }
void sendRun(int v) { Serial.printf("{\"c\":\"run\",\"v\":%d}\n", v); }
void sendSet(const char *k, double v) { Serial.printf("{\"c\":\"set\",\"k\":\"%s\",\"v\":%.0f}\n", k, v); }

const char *KEYS_[6] = {"start", "stop", "pts", "ifbw", "lvl", "avg"};

// ------------------------------------------------------------------ layout (touch) + tastierino
const int ROW_Y0 = 96, ROW_H = 50;
Btn fieldRect(int r) { return {410, ROW_Y0 + r * ROW_H - 12, 380, ROW_H - 4}; }
const Btn B_RUN = {10, 410, 190, 60}, B_SINGLE = {210, 410, 190, 60}, B_PRESET = {410, 410, 190, 60}, B_CAL = {610, 410, 180, 60};
bool hit(Btn b, int x, int y) { return x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h; }

const Unit U_F[3] = {{"GHz", 1e9}, {"MHz", 1e6}, {"kHz", 1e3}};
const Unit U_BW[2] = {{"kHz", 1e3}, {"Hz", 1}};
const Unit U_OK[1] = {{"OK", 1}};
const char KP[4][4] = {{'7', '8', '9', '<'}, {'4', '5', '6', 'C'}, {'1', '2', '3', '-'}, {'0', '.', 0, 0}};
Btn kpKey(int c, int r) { return {20 + c * 120, 150 + r * 70, 110, 60}; }
Btn kpUnitBtn(int i) { return {520, 150 + i * 70, 260, 60}; }
const Btn KP_CANCEL = {520, 360, 260, 60};
int kpRow = -1;          // riga dello stimolo in modifica (-1 = tastierino chiuso)
char kpBuf[16] = "";
const char *LBL[6] = {"Start", "Stop", "Punti", "IFBW", "Livello", "Media"};

const Unit *kpUnits(int &n) {
  if (kpRow <= 1) { n = 3; return U_F; }
  if (kpRow == 3) { n = 2; return U_BW; }
  n = 1; return U_OK;
}
void kpOpen(int r) { if (!S.valid) return; kpRow = r; kpBuf[0] = 0; dirty = true; }
void kpClose() { kpRow = -1; dirty = true; }

void kpTouch(int x, int y) {
  dirty = true;
  if (hit(KP_CANCEL, x, y)) { kpClose(); return; }
  int nu; const Unit *u = kpUnits(nu);
  for (int i = 0; i < nu; i++) if (hit(kpUnitBtn(i), x, y)) {
    if (kpBuf[0] && strcmp(kpBuf, "-") && strcmp(kpBuf, ".")) sendSet(KEYS_[kpRow], atof(kpBuf) * u[i].m);
    kpClose();
    return;
  }
  for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) {
    char k = KP[r][c];
    if (!k || !hit(kpKey(c, r), x, y)) continue;
    size_t n = strlen(kpBuf);
    if (k == '<') { if (n) kpBuf[n - 1] = 0; }
    else if (k == 'C') kpBuf[0] = 0;
    else if (k == '-') {
      if (kpRow != 4) return;                                   // segno solo per il livello
      if (kpBuf[0] == '-') memmove(kpBuf, kpBuf + 1, n); else if (n < 10) { memmove(kpBuf + 1, kpBuf, n + 1); kpBuf[0] = '-'; }
    } else if (k == '.') {
      if (kpRow == 2 || kpRow == 4 || kpRow == 5 || strchr(kpBuf, '.') || n >= 10) return;
      kpBuf[n] = '.'; kpBuf[n + 1] = 0;
    } else if (n < 10) { kpBuf[n] = k; kpBuf[n + 1] = 0; }
    return;
  }
}

void onTouch(int x, int y) {
  if (kpRow >= 0) { kpTouch(x, y); return; }
  if (hit(B_RUN, x, y)) sendRun(S.run ? 0 : 1);
  else if (hit(B_SINGLE, x, y)) sendCmd("single");
  else if (hit(B_PRESET, x, y)) sendCmd("preset");
  else if (hit(B_CAL, x, y)) sendCmd("calref");
  else for (int r = 0; r < 6; r++) if (hit(fieldRect(r), x, y)) { kpOpen(r); break; }
}

// ------------------------------------------------------------------ disegno
void txt(int x, int y, const char *s, uint16_t col, uint8_t sz = 2) {
  cv->setTextSize(sz); cv->setTextColor(col); cv->setCursor(x, y); cv->print(s);
}
void button(Btn b, const char *label, bool primary) {
  cv->fillRoundRect(b.x, b.y, b.w, b.h, 8, primary ? C_ACC : C_PANEL);
  if (!primary) cv->drawRoundRect(b.x, b.y, b.w, b.h, 8, C_BORDER);
  int tw = strlen(label) * 12;
  txt(b.x + (b.w - tw) / 2, b.y + (b.h - 16) / 2, label, primary ? C_ONACC : C_TEXT);
}
void ledDraw(int x, int y, int w, const char *name, uint16_t col, const char *t) {
  cv->fillRoundRect(x, y, w, 44, 6, C_PANEL);
  cv->drawRoundRect(x, y, w, 44, 6, C_BORDER);
  cv->fillCircle(x + 20, y + 22, 8, col);
  txt(x + 38, y + 5, name, C_DIM, 1);
  txt(x + 38, y + 20, t, col);
}
void led(int x, int y, int w, const char *name, int v, bool goodIsOne, const char *okTxt, const char *badTxt) {
  bool good = (v == 1) == goodIsOne;
  ledDraw(x, y, w, name, (v < 0) ? C_DIM : (good ? C_OK : C_ERR), v < 0 ? "--" : (good ? okTxt : badTxt));
}
void ledInfo(int x, int y, int w, const char *name, int v, const char *yes, const char *no) {  // informativo: mai rosso
  ledDraw(x, y, w, name, v == 1 ? C_OK : C_DIM, v < 0 ? "--" : (v == 1 ? yes : no));
}
void tempBar(int y, const char *name, float t) {
  txt(10, y + 4, name, C_DIM);
  cv->drawRoundRect(70, y, 210, 22, 4, C_BORDER);
  if (!isnan(t)) {
    int w = constrain((int)(t / 80.0f * 206), 0, 206);
    cv->fillRoundRect(72, y + 2, w, 18, 3, t > 65 ? C_ERR : C_ACC);
    char b[16]; snprintf(b, sizeof b, "%.0f C", t);
    txt(292, y + 4, b, C_TEXT);
  } else txt(292, y + 4, "--", C_DIM);
}
void fmtF(char *b, size_t n, double f) {
  if (f >= 1e9) snprintf(b, n, "%.4f GHz", f / 1e9);
  else if (f >= 1e6) snprintf(b, n, "%.3f MHz", f / 1e6);
  else snprintf(b, n, "%.0f kHz", f / 1e3);
}

void drawKeypad() {
  char b[48];
  snprintf(b, sizeof b, "MODIFICA  %s", LBL[kpRow]);
  txt(20, 54, b, C_DIM);
  cv->fillRoundRect(20, 76, 760, 62, 8, C_PANEL);
  cv->drawRoundRect(20, 76, 760, 62, 8, C_ACC);
  if (kpBuf[0]) txt(34, 90, kpBuf, C_TEXT, 4);
  else {
    switch (kpRow) {
      case 0: fmtF(b, sizeof b, S.start); break;
      case 1: fmtF(b, sizeof b, S.stop); break;
      case 2: snprintf(b, sizeof b, "%d", S.pts); break;
      case 3: snprintf(b, sizeof b, "%d Hz", S.ifbw); break;
      case 4: snprintf(b, sizeof b, "%d dBm", S.lvl); break;
      default: snprintf(b, sizeof b, "%d", S.avg);
    }
    txt(34, 90, b, C_DIM, 4);
  }
  for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) {
    char k = KP[r][c];
    if (!k) continue;
    char l[6] = {k, 0};
    if (k == '<') strcpy(l, "DEL");
    if (k == '-') strcpy(l, "+/-");
    button(kpKey(c, r), l, false);
  }
  int nu; const Unit *u = kpUnits(nu);
  for (int i = 0; i < nu; i++) button(kpUnitBtn(i), u[i].n, true);
  button(KP_CANCEL, "ANNULLA", false);
}

void draw() {
  cv->fillScreen(C_BG);
  // intestazione
  cv->fillRect(0, 0, 800, 44, C_PANEL);
  cv->drawFastHLine(0, 44, 800, C_BORDER);
  cv->fillTriangle(14, 8, 26, 8, 12, 24, C_ACC); cv->fillTriangle(22, 20, 12, 36, 28, 20, C_ACC);  // fulmine stilizzato
  txt(42, 12, "TornadoVNA", C_ACC, 3);
  bool pcOk = S.valid && (millis() - lastRx < 3000);
  char b[64];
  if (!pcOk) { txt(420, 14, "PC non collegato", C_ERR); }
  else {
    cv->fillCircle(430, 22, 7, S.conn ? C_OK : C_ERR);
    snprintf(b, sizeof b, "VNA %s", S.conn ? "connesso" : "assente");
    txt(446, 14, b, C_TEXT);
    snprintf(b, sizeof b, "%.1f sweep/s", S.rate);
    txt(640, 14, b, C_DIM);
  }
  if (kpRow >= 0) { drawKeypad(); gfx->draw16bitRGBBitmap(0, 0, (uint16_t *)canvas->getFramebuffer(), 800, 480); return; }
  // stato RF / elettrico
  txt(10, 54, "STATO STRUMENTO", C_DIM);
  led(10, 76, 186, "PLL SORGENTE", S.src, true, "LOCK", "UNLOCK");
  led(204, 76, 186, "PLL LO", S.lo, true, "LOCK", "UNLOCK");
  led(10, 126, 186, "ADC", S.ovl, false, "OK", "OVERLOAD");
  led(204, 126, 186, "LIVELLO", S.unl, false, "OK", "UNLEVEL");
  ledInfo(10, 176, 186, "RIF. EST. DISP.", S.xav, "SI", "NO");
  led(204, 176, 186, "RIFERIMENTO", S.xref, true, "ESTERNO", "interno");
  txt(10, 232, "TEMPERATURE", C_DIM);
  tempBar(254, "SRC", S.tsrc);
  tempBar(284, "LO", S.tlo);
  tempBar(314, "MCU", S.tmcu);
  if (!isnan(S.vs) || !isnan(S.isup)) {
    snprintf(b, sizeof b, "Alim. %.2f V  %.0f mA", isnan(S.vs) ? 0 : S.vs / 1000.0f, isnan(S.isup) ? 0 : S.isup);
    txt(10, 348, b, C_TEXT);
  }
  if (!isnan(S.drift)) {
    snprintf(b, sizeof b, "Deriva da cal: %+.1f C", S.drift);
    txt(10, 372, b, fabsf(S.drift) > 3.0f ? C_ERR : C_TEXT);
  }
  // stimolo: campi toccabili
  txt(410, 54, "STIMOLO  (tocca per modificare)", C_DIM);
  for (int r = 0; r < 6; r++) {
    Btn f = fieldRect(r);
    cv->fillRoundRect(f.x, f.y, f.w, f.h, 6, C_PANEL);
    cv->drawRoundRect(f.x, f.y, f.w, f.h, 6, C_BORDER);
    txt(f.x + 10, f.y + 3, LBL[r], C_DIM, 1);
    switch (r) {
      case 0: fmtF(b, sizeof b, S.start); break;
      case 1: fmtF(b, sizeof b, S.stop); break;
      case 2: snprintf(b, sizeof b, "%d", S.pts); break;
      case 3: if (S.ifbw >= 1000) snprintf(b, sizeof b, "%g kHz", S.ifbw / 1000.0); else snprintf(b, sizeof b, "%d Hz", S.ifbw); break;
      case 4: snprintf(b, sizeof b, "%d dBm", S.lvl); break;
      default: snprintf(b, sizeof b, "%d", S.avg);
    }
    txt(f.x + 10, f.y + 14, S.valid ? b : "--", C_TEXT, 3);
  }
  // ultimo evento
  if (lastEv[0]) txt(10, 392, lastEv, C_DIM, 1);
  // comandi
  button(B_RUN, S.run ? "HOLD" : "CONTINUO", true);
  button(B_SINGLE, "SINGOLO", false);
  button(B_PRESET, "PRESET", false);
  button(B_CAL, "CAL REF", false);
  // copia unica del frame completo sul framebuffer del pannello (niente cancellazioni visibili)
  gfx->draw16bitRGBBitmap(0, 0, (uint16_t *)canvas->getFramebuffer(), 800, 480);
}

// ------------------------------------------------------------------ CH422G + touch GT911
bool i2cWrite(uint8_t addr, uint8_t reg_or_val, const uint8_t *p = nullptr, size_t n = 0) {
  Wire.beginTransmission(addr);
  Wire.write(reg_or_val);
  if (p) Wire.write(p, n);
  return Wire.endTransmission() == 0;
}
void exioWrite(uint8_t v) { i2cWrite(CH422G_OUT, v); }

uint8_t gtAddr = 0;
bool gtRead(uint16_t reg, uint8_t *buf, size_t n) {
  Wire.beginTransmission(gtAddr);
  Wire.write(reg >> 8); Wire.write(reg & 0xFF);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)gtAddr, (int)n) != (int)n) return false;
  for (size_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}
void gtClear() {
  Wire.beginTransmission(gtAddr);
  Wire.write(0x81); Wire.write(0x4E); Wire.write(0);
  Wire.endTransmission();
}
void touchInit() {
  i2cWrite(CH422G_MODE, 0x01);                                  // EXIO come uscite
  pinMode(TOUCH_INT, OUTPUT); digitalWrite(TOUCH_INT, LOW);     // INT basso durante il reset -> indirizzo 0x14
  exioWrite(EXIO_SD_CS);                                        // TP_RST, LCD_RST bassi, backlight spento
  delay(20);
  exioWrite(EXIO_SD_CS | EXIO_LCD_RST | EXIO_BL);               // LCD fuori da reset, backlight acceso; TP_RST ancora basso
  delay(10);
  exioWrite(EXIO_SD_CS | EXIO_LCD_RST | EXIO_BL | EXIO_TP_RST); // rilascio reset touch
  delay(10);
  pinMode(TOUCH_INT, INPUT);
  delay(60);
  for (uint8_t a : {(uint8_t)0x14, (uint8_t)0x5D}) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { gtAddr = a; break; }
  }
}
bool readTouch(int &x, int &y) {
  if (!gtAddr) return false;
  uint8_t st;
  if (!gtRead(0x814E, &st, 1)) return false;
  bool ok = false;
  if ((st & 0x80) && (st & 0x0F)) {
    uint8_t p[4];
    if (gtRead(0x8150, p, 4)) { x = p[0] | (p[1] << 8); y = p[2] | (p[3] << 8); ok = true; }
  }
  if (st & 0x80) gtClear();
  return ok;
}

// ------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  Wire.begin(I2C_SDA, I2C_SCL, 400000);
  delay(300);
  Serial.printf("{\"dbg\":\"boot psram=%u\"}\n", (unsigned)ESP.getPsramSize());
  for (uint8_t a = 1; a < 127; a++) {          // scansione I2C (righe JSON senza 'c': il PC le ignora)
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) Serial.printf("{\"dbg\":\"i2c 0x%02X\"}\n", a);
  }
  touchInit();
  Serial.printf("{\"dbg\":\"gt911 addr=0x%02X\"}\n", gtAddr);
  bool ok = gfx->begin();
  Serial.printf("{\"dbg\":\"gfx begin=%d\"}\n", ok);
  while (!ok) { delay(1000); Serial.println("{\"dbg\":\"gfx begin FALLITO: PSRAM OPI attiva?\"}"); }
  // test colori: rosso, verde, blu, bianco (se li vedi, pannello e backlight sono a posto)
  const uint16_t tc[4] = {RGB(255, 0, 0), RGB(0, 255, 0), RGB(0, 0, 255), RGB(255, 255, 255)};
  for (int i = 0; i < 4; i++) { gfx->fillScreen(tc[i]); delay(500); }
  canvas->begin();
  gfx->fillScreen(C_BG);
  draw();
  sendCmd("hello");
}

void loop() {
  static char line[768];
  static size_t n = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { line[n] = 0; if (n) handleLine(line); n = 0; }
    else if (c != '\r' && n < sizeof line - 1) line[n++] = c;
    else if (n >= sizeof line - 1) n = 0;
  }
  static bool down = false;
  static uint32_t tTouch = 0;
  int x, y;
  if (millis() - tTouch > 30) {
    tTouch = millis();
    if (readTouch(x, y)) { if (!down) { down = true; onTouch(x, y); } }
    else down = false;
  }
  static uint32_t tDraw = 0, tHello = 0;
  static bool wasOk = false;
  bool pcOk = S.valid && (millis() - lastRx < 3000);
  if (pcOk != wasOk) { wasOk = pcOk; dirty = true; }
  if (!pcOk && millis() - tHello > 2000) { tHello = millis(); sendCmd("hello"); }
  if (dirty && millis() - tDraw > 250) { tDraw = millis(); dirty = false; draw(); }
}
