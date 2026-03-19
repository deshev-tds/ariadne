from types import SimpleNamespace

from langchain_core.documents import Document

import open_webui.retrieval.utils as retrieval_utils


def _request_stub():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_WEB_LOADER_SSL_VERIFICATION=True,
                    WEB_SEARCH_TRUST_ENV=False,
                    WEB_LOADER_TIMEOUT="",
                    YOUTUBE_LOADER_LANGUAGE="en",
                    YOUTUBE_LOADER_PROXY_URL="",
                )
            )
        )
    )


def test_get_content_from_url_preserves_usable_primary_content(monkeypatch):
    class Loader:
        def load(self):
            return [
                Document(
                    page_content=(
                        "This is a long primary article body with enough useful text to "
                        "skip fallback extraction entirely. " * 8
                    ),
                    metadata={"source": "https://example.org/article"},
                )
            ]

    monkeypatch.setattr(retrieval_utils, "get_loader", lambda _request, _url: Loader())

    def _unexpected_fallback(_request, _url):
        raise AssertionError("Fallback should not be used for high-signal primary content")

    monkeypatch.setattr(retrieval_utils, "_direct_fetch_html", _unexpected_fallback)

    content, docs = retrieval_utils.get_content_from_url(
        _request_stub(), "https://example.org/article"
    )

    assert "long primary article body" in content
    assert docs[0].metadata["source"] == "https://example.org/article"
    assert "loader_fallback" not in docs[0].metadata


def test_get_content_from_url_falls_back_for_low_signal_primary_content(monkeypatch):
    class Loader:
        def load(self):
            return [
                Document(
                    page_content="reuters.comPlease enable JS and disable any ad blocker",
                    metadata={"source": "https://example.org/reuters-like"},
                )
            ]

    monkeypatch.setattr(retrieval_utils, "get_loader", lambda _request, _url: Loader())
    monkeypatch.setattr(
        retrieval_utils,
        "_direct_fetch_html",
        lambda _request, _url: """
        <html>
          <body>
            <article>
              <h1>Fallback Title</h1>
              <div data-testid="paragraph-0">
                First useful paragraph with concrete economic and logistics details that
                should be preserved by the fallback extractor.
              </div>
              <div data-testid="paragraph-1">
                Second useful paragraph with enough extra context to pass the minimum
                quality threshold for stored evidence artifacts.
              </div>
            </article>
          </body>
        </html>
        """,
    )

    content, docs = retrieval_utils.get_content_from_url(
        _request_stub(), "https://example.org/reuters-like"
    )

    assert "First useful paragraph" in content
    assert "Second useful paragraph" in content
    assert docs[0].metadata["loader_fallback"] == "direct_browser_fetch"
