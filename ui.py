"""Compatibility entry point; existing deployment paths remain valid."""
import importlib
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("gateway.telegram.ui", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module("gateway.telegram.ui")
