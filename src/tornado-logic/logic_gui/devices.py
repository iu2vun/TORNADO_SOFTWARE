"""Fabbrica dei dispositivi selezionabili dalla GUI."""
from __future__ import annotations

import os

from logic_core import DeviceError, LogicDevice, SigrokLogicDevice, SimLogicDevice

# Nel programma si usa solo l'hardware vero. I simulatori restano nel codice per i test e per lo sviluppo
# senza scheda: si attivano con la variabile d'ambiente TORNADO_LOGIC_SIM=1.
SIM_ENABLED = os.environ.get("TORNADO_LOGIC_SIM") == "1"
HARDWARE = "Hardware (sigrok)"
DEVICE_KINDS = (((("Simulato", "Simulato (seriale)", "Simulato (walk)") if SIM_ENABLED else ())) + (HARDWARE,))


def create_device(kind: str) -> LogicDevice:
    if kind == "Simulato":
        return SimLogicDevice()
    if kind == "Simulato (seriale)":
        return SimLogicDevice(scenario="serial")
    if kind == "Simulato (walk)":
        return SimLogicDevice(scenario="walk")
    if kind == HARDWARE:
        return SigrokLogicDevice()
    raise DeviceError(f"dispositivo sconosciuto: {kind}")
