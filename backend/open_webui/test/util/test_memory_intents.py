from open_webui.extensions.simon_engine.memory_intents import (
    detect_archive_recall,
    detect_memory_save,
)


def test_detect_archive_recall_explicit_prefix():
    trigger, explicit, query = detect_archive_recall("/archive: auth rollback plan")

    assert trigger is True
    assert explicit is True
    assert query == "auth rollback plan"


def test_detect_archive_recall_pattern_en_bg():
    trigger_en, explicit_en, query_en = detect_archive_recall(
        "remember when we discussed rate limits?"
    )
    trigger_bg, explicit_bg, query_bg = detect_archive_recall("помниш ли какво говорихме")

    assert trigger_en is True
    assert explicit_en is False
    assert query_en == "remember when we discussed rate limits?"

    assert trigger_bg is True
    assert explicit_bg is False
    assert query_bg == "помниш ли какво говорихме"


def test_detect_memory_save_pattern_en_bg():
    assert detect_memory_save("remember this: my ssh key rotates monthly") is True
    assert detect_memory_save("запомни това: pin е 1234") is True
    assert detect_memory_save("Let's continue with the same endpoint.") is False
