#!/usr/bin/env python3
"""
test_usb.py — diagnostica driver USB LibreVNA fuori dalla GUI.

Esercita il driver passo-passo e stampa tutto quello che succede:
scoperta, handshake, sweep, tipi di pacchetto ricevuti.

Usage:  python test_usb.py
"""
import sys, os, time, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from benchvna.usb.driver import LibreVNAUSBDriver, LibreVNAUSBError
from benchvna.usb.protocol import SweepSettings, PacketType

print("=" * 60)
print("[1] Scoperta device")
print("=" * 60)
drv = LibreVNAUSBDriver()
try:
    serials = drv.list_devices()
except Exception as e:
    print(f"ERRORE list_devices: {e!r}")
    sys.exit(1)
print(f"Device trovati: {serials}")
if not serials:
    print("Nessun device — controlla Zadig/libusb.")
    sys.exit(1)

print("\n" + "=" * 60)
print("[2] Connessione + handshake")
print("=" * 60)
try:
    drv.connect(serials[0])
except LibreVNAUSBError as e:
    print(f"ERRORE connect: {e!r}")
    sys.exit(1)
print(f"DeviceInfo: {drv.device_info}")

print("\n" + "=" * 60)
print("[3] Coda pacchetti dopo connect (2s di ascolto passivo)")
print("=" * 60)
time.sleep(2.0)
counts = {}
while not drv._pkt_queue.empty():
    try:
        ptype, payload = drv._pkt_queue.get_nowait()
    except Exception:
        break
    counts[ptype.name] = counts.get(ptype.name, 0) + 1
print(f"Pacchetti in coda (device idle): {counts or 'nessuno'}")

print("\n" + "=" * 60)
print("[4] Invio SweepSettings + INITIATE_SWEEP")
print("=" * 60)
s = SweepSettings(
    f_start=100_000_000, f_stop=6_000_000_000, points=101,
    if_bandwidth=1000, cdbm_excitation_start=-1000,
    cdbm_excitation_stop=-1000, stages=1, port1_stage=0, port2_stage=1,
)
try:
    print("→ set_sweep()…")
    t0 = time.time()
    drv.set_sweep(s)
    print(f"  set_sweep completato in {time.time()-t0:.2f}s")
except Exception as e:
    print(f"ERRORE set_sweep: {e!r}")
    drv.disconnect()
    sys.exit(1)

print("\n" + "=" * 60)
print("[5] Ricezione datapoint (5s)")
print("=" * 60)
t_end = time.time() + 5.0
counts = {}
datapoints = 0
point_nums = set()
while time.time() < t_end:
    try:
        ptype, payload = drv._pkt_queue.get(timeout=0.3)
    except Exception:
        continue
    counts[ptype.name] = counts.get(ptype.name, 0) + 1
    if ptype == PacketType.VNA_DATAPOINT:
        from benchvna.usb.protocol import VNADatapoint
        dp = VNADatapoint.decode(payload)
        datapoints += 1
        point_nums.add(dp.point_num)
        if datapoints <= 3:
            print(f"  DP #{dp.point_num}: f={dp.frequency/1e6:.1f}MHz "
                  f"n_vals={dp.num_values} descs={[hex(d) for d in dp.desc_values]}")

print(f"\nTipi pacchetto ricevuti in 5s: {counts}")
print(f"Datapoint totali: {datapoints}")
print(f"Point_num distinti: {len(point_nums)} (range {min(point_nums) if point_nums else '-'}..{max(point_nums) if point_nums else '-'})")

if datapoints == 0:
    print("\n⚠ NESSUN DATAPOINT — il device non sweeppa dopo INITIATE_SWEEP.")
elif len(point_nums) < 101:
    print(f"\n⚠ Sweep parziale: {len(point_nums)}/101 punti — poi si ferma.")
    print("  → serve re-trigger dopo ogni sweep completo.")
else:
    print("\n✓ Sweep completo ricevuto.")

print("\n" + "=" * 60)
print("[6] Test re-trigger: 3 sweep consecutivi")
print("=" * 60)
for i in range(3):
    drv.initiate_sweep()
    t_end = time.time() + 3.0
    pnums = set()
    while time.time() < t_end and len(pnums) < 101:
        try:
            ptype, payload = drv._pkt_queue.get(timeout=0.3)
        except Exception:
            continue
        if ptype == PacketType.VNA_DATAPOINT:
            from benchvna.usb.protocol import VNADatapoint
            dp = VNADatapoint.decode(payload)
            pnums.add(dp.point_num)
    print(f"  Sweep {i+1}: {len(pnums)} punti")

drv.disconnect()
print("\n[done]")
