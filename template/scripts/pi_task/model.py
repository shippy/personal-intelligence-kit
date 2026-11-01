import json
from urllib.parse import urlsplit, urlunsplit


def local_model(home):
    settings = json.loads((home / ".pi/agent/settings.json").read_text())
    providers = json.loads((home / ".pi/agent/models.json").read_text())["providers"]
    provider = settings["defaultProvider"]
    model_id = settings["defaultModel"]
    source = providers[provider]
    endpoint = urlsplit(source["baseUrl"])
    if endpoint.scheme != "http" or endpoint.hostname not in ("localhost", "127.0.0.1", "::1") or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("Quick launch requires a local HTTP model endpoint; no cloud fallback")
    model = next(model for model in source["models"] if model["id"] == model_id)
    safe_model = {key: model[key] for key in ("id", "name", "reasoning", "input", "contextWindow", "maxTokens") if key in model}
    base_url = urlunsplit(("http", f"host.docker.internal:{endpoint.port or 80}", endpoint.path, "", ""))
    config = {"providers": {provider: {"baseUrl": base_url, "api": source.get("api", model.get("api", "openai-completions")), "apiKey": "local-pilot", "models": [safe_model]}}}
    return provider, model_id, base_url, config
