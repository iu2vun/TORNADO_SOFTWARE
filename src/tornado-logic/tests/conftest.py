import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("TORNADO_LOGIC_SIM", "1")      # i test usano i simulatori; il programma normale no
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
