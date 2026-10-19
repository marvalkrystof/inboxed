import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from inboxed.config import ClassifierConfig, load_settings, save_classifier_config
from inboxed.domain.errors import InboxedError, ModelError
from inboxed.models.ollama.catalog import InstalledModel, list_installed_models


def test_catalog_lists_installed_models_and_metadata_without_cloud_entries():
    payload = {
        "models": [
            {
                "name": "zeta:9b",
                "size": 5 * 1024**3,
                "details": {"parameter_size": "9B", "quantization_level": "Q4_K_M"},
            },
            {"model": "alpha:3b", "size": 500 * 1024**2},
            {
                "name": "zeta:9b",
                "size": 5 * 1024**3,
                "details": {"parameter_size": "9B", "quantization_level": "Q4_K_M"},
            },
            {"name": "hosted:cloud"},
            {"name": "hosted:123b-cloud"},
            {"name": "alias:latest", "remote_host": "https://ollama.com"},
            {"name": "alias2:latest", "remote_model": "hosted:cloud"},
            {"name": ""},
        ]
    }
    opener = Mock()

    def respond(request, **kwargs):
        if request.get_method() == "GET":
            return io.BytesIO(json.dumps(payload).encode())
        return io.BytesIO(b'{"capabilities":["completion","decision"]}')

    opener.open.side_effect = respond
    with patch("inboxed.models.ollama.catalog.build_opener", return_value=opener) as build:
        models = list_installed_models("http://localhost:11434/")
    local = [model for model in models if not model.remote]
    assert [model.name for model in local] == ["alpha:3b", "zeta:9b"]
    assert local[1].description == "9B · Q4_K_M · 5.0 GiB"
    assert local[0].description == "500 MiB"
    assert all(model.capabilities == frozenset({"completion", "decision"}) for model in local)
    assert {model.name for model in models if model.remote} == {
        "hosted:cloud",
        "hosted:123b-cloud",
        "alias:latest",
        "alias2:latest",
    }
    args, kwargs = opener.open.call_args_list[0]
    assert args[0].full_url == "http://localhost:11434/api/tags"
    assert args[0].get_method() == "GET"
    assert kwargs["timeout"] == 5
    show_requests = [call.args[0] for call in opener.open.call_args_list[1:]]
    assert len(show_requests) == 2
    assert all(req.full_url == "http://localhost:11434/api/show" for req in show_requests)
    assert all(req.get_method() == "POST" for req in show_requests)
    assert {json.loads(req.data)["model"] for req in show_requests} == {"alpha:3b", "zeta:9b"}
    assert all(json.loads(req.data)["verbose"] is False for req in show_requests)
    proxy_handler, redirect_handler = build.call_args.args
    assert proxy_handler.proxies == {}
    assert redirect_handler.redirect_request(None, None, 302, "", {}, "https://example.com") is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://ollama.com",
        "http://localhost.evil.com",
        "ftp://localhost",
        "http://user:password@localhost:11434",
        "http://localhost:11434/?token=secret",
    ],
)
def test_catalog_rejects_nonlocal_or_credentialed_endpoints_before_network(endpoint):
    with patch("inboxed.models.ollama.catalog.build_opener") as build:
        with pytest.raises(ModelError, match="loopback"):
            list_installed_models(endpoint)
        build.assert_not_called()


@pytest.mark.parametrize("payload", [b"not JSON", b"[]", b'{"models":{}}', b'{"models":[null]}'])
def test_catalog_invalid_responses_are_actionable(payload):
    opener = Mock()
    opener.open.return_value = io.BytesIO(payload)
    with (
        patch("inboxed.models.ollama.catalog.build_opener", return_value=opener),
        pytest.raises(ModelError, match="invalid model list"),
    ):
        list_installed_models("http://localhost:11434")


def test_catalog_connection_failure_has_manual_fallback_hint():
    opener = Mock()
    opener.open.side_effect = ConnectionRefusedError()
    with (
        patch("inboxed.models.ollama.catalog.build_opener", return_value=opener),
        pytest.raises(ModelError, match="manually"),
    ):
        list_installed_models("http://localhost:11434")


