import pytest

from clipper import config, llm

CLAUDE_KEY = "sk-ant-test-1234"  # pragma: allowlist secret (a made-up value)
ALIBABA_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
ANTHROPIC = "https://api.anthropic.com"


def load(**env):
    return config.load_ai_config(env)


# --- Claude, the default --------------------------------------------------------------------------------


def test_a_claude_key_alone_is_all_it_takes():
    cfg = load(CLIPPER_API_KEY=CLAUDE_KEY)
    assert (cfg.provider, cfg.base_url, cfg.model) == ("anthropic", ANTHROPIC, "claude-opus-5-5")
    assert cfg.is_claude and cfg.effort is None and cfg.notes == ()
    assert cfg.as_dict() == {"api_key": CLAUDE_KEY, "base_url": ANTHROPIC, "model": "claude-opus-5-5", "provider": "anthropic"}


def test_with_nothing_else_to_go_on_a_key_means_claude():
    # not Alibaba any more: the default service is Claude, and the service itself says if the key is wrong
    cfg = load(CLIPPER_API_KEY="sk-abcdefgh1234")  # pragma: allowlist secret
    assert (cfg.provider, cfg.base_url) == ("anthropic", ANTHROPIC)


def test_anthropic_api_key_is_accepted_as_the_claude_key():
    assert load(ANTHROPIC_API_KEY=CLAUDE_KEY).api_key == CLAUDE_KEY
    assert load(CLIPPER_API_KEY="sk-ant-first-1111", ANTHROPIC_API_KEY=CLAUDE_KEY).api_key == "sk-ant-first-1111"  # CLIPPER's own wins  # pragma: allowlist secret


def test_the_model_can_be_any_claude_model():
    for name in ("claude-sonnet-5-5", "claude-haiku-5-5", "Claude-Opus-5-5"):
        assert load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_MODEL=name).model == name


@pytest.mark.parametrize("url", [ANTHROPIC, ANTHROPIC + "/", ANTHROPIC + "/v1", ANTHROPIC + "/v1/"])
def test_anthropics_own_address_is_accepted_however_it_is_written(url):
    assert load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_BASE_URL=url).base_url == ANTHROPIC


# --- what happens to the old Alibaba lines in .env ----------------------------------------------------------


def test_old_alibaba_lines_are_ignored_and_reported_when_the_key_is_a_claude_key():
    # exactly the situation of someone who swaps only the key line of their .env
    cfg = load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_BASE_URL=ALIBABA_URL, CLIPPER_MODEL="qwen-flash")
    assert (cfg.provider, cfg.base_url, cfg.model) == ("anthropic", ANTHROPIC, "claude-opus-5-5")
    assert len(cfg.notes) == 2
    assert "CLIPPER_BASE_URL" in cfg.notes[0] and "dashscope-intl.aliyuncs.com" in cfg.notes[0]
    assert "CLIPPER_MODEL=qwen-flash" in cfg.notes[1] and "claude-opus-5-5" in cfg.notes[1]


@pytest.mark.parametrize(
    "url",
    [ALIBABA_URL, "https://api.openai.com/v1", "https://example.com/v1", "http://127.0.0.1:8000/v1", "https://api.anthropic.com.evil.example/v1"],
)
def test_a_claude_key_is_never_sent_anywhere_but_anthropic(url):
    cfg = load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_BASE_URL=url)
    assert cfg.provider == "anthropic" and cfg.base_url == ANTHROPIC
    assert cfg.notes, "the ignored address is reported"


def test_the_same_goes_for_a_claude_key_in_anthropic_api_key():
    cfg = load(ANTHROPIC_API_KEY=CLAUDE_KEY, CLIPPER_BASE_URL=ALIBABA_URL)
    assert (cfg.provider, cfg.base_url, cfg.api_key) == ("anthropic", ANTHROPIC, CLAUDE_KEY)


def test_you_can_still_point_claude_at_a_gateway_of_your_own_by_saying_so():
    cfg = load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_PROVIDER="anthropic", CLIPPER_BASE_URL="https://gateway.example/anthropic", CLIPPER_MODEL="my-alias")
    assert (cfg.base_url, cfg.model, cfg.notes) == ("https://gateway.example/anthropic", "my-alias", ())


# --- other services keep working ----------------------------------------------------------------------------------


def test_another_service_is_used_when_its_address_is_set_and_the_key_is_not_a_claude_key():
    cfg = load(CLIPPER_API_KEY="k", CLIPPER_BASE_URL="https://example.com/v1", CLIPPER_MODEL="their-model")
    assert (cfg.provider, cfg.base_url, cfg.model, cfg.is_claude) == ("openai", "https://example.com/v1", "their-model", False)
    assert cfg.as_dict() == {"api_key": "k", "base_url": "https://example.com/v1", "model": "their-model", "provider": "openai"}


