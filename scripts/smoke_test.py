"""Start the production ASGI command and verify the public health contract."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    port = available_port()
    environment = {**os.environ, "PORT": str(port)}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    body = json.loads(response.read().decode("utf-8"))
                    if (
                        response.status == 200
                        and body.get("status") == "ok"
                        and body.get("mcp_connected") is True
                        and body.get("rag_status_source") == "mcp_child"
                        and body.get("rag_backend") == body.get("configured_rag_backend")
                    ):
                        print("Production server smoke test passed")
                        return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError("The production server did not become healthy within 15 seconds.")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
