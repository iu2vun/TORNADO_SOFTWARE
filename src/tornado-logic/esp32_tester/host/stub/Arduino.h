// Stub minimale dell'API Arduino-ESP32 usata da main.cpp: serve solo a controllare sintassi e tipi sul PC (non è il core reale).
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string>
#define F(x) x
#define OUTPUT 1
#define HIGH 1
#define LOW 0
struct String {
    std::string s;
    String() {}
    String(const char* c) : s(c) {}
    String(const std::string& c) : s(c) {}
    void trim() { while (!s.empty() && isspace(s.back())) s.pop_back(); size_t i = 0; while (i < s.size() && isspace(s[i])) ++i; s = s.substr(i); }
    void toLowerCase() { for (auto& c : s) c = tolower(c); }
    int indexOf(char c) const { auto p = s.find(c); return p == std::string::npos ? -1 : (int)p; }
    String substring(int a) const { return String(s.substr(a)); }
    String substring(int a, int b) const { return String(s.substr(a, b - a)); }
    unsigned length() const { return s.size(); }
    const char* c_str() const { return s.c_str(); }
    String& operator+=(char c) { s += c; return *this; }
    String& operator=(const char* c) { s = c; return *this; }
    bool operator==(const char* c) const { return s == c; }
    bool operator!=(const char* c) const { return s != c; }
};
struct SerialClass { void begin(int); void println(const char*); void printf(const char*, ...) __attribute__((format(printf, 2, 3))); int available(); int read(); };
extern SerialClass Serial;
struct EspClass { uint32_t getCycleCount(); };
extern EspClass ESP;
void pinMode(int, int); void digitalWrite(int, int); void delay(uint32_t);
uint32_t millis(); uint32_t micros(); uint32_t getCpuFrequencyMhz();
void ledcSetup(uint8_t, uint32_t, uint8_t); void ledcAttachPin(uint8_t, uint8_t); void ledcWrite(uint8_t, uint32_t); void ledcDetachPin(uint8_t);
void noInterrupts(); void interrupts();
