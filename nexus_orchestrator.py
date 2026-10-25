import logging
import os
import socket
import subprocess  # nosec B404
import sys
import time
from typing import List, Dict, Any

from nexus.utils.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Orchestrator")


class NexusOrchestrator:
    """Unified orchestrator for the Nexus Quantitative Platform."""

    def __init__(self) -> None:
        self.processes: List[Dict[str, Any]] = []
        self.restart_attempts: Dict[str, int] = {}
        self.root = os.getcwd()

    def find_free_port(self, preferred_port: int, host: str = "127.0.0.1") -> int:
        port = preferred_port
        while port <= 65535:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((host, port))
                    # Port is free, wait a tiny bit after closing
                    time.sleep(0.1)
                    return port
                except OSError:
                    port += 1
        raise RuntimeError("Unable to find a free TCP port.")

    def start_process(
        self, name: str, cmd: List[str], cwd: str, env: Dict[str, str]
    ) -> None:
        logger.info("Launching %s...", name)
        # shell=False to avoid command injection and satisfy security linters.
        p = subprocess.Popen(cmd, cwd=cwd, shell=False, env=env)  # nosec B603

        self.processes.append(
            {"name": name, "cmd": cmd, "cwd": cwd, "env": env, "proc": p}
        )
        self.restart_attempts.setdefault(name, 0)

    def stop_process(self, process: Dict[str, Any]) -> None:
        p = process["proc"]
        if p.poll() is None:
            logger.info("Stopping %s...", process["name"])
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("%s did not terminate, killing it.", process["name"])
                p.kill()

    def restart_process(self, process: Dict[str, Any]) -> None:
        name = process["name"]
        self.stop_process(process)
        self.processes.remove(process)

        self.restart_attempts[name] = self.restart_attempts.get(name, 0) + 1
        if self.restart_attempts[name] > Config.MAX_RESTARTS:
            logger.error(
                "%s exceeded maximum restart attempts (%s). Waiting longer "
                "before retrying.",
                name,
                Config.MAX_RESTARTS,
            )
            time.sleep(60)
            self.restart_attempts[name] = 0

        backoff = min(60, 2 ** self.restart_attempts[name])
        logger.warning(
            "Restarting %s in %ss (attempt %s).",
            name,
            backoff,
            self.restart_attempts[name],
        )
        time.sleep(backoff)
        self.start_process(
            name,
            process["cmd"],
            process["cwd"],
            process["env"],
        )

    def run(self) -> None:

        api_port = self.find_free_port(Config.API_PORT)

        streamlit_port = self.find_free_port(Config.STREAMLIT_PORT)
        backend_url = f"http://127.0.0.1:{api_port}"

        common_env = os.environ.copy()
        common_env["PYTHONPATH"] = self.root
        common_env["NEXUS_API_PORT"] = str(api_port)
        common_env["NEXUS_STREAMLIT_PORT"] = str(streamlit_port)
        common_env["NEXUS_BACKEND_URL"] = backend_url
        common_env["PYTHONUNBUFFERED"] = "1"

        self.start_process(
            "API-Backend",
            [sys.executable, "-m", "nexus.api.main"],
            self.root,
            common_env,
        )

        self.start_process(
            "Core-Engine",
            [sys.executable, "-m", "nexus.core.engine"],
            self.root,
            common_env,
        )

        common_env["NEXUS_STREAMLIT_PORT"] = str(streamlit_port)
        common_env["PYTHONIOENCODING"] = "utf-8"

        streamlit_cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "nexus/ui/app.py",
            "--server.port",
            str(streamlit_port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--server.address",
            Config.API_HOST,
        ]
        self.start_process(
            "Terminal-UI",
            streamlit_cmd,
            self.root,
            common_env,
        )
        logger.info(
            "Nexus Platform is now online. API on %s, UI on " "http://localhost:%s",
            backend_url,
            streamlit_port,
        )

        try:
            while True:
                for process in list(self.processes):
                    p = process["proc"]
                    if p.poll() is not None:
                        logger.error(
                            "Process %s terminated with code %s.",
                            process["name"],
                            p.returncode,
                        )
                        self.restart_process(process)
                time.sleep(10)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        logger.info("Shutting down Nexus Platform...")
        for process in list(self.processes):
            self.stop_process(process)
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    os.environ["PYTHONPATH"] = os.getcwd()
    orchestrator = NexusOrchestrator()
    orchestrator.run()
