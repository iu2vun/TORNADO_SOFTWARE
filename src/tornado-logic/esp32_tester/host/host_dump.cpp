// Stampa i nastri generati da scenario.cpp (per il test sul PC): host_dump <mixed|serial> <cicli> [baud=%] [delead=us] [delag=us] [parity=0|1] [crc=0|1]
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "../src/scenario.h"

int main(int argc, char** argv) {
    if (argc < 3) return 2;
    using namespace tester;
    const Scenario sc = std::strcmp(argv[1], "serial") == 0 ? Scenario::Serial : Scenario::Mixed;
    const int cycles = std::atoi(argv[2]);
    Faults f;
    for (int i = 3; i < argc; ++i) {
        if (!std::strncmp(argv[i], "baud=", 5)) f.baud_err_pct = std::atof(argv[i] + 5);
        else if (!std::strncmp(argv[i], "delead=", 7)) f.de_lead_us = std::atoi(argv[i] + 7);
        else if (!std::strncmp(argv[i], "delag=", 6)) f.de_lag_us = std::atoi(argv[i] + 6);
        else if (!std::strncmp(argv[i], "parity=", 7)) f.parity_err = std::atoi(argv[i] + 7) != 0;
        else if (!std::strncmp(argv[i], "crc=", 4)) f.crc_err = std::atoi(argv[i] + 4) != 0;
    }
    static Tape tp;
    std::printf("P %u\nI %u\n", cyclePeriodUs(sc), idleMask(sc));
    for (int c = 0; c < cycles; ++c) {
        if (!compose(sc, c, f, tp)) {
            std::printf("OVERFLOW\n");
            return 1;
        }
        std::printf("C %d %zu %u %u %u\n", c, tp.n, tp.length_ns, tp.idle, tp.end);
        for (size_t i = 0; i < tp.n; ++i)
            std::printf("E %llu %u %u\n", static_cast<unsigned long long>(c) * cyclePeriodUs(sc) * 1000ull + tp.ev[i].t_ns,
                        tp.ev[i].set, tp.ev[i].clr);
    }
    return 0;
}
