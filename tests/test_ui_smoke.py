# tests/test_ui_smoke.py
import importlib
import os
import py_compile


_ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_app_py_compiles():
    py_compile.compile(os.path.join(_ROOT, "ui", "app.py"), doraise=True)


def test_record_trace_compiles_and_imports():
    py_compile.compile(os.path.join(_ROOT, "ui", "record_trace.py"), doraise=True)
    importlib.import_module("ui.record_trace")  # top-level 无副作用,可安全 import