def test_alibaba_still_works_with_its_own_address_and_the_old_defaults():
    cfg = load(CLIPPER_API_KEY="k", CLIPPER_BASE_URL=ALIBABA_URL)
    assert (cfg.provider, cfg.base_url, cfg.model) == ("openai", ALIBABA_URL, "qwen-plus")
    cfg = load(CLIPPER_API_KEY="k", CLIPPER_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1", CLIPPER_MODEL="qwen-max")
    assert cfg.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1" and cfg.model == "qwen-max"


def test_any_other_service_has_to_say_which_model():
    with pytest.raises(config.ConfigError, match="CLIPPER_MODEL is not set.*example.com"):
        load(CLIPPER_API_KEY="k", CLIPPER_BASE_URL="https://example.com/v1")


def test_dashscope_alias_accepted_only_for_alibaba_hosts():
    cfg = load(DASHSCOPE_API_KEY="ds-key", CLIPPER_BASE_URL=ALIBABA_URL)  # pragma: allowlist secret
    assert (cfg.api_key, cfg.provider) == ("ds-key", "openai")
    with pytest.raises(config.ConfigError):
        load(DASHSCOPE_API_KEY="ds-key", CLIPPER_BASE_URL="https://example.com/v1", CLIPPER_MODEL="m")  # pragma: allowlist secret
    with pytest.raises(config.ConfigError, match="CLIPPER_API_KEY"):
        load(DASHSCOPE_API_KEY="ds-key")  # no Alibaba address, so the alias is not used  # pragma: allowlist secret


def test_other_providers_keys_are_never_borrowed():
    # An OPENAI_API_KEY must not be sent to whatever endpoint is configured.
    with pytest.raises(config.ConfigError, match="CLIPPER_API_KEY"):
        load(OPENAI_API_KEY="sk-openai-secret")  # pragma: allowlist secret
    with pytest.raises(config.ConfigError, match="CLIPPER_API_KEY"):
        load(OPENAI_API_KEY="sk-openai-secret", CLIPPER_BASE_URL="https://api.openai.com/v1", CLIPPER_MODEL="m")  # pragma: allowlist secret


def test_a_key_set_for_another_service_in_anthropic_api_key_is_not_sent_there_either():
    # with an Alibaba address configured and no CLIPPER key, ANTHROPIC_API_KEY is only a Claude key if it says so
    with pytest.raises(config.ConfigError, match="CLIPPER_API_KEY"):
        load(ANTHROPIC_API_KEY="not-a-claude-key", CLIPPER_BASE_URL=ALIBABA_URL)  # pragma: allowlist secret


# --- naming the provider, and the things that must be refused --------------------------------------------------------


@pytest.mark.parametrize("name", ["anthropic", "Anthropic", "claude", " CLAUDE "])
def test_the_provider_can_be_named_as_claude(name):
    assert load(CLIPPER_API_KEY="k", CLIPPER_PROVIDER=name).provider == "anthropic"


@pytest.mark.parametrize("name", ["openai", "OpenAI-compatible", "compatible"])
def test_the_provider_can_be_named_as_openai_compatible(name):
    assert load(CLIPPER_API_KEY="k", CLIPPER_PROVIDER=name, CLIPPER_BASE_URL="https://example.com/v1", CLIPPER_MODEL="m").provider == "openai"


def test_a_provider_that_does_not_exist_is_an_error():
    with pytest.raises(config.ConfigError, match="CLIPPER_PROVIDER must be 'anthropic' or 'openai', not 'gemini'"):
        load(CLIPPER_API_KEY="k", CLIPPER_PROVIDER="gemini")


def test_openai_without_an_address_is_an_error():
    with pytest.raises(config.ConfigError, match="CLIPPER_PROVIDER=openai needs CLIPPER_BASE_URL"):
        load(CLIPPER_API_KEY="k", CLIPPER_PROVIDER="openai")


def test_a_claude_key_cannot_be_forced_onto_another_service():
    with pytest.raises(config.ConfigError, match=r"Claude key.*only sent to Anthropic"):
        load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_PROVIDER="openai", CLIPPER_BASE_URL="https://example.com/v1", CLIPPER_MODEL="m")


def test_missing_key_is_an_error_that_says_where_to_get_one():
    with pytest.raises(config.ConfigError, match="CLIPPER_API_KEY.*sk-ant-.*platform.claude.com"):
        load()


