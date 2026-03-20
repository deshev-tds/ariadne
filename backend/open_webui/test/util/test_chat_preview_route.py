from types import SimpleNamespace

import pytest

import open_webui.routers.chats as chats_router


@pytest.mark.asyncio
async def test_preview_context_window_uses_merged_models_for_preset_ids(monkeypatch):
    wrapper_id = "assistant-step-35-flash-ablitiratedi1-iq4xs"
    base_id = "Step-3.5-Flash-Ablitirated.i1-IQ4_XS"
    history = [
        {"id": "u1", "role": "user", "content": "question"},
        {"id": "a1", "role": "assistant", "content": "answer"},
    ]

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={
                    base_id: {
                        "id": base_id,
                        "name": base_id,
                        "owned_by": "openai",
                        "urlIdx": 0,
                        "openai": {"id": base_id},
                        "status": {"value": "loaded"},
                    }
                }
            )
        )
    )

    monkeypatch.setattr(
        chats_router.Chats,
        "get_chat_by_id_and_user_id",
        lambda *_args, **_kwargs: SimpleNamespace(
            chat={"history": {"currentId": "a1", "messages": {}}}
        ),
    )
    monkeypatch.setattr(chats_router, "get_message_list", lambda *_args, **_kwargs: history)
    monkeypatch.setattr(
        chats_router,
        "inject_image_files_into_history",
        lambda messages: messages,
    )
    monkeypatch.setattr(chats_router, "get_chat_maintenance_state", lambda *_args, **_kwargs: {})

    async def fake_get_all_models(_request, refresh=False, user=None):
        return [
            request.app.state.MODELS[base_id],
            {
                "id": wrapper_id,
                "name": "Assistant Step-3.5-Flash-Ablitirated.i1-IQ4_XS",
                "owned_by": "openai",
                "info": {"base_model_id": base_id},
            },
        ]

    monkeypatch.setattr("open_webui.utils.models.get_all_models", fake_get_all_models)

    captured = {}

    async def fake_build_preview(
        _request,
        *,
        models_map,
        main_model_ids,
        system_message,
        history_messages,
        form_data,
        metadata,
        summary_state,
        maintenance_enabled,
    ):
        captured["models_map_keys"] = sorted(models_map.keys())
        captured["main_model_ids"] = list(main_model_ids)
        return {
            "model_id": base_id,
            "model_name": base_id,
            "live_prompt_cap": 131072,
            "live_prompt_cap_source": "probe:estimate",
            "current_request_tokens": 15186,
            "soft_trigger_tokens": 108544,
            "hard_trigger_tokens": 116736,
            "summary_active": False,
            "compaction_version": 0,
            "maintenance_enabled": maintenance_enabled,
            "token_count_confidence": "fallback",
            "token_count_source": "tiktoken",
            "limiting_model_id": base_id,
            "limiting_model_name": base_id,
            "active_main_model_ids": [base_id],
            "multi_model": False,
            "model_previews": [
                {
                    "model_id": base_id,
                    "model_name": base_id,
                    "live_prompt_cap": 131072,
                    "live_prompt_cap_source": "probe:estimate",
                    "current_request_tokens": 15186,
                    "soft_trigger_tokens": 108544,
                    "hard_trigger_tokens": 116736,
                    "summary_active": False,
                    "compaction_version": 0,
                    "maintenance_enabled": maintenance_enabled,
                    "token_count_confidence": "fallback",
                    "token_count_source": "tiktoken",
                }
            ],
        }

    monkeypatch.setattr(
        chats_router,
        "build_aggregate_context_window_preview",
        fake_build_preview,
    )

    form = chats_router.ContextWindowPreviewForm(
        chat_id="chat-1",
        main_model_ids=[wrapper_id],
        context_maintenance_enabled=True,
    )

    response = await chats_router.preview_context_window(
        request=request,
        form_data=form,
        user=SimpleNamespace(id="user-1"),
        db=None,
    )

    assert wrapper_id in captured["models_map_keys"]
    assert base_id in captured["models_map_keys"]
    assert captured["main_model_ids"] == [wrapper_id]
    assert response.limiting_model_id == base_id
    assert response.live_prompt_cap == 131072
