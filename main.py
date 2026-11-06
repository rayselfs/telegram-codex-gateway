"""Compatibility entry point; existing deployment paths remain valid."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        del sys.argv[1]
        runpy.run_module("gateway.core.init", run_name="__main__")
    else:
        runpy.run_module("gateway.core.main", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("gateway.core.main")
