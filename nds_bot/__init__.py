"""NDS (Nodal Displacement Sequencing) signal engine + AI meta-labeling.

SIGNAL ONLY: this package never sends orders to a broker.
"""
from .config import NDSConfig
from .structure import Setup, scan_latest

__all__ = ["NDSConfig", "Setup", "scan_latest"]
__version__ = "1.0.0"
