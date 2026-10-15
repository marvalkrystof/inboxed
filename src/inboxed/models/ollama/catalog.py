"""Read Ollama's installed model catalogue without loading a model."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from inboxed.config import validate_local_ollama_endpoint, validate_local_ollama_model
from inboxed.domain.errors import ModelError


@dataclass(frozen=True)
class InstalledModel:
    name: str
    size: int = 0
    parameters: str = ""
    quantization: str = ""
    capabilities: frozenset[str] | None = None
    inspection_error: str | None = None
    remote: bool = False

    def unavailable_reason(self, capability: str) -> str | None:
        if self.remote:
            return "This model uses a cloud service. Choose a local model."
        if self.inspection_error:
            return self.inspection_error
        if self.capabilities is None:
            return "Ollama did not report this model's capabilities. Update Ollama and refresh."
        if capability not in self.capabilities:
            role = "SystemOne classification" if capability == "decision" else "chat tag generation"
            return f"This model does not support {role}. Choose a compatible model."
        return None

    @property
    def description(self) -> str:
        details = []
        if self.parameters:
            details.append(self.parameters)
        if self.quantization:
            details.append(self.quantization)
        if self.size:
            size = self.size / 1024**3
            details.append(f"{size:.1f} GiB" if size >= 1 else f"{self.size / 1024**2:.0f} MiB")
        return " · ".join(details)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_json(endpoint: str, path: str, timeout: float, body: dict | None = None) -> dict:
    # Discovery never uses environment proxies or follows redirects away from
    # the loopback endpoint. Neither request contains prompts or mailbox data.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        endpoint.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
    )
    with opener.open(request, timeout=timeout) as response:
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Ollama response is too large")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise TypeError("Invalid Ollama response")
    return data


def _inspect_model(endpoint: str, model: InstalledModel, timeout: float) -> InstalledModel:
    if model.remote:
        return model
    try:
        data = _request_json(
            endpoint, "/api/show", timeout, {"model": model.name, "verbose": False}
        )
        if data.get("remote_host") or data.get("remote_model"):
            return replace(model, remote=True)
        capabilities = data.get("capabilities")
        if capabilities is None:
            return model  # Unknown capabilities remain unavailable, without name-based guesses.
        if not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities):
            raise TypeError("Invalid capabilities")
        return replace(model, capabilities=frozenset(capabilities))
    except (URLError, OSError, HTTPException, ValueError, TypeError):
        # A broken/removed model should not hide successfully inspected models.
        # Avoid storing server response bodies in the UI or run configuration.
        return replace(
            model, inspection_error="Could not inspect this model's capabilities. Refresh to retry."
        )


def list_installed_models(endpoint: str, timeout: float = 5) -> list[InstalledModel]:
    try:
        endpoint = validate_local_ollama_endpoint(endpoint)
    except ValueError as exc:
        raise ModelError(str(exc)) from exc
    try:
        data = _request_json(endpoint, "/api/tags", timeout)
        if not isinstance(data.get("models"), list):
            raise TypeError("Invalid model list")
        models = {}
        for item in data["models"]:
            if not isinstance(item, dict):
                raise TypeError("Invalid model entry")
            name = item.get("name") or item.get("model")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            remote = bool(item.get("remote_host") or item.get("remote_model"))
            try:
                validate_local_ollama_model(name)
            except ValueError:
                remote = True
            details = item.get("details") or {}
            if not isinstance(details, dict):
                details = {}
            size = item.get("size", 0)
            models[name] = InstalledModel(
                name=name,
                size=size if isinstance(size, int) and size >= 0 else 0,
                parameters=str(details.get("parameter_size") or ""),
                quantization=str(details.get("quantization_level") or ""),
                remote=remote,
            )
        ordered = sorted(models.values(), key=lambda model: model.name.casefold())
        # Metadata inspection is bounded and does not load models for inference.
        with ThreadPoolExecutor(max_workers=4) as executor:
            return list(
                executor.map(lambda model: _inspect_model(endpoint, model, timeout), ordered)
            )
    except (URLError, OSError, HTTPException) as exc:
        raise ModelError(
            "Could not reach local Ollama. Start it with `ollama serve`, check the endpoint, "
            "and refresh models. You can still enter a model tag manually."
        ) from exc
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ModelError(
            "Ollama returned an invalid model list. Check the endpoint and refresh"
        ) from exc
