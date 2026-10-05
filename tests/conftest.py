"""Stub GStreamer (`gi`) so the contribution seam imports on a CI runner with no
GStreamer installed.

contribution_core.py does `import gi; gi.require_version('Gst','1.0'); from
gi.repository import Gst, GLib` at module top, and constructs a pipeline only inside
ContributionCore.__init__ (not at import). So to unit-test the PURE logic — launch
string construction, the orthogonal needs_conform/timing_policy decisions, the
cadence constants, the zoomiso factory — we only need `import gi` to succeed and
`Gst.parse_launch` to be callable. We never run a real pipeline here; the actual
media smoke test is a separate integration job on AVX-capable hardware.

This is the same stubbing idea the repo already used informally in the stubbed-gi
dry run that proved the guest launch string was byte-identical after the seam
refactor — formalized into a reusable fixture so those parity checks run in CI.
"""
import sys
import types
from pathlib import Path

# Make tools/ importable (adapters.py does `from contribution_core import ...`).
TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _install_gi_stub():
    """Install a minimal fake `gi` + `gi.repository` into sys.modules.

    Only the surface contribution_core touches at import and in __init__ is faked:
      gi.require_version(...)          -> no-op
      gi.repository.Gst.init(None)     -> no-op
      gi.repository.Gst.parse_launch(s)-> records s, returns a stub pipeline
      Gst.CLOCK_TIME_NONE, Gst.State.*, Gst.PadProbeType.*, Gst.PadProbeReturn.*
      gi.repository.GLib.MainLoop()    -> stub
    """
    if "gi" in sys.modules and getattr(sys.modules["gi"], "_mxl_stub", False):
        return  # already installed

    gi = types.ModuleType("gi")
    gi._mxl_stub = True
    gi.require_version = lambda *a, **k: None

    repo = types.ModuleType("gi.repository")

    # --- a recording stub pipeline so tests can read back the launch string ---
    class _StubElement:
        def __init__(self, name=None):
            self._name = name

        def get_by_name(self, name):
            return _StubElement(name)

        def get_static_pad(self, _name):
            return _StubPad()

        def set_name(self, name):
            self._name = name

        def link(self, _other):
            return True

        def unlink(self, _other):
            return None

        def set_state(self, _s):
            return None

        def sync_state_with_parent(self):
            return None

    class _StubPad:
        def add_probe(self, *_a, **_k):
            return 1

        def is_linked(self):
            return False

        def get_peer(self):
            return None

        def unlink(self, _other):
            return None

    class _StubPipeline:
        last_launch = None  # class attr: the most recent parse_launch string

        def __init__(self, launch):
            _StubPipeline.last_launch = launch
            self._launch = launch

        def get_by_name(self, _name):
            return _StubElement(_name)

        def add(self, _el):
            return None

        def remove(self, _el):
            return None

        def get_clock(self):
            return None

        def get_base_time(self):
            return 0

        def get_bus(self):
            return _StubBus()

        def set_state(self, _s):
            return None

    class _StubBus:
        def add_signal_watch(self):
            pass

        def connect(self, *_a, **_k):
            pass

    class _Enum:
        def __getattr__(self, name):
            return name

    class Gst:
        CLOCK_TIME_NONE = -1
        State = _Enum()
        PadProbeType = _Enum()
        PadProbeReturn = _Enum()
        IteratorResult = _Enum()
        PadLinkReturn = _Enum()

        # persistent-flow mode records the tail + leg bin descriptions here so tests
        # can assert the split pipeline matches the one-shot launch's conform/sink.
        last_bins = []

        class Pipeline:
            @staticmethod
            def new(_name):
                return _StubPipeline("<persistent:%s>" % _name)

        @staticmethod
        def init(_a):
            return None

        @staticmethod
        def parse_launch(launch):
            return _StubPipeline(launch)

        @staticmethod
        def parse_bin_from_description(desc, _ghost):
            Gst.last_bins.append(desc)
            return _StubElement()

    class GLib:
        class MainLoop:
            def run(self):
                raise RuntimeError("MainLoop.run() must not be called in unit tests")

            def quit(self):
                pass

    repo.Gst = Gst
    repo.GLib = GLib
    gi.repository = repo
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo


_install_gi_stub()
