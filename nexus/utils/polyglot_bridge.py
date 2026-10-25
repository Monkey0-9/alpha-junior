import json
import os
import platform
import shutil
import subprocess
import logging
import math
import re
from typing import Dict, Any, List, cast

logger = logging.getLogger(__name__)


class PolyglotBridge:
    """Cross-platform bridge for high-performance satellite engines."""

    @staticmethod
    def calculate_risk_rust(
        returns: List[float],
        conf: float = 0.95,
    ) -> Dict[str, Any]:
        """Execute Rust Risk Engine for VaR calculation."""
        input_data = json.dumps({"returns": returns, "confidence_level": conf})
        try:
            if not shutil.which("cargo"):
                return {
                    "var": 0.0,
                    "expected_shortfall": 0.0,
                    "status": "RUST_NOT_INSTALLED",
                }

            cmd = [
                "cargo",
                "run",
                "--quiet",
                "--manifest-path",
                "nexus/polyglot/rust_risk_engine/Cargo.toml",
                "--",
                input_data,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
            return cast(Dict[str, Any], json.loads(result.stdout))
        except Exception as exc:
            logger.debug(f"Rust Risk Engine fallback: {exc}")
            return {
                "var": 0.0,
                "expected_shortfall": 0.0,
                "status": "FALLBACK",
            }

    @staticmethod
    def audit_platform_go(
        backend_url: str = "http://127.0.0.1:8000",
    ) -> Dict[str, Any]:
        """Execute Go Platform Auditor for health checks."""
        try:
            # Platform-aware binary selection
            if platform.system() == "Windows":
                exe_path = "nexus/polyglot/go_auditor/auditor.exe"
            else:
                exe_path = "nexus/polyglot/go_auditor/auditor"

            if not os.path.exists(exe_path):
                return {
                    "overall_health": "DEGRADED",
                    "results": [
                        {
                            "service": "auditor",
                            "status": "BINARY_MISSING",
                        }
                    ],
                }

            cmd = [exe_path, backend_url]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
            return cast(Dict[str, Any], json.loads(result.stdout))
        except Exception as exc:
            logger.error(f"Go Auditor failure: {exc}")
            return {"overall_health": "UNKNOWN", "results": []}

    @staticmethod
    def validate_order_zig(order: Dict[str, Any]) -> Dict[str, Any]:
        """Validate order structure in Python; optionally add the native Zig check.

        Python validation is the mandatory baseline and is not disabled when Zig
        is missing. Set NEXUS_STRICT_POLYGLOT=true to require the native check too.
        """
        strict_polyglot = os.getenv("NEXUS_STRICT_POLYGLOT", "false").lower() in (
            "true", "1", "yes"
        )

        symbol = order.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", symbol.strip().upper()):
            return {"valid": False, "error": "Symbol has invalid format"}
        if symbol != symbol.strip() or symbol != symbol.upper():
            return {"valid": False, "error": "Symbol must be trimmed and uppercase"}

        try:
            qty = float(order.get("qty", 0))
        except (TypeError, ValueError):
            return {"valid": False, "error": "Invalid quantity specification"}
        if not math.isfinite(qty) or qty <= 0:
            return {"valid": False, "error": "Quantity must be finite and positive"}

        try:
            price = float(order.get("price", 0))
        except (TypeError, ValueError):
            return {"valid": False, "error": "Invalid price specification"}
        if not math.isfinite(price) or price <= 0:
            return {"valid": False, "error": "Price must be finite and positive"}

        side = order.get("side")
        if side not in {"buy", "sell"}:
            return {"valid": False, "error": "Side must be buy or sell"}

        order_type = order.get("order_type", "market")
        if order_type not in {"market", "limit", "stop", "stop_limit", "trailing_stop"}:
            return {"valid": False, "error": "Unsupported order type"}

        if order_type in {"limit", "stop_limit"}:
            raw_limit_price = order.get("limit_price")
            if raw_limit_price is None:
                return {"valid": False, "error": "Limit orders require a valid limit_price"}
            try:
                limit_price = float(raw_limit_price)
            except (TypeError, ValueError):
                return {"valid": False, "error": "Limit orders require a valid limit_price"}
            if not math.isfinite(limit_price) or limit_price <= 0:
                return {"valid": False, "error": "limit_price must be finite and positive"}

        if order_type in {"stop", "stop_limit"}:
            raw_stop_price = order.get("stop_price")
            if raw_stop_price is None:
                return {"valid": False, "error": "Stop orders require a valid stop_price"}
            try:
                stop_price = float(raw_stop_price)
            except (TypeError, ValueError):
                return {"valid": False, "error": "Stop orders require a valid stop_price"}
            if not math.isfinite(stop_price) or stop_price <= 0:
                return {"valid": False, "error": "stop_price must be finite and positive"}

        if order_type == "trailing_stop":
            if (order.get("trail_price") is None) == (order.get("trail_percent") is None):
                return {"valid": False, "error": "Trailing stops require exactly one of trail_price or trail_percent"}
            field = "trail_price" if order.get("trail_price") is not None else "trail_percent"
            raw_trail_value = order.get(field)
            if raw_trail_value is None:
                return {"valid": False, "error": f"{field} must be finite and positive"}
            try:
                trail_value = float(raw_trail_value)
            except (TypeError, ValueError):
                return {"valid": False, "error": f"{field} must be finite and positive"}
            if not math.isfinite(trail_value) or trail_value <= 0:
                return {"valid": False, "error": f"{field} must be finite and positive"}

        if not shutil.which("zig"):
            if strict_polyglot:
                return {"valid": False, "error": "ZIG_NOT_INSTALLED_FAIL_CLOSED"}
            return {"valid": True, "error": None, "source": "PYTHON_STRICT_VALIDATOR"}

        try:
            input_data = json.dumps(order, allow_nan=False)
            result = subprocess.run(
                [
                    "zig",
                    "run",
                    "nexus/polyglot/zig_validator/main.zig",
                    "--",
                    input_data,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            native_result = cast(Dict[str, Any], json.loads(result.stdout))
            if native_result.get("valid") is True:
                return {**native_result, "source": "ZIG_AND_PYTHON"}
            return {"valid": False, "error": native_result.get("error", "ZIG_VALIDATION_FAILED")}
        except Exception as exc:
            logger.warning("Zig validator unavailable or failed: %s", exc)
            if strict_polyglot:
                return {"valid": False, "error": f"VALIDATOR_OFFLINE_FAIL_CLOSED: {exc}"}
            # The full Python schema above remains active as an independently
            # enforced validator; no exception is treated as an automatic pass.
            return {"valid": True, "error": None, "source": "PYTHON_STRICT_VALIDATOR_FALLBACK"}
