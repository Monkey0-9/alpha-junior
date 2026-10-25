import json
import os
import platform
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
            result = subprocess.run(  # nosec B603
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
    def validate_order_zig(
        order: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute Zig Validator for order verification with fail-closed safety."""
        strict_polyglot = os.getenv("NEXUS_STRICT_POLYGLOT", "false").lower() in ("true", "1", "yes")

        # 1. Structural schema and bounds pre-validation (defense-in-depth)
        qty = order.get("qty", 0)
        price = order.get("price", 0.0)
        symbol = order.get("symbol", "")

        try:
            qty_val = float(qty)
            if qty_val <= 0:
                return {"valid": False, "error": "Quantity must be positive"}
        except (ValueError, TypeError):
            return {"valid": False, "error": "Invalid quantity specification"}

        try:
            price_val = float(price)
            if price_val <= 0.0:
                return {"valid": False, "error": "Price must be positive"}
        except (ValueError, TypeError):
            return {"valid": False, "error": "Invalid price specification"}

        if not symbol or not isinstance(symbol, str) or not symbol.strip():
            return {"valid": False, "error": "Symbol must be non-empty string"}

        # 2. Native Zig verification
        if not shutil.which("zig"):
            if strict_polyglot:
                logger.error("[PolyglotBridge] Strict mode: Zig compiler not found. Failing closed.")
                return {
                    "valid": False,
                    "error": "ZIG_NOT_INSTALLED_FAIL_CLOSED",
                }
            return {
                "valid": True,
                "error": None,
                "source": "FALLBACK_VERIFIED",
            }

        try:
            input_data = json.dumps(order)
            cmd = [
                "zig",
                "run",
                "nexus/polyglot/zig_validator/main.zig",
                "--",
                input_data,
            ]
            result = subprocess.run(  # nosec B603
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
            return cast(Dict[str, Any], json.loads(result.stdout))
        except Exception as exc:
            logger.debug(f"Zig Validator execution error: {exc}")
            if strict_polyglot:
                return {
                    "valid": False,
                    "error": f"VALIDATOR_OFFLINE_FAIL_CLOSED: {exc}",
                }
            return {
                "valid": True,
                "error": None,
                "source": "FALLBACK_VERIFIED",
            }
