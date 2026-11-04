import json
import os
import platform
import math
import re
import shutil
import subprocess  # nosec B404
import logging
from typing import Dict, Any, List, cast

logger = logging.getLogger(__name__)


class PolyglotBridge:
    """Cross-platform bridge for high-performance satellite engines."""

    @staticmethod
    def calculate_risk_rust(
        returns: List[float],
        conf: float = 0.95,
    ) -> Dict[str, Any]:
        """Run the Rust risk kernel; unavailable results are never represented as zero."""
        if not returns:
            return {"var": None, "expected_shortfall": None, "status": "INVALID_INPUT"}
        try:
            numeric_returns = [float(value) for value in returns]
            if not all(math.isfinite(value) for value in numeric_returns):
                return {"var": None, "expected_shortfall": None, "status": "INVALID_INPUT"}
            confidence = float(conf)
            if not math.isfinite(confidence) or not 0.0 < confidence < 1.0:
                return {"var": None, "expected_shortfall": None, "status": "INVALID_CONFIDENCE"}
            if not shutil.which("cargo"):
                return {"var": None, "expected_shortfall": None, "status": "RUST_NOT_INSTALLED"}

            input_data = json.dumps(
                {"returns": numeric_returns, "confidence_level": confidence},
                allow_nan=False,
            )
            command = [
                "cargo", "run", "--quiet", "--manifest-path",
                "nexus/polyglot/rust_risk_engine/Cargo.toml", "--", input_data,
            ]
            result = subprocess.run(
                command, capture_output=True, text=True, check=True, timeout=30
            )
            payload = json.loads(result.stdout)
            var_value = payload.get("var")
            es_value = payload.get("expected_shortfall")
            if var_value is None or es_value is None:
                raise ValueError("Rust risk engine returned incomplete metrics.")
            var_number = float(var_value)
            es_number = float(es_value)
            if not math.isfinite(var_number) or not math.isfinite(es_number):
                raise ValueError("Rust risk engine returned non-finite metrics.")
            return {
                "var": var_number,
                "expected_shortfall": es_number,
                "status": "OK",
            }
        except Exception as exc:
            logger.error("Rust risk calculation unavailable: %s", exc)
            return {
                "var": None,
                "expected_shortfall": None,
                "status": "RUST_ERROR",
                "error": str(exc),
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
            result = subprocess.run(  # nosec B603
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
        """Apply a strict Python order schema, with optional native Zig verification."""
        strict_polyglot = os.getenv("NEXUS_STRICT_POLYGLOT", "false").strip().lower() in {
            "true", "1", "yes"
        }

        symbol = order.get("symbol")
        if (
            not isinstance(symbol, str)
            or symbol != symbol.strip()
            or symbol != symbol.upper()
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", symbol)
        ):
            return {"valid": False, "error": "Symbol must be uppercase and match the allowed format."}

        side = order.get("side")
        if side not in {"buy", "sell"}:
            return {"valid": False, "error": "Side must be buy or sell."}

        def finite_positive(value: Any, field: str) -> Optional[str]:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return f"{field} must be numeric."
            if not math.isfinite(number) or number <= 0:
                return f"{field} must be finite and positive."
            return None

        for field in ("qty", "price"):
            error = finite_positive(order.get(field), field)
            if error:
                return {"valid": False, "error": error}

        order_type = order.get("order_type", "market")
        if order_type not in {"market", "limit", "stop", "stop_limit", "trailing_stop"}:
            return {"valid": False, "error": "Unsupported order type."}

        for field in ("limit_price", "stop_price", "trail_price", "trail_percent"):
            value = order.get(field)
            if value is not None:
                error = finite_positive(value, field)
                if error:
                    return {"valid": False, "error": error}

        if order_type in {"limit", "stop_limit"} and order.get("limit_price") is None:
            return {"valid": False, "error": "Limit orders require limit_price."}
        if order_type in {"stop", "stop_limit"} and order.get("stop_price") is None:
            return {"valid": False, "error": "Stop orders require stop_price."}
        if order_type == "trailing_stop":
            has_price = order.get("trail_price") is not None
            has_percent = order.get("trail_percent") is not None
            if has_price == has_percent:
                return {
                    "valid": False,
                    "error": "Trailing stops require exactly one trail_price or trail_percent.",
                }

        # Python validation is mandatory. Zig is an additional independent check.
        if not shutil.which("zig"):
            if strict_polyglot:
                return {"valid": False, "error": "ZIG_NOT_INSTALLED_FAIL_CLOSED"}
            return {"valid": True, "error": None, "source": "PYTHON_STRICT_VALIDATOR"}

        try:
            input_data = json.dumps(order, allow_nan=False)
            result = subprocess.run(
                [
                    "zig", "run", "nexus/polyglot/zig_validator/main.zig",
                    "--", input_data,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            native = json.loads(result.stdout)
            if not isinstance(native, dict) or not isinstance(native.get("valid"), bool):
                raise ValueError("Zig validator returned a malformed response.")
            if native["valid"] is not True:
                return {"valid": False, "error": native.get("error", "ZIG_VALIDATION_FAILED")}
            return {"valid": True, "error": None, "source": "PYTHON_AND_ZIG"}
        except Exception as exc:
            logger.warning("Zig validator unavailable or failed: %s", exc)
            if strict_polyglot:
                return {"valid": False, "error": f"VALIDATOR_OFFLINE_FAIL_CLOSED: {exc}"}
            return {"valid": True, "error": None, "source": "PYTHON_STRICT_VALIDATOR_FALLBACK"}