@pytest.mark.parametrize("bad", ["not a url", "ftp://x/y", "https://"])
def test_invalid_base_url_rejected(bad):
    with pytest.raises(config.ConfigError, match="CLIPPER_BASE_URL"):
        load(CLIPPER_API_KEY="k", CLIPPER_BASE_URL=bad)


# --- effort, and tidying what was pasted --------------------------------------------------------------------------------


def test_effort_is_for_claude_and_checked():
    assert load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_EFFORT=" Low ").effort == "low"
    assert load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_EFFORT="low").as_dict()["effort"] == "low"
    with pytest.raises(config.ConfigError, match="CLIPPER_EFFORT must be one of low, medium, high, xhigh, max, not 'turbo'"):
        load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_EFFORT="turbo")
    other = load(CLIPPER_API_KEY="k", CLIPPER_BASE_URL="https://example.com/v1", CLIPPER_MODEL="m", CLIPPER_EFFORT="low")
    assert other.effort is None and "only applies to Claude" in other.notes[0]


@pytest.mark.parametrize("pasted", [f'"{CLAUDE_KEY}"', f"'{CLAUDE_KEY}'", f"  {CLAUDE_KEY}\n", f' " {CLAUDE_KEY} " '])
def test_quotes_and_spaces_around_a_pasted_key_are_removed(pasted):
    cfg = load(CLIPPER_API_KEY=pasted)
    assert cfg.api_key == CLAUDE_KEY and cfg.provider == "anthropic"


def test_masked_key_never_reveals_the_whole_key():
    cfg = load(CLIPPER_API_KEY="sk-ant-supersecret1234")  # pragma: allowlist secret
    assert cfg.masked_key == "...1234"
    assert "supersecret" not in cfg.masked_key
    assert "supersecret" not in cfg.describe()


def test_describe_names_the_service_and_never_shows_the_key():
    claude = load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_EFFORT="high").describe()
    assert claude == "Claude (Anthropic): model claude-opus-5-5, effort high (key ...1234)"
    other = load(CLIPPER_API_KEY="abcd1234efgh", CLIPPER_BASE_URL="https://example.com/v1", CLIPPER_MODEL="m").describe()  # pragma: allowlist secret
    assert other == "model m @ https://example.com/v1 (key ...efgh)"
    gateway = load(CLIPPER_API_KEY=CLAUDE_KEY, CLIPPER_PROVIDER="claude", CLIPPER_BASE_URL="https://gateway.example").describe()
    assert "@ https://gateway.example" in gateway


# --- which provider a settings dict means ---------------------------------------------------------------------------------


def test_a_settings_dict_that_names_its_provider_is_taken_at_its_word():
    assert llm.provider_of({"provider": "anthropic", "api_key": "k", "base_url": "https://example.com"}) == "anthropic"
    assert llm.provider_of({"provider": "openai", "api_key": CLAUDE_KEY}) == "openai"


@pytest.mark.parametrize(
    "ai, expected",
    [
        ({"api_key": CLAUDE_KEY, "base_url": "http://127.0.0.1:1"}, "anthropic"),  # a Claude key
        ({"api_key": "k", "base_url": "https://api.anthropic.com"}, "anthropic"),  # Anthropic's address
        ({"api_key": "k", "base_url": "https://eu.api.anthropic.com"}, "anthropic"),
        ({"api_key": "k", "base_url": ALIBABA_URL}, "openai"),
        ({"api_key": "k", "base_url": "https://api.anthropic.com.evil.example/v1"}, "openai"),  # not Anthropic's address
        ({"api_key": "k"}, "openai"),
        ({"api_key": "k", "provider": None}, "openai"),
    ],
)
def test_an_older_settings_dict_is_judged_by_its_key_and_address(ai, expected):
    assert llm.provider_of(ai) == expected


# --- .env ---------------------------------------------------------------------------------------------------------


def test_dotenv_does_not_override_real_environment(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    env_file = tmp_path / ".env"
    env_file.write_text("CLIPPER_MODEL=claude-from-file\nCLIPPER_API_KEY=sk-ant-file-5678\n")  # pragma: allowlist secret
    monkeypatch.setenv("CLIPPER_MODEL", "claude-from-env")
    monkeypatch.delenv("CLIPPER_API_KEY", raising=False)
    assert config.load_dotenv_if_present(env_file) is True
    cfg = config.load_ai_config()
    assert cfg.model == "claude-from-env"  # real environment wins
    assert cfg.api_key == "sk-ant-file-5678"  # file fills what is unset  # pragma: allowlist secret
