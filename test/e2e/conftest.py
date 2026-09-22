import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def pytest_ignore_collect(collection_path, config):
    # Skip even importing the optional stack during the default unit-test run.
    if config.getoption("markexpr") == "not e2e":
        return collection_path.name == "test_concurrency.py"
    return None


@pytest.fixture
def e2e_server(tmp_path):
    import requests

    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    log_path = tmp_path / "granian.log"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["SENTRY_E2E_EVENTS"] = str(events_path)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(root), env.get("PYTHONPATH")])
    )

    with log_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "granian",
                "--interface",
                "wsgi",
                "--workers",
                "1",
                "--blocking-threads",
                "8",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "test.e2e.app:application",
            ],
            cwd=root,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(f"Granian exited at startup:\n{log_path.read_text()}")
                try:
                    # A 404 exercises Django readiness without emitting an error event.
                    response = requests.get(f"{base_url}/", timeout=1)
                    if response.status_code == 404:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.05)
            else:
                pytest.fail(f"Granian readiness timed out:\n{log_path.read_text()}")
            yield base_url, events_path
        finally:
            # Include worker children, even when startup or a test assertion fails.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
