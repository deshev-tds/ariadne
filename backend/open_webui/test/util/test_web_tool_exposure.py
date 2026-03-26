from types import SimpleNamespace

import open_webui.utils.tools as tool_utils


def _request():
    config = SimpleNamespace(
        ENABLE_WEB_SEARCH=True,
        ENABLE_LOCAL_CORPUS_TOOLS=False,
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _model():
    return {
        "info": {
            "meta": {
                "capabilities": {"web_search": True},
                "builtinTools": {"web_search": True},
            }
        }
    }


def test_builtin_web_tools_restore_vanilla_general_lane():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {"__metadata__": {"params": {"working_mode": "general"}}},
        features={"web_search": True, "focused_search": False},
        model=_model(),
    )

    assert "search_web" in tools
    assert "fetch_url" in tools
    assert "read_web_page" not in tools
    assert "web_research_strong" not in tools


def test_builtin_web_tools_keep_read_first_lane_for_science():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {"__metadata__": {"params": {"working_mode": "science", "local_corpus_mode": "off"}}},
        features={"web_search": True, "focused_search": False},
        model=_model(),
    )

    assert "search_web" in tools
    assert "fetch_url" in tools
    assert "read_web_page" in tools
    assert "web_research_strong" not in tools


def test_builtin_web_tools_keep_focused_lane_tools():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {"__metadata__": {"params": {"working_mode": "general"}}},
        features={"web_search": True, "focused_search": True},
        model=_model(),
    )

    assert "search_web" in tools
    assert "fetch_url" in tools
    assert "read_web_page" in tools
    assert "web_research_strong" in tools
