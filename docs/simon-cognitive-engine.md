# Simon Cognitive Engine Pipe (Proxy-Only)

This integration registers Simon as a custom OpenWebUI `pipe()` model named `Simon Cognitive Engine`.

## 1) Install Simon package into OpenWebUI Python env

```bash
pip install -e /Users/damyandeshev/projects/simon
```

## 2) Register function + model override in OpenWebUI DB

```bash
python scripts/install_simon_cognitive_engine.py \
  --simon-default-model "<your_lm_studio_model_id>"
```

Optional flags:

```bash
--disable-deep-mode
--emit-trace-status
--max-status-events-per-turn 8
```

## 3) Select model in UI

Choose `Simon Cognitive Engine` from model picker.

## Notes

- The installer upserts:
  - Function ID: `simon_cognitive_engine` (`type=pipe`, active)
  - Model override ID: `simon_cognitive_engine` with capabilities disabled:
    - `file_upload`, `file_context`, `web_search`, `image_generation`,
      `code_interpreter`, `builtin_tools`, `vision`
  - Model params include `function_calling: "native"` to avoid forced OpenWebUI feature injections.
- Session continuity mapping lives in Simon DB:
  - `owui_chat_session_map(chat_id TEXT PRIMARY KEY, session_id INTEGER, created_at REAL, updated_at REAL)`
- Phase 1 scope is text-only.
