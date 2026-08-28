"""Run the API and web dashboard as one local development process."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def run_dev(
    *,
    host: str = "127.0.0.1",
    api_port: int = 8000,
    web_port: int = 3000,
    reload: bool = True,
    project_root: Path | None = None,
) -> int:
    """Start both servers and stop them together."""
    root = project_root or Path(__file__).resolve().parents[2]
    web_root = root / "web"
    if not (web_root / "package.json").is_file():
        raise RuntimeError(f"Next.js app not found at {web_root}")
    if shutil.which("npm") is None:
        raise RuntimeError("npm is required to run the dashboard")
    if not (web_root / "node_modules" / ".bin" / "next").is_file():
        raise RuntimeError("web dependencies are missing; run `npm install` in web/")

    api_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "helmet.api:app",
        "--host",
        host,
        "--port",
        str(api_port),
    ]
    if reload:
        api_command.append("--reload")
    web_command = [
        "npm",
        "run",
        "dev",
        "--",
        "--hostname",
        host,
        "--port",
        str(web_port),
    ]
    web_environment = {
        **os.environ,
        "NEXT_PUBLIC_API_URL": f"http://{host}:{api_port}",
    }

    print(f"Helmet API: http://{host}:{api_port}")
    print(f"Helmet web: http://{host}:{web_port}")
    print("Press Ctrl+C to stop both servers.")

    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes.append(subprocess.Popen(api_command, cwd=root, start_new_session=True))
        processes.append(
            subprocess.Popen(
                web_command,
                cwd=web_root,
                env=web_environment,
                start_new_session=True,
            )
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        return next(
            (process.returncode for process in processes if process.returncode not in {None, 0}),
            0,
        )
    except KeyboardInterrupt:
        return 0
    finally:
        _stop_processes(processes)


def _stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
