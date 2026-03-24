import open_webui.main as main_module


def test_app_state_registers_deep_research_config_keys():
    config = main_module.app.state.config

    assert config.ENABLE_DEEP_RESEARCH in (True, False)
    assert config.ENABLE_RESEARCH_GUIDED in (True, False)
    assert isinstance(config.DEEP_RESEARCH_SIDECAR_URL, str)
    assert isinstance(config.DEEP_RESEARCH_SIDECAR_USERNAME, str)
    assert isinstance(config.DEEP_RESEARCH_SIDECAR_PASSWORD, str)
    assert isinstance(config.DEEP_RESEARCH_POLL_INTERVAL_MS, int)
    assert isinstance(config.DEEP_RESEARCH_TIMEOUT_SECONDS, int)
    assert isinstance(config.DEEP_RESEARCH_EXPORT_FORMAT, str)
