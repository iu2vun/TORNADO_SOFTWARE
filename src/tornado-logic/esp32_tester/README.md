# Tester per analizzatore logico (ESP32)

Genera su 8 uscite segnali noti, gli stessi del simulatore del software (`logic_core/sim.py`), per provare
analizzatore, collegamenti e software con ingressi di cui si conosce il contenuto esatto.
Le forme d'onda sono composte da `src/scenario.cpp` (C++ puro) e riprodotte con temporizzazione a ciclo di clock
(240 MHz, interrupt disabilitati durante il ciclo; l'incertezza reale dei fronti è da misurare sul banco). Non serve nessun modulo aggiuntivo.

## Collegamenti (pin proposti in `include/config.h`, da verificare sulla propria scheda)

| Uscita | GPIO | Analizzatore | Modo `mixed` | Modo `serial` |
|---|---|---|---|---|
| 1 | 13 | CH1 | UART 115200 8N1 `Hello\r\n` | RS-232 TX, 9600 8N1 `GET\r` |
| 2 | 14 | CH2 | SPI CLK (modo 0, 1 MHz) | RS-232 RX, 9600 8N1 `OK\r\n` |
| 3 | 18 | CH3 | SPI MOSI `9F 00 00` | RO (dato RS-485) |
| 4 | 19 | CH4 | SPI MISO `00 EF 40` | DE (master) |
| 5 | 21 | CH5 | SPI CS (attivo basso) | A (= !dato) |
| 6 | 22 | CH6 | I²C SCL 100 kHz | B (= dato) |
| 7 | 23 | CH7 | I²C SDA: scrittura 0x50, dati `10 3C`, ACK | libero (alto) |
| 8 | 25 | CH8 | PWM 1 kHz, duty 30 % (LEDC) | PWM 1 kHz, duty 30 % |
| GND | GND | GND | collegare almeno una massa in comune | |

Nel modo `serial` il traffico RS-485 è Modbus RTU 19200 8E1: richiesta `01 03 00 00 00 02 C4 0B`, risposta
`01 03 04 <reg> 00 0B <CRC>` con il registro che cresce di 1 a ogni ciclo (20 ms). DE sale 100 µs prima della richiesta
e scende 60 µs dopo; tra richiesta e risposta passano 1,5 ms e A = B per ~1,25 ms (bus non pilotato).
A e B sono **emulati** con due uscite logiche (A = !dato, B = dato), quindi vanno agli ingressi come ogni altro canale.

## Sicurezza

- Le uscite sono a 3,3 V: compatibili con l'analizzatore (soglia alta 2 V).
- RS-232 vero ha livelli ±5…±15 V. **Mai collegarli direttamente agli ingressi dell'analizzatore**
  (massimo ~5,25 V secondo le schede dei cloni): serve un convertitore a TTL (MAX3232). Il tester simula già la linea
  convertita; inverti la linea nel software ("Linea invertita") solo se prelevi il segnale prima del convertitore.

## Console USB (115200 baud)

`help`, `status`, `mode off|mixed|serial|walk`, `baud <%>`, `delead <µs>`, `delag <µs>`, `parity on|off`, `crc on|off` (CRC errato nelle risposte Modbus dei cicli dispari), `faults reset`.

- `walk`: un canale alto alla volta ogni 500 ms (CH1 → CH8). Con l'analizzatore in acquisizione mostra quale filo
  del cavo corrisponde a quale canale: serve per verificare il pinout nella scheda "Pinout" del software.
- Difetti (solo `serial`, validi dal ciclo successivo): `baud 2` = linea più veloce del 2 %; `delead -100` = DE attivato
  100 µs dopo lo start bit; `delag -150` = DE rilasciato 150 µs prima della fine; `parity on` = bit di parità invertito
  (errori su tutti i byte RS-485). Il software deve riportare baud misurato +2 %, "DE attivato in ritardo",
  "DE rilasciato prima della fine", byte con errore.

## Modulo RS-485 reale (opzionale)

Per vedere A/B veri di un transceiver (MAX3485/SN65HVD3082 a 3,3 V; un MAX485 a 5 V va bene per l'analizzatore ma non
mandare mai le sue uscite a un pin ESP32): in `config.h` imposta `MIRROR_DI_PIN` e `MIRROR_DE_PIN` (copie di CH3 e CH4),
collega DI e DE (con RE a GND) e porta A e B del modulo agli ingressi CH5/CH6 al posto delle uscite emulate.
Su moduli con resistenze di polarizzazione il bus non pilotato non apparirà come A = B.

## Compilazione

`pio run -t upload` (core Arduino-ESP32 2.x fissato da `platformio.ini`: le chiamate `ledc*` sono del core 2.x),
`pio device monitor`. I pin della tabella sono proposte per un ESP32 DevKit generico: verifica che siano liberi.
Sui moduli WROVER evita GPIO 16/17.

## Stato di verifica

- **Verificato sul PC** (test `tests/test_esp32_tester.py`): `scenario.cpp` compilato con g++, le forme d'onda campionate
  a 24 MS/s e decodificate dal software (UART, SPI, I²C, RS-232, Modbus RTU su RS-485 con DE, A/B, bus libero, difetti
  iniettati). `main.cpp` controllato solo per sintassi e tipi contro uno stub dell'API Arduino.
- **Non verificato**: compilazione con PlatformIO e funzionamento sulla scheda (il sandbox non raggiunge i server di
  PlatformIO e non ha hardware). Al primo `pio run` potrebbero servire piccole correzioni.
- Da misurare sul banco: la temporizzazione reale dei fronti con l'analizzatore (jitter, durata del ciclo seriale).
