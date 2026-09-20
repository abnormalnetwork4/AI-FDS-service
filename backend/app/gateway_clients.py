"""Fixed server-side destinations; callers cannot select an arbitrary upstream URL."""
from typing import Protocol

import httpx

from .contracts import FDSIngest, ModelReply, ModelRequest


class ModelClient(Protocol):
    def generate(self, body: ModelRequest) -> ModelReply: ...


class FDSSink(Protocol):
    def send(self, body: FDSIngest) -> None: ...


class HTTPModelClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def generate(self, body: ModelRequest) -> ModelReply:
        # trust_env=False prevents ambient proxy settings from routing internal content outside.
        with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
            response = client.post(self.base_url + "/generate", json=body.model_dump(mode="json"))
            response.raise_for_status()
            return ModelReply.model_validate(response.json())


class HTTPFDSSink:
    def __init__(self, base_url: str, timeout: float):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def send(self, body: FDSIngest) -> None:
        with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
            response = client.post(self.base_url + "/api/v1/ingest/gateway", json=body.model_dump(mode="json"))
            response.raise_for_status()