def test_catalog_can_be_empty():
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"models":[]}')
    with patch("inboxed.models.ollama.catalog.build_opener", return_value=opener):
        assert list_installed_models("http://localhost:11434") == []
    assert InstalledModel("custom").description == ""


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(Path("config.example.toml").read_text())
    return path


def test_classifier_settings_preserve_accounts_classes_options(config_path):
    original = load_settings(config_path)
    classifier = ClassifierConfig(
        model="custom-classifier:latest", options={"nested": {"num_ctx": 8192}}
    )
    save_classifier_config(config_path, classifier)
    saved = load_settings(config_path)
    assert saved.classifier == classifier
    assert saved.accounts == original.accounts
    assert saved.classes == original.classes


def test_invalid_classifier_settings_cannot_save(config_path):
    before = config_path.read_bytes()
    classifier = ClassifierConfig.model_construct(model="hosted:cloud")
    with pytest.raises(InboxedError, match="cloud model"):
        save_classifier_config(config_path, classifier)
    assert config_path.read_bytes() == before


def test_failed_config_replacement_keeps_original_and_removes_temp(config_path):
    before = config_path.read_bytes()
    with (
        patch.object(Path, "replace", side_effect=OSError("disk failure")),
        pytest.raises(InboxedError, match="disk failure"),
    ):
        save_classifier_config(config_path, ClassifierConfig())
    assert config_path.read_bytes() == before
    assert list(config_path.parent.glob(".config.toml.*")) == []


@pytest.mark.parametrize("model", ["", "   ", "qwen:cloud", "qwen:123b-cloud"])
def test_model_config_rejects_empty_or_cloud_tags(model):
    with pytest.raises(ValueError):
        ClassifierConfig(model=model)


def test_model_roles_use_reported_capabilities_instead_of_names():
    ordinary_name = InstalledModel("anything:local", capabilities=frozenset({"decision"}))
    misleading_name = InstalledModel("kev-systemone:latest", capabilities=frozenset({"completion"}))
    embedding = InstalledModel("embed:latest", capabilities=frozenset({"embedding"}))
    unknown = InstalledModel("unknown:latest")
    empty = InstalledModel("empty:latest", capabilities=frozenset())
    assert ordinary_name.unavailable_reason("decision") is None
    assert ordinary_name.unavailable_reason("completion") is not None
    assert misleading_name.unavailable_reason("decision") is not None
    assert misleading_name.unavailable_reason("completion") is None
    for model in (embedding, unknown, empty):
        assert model.unavailable_reason("decision") is not None
        assert model.unavailable_reason("completion") is not None


def test_inspection_failures_are_isolated_and_cloud_aliases_are_detected():
    names = [
        "good:local",
        "embedding:local",
        "unknown:local",
        "broken:local",
        "bad-caps:local",
        "cloud-alias:local",
    ]
    shows = {
        "good:local": {"capabilities": ["completion", "vision"]},
        "embedding:local": {"capabilities": ["embedding"]},
        "unknown:local": {},
        "bad-caps:local": {"capabilities": "completion"},
        "cloud-alias:local": {"remote_host": "https://ollama.com", "capabilities": ["completion"]},
    }

    def respond(request, **kwargs):
        if request.get_method() == "GET":
            return io.BytesIO(json.dumps({"models": [{"name": name} for name in names]}).encode())
        name = json.loads(request.data)["model"]
        if name == "broken:local":
            raise OSError("broken model details")
        return io.BytesIO(json.dumps(shows[name]).encode())

    opener = Mock()
    opener.open.side_effect = respond
    with patch("inboxed.models.ollama.catalog.build_opener", return_value=opener):
        models = {model.name: model for model in list_installed_models("http://localhost:11434")}
    assert models["good:local"].unavailable_reason("completion") is None
    assert models["good:local"].unavailable_reason("decision") is not None
    assert models["cloud-alias:local"].remote
    assert models["cloud-alias:local"].unavailable_reason("completion") is not None
    assert models["unknown:local"].capabilities is None
    assert models["broken:local"].inspection_error
    assert models["bad-caps:local"].inspection_error
    assert len(models) == len(names)
