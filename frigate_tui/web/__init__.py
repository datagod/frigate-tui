"""Web interface package for frigate-tui.

Provides a FastAPI-based local-network dashboard with feature parity to the TUI.
The heavy lifting (polling, MQTT, health computation, event merging, activity log)
is performed by the shared core (frigate_tui.core) so both frontends stay in sync.
"""

from frigate_tui import __version__

__all__ = ["__version__"]
