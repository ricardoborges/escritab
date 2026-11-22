"""EscriTab. Importing the package quiets two harmless console messages, whatever starts the server
(run.py, `uvicorn app.main:app`, tests)."""
import sys
import warnings

# resampy (pulled in by torchcrepe / Dettato) still imports the deprecated pkg_resources
warnings.filterwarnings("ignore", message=r"pkg_resources is deprecated", category=UserWarning)


def _silence_proactor_connection_reset() -> None:
    """Windows' asyncio Proactor loop prints a ConnectionResetError traceback (WinError 10054) when the
    browser aborts a request mid-stream (e.g. audio Range requests while seeking). It is harmless:
    the client already closed the socket. Swallow only that case."""
    from asyncio.proactor_events import _ProactorBasePipeTransport

    original = _ProactorBasePipeTransport._call_connection_lost
    if getattr(original, "_escritab_patched", False):
        return

    def _call_connection_lost(self, exc):
        try:
            original(self, exc)
        except ConnectionResetError:
            pass

    _call_connection_lost._escritab_patched = True
    _ProactorBasePipeTransport._call_connection_lost = _call_connection_lost


if sys.platform == "win32":
    _silence_proactor_connection_reset()
