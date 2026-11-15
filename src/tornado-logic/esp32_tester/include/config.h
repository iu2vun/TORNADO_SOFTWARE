// Configurazione del tester. Scheda di Riccardo: ESP32 DevKit 38 pin con modulo ESP-32D (senza PSRAM), USB-C, CP2102;
// i GPIO 13, 14, 18, 19, 21, 22, 23, 25 sono tutti serigrafati sulla scheda (verificato da foto). Evitati: GPIO 6-11 (flash), 0/2/12/15 (strapping), 34-39 (solo ingresso),
// 16/17 (PSRAM sui moduli WROVER). Tutti i pin di uscita devono essere < 32 (si scrive il registro GPIO_OUT diretto).
#pragma once
#include <stdint.h>

// Uscita k  ->  ingresso k+1 dell'analizzatore (CH1..CH8). L'uscita 8 è il PWM hardware (LEDC).
static const int8_t OUT_PINS[8] = {13, 14, 18, 19, 21, 22, 23, 25};

// Opzionale: replica del dato RS-485 (DI) e di DE su due pin per pilotare un modulo transceiver reale (MAX3485/MAX485).
// -1 = non usato.
static const int8_t MIRROR_DI_PIN = -1;   // es. 26: ripete CH3 (dato)
static const int8_t MIRROR_DE_PIN = -1;   // es. 27: ripete CH4 (DE)

// Modo all'accensione: 0 off, 1 mixed, 2 serial, 3 walk
#define BOOT_MODE 1

#define PWM_LEDC_CHANNEL 0       // canale LEDC (core Arduino 2.x)
#define WALK_MS 500              // modo walk: durata di ogni canale alto
