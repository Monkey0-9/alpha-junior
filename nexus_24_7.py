import subprocess  # nosec B404
import time
import sys
import logging
import signal
import threading
from typing import Optional, Any, Dict, TextIO, cast
from datetime import datetime, timedelta
from pathlib import Path

# Setup enhanced logging for 24/7 operation
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

log_file = log_dir / f"nexus_24_7_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("Nexus24x7")


class Nexus24x7Manager:
    """Manages Nexus platform for 24/7 continuous operation with real-time visibility."""

    def __init__(self) -> None:
        self.running: bool = False
        self.orchestrator_process: Optional[subprocess.Popen[str]] = None
        self.start_time: Optional[datetime] = None
        self.restart_count: int = 0
        self.total_runtime: timedelta = timedelta()
        self.root: str = str(Path(__file__).resolve().parent)
        self.log_thread: Optional[threading.Thread] = None
        self._orchestrator_log: Optional[TextIO] = None

        # Signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, frame: Any) -> None:
        """Handle graceful shutdown."""
        logger.info("[SHUTDOWN] Signal received. Terminating all processes...")
        self.shutdown()
        sys.exit(0)

    def _stream_logs(self, pipe: Any, log_file_handle: TextIO) -> None:
        """Streams logs from the subprocess to terminal and file."""
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                # Print to terminal
                sys.stdout.write(line)
                sys.stdout.flush()
                # Write to file
                log_file_handle.write(line)
                log_file_handle.flush()
        except Exception as e:
            logger.error(f"[ERROR] Log streaming error: {e}")
        finally:
            pipe.close()

    def start(self) -> bool:
        """Start the Nexus platform with real-time log streaming."""
        if self.running:
            return True

        logger.info("=" * 80)
        logger.info(
            f"INITIALIZING NEXUS 24/7 ELITE TRADING PLATFORM - "
            f"SESSION #{self.restart_count}"
        )
        logger.info("=" * 80)

        self.running = True
        self.start_time = datetime.now()
        self.restart_count += 1

        try:
            # Create orchestrator log
            now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = Path("logs") / f"nexus_orchestrator_{now_str}.log"
            self._orchestrator_log = open(log_path, "w", encoding="utf-8")

            # Launch orchestrator with pipe
            self.orchestrator_process = subprocess.Popen(  # nosec B603
                [sys.executable, "nexus_orchestrator.py"],
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Start background thread to stream logs to terminal
            self.log_thread = threading.Thread(
                target=self._stream_logs,
                args=(self.orchestrator_process.stdout, self._orchestrator_log),
                daemon=True,
            )
            self.log_thread.start()

            logger.info(
                f"[SYSTEM] Orchestrator online (PID: {self.orchestrator_process.pid})"
            )
            logger.info("[MONITOR] Streaming real-time execution to terminal...")

        except Exception as exc:
            logger.error(f"[FAIL] Failed to start platform: {exc}")
            self.running = False
            return False

        return True

    def monitor(self) -> bool:
        """Monitor the running platform."""
        if not self.running or not self.orchestrator_process:
            return False

        poll_status = self.orchestrator_process.poll()
        if poll_status is not None:
            logger.error(
                f"[CRASH] Platform exited unexpectedly with code: {poll_status}"
            )
            self.running = False
            return False

        return True

    def get_runtime_stats(self) -> Dict[str, Any]:
        """Get runtime statistics."""
        if self.start_time:
            current_runtime = datetime.now() - self.start_time
            return {
                "uptime": str(current_runtime).split(".")[0],
                "restarts": self.restart_count,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
        return cast(Dict[str, Any], {})

    def log_status(self) -> None:
        """Log current system status with high visibility."""
        stats = self.get_runtime_stats()
        logger.info(
            f"[HEARTBEAT] Uptime: {stats.get('uptime')} | "
            f"Restarts: {stats.get('restarts')} | "
            f"Status: ACTIVE 24/7"
        )

    def shutdown(self) -> None:
        """Shutdown the platform gracefully."""
        if not self.running:
            return

        logger.info("=" * 80)
        logger.info("SHUTTING DOWN NEXUS 24/7 TRADING PLATFORM")
        logger.info("=" * 80)

        if self.orchestrator_process:
            try:
                self.orchestrator_process.terminate()
                self.orchestrator_process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Force killing orchestrator: {e}")
                self.orchestrator_process.kill()

        if hasattr(self, "_orchestrator_log") and self._orchestrator_log:
            self._orchestrator_log.close()

        self.running = False

    def run_24_7(self) -> None:
        """Main 24/7 operation loop with real-time monitoring."""
        logger.info("[GUARD] Nexus 24/7 Sentinel Started")

        first_run = True
        try:
            while True:
                if not first_run:
                    logger.info("[RECOVERY] Attempting auto-restart in 5 seconds...")
                    time.sleep(5)

                first_run = False

                if not self.start():
                    time.sleep(10)
                    continue

                # Monitoring loop - check every 10 seconds
                last_status_log = time.time()
                while self.monitor():
                    current_time = time.time()
                    # Heartbeat every 60 seconds
                    if current_time - last_status_log >= 60:
                        self.log_status()
                        last_status_log = current_time
                    time.sleep(10)

                self.shutdown()

        except KeyboardInterrupt:
            logger.info("\n[USER] Manual shutdown requested.")
        finally:
            self.shutdown()


if __name__ == "__main__":
    manager = Nexus24x7Manager()
    manager.run_24_7()
