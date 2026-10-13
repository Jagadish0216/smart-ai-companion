"""Best-effort default logging for the two CPU ONNX initialization sites only."""

import importlib


def configure_cpu_onnx_logging():
    """Reduce optional ONNX warnings without affecting model/application errors."""
    try:
        runtime = importlib.import_module("onnxruntime")
        setter = getattr(runtime, "set_default_logger_severity", None)
        if not callable(setter):
            return False
        setter(3)  # ERROR; warnings such as GPU discovery are cosmetic on this Pi.
        return True
    except Exception:
        return False  # Optional logging support must not prevent voice startup.
