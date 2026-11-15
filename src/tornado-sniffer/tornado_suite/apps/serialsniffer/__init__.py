"""serialsniffer - core di acquisizione, registrazione, riproduzione ed export per flussi seriali."""
from .autobaud import COMMON_BAUDS, BaudResult, detect_baud, printable_score
from .channel import Channel
from .chunk import DIR_NAMES, GLOBAL, KIND_DATA, KIND_EVENT, RX, TX, Chunk
from .config import FLOW_MODES, PortConfig, parse_port_spec
from .exporters import EXPORT_KINDS, export
from .formats import FORMATS, StreamDecoder, hexdump, parse_payload, render
from .player import FINISHED, PAUSED, PLAYING, STOPPED, Player
from .recorder import Recorder
from .session import Session
from .ssnfile import Recording, SsnError, SsnWriter, make_header, read_ssn, select_chunks

__version__ = "0.1.0"
