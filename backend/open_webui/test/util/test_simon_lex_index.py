from open_webui.models.simon_lex_index import _build_subqueries, flatten_content


def test_build_subqueries_dedupes_and_respects_max_branches():
    subqueries = _build_subqueries(["alpha", "beta", "gamma", "delta"], max_branches=4)

    assert len(subqueries) <= 4
    assert len(set(subqueries)) == len(subqueries)


def test_flatten_content_handles_string_list_and_dict():
    assert flatten_content(" hello  world ") == "hello world"
    assert flatten_content({"text": "  hi there  "}) == "hi there"
    assert (
        flatten_content([{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}])
        == "hello world"
    )
