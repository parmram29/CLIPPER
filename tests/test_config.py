import pytest

from clipper import config


def test_defaults_target_alibaba_international_endpoint():
    cfg = config.load_ai_config({"CLIPPER_API_KEY": "sk-abcdefgh1234"})
    assert cfg.base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert cfg.model == "qwen-plus"
    assert cfg.as_dict() == {
        "api_key": "sk-abcdefgh1234",  # pragma: allowlist secret (a made-up value)
        "base_url": cfg.base_url,
        "model": "qwen-plus",
    }


def test_explicit_settings_override_defaults():
    cfg = config.load_ai_config(
        {
            "CLIPPER_API_KEY": "k",
            "CLIPPER_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "CLIPPER_MODEL": "qwen-max",
        }
    )
    assert cfg.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert cfg.model == "qwen-max"


def test_missing_key_is_an_error():
    with pytest.raises(config.ConfigError, match="CLIPPER_API_KEY"):
        config.load_ai_config({})


def test_dashscope_alias_accepted_only_for_alibaba_hosts():
    assert config.load_ai_config({"DASHSCOPE_API_KEY": "ds-key"}).api_key == "ds-key"  # pragma: allowlist secret
    with pytest.raises(config.ConfigError):
        config.load_ai_config(
            {"DASHSCOPE_API_KEY": "ds-key", "CLIPPER_BASE_URL": "https://example.com/v1"}  # pragma: allowlist secret
        )


def test_other_providers_keys_are_never_borrowed():
    # An OPENAI_API_KEY must not be sent to whatever endpoint is configured.
    with pytest.raises(config.ConfigError):
        config.load_ai_config({"OPENAI_API_KEY": "sk-openai-secret"})  # pragma: allowlist secret
    with pytest.raises(config.ConfigError):
        config.load_ai_config(
            {"OPENAI_API_KEY": "sk-openai-secret", "CLIPPER_BASE_URL": "https://api.openai.com/v1"}  # pragma: allowlist secret
        )


@pytest.mark.parametrize("bad", ["not a url", "ftp://x/y", "https://"])
def test_invalid_base_url_rejected(bad):
    with pytest.raises(config.ConfigError, match="CLIPPER_BASE_URL"):
        config.load_ai_config({"CLIPPER_API_KEY": "k", "CLIPPER_BASE_URL": bad})


def test_masked_key_never_reveals_the_whole_key():
    cfg = config.load_ai_config({"CLIPPER_API_KEY": "sk-supersecret1234"})  # pragma: allowlist secret
    assert cfg.masked_key == "...1234"
    assert "supersecret" not in cfg.masked_key


def test_dotenv_does_not_override_real_environment(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    env_file = tmp_path / ".env"
    env_file.write_text("CLIPPER_MODEL=from-file\nCLIPPER_API_KEY=file-key\n")  # pragma: allowlist secret
    monkeypatch.setenv("CLIPPER_MODEL", "from-env")
    monkeypatch.delenv("CLIPPER_API_KEY", raising=False)
    assert config.load_dotenv_if_present(env_file) is True
    cfg = config.load_ai_config()
    assert cfg.model == "from-env"  # real environment wins
    assert cfg.api_key == "file-key"  # file fills what is unset  # pragma: allowlist secret
