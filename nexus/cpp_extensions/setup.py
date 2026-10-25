import os
import sys

from setuptools import setup, Extension
import pybind11
from setuptools.command.build_ext import build_ext as _build_ext

# Use IEEE-compliant floating-point semantics. Fast-math can invalidate NaN/Inf
# guards used by quantitative risk checks, so do not enable it for this engine.
use_mingw = (
    sys.platform == "win32"
    and os.getenv("NEXUS_USE_MINGW", "0").strip().lower() in {"1", "true", "yes"}
)
if sys.platform == "win32" and not use_mingw:
    common_args = ["/O2", "/std:c++14"]
    link_args = []
elif use_mingw:
    common_args = ["-O3", "-std=c++11"]
    link_args = ["-static-libgcc", "-static-libstdc++"]
else:
    common_args = ["-O3", "-std=c++11"]
    link_args = []

class PlatformBuildExt(_build_ext):
    """Ensure setuptools emits platform-native extension artifacts."""

    def get_export_symbols(self, ext):
        symbols = super().get_export_symbols(ext)
        if sys.platform != "win32":
            # Windows .def export lists are not accepted by ELF/Mach-O linkers.
            symbols = [symbol for symbol in symbols if not symbol.lower().endswith(".def")]
        return symbols


build_options = {"build_ext": {"compiler": "mingw32"}} if use_mingw else {}

ext_modules = [
    Extension(
        "nexus_cpp",
        sources=[
            "fast_math.cpp",
            "kalman.cpp",
            "fractal.cpp",
            "stats.cpp",
            "timeseries.cpp",
            "signals.cpp",
            "matrix.cpp",
            "optimization.cpp",
            "feed.cpp",
            "backtest.cpp",
            "onnx_inference.cpp",
        ],
        include_dirs=[pybind11.get_include()],
        language="c++",
        extra_compile_args=common_args,
        extra_link_args=link_args,
    ),
    Extension(
        "nexus_onnx",
        sources=["onnx_inference.cpp"],
        include_dirs=[pybind11.get_include()],
        language="c++",
        extra_compile_args=common_args,
        extra_link_args=link_args,
    ),
    Extension(
        "nexus_backtester",
        sources=["backtester.cpp"],
        include_dirs=[pybind11.get_include()],
        language="c++",
        extra_compile_args=common_args,
        extra_link_args=link_args,
    ),
    Extension(
        "nexus_feed",
        sources=["market_feed.cpp"],
        include_dirs=[pybind11.get_include()],
        language="c++",
        extra_compile_args=common_args,
        extra_link_args=link_args,
    ),
]

setup(
    name="nexus_cpp_extensions",
    version="0.2.0",
    description="Nexus High-Performance C++ Trading Engine",
    ext_modules=ext_modules,
    options=build_options,
    cmdclass={"build_ext": PlatformBuildExt},
)
