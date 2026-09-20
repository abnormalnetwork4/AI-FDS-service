"""Exercise real HTTP between three local processes; always stop processes started here."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OPENER = build_opener(ProxyHandler({}))


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def call(base, route, body=None):
    request = Request(base + route, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def main():
    run_dir = ROOT / "data" / ("smoke-" + uuid4().hex)
    run_dir.mkdir(parents=True)
    ports = set()
    while len(ports) < 3:
        ports.add(port())
    fds_port, model_port, gateway_port = sorted(ports)
    fds, model, gateway = [f"http://127.0.0.1:{p}" for p in (fds_port, model_port, gateway_port)]
    env = dict(os.environ, DATABASE_PATH=str(run_dir / "fds.db"), GATEWAY_DATABASE_PATH=str(run_dir / "gateway.db"),
               FDS_BASE_URL=fds, MODEL_BASE_URL=model, GATEWAY_ALLOWED_MODELS="local-demo",
               GATEWAY_MAX_PROMPT_BYTES="8192", MODEL_TIMEOUT_SECONDS="3", FDS_TIMEOUT_SECONDS="3")
    processes = []
    try:
        for module, listen_port in [("app.main", fds_port), ("app.demo_model", model_port), ("app.gateway", gateway_port)]:
            processes.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", module + ":app", "--host", "127.0.0.1", "--port", str(listen_port)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        deadline = time.monotonic() + 15
        for base in (fds, model, gateway):
            while True:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("A service exited before startup completed")
                try:
                    if call(base, "/openapi.json")[0] == 200:
                        break
                except (URLError, TimeoutError):
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Service startup timed out")
                time.sleep(0.1)
        body = {"user_id": "smoke-user", "device_id": "smoke-device", "model": "local-demo", "text": "Test request"}
        status, allowed = call(gateway, "/api/v1/chat", body)
        assert status == 200 and allowed["model_mode"] == "demo", allowed
        status, blocked = call(gateway, "/api/v1/chat", dict(body, model="unapproved"))
        assert status == 403 and blocked["answer"] is None, blocked
        for result in (allowed, blocked):
            deadline = time.monotonic() + 10
            while True:
                _, audit = call(gateway, "/api/v1/requests/" + result["request_id"])
                if audit["fds_delivery"] == "delivered":
                    break
                if audit["fds_delivery"] == "failed" or time.monotonic() > deadline:
                    raise RuntimeError("FDS delivery failed")
                time.sleep(0.1)
            status, assessment = call(fds, "/api/v1/assessments/" + result["request_id"])
            assert status == 200 and len(assessment["results"]) == 3
            assert assessment["policy_action"] == result["action"]
            assert assessment["final_grade"] == "unassessed"
        print("PASS: Gateway allow/block, local model HTTP, FDS delivery and correlated assessments")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
