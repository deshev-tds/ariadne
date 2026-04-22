import importlib
import sys
import types
from dataclasses import dataclass


def _install_stub(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _import_or_stub(name: str, **attrs):
    try:
        importlib.import_module(name)
    except Exception:
        sys.modules.pop(name, None)
        _install_stub(name, **attrs)


async def _noop_async(*args, **kwargs):
    return None


def _noop(*args, **kwargs):
    return None


@dataclass
class _EmptyForm:
    pass


_import_or_stub(
    "open_webui.routers.tasks",
    generate_queries=_noop_async,
    generate_title=_noop_async,
    generate_follow_ups=_noop_async,
    generate_image_prompt=_noop_async,
    generate_chat_tags=_noop_async,
)

_import_or_stub(
    "open_webui.routers.retrieval",
    process_web_search=_noop_async,
    process_file=_noop_async,
    process_files_batch=_noop_async,
    SearchForm=_EmptyForm,
    ProcessFileForm=_EmptyForm,
    BatchProcessFilesForm=_EmptyForm,
    search_web=_noop_async,
)

_import_or_stub(
    "open_webui.utils.tools",
    get_tools=_noop_async,
    get_builtin_tools=lambda *args, **kwargs: {},
    get_updated_tool_function=lambda *args, **kwargs: None,
    get_terminal_tools=lambda *args, **kwargs: {},
)

_import_or_stub(
    "open_webui.routers.images",
    image_generations=_noop_async,
    image_edits=_noop_async,
    get_image_data=lambda *args, **kwargs: (b"", "image/png"),
    upload_image=lambda *args, **kwargs: (b"", "stub-image-id"),
    CreateImageForm=_EmptyForm,
    EditImageForm=_EmptyForm,
)

_import_or_stub(
    "open_webui.routers.pipelines",
    process_pipeline_inlet_filter=_noop_async,
    process_pipeline_outlet_filter=_noop_async,
)

_import_or_stub(
    "open_webui.routers.memories",
    query_memory=_noop_async,
    QueryMemoryForm=_EmptyForm,
)

_import_or_stub(
    "open_webui.routers.files",
    upload_file_handler=_noop_async,
)

_import_or_stub(
    "open_webui.retrieval.utils",
    get_sources_from_items=lambda *args, **kwargs: [],
)

_import_or_stub(
    "open_webui.retrieval.web.utils",
    validate_url=_noop,
)

_import_or_stub(
    "open_webui.retrieval.web.planner",
    PLANNER_MODES={},
    build_planned_queries_from_rewriter=lambda *args, **kwargs: [],
    build_base_planned_queries=lambda *args, **kwargs: [],
    build_rewriter_prompt=lambda *args, **kwargs: "",
    build_web_search_plan=lambda *args, **kwargs: {},
    parse_rewriter_output=lambda *args, **kwargs: [],
    validate_or_repair_rewriter_queries=lambda *args, **kwargs: [],
)

_import_or_stub(
    "open_webui.retrieval.corpus_runtime",
    resolve_corpus_runtime=lambda *args, **kwargs: None,
)

_import_or_stub(
    "open_webui.retrieval.local_corpus_reasoning",
    normalize_local_corpus_mode=lambda *args, **kwargs: None,
)

_import_or_stub(
    "open_webui.retrieval.working_mode",
    normalize_working_mode=lambda *args, **kwargs: None,
)

_import_or_stub(
    "open_webui.utils.chat",
    generate_chat_completion=_noop_async,
)

_import_or_stub(
    "open_webui.utils.context_maintenance",
    build_inline_maintained_messages=_noop_async,
    build_aggregate_context_window_preview=lambda *args, **kwargs: {},
    get_chat_maintenance_state=lambda *args, **kwargs: {},
    inject_image_files_into_history=lambda messages, *args, **kwargs: messages,
    run_background_context_maintenance=_noop_async,
)

_import_or_stub(
    "open_webui.utils.chat_recall",
    enqueue_branch_backfill=_noop_async,
    extract_branch_message_ids=lambda *args, **kwargs: [],
    maybe_apply_chat_recall=lambda messages, *args, **kwargs: messages,
    resolve_chat_recall_enabled=lambda *args, **kwargs: False,
)

_import_or_stub(
    "open_webui.utils.ledger",
    maybe_apply_ledger=lambda messages, *args, **kwargs: messages,
    run_background_ledger_capture=_noop_async,
)

_import_or_stub(
    "open_webui.utils.task",
    get_task_model_id=lambda *args, **kwargs: "",
    query_generation_template=lambda *args, **kwargs: "",
    rag_template=lambda *args, **kwargs: "",
    tools_function_calling_generation_template=lambda *args, **kwargs: "",
)

_import_or_stub(
    "open_webui.utils.travel_orchestration",
    maybe_run_travel_orchestration=_noop_async,
    should_activate_travel_orchestration=lambda *args, **kwargs: False,
)


class _Storage:
    @staticmethod
    def get_file(path):
        return path


_import_or_stub(
    "open_webui.storage.provider",
    Storage=_Storage,
)
