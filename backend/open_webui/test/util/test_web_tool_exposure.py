from types import SimpleNamespace

import open_webui.utils.middleware as middleware
import open_webui.utils.tools as tool_utils
from open_webui.config import UPSTREAM_DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE


def _request(**config_overrides):
    config_dict = {
        "ENABLE_WEB_SEARCH": True,
        "ENABLE_LOCAL_CORPUS_TOOLS": False,
        "ENABLE_NOTES": True,
        "ENABLE_CHANNELS": False,
        "ENABLE_IMAGE_GENERATION": False,
        "ENABLE_IMAGE_EDIT": False,
        "ENABLE_CODE_INTERPRETER": True,
        "TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE": "CUSTOM_TEMPLATE",
    }
    config_dict.update(config_overrides)
    config = SimpleNamespace(
        **config_dict,
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _model(**meta_overrides):
    meta = {
        "capabilities": {
            "web_search": True,
            "memory": False,
            "code_interpreter": True,
        },
        "builtinTools": {
            "web_search": True,
            "knowledge": True,
            "chats": True,
            "memory": True,
            "notes": True,
            "code_interpreter": True,
        },
    }
    meta.update(meta_overrides)
    return {"info": {"meta": meta}}


def test_upstream_vanilla_general_lane_predicate_requires_exact_runtime_state():
    assert tool_utils.is_upstream_vanilla_general_lane(
        features={"web_search": True, "focused_search": False, "deep_research": False},
        params={"working_mode": "general", "research_guided_mode": False},
    )
    assert not tool_utils.is_upstream_vanilla_general_lane(
        features={"web_search": True, "focused_search": True, "deep_research": False},
        params={"working_mode": "general", "research_guided_mode": False},
    )
    assert not tool_utils.is_upstream_vanilla_general_lane(
        features={"web_search": True, "focused_search": False, "deep_research": False},
        params={"working_mode": "science", "research_guided_mode": False},
    )
    assert not tool_utils.is_upstream_vanilla_general_lane(
        features={"web_search": True, "focused_search": False, "deep_research": True},
        params={"working_mode": "general", "research_guided_mode": False},
    )
    assert not tool_utils.is_upstream_vanilla_general_lane(
        features={"web_search": True, "focused_search": False, "deep_research": False},
        params={"working_mode": "general", "research_guided_mode": True},
    )


def test_builtin_tools_restore_true_upstream_vanilla_general_lane():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {"__metadata__": {"params": {"working_mode": "general", "old_chats_search_enabled": False}}},
        features={"web_search": True, "focused_search": False, "deep_research": False},
        model=_model(),
    )

    assert "search_web" in tools
    assert "fetch_url" in tools
    assert "search_chats" in tools
    assert "view_chat" in tools
    assert "search_notes" in tools
    assert "notes_lookup" not in tools
    assert "read_web_page" not in tools
    assert "web_research_strong" not in tools
    assert "local_corpus_list_domains" not in tools


def test_builtin_tools_restore_upstream_knowledge_surface_for_attached_knowledge():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {
            "__metadata__": {"params": {"working_mode": "general"}},
        },
        features={"web_search": True, "focused_search": False, "deep_research": False},
        model=_model(
            knowledge=[
                {"type": "collection", "id": "kb-1"},
                {"type": "file", "id": "file-1"},
                {"type": "note", "id": "note-1"},
            ]
        ),
    )

    assert "list_knowledge" in tools
    assert "search_knowledge_files" in tools
    assert "query_knowledge_files" in tools
    assert "view_file" in tools
    assert "view_knowledge_file" in tools
    assert "view_note" in tools
    assert "list_knowledge_bases" not in tools


def test_builtin_tools_keep_read_first_lane_for_science():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {"__metadata__": {"params": {"working_mode": "science", "local_corpus_mode": "off"}}},
        features={"web_search": True, "focused_search": False, "deep_research": False},
        model=_model(),
    )

    assert "search_web" in tools
    assert "fetch_url" in tools
    assert "read_web_page" in tools
    assert "web_research_strong" not in tools


def test_builtin_tools_keep_focused_lane_tools():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {"__metadata__": {"params": {"working_mode": "general"}}},
        features={"web_search": True, "focused_search": True, "deep_research": False},
        model=_model(),
    )

    assert "search_web" in tools
    assert "fetch_url" in tools
    assert "read_web_page" in tools
    assert "web_research_strong" in tools


def test_builtin_tools_use_upstream_native_web_schemas_in_vanilla_lane():
    tools = tool_utils.get_builtin_tools(
        _request(),
        {"__metadata__": {"params": {"working_mode": "general"}}},
        features={"web_search": True, "focused_search": False, "deep_research": False},
        model=_model(),
    )

    search_spec = tools["search_web"]["spec"]
    fetch_spec = tools["fetch_url"]["spec"]
    search_properties = search_spec["parameters"]["properties"]
    fetch_properties = fetch_spec["parameters"]["properties"]

    assert set(search_properties) == {"query", "count"}
    assert set(fetch_properties) == {"url"}
    assert "read_web_page" not in search_spec["description"]
    assert "web_research_strong" not in search_spec["description"]
    assert "mode" not in fetch_spec["description"]
    assert "store" not in fetch_spec["description"]


def test_vanilla_general_lane_uses_upstream_default_prompt_template():
    metadata = {
        "params": {"working_mode": "general", "function_calling": "default"},
        "features": {"web_search": True, "focused_search": False, "deep_research": False},
    }

    template = middleware._resolve_tools_function_calling_prompt_template(
        _request(TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE="SHOULD_NOT_APPLY"),
        metadata,
    )

    assert template == UPSTREAM_DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
    assert middleware._resolve_default_selector_guidance(
        metadata,
        {"search_web": {}, "fetch_url": {}},
        [],
    ) == ""


def test_non_vanilla_lane_keeps_custom_prompt_template():
    metadata = {
        "params": {"working_mode": "science", "function_calling": "default"},
        "features": {"web_search": True, "focused_search": False, "deep_research": False},
    }

    template = middleware._resolve_tools_function_calling_prompt_template(
        _request(TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE="CUSTOM_TEMPLATE"),
        metadata,
    )

    assert template == "CUSTOM_TEMPLATE"
