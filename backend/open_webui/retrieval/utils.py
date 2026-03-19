import logging
import os
import json
from typing import Any, Awaitable, Optional, Union

import requests
import aiohttp
import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
import time
import re
import mimetypes
import tempfile

from urllib.parse import quote, urlparse, unquote
from huggingface_hub import snapshot_download
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from open_webui.config import VECTOR_DB
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT


from open_webui.models.users import UserModel
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges

from open_webui.models.chats import Chats
from open_webui.models.notes import Notes
from open_webui.models.access_grants import AccessGrants

from open_webui.retrieval.vector.main import GetResult
from open_webui.utils.headers import include_user_info_headers
from open_webui.utils.misc import get_message_list

from open_webui.retrieval.web.utils import get_web_loader
from open_webui.retrieval.loaders.main import Loader
from open_webui.retrieval.loaders.youtube import YoutubeLoader


from open_webui.env import (
    AIOHTTP_CLIENT_TIMEOUT,
    OFFLINE_MODE,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    AIOHTTP_CLIENT_SESSION_SSL,
)
from open_webui.config import (
    RAG_EMBEDDING_QUERY_PREFIX,
    RAG_EMBEDDING_CONTENT_PREFIX,
    RAG_EMBEDDING_PREFIX_FIELD_NAME,
)

log = logging.getLogger(__name__)


from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.retrievers import BaseRetriever


_BROWSER_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

_LOW_SIGNAL_FETCH_PATTERNS = (
    "please enable js and disable any ad blocker",
    "please enable javascript",
    "enable javascript and cookies",
    "access denied",
    "verify you are human",
    "checking if the site connection is secure",
    "before we continue to reuters",
)

_SUPPORTED_DOCUMENT_EXTENSIONS = {"pdf", "doc", "docx", "md"}
_UNSUPPORTED_BINARY_EXTENSIONS = {
    "ppt",
    "pptx",
    "xls",
    "xlsx",
    "csv",
    "zip",
    "epub",
    "odt",
    "ods",
    "odp",
    "rtf",
    "msg",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "gif",
    "tiff",
    "bmp",
    "mp3",
    "mp4",
    "avi",
    "mov",
}


def is_youtube_url(url: str) -> bool:
    youtube_regex = r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$"
    return re.match(youtube_regex, url) is not None


def get_loader(request, url: str):
    if is_youtube_url(url):
        return YoutubeLoader(
            url,
            language=request.app.state.config.YOUTUBE_LOADER_LANGUAGE,
            proxy_url=request.app.state.config.YOUTUBE_LOADER_PROXY_URL,
        )
    else:
        return get_web_loader(
            url,
            verify_ssl=request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            requests_per_second=request.app.state.config.WEB_LOADER_CONCURRENT_REQUESTS,
            trust_env=request.app.state.config.WEB_SEARCH_TRUST_ENV,
        )


def _get_timeout_value(request, default: float = 20.0) -> float:
    timeout_value = default
    configured_timeout = getattr(request.app.state.config, "WEB_LOADER_TIMEOUT", "")
    try:
        if configured_timeout:
            timeout_value = float(configured_timeout)
    except Exception:
        pass
    return timeout_value


def _infer_url_extension(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    _, ext = os.path.splitext(path)
    return ext.lstrip(".").lower()


def _classify_url_resource(url: str) -> tuple[str, Optional[str]]:
    ext = _infer_url_extension(url)
    if ext in _SUPPORTED_DOCUMENT_EXTENSIONS:
        return "document_supported", ext
    if ext in _UNSUPPORTED_BINARY_EXTENSIONS:
        return "binary_unsupported", ext
    return "html_like", ext or None


def _build_document_loader(request) -> Loader:
    return Loader(
        engine=request.app.state.config.CONTENT_EXTRACTION_ENGINE,
        DATALAB_MARKER_API_KEY=request.app.state.config.DATALAB_MARKER_API_KEY,
        DATALAB_MARKER_API_BASE_URL=request.app.state.config.DATALAB_MARKER_API_BASE_URL,
        DATALAB_MARKER_ADDITIONAL_CONFIG=request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG,
        DATALAB_MARKER_SKIP_CACHE=request.app.state.config.DATALAB_MARKER_SKIP_CACHE,
        DATALAB_MARKER_FORCE_OCR=request.app.state.config.DATALAB_MARKER_FORCE_OCR,
        DATALAB_MARKER_PAGINATE=request.app.state.config.DATALAB_MARKER_PAGINATE,
        DATALAB_MARKER_STRIP_EXISTING_OCR=request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR,
        DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION=request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
        DATALAB_MARKER_FORMAT_LINES=request.app.state.config.DATALAB_MARKER_FORMAT_LINES,
        DATALAB_MARKER_USE_LLM=request.app.state.config.DATALAB_MARKER_USE_LLM,
        DATALAB_MARKER_OUTPUT_FORMAT=request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT,
        EXTERNAL_DOCUMENT_LOADER_URL=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL,
        EXTERNAL_DOCUMENT_LOADER_API_KEY=request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
        TIKA_SERVER_URL=request.app.state.config.TIKA_SERVER_URL,
        DOCLING_SERVER_URL=request.app.state.config.DOCLING_SERVER_URL,
        DOCLING_API_KEY=request.app.state.config.DOCLING_API_KEY,
        DOCLING_PARAMS=request.app.state.config.DOCLING_PARAMS,
        PDF_EXTRACT_IMAGES=request.app.state.config.PDF_EXTRACT_IMAGES,
        PDF_LOADER_MODE=request.app.state.config.PDF_LOADER_MODE,
        DOCUMENT_INTELLIGENCE_ENDPOINT=request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        DOCUMENT_INTELLIGENCE_KEY=request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
        DOCUMENT_INTELLIGENCE_MODEL=request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL,
        MISTRAL_OCR_API_BASE_URL=request.app.state.config.MISTRAL_OCR_API_BASE_URL,
        MISTRAL_OCR_API_KEY=request.app.state.config.MISTRAL_OCR_API_KEY,
        MINERU_API_MODE=request.app.state.config.MINERU_API_MODE,
        MINERU_API_URL=request.app.state.config.MINERU_API_URL,
        MINERU_API_KEY=request.app.state.config.MINERU_API_KEY,
        MINERU_API_TIMEOUT=request.app.state.config.MINERU_API_TIMEOUT,
        MINERU_PARAMS=request.app.state.config.MINERU_PARAMS,
    )


def _fetch_document_bytes(request, url: str) -> tuple[bytes, Optional[str]]:
    verify_ssl = bool(request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION)
    trust_env = bool(request.app.state.config.WEB_SEARCH_TRUST_ENV)
    timeout_value = _get_timeout_value(request)

    session = requests.Session()
    session.trust_env = trust_env
    session.headers.update(
        {
            **_BROWSER_FETCH_HEADERS,
            "Accept": "application/pdf,text/markdown,text/plain,*/*;q=0.8",
        }
    )
    response = session.get(
        url,
        timeout=timeout_value,
        allow_redirects=True,
        verify=verify_ssl,
    )
    response.raise_for_status()
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    return response.content, (content_type or None)


def _document_failure_payload(
    status: str,
    *,
    url: str,
    resource_kind: Optional[str],
    content_type: Optional[str] = None,
    extraction_engine: Optional[str] = None,
    message: Optional[str] = None,
    error_class: Optional[str] = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "url": url,
        "resource_kind": resource_kind,
        "content_type": content_type,
        "content_source": "document_extractor",
        "binary_handling": (
            "unsupported_binary"
            if status == "unsupported_binary"
            else "direct_document_extract"
        ),
        "extraction_engine": extraction_engine,
        "retry_recommended": False,
        "next_action": "choose_another_source",
        "message": message,
    }
    if error_class:
        payload["error_class"] = error_class
    return payload


def _extract_supported_document(
    request, url: str, resource_kind: str
) -> tuple[str, list[Document], dict[str, Any]]:
    extraction_engine = getattr(
        request.app.state.config, "CONTENT_EXTRACTION_ENGINE", ""
    )
    temp_path = None
    guessed_content_type = ""
    try:
        content_bytes, content_type = _fetch_document_bytes(request, url)
        guessed_content_type = content_type or mimetypes.guess_type(url)[0] or ""

        with tempfile.NamedTemporaryFile(suffix=f".{resource_kind}", delete=False) as tmp:
            tmp.write(content_bytes)
            temp_path = tmp.name

        docs = _build_document_loader(request).load(
            filename=f"fetched_document.{resource_kind}",
            file_content_type=guessed_content_type,
            file_path=temp_path,
        )
        content = _normalize_extracted_text(
            "\n\n".join([doc.page_content for doc in docs])
        )
        if not content:
            return "", [], _document_failure_payload(
                "document_extract_failed",
                url=url,
                resource_kind=resource_kind,
                content_type=guessed_content_type or None,
                extraction_engine=extraction_engine,
                message="Document extraction returned no usable text.",
            )

        normalized_docs: list[Document] = []
        for doc in docs:
            metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
            normalized_docs.append(
                Document(
                    page_content=_normalize_extracted_text(doc.page_content),
                    metadata={
                        **metadata,
                        "source": url,
                        "content_source": "document_extractor",
                        "loader_fallback": "document_extractor",
                        "resource_kind": resource_kind,
                        "content_type": guessed_content_type or None,
                        "binary_handling": "direct_document_extract",
                        "extraction_engine": extraction_engine,
                    },
                )
            )

        return content, normalized_docs, {
            "status": "ok",
            "resource_kind": resource_kind,
            "content_type": guessed_content_type or None,
            "content_source": "document_extractor",
            "binary_handling": "direct_document_extract",
            "extraction_engine": extraction_engine,
        }
    except Exception as exc:
        log.exception("Document extraction failed for %s: %s", url, exc)
        return "", [], _document_failure_payload(
            "document_extract_failed",
            url=url,
            resource_kind=resource_kind,
            content_type=guessed_content_type or None,
            extraction_engine=extraction_engine,
            message=f"Document extraction failed for this {resource_kind.upper()} resource.",
            error_class=exc.__class__.__name__,
        )
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _normalize_extracted_text(text: str) -> str:
    normalized = str(text or "").replace("\xa0", " ")
    lines = []
    for line in normalized.splitlines():
        compact = re.sub(r"\s+", " ", line).strip()
        if compact:
            lines.append(compact)
    return "\n".join(lines)


def _is_low_signal_content(text: str) -> bool:
    normalized = _normalize_extracted_text(text)
    if len(normalized) < 200:
        return True

    lowered = normalized.lower()
    if any(pattern in lowered for pattern in _LOW_SIGNAL_FETCH_PATTERNS):
        return True

    return False


def _merge_unique_blocks(blocks: list[str]) -> str:
    seen: set[str] = set()
    merged: list[str] = []
    for block in blocks:
        normalized = _normalize_extracted_text(block)
        if len(normalized) < 30 or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return "\n\n".join(merged)


def _extract_structured_text_from_html(html: str, url: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")

    for tag in soup(["script", "style", "noscript", "svg", "form", "button", "input"]):
        tag.decompose()

    roots = []
    for selector in ("article", "main article", "main", '[role="main"]'):
        roots.extend(soup.select(selector))
    if not roots:
        roots = [soup.body or soup]

    paragraph_selectors = (
        '[data-testid*="paragraph"]',
        '[class*="paragraph"]',
        "p",
        "blockquote",
        "li",
    )

    for root in roots:
        blocks: list[str] = []

        headline = root.find(["h1", "h2"])
        if headline:
            headline_text = _normalize_extracted_text(headline.get_text(" ", strip=True))
            if headline_text:
                blocks.append(headline_text)

        for selector in paragraph_selectors:
            for node in root.select(selector):
                text = node.get_text(" ", strip=True)
                blocks.append(text)

        merged = _merge_unique_blocks(blocks)
        if len(merged) >= 500 and not _is_low_signal_content(merged):
            return merged

    json_ld_blocks: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        payloads = payload if isinstance(payload, list) else [payload]
        for item in payloads:
            if not isinstance(item, dict):
                continue
            article_body = item.get("articleBody") or item.get("description")
            headline = item.get("headline")
            if isinstance(headline, str):
                json_ld_blocks.append(headline)
            if isinstance(article_body, str):
                json_ld_blocks.append(article_body)

    merged_json_ld = _merge_unique_blocks(json_ld_blocks)
    if len(merged_json_ld) >= 300 and not _is_low_signal_content(merged_json_ld):
        return merged_json_ld

    fallback = _merge_unique_blocks([soup.get_text("\n", strip=True)])
    return fallback


def _direct_fetch_html(request, url: str) -> Optional[str]:
    verify_ssl = bool(request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION)
    trust_env = bool(request.app.state.config.WEB_SEARCH_TRUST_ENV)
    timeout_value = _get_timeout_value(request)

    session = requests.Session()
    session.trust_env = trust_env
    session.headers.update(_BROWSER_FETCH_HEADERS)
    response = session.get(
        url,
        timeout=timeout_value,
        allow_redirects=True,
        verify=verify_ssl,
    )
    response.raise_for_status()
    return response.text


def get_content_from_url(request, url: str) -> tuple[str, list[Document], dict[str, Any]]:
    resource_mode, resource_kind = _classify_url_resource(url)

    if resource_mode == "document_supported" and resource_kind:
        return _extract_supported_document(request, url, resource_kind)

    if resource_mode == "binary_unsupported":
        return "", [], _document_failure_payload(
            "unsupported_binary",
            url=url,
            resource_kind=resource_kind,
            content_type=mimetypes.guess_type(url)[0],
            message=(
                f"Direct fetch/extraction is not supported for .{resource_kind} resources. "
                "Choose another source."
            ),
        )

    loader = get_loader(request, url)
    docs = loader.load()
    content = _normalize_extracted_text("\n\n".join([doc.page_content for doc in docs]))

    if not _is_low_signal_content(content):
        return content, docs, {
            "status": "ok",
            "resource_kind": "html",
            "content_source": "primary_loader",
        }

    try:
        html = _direct_fetch_html(request, url)
        fallback_content = _normalize_extracted_text(
            _extract_structured_text_from_html(html, url)
        )
        if not _is_low_signal_content(fallback_content):
            fallback_doc = Document(
                page_content=fallback_content,
                metadata={
                    "source": url,
                    "loader_fallback": "direct_browser_fetch",
                    "content_source": "direct_browser_fetch",
                    "resource_kind": "html",
                    "binary_handling": "html_fetch",
                },
            )
            return fallback_content, [fallback_doc], {
                "status": "ok",
                "resource_kind": "html",
                "content_source": "direct_browser_fetch",
                "binary_handling": "html_fetch",
            }
    except Exception as exc:
        log.debug("Direct browser-like fallback fetch failed for %s: %s", url, exc)

    return content, docs, {
        "status": "ok",
        "resource_kind": "html",
        "content_source": "primary_loader",
        "binary_handling": "html_fetch",
    }


CHUNK_HASH_KEY = "_chunk_hash"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _get_document_hash(doc: Document) -> Optional[str]:
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
    metadata_hash = metadata.get(CHUNK_HASH_KEY)
    if isinstance(metadata_hash, str) and metadata_hash:
        return metadata_hash
    if isinstance(doc.page_content, str):
        return _content_hash(doc.page_content)
    return None


class VectorSearchRetriever(BaseRetriever):
    collection_name: Any
    embedding_function: Any
    top_k: int

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        """Get documents relevant to a query.

        Args:
            query: String to find relevant documents for.
            run_manager: The callback handler to use.

        Returns:
            List of relevant documents.
        """
        return []

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        embedding = await self.embedding_function(query, RAG_EMBEDDING_QUERY_PREFIX)
        result = VECTOR_DB_CLIENT.search(
            collection_name=self.collection_name,
            vectors=[embedding],
            limit=self.top_k,
        )

        ids = result.ids[0]
        metadatas = result.metadatas[0]
        documents = result.documents[0]

        results = []
        for idx in range(len(ids)):
            metadata = dict(metadatas[idx] or {})
            if isinstance(documents[idx], str):
                metadata[CHUNK_HASH_KEY] = _content_hash(documents[idx])
            results.append(
                Document(
                    metadata=metadata,
                    page_content=documents[idx],
                )
            )
        return results


def query_doc(
    collection_name: str, query_embedding: list[float], k: int, user: UserModel = None
):
    try:
        log.debug(f"query_doc:doc {collection_name}")
        result = VECTOR_DB_CLIENT.search(
            collection_name=collection_name,
            vectors=[query_embedding],
            limit=k,
        )

        if result:
            log.info(f"query_doc:result {result.ids} {result.metadatas}")

        return result
    except Exception as e:
        log.exception(f"Error querying doc {collection_name} with limit {k}: {e}")
        raise e


def get_doc(collection_name: str, user: UserModel = None):
    try:
        log.debug(f"get_doc:doc {collection_name}")
        result = VECTOR_DB_CLIENT.get(collection_name=collection_name)

        if result:
            log.info(f"query_doc:result {result.ids} {result.metadatas}")

        return result
    except Exception as e:
        log.exception(f"Error getting doc {collection_name}: {e}")
        raise e


def get_enriched_texts(collection_result: GetResult) -> list[str]:
    enriched_texts = []
    for idx, text in enumerate(collection_result.documents[0]):
        metadata = collection_result.metadatas[0][idx]
        metadata_parts = [text]

        # Add filename (repeat twice for extra weight in BM25 scoring)
        if metadata.get("name"):
            filename = metadata["name"]
            filename_tokens = (
                filename.replace("_", " ").replace("-", " ").replace(".", " ")
            )
            metadata_parts.append(
                f"Filename: {filename} {filename_tokens} {filename_tokens}"
            )

        # Add title if available
        if metadata.get("title"):
            metadata_parts.append(f"Title: {metadata['title']}")

        # Add document section headings if available (from markdown splitter)
        if metadata.get("headings") and isinstance(metadata["headings"], list):
            headings = " > ".join(str(h) for h in metadata["headings"])
            metadata_parts.append(f"Section: {headings}")

        # Add source URL/path if available
        if metadata.get("source"):
            metadata_parts.append(f"Source: {metadata['source']}")

        # Add snippet for web search results
        if metadata.get("snippet"):
            metadata_parts.append(f"Snippet: {metadata['snippet']}")

        enriched_texts.append(" ".join(metadata_parts))

    return enriched_texts


async def query_doc_with_hybrid_search(
    collection_name: str,
    collection_result: GetResult,
    query: str,
    embedding_function,
    k: int,
    reranking_function,
    k_reranker: int,
    r: float,
    hybrid_bm25_weight: float,
    enable_enriched_texts: bool = False,
) -> dict:
    try:
        # First check if collection_result has the required attributes
        if (
            not collection_result
            or not hasattr(collection_result, "documents")
            or not hasattr(collection_result, "metadatas")
        ):
            log.warning(f"query_doc_with_hybrid_search:no_docs {collection_name}")
            return {"documents": [], "metadatas": [], "distances": []}

        # Now safely check the documents content after confirming attributes exist
        if (
            not collection_result.documents
            or len(collection_result.documents) == 0
            or not collection_result.documents[0]
        ):
            log.warning(f"query_doc_with_hybrid_search:no_docs {collection_name}")
            return {"documents": [], "metadatas": [], "distances": []}

        log.debug(f"query_doc_with_hybrid_search:doc {collection_name}")

        original_documents = collection_result.documents[0]
        bm25_texts = (
            get_enriched_texts(collection_result)
            if enable_enriched_texts
            else original_documents
        )

        candidate_k = max(k, k_reranker)

        # Keep content index in metadata so we can restore original document text when
        # enriched BM25 text is enabled.
        bm25_metadatas = []
        for idx, metadata in enumerate(collection_result.metadatas[0]):
            metadata_copy = dict(metadata or {})
            metadata_copy["_content_idx"] = idx
            if idx < len(original_documents) and isinstance(original_documents[idx], str):
                metadata_copy[CHUNK_HASH_KEY] = _content_hash(original_documents[idx])
            bm25_metadatas.append(metadata_copy)

        bm25_retriever = BM25Retriever.from_texts(
            texts=bm25_texts,
            metadatas=bm25_metadatas,
        )
        bm25_retriever.k = candidate_k

        vector_search_retriever = VectorSearchRetriever(
            collection_name=collection_name,
            embedding_function=embedding_function,
            top_k=candidate_k,
        )

        lexical_docs = []
        if hybrid_bm25_weight > 0:
            lexical_docs = await asyncio.to_thread(bm25_retriever.invoke, query)

            restored_lexical_docs = []
            for doc in lexical_docs:
                metadata = dict(doc.metadata or {})
                content_idx = metadata.pop("_content_idx", None)
                page_content = doc.page_content
                if (
                    isinstance(content_idx, int)
                    and 0 <= content_idx < len(original_documents)
                ):
                    page_content = original_documents[content_idx]
                restored_lexical_docs.append(
                    Document(page_content=page_content, metadata=metadata)
                )
            lexical_docs = restored_lexical_docs

        vector_docs = []
        if hybrid_bm25_weight < 1:
            vector_docs = await vector_search_retriever.ainvoke(query)

        # Stage 1: deterministic lexical shortlist (when enabled)
        # Stage 2: vector expansion
        # Stage 3: rerank over merged candidates
        candidate_docs = []
        seen_hashes = set()
        for doc in [*lexical_docs, *vector_docs]:
            doc_hash = _get_document_hash(doc)
            if not doc_hash:
                continue
            if doc_hash in seen_hashes:
                continue
            candidate_docs.append(doc)
            seen_hashes.add(doc_hash)

        if not candidate_docs:
            return {"documents": [], "metadatas": [], "distances": []}

        compressor = RerankCompressor(
            embedding_function=embedding_function,
            top_n=candidate_k,
            reranking_function=reranking_function,
            r_score=r,
        )
        reranked_docs = await compressor.acompress_documents(candidate_docs, query)

        lexical_floor_count = 0
        if hybrid_bm25_weight > 0 and lexical_docs:
            lexical_floor_count = max(1, int(round(k * min(hybrid_bm25_weight, 1.0))))
            lexical_floor_count = min(k, len(lexical_docs), lexical_floor_count)

        log.info(
            "query_doc_with_hybrid_search:stages "
            + f"lexical={len(lexical_docs)} "
            + f"vector={len(vector_docs)} "
            + f"candidates={len(candidate_docs)} "
            + f"reranked={len(reranked_docs)} "
            + f"lexical_floor={lexical_floor_count}"
        )

        reranked_by_hash = {}
        for doc in reranked_docs:
            doc_hash = _get_document_hash(doc)
            if not doc_hash:
                continue
            if doc_hash not in reranked_by_hash:
                reranked_by_hash[doc_hash] = doc

        final_docs = []
        final_hashes = set()

        # Lexical floor: preserve top deterministic hits in final output.
        for idx, doc in enumerate(lexical_docs[:lexical_floor_count]):
            doc_hash = _get_document_hash(doc)
            if not doc_hash:
                continue
            if doc_hash in final_hashes:
                continue

            selected = reranked_by_hash.pop(doc_hash, None)
            if selected is None:
                metadata = dict(doc.metadata or {})
                if metadata.get("score") is None:
                    metadata["score"] = 1.0 - (idx * 1e-4)
                selected = Document(page_content=doc.page_content, metadata=metadata)

            final_docs.append(selected)
            final_hashes.add(doc_hash)

        # Fill remaining slots with reranked candidates.
        for doc in reranked_docs:
            doc_hash = _get_document_hash(doc)
            if not doc_hash:
                continue
            if doc_hash in final_hashes:
                continue
            final_docs.append(doc)
            final_hashes.add(doc_hash)
            if len(final_docs) >= k:
                break

        # Safety fill if reranker filtered too aggressively.
        if len(final_docs) < k:
            for doc in candidate_docs:
                doc_hash = _get_document_hash(doc)
                if not doc_hash:
                    continue
                if doc_hash in final_hashes:
                    continue
                metadata = dict(doc.metadata or {})
                metadata.setdefault("score", 0.0)
                final_docs.append(
                    Document(page_content=doc.page_content, metadata=metadata)
                )
                final_hashes.add(doc_hash)
                if len(final_docs) >= k:
                    break

        final_docs = final_docs[:k]

        distances = [d.metadata.get("score") for d in final_docs]
        documents = [d.page_content for d in final_docs]
        metadatas = [d.metadata for d in final_docs]

        result = {
            "distances": [distances],
            "documents": [documents],
            "metadatas": [metadatas],
        }

        log.info(
            "query_doc_with_hybrid_search:result "
            + f'{result["metadatas"]} {result["distances"]}'
        )
        return result
    except Exception as e:
        log.exception(f"Error querying doc {collection_name} with hybrid search: {e}")
        raise e


def merge_get_results(get_results: list[dict]) -> dict:
    # Initialize lists to store combined data
    combined_documents = []
    combined_metadatas = []
    combined_ids = []

    for data in get_results:
        combined_documents.extend(data["documents"][0])
        combined_metadatas.extend(data["metadatas"][0])
        combined_ids.extend(data["ids"][0])

    # Create the output dictionary
    result = {
        "documents": [combined_documents],
        "metadatas": [combined_metadatas],
        "ids": [combined_ids],
    }

    return result


def merge_and_sort_query_results(query_results: list[dict], k: int) -> dict:
    # Initialize lists to store combined data
    combined = dict()  # To store documents with unique document hashes

    for data in query_results:
        if (
            len(data.get("distances", [])) == 0
            or len(data.get("documents", [])) == 0
            or len(data.get("metadatas", [])) == 0
        ):
            continue

        distances = data["distances"][0]
        documents = data["documents"][0]
        metadatas = data["metadatas"][0]

        for distance, document, metadata in zip(distances, documents, metadatas):
            doc_hash = (
                metadata.get(CHUNK_HASH_KEY) if isinstance(metadata, dict) else None
            )
            if not isinstance(doc_hash, str) or not doc_hash:
                if not isinstance(document, str):
                    continue
                doc_hash = _content_hash(document)

            if doc_hash not in combined.keys():
                combined[doc_hash] = (distance, document, metadata)
                continue  # if doc is new, no further comparison is needed

            # if doc is alredy in, but new distance is better, update
            if distance > combined[doc_hash][0]:
                combined[doc_hash] = (distance, document, metadata)

    combined = list(combined.values())
    # Sort the list based on distances
    combined.sort(key=lambda x: x[0], reverse=True)

    # Slice to keep only the top k elements
    sorted_distances, sorted_documents, sorted_metadatas = (
        zip(*combined[:k]) if combined else ([], [], [])
    )

    # Create and return the output dictionary
    return {
        "distances": [list(sorted_distances)],
        "documents": [list(sorted_documents)],
        "metadatas": [list(sorted_metadatas)],
    }


def estimate_query_result_char_length(query_result: dict | None) -> int:
    if not query_result:
        return 0

    documents = query_result.get("documents", [])
    if not documents or not isinstance(documents, list) or not documents[0]:
        return 0

    total_chars = 0
    for doc in documents[0]:
        if isinstance(doc, str):
            total_chars += len(doc)
    return total_chars


def get_all_items_from_collections(collection_names: list[str]) -> dict:
    results = []

    for collection_name in collection_names:
        if collection_name:
            try:
                result = get_doc(collection_name=collection_name)
                if result is not None:
                    results.append(result.model_dump())
            except Exception as e:
                log.exception(f"Error when querying the collection: {e}")
        else:
            pass

    return merge_get_results(results)


async def query_collection(
    collection_names: list[str],
    queries: list[str],
    embedding_function,
    k: int,
) -> dict:
    results = []
    error = False

    def process_query_collection(collection_name, query_embedding):
        try:
            if collection_name:
                result = query_doc(
                    collection_name=collection_name,
                    k=k,
                    query_embedding=query_embedding,
                )
                if result is not None:
                    return result.model_dump(), None
            return None, None
        except Exception as e:
            log.exception(f"Error when querying the collection: {e}")
            return None, e

    # Generate all query embeddings (in one call)
    query_embeddings = await embedding_function(
        queries, prefix=RAG_EMBEDDING_QUERY_PREFIX
    )
    log.debug(
        f"query_collection: processing {len(queries)} queries across {len(collection_names)} collections"
    )

    with ThreadPoolExecutor() as executor:
        future_results = []
        for query_embedding in query_embeddings:
            for collection_name in collection_names:
                result = executor.submit(
                    process_query_collection, collection_name, query_embedding
                )
                future_results.append(result)
        task_results = [future.result() for future in future_results]

    for result, err in task_results:
        if err is not None:
            error = True
        elif result is not None:
            results.append(result)

    if error and not results:
        log.warning("All collection queries failed. No results returned.")

    return merge_and_sort_query_results(results, k=k)


async def query_collection_with_hybrid_search(
    collection_names: list[str],
    queries: list[str],
    embedding_function,
    k: int,
    reranking_function,
    k_reranker: int,
    r: float,
    hybrid_bm25_weight: float,
    enable_enriched_texts: bool = False,
) -> dict:
    results = []
    error = False
    # Fetch collection data once per collection sequentially
    # Avoid fetching the same data multiple times later
    collection_results = {}
    for collection_name in collection_names:
        try:
            log.debug(
                f"query_collection_with_hybrid_search:VECTOR_DB_CLIENT.get:collection {collection_name}"
            )
            collection_results[collection_name] = VECTOR_DB_CLIENT.get(
                collection_name=collection_name
            )
        except Exception as e:
            log.exception(f"Failed to fetch collection {collection_name}: {e}")
            collection_results[collection_name] = None

    log.info(
        f"Starting hybrid search for {len(queries)} queries in {len(collection_names)} collections..."
    )

    async def process_query(collection_name, query):
        try:
            result = await query_doc_with_hybrid_search(
                collection_name=collection_name,
                collection_result=collection_results[collection_name],
                query=query,
                embedding_function=embedding_function,
                k=k,
                reranking_function=reranking_function,
                k_reranker=k_reranker,
                r=r,
                hybrid_bm25_weight=hybrid_bm25_weight,
                enable_enriched_texts=enable_enriched_texts,
            )
            return result, None
        except Exception as e:
            log.exception(f"Error when querying the collection with hybrid_search: {e}")
            return None, e

    # Prepare tasks for all collections and queries
    # Avoid running any tasks for collections that failed to fetch data (have assigned None)
    tasks = [
        (collection_name, query)
        for collection_name in collection_names
        if collection_results[collection_name] is not None
        for query in queries
    ]

    # Run all queries in parallel using asyncio.gather
    task_results = await asyncio.gather(
        *[process_query(collection_name, query) for collection_name, query in tasks]
    )

    for result, err in task_results:
        if err is not None:
            error = True
        elif result is not None:
            results.append(result)

    if error and not results:
        raise Exception(
            "Hybrid search failed for all collections. Using Non-hybrid search as fallback."
        )

    return merge_and_sort_query_results(results, k=k)


async def query_collection_with_hybrid_fallback(
    collection_names: list[str],
    queries: list[str],
    embedding_function,
    k: int,
    reranking_function,
    k_reranker: int,
    r: float,
    hybrid_bm25_weight: float,
    enable_hybrid_search: bool,
    enable_enriched_texts: bool = False,
) -> dict:
    query_result = None
    use_hybrid = bool(enable_hybrid_search)

    if use_hybrid:
        try:
            query_result = await query_collection_with_hybrid_search(
                collection_names=collection_names,
                queries=queries,
                embedding_function=embedding_function,
                k=k,
                reranking_function=reranking_function,
                k_reranker=k_reranker,
                r=r,
                hybrid_bm25_weight=hybrid_bm25_weight,
                enable_enriched_texts=enable_enriched_texts,
            )
        except Exception:
            log.debug(
                "Error when using hybrid search, using non-hybrid search as fallback."
            )

    if query_result is None:
        query_result = await query_collection(
            collection_names=collection_names,
            queries=queries,
            embedding_function=embedding_function,
            k=k,
        )

    return query_result


def generate_openai_batch_embeddings(
    model: str,
    texts: list[str],
    url: str = "https://api.openai.com/v1",
    key: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"generate_openai_batch_embeddings:model {model} batch size: {len(texts)}"
        )
        json_data = {"input": texts, "model": model}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            json_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
            headers = include_user_info_headers(headers, user)

        r = requests.post(
            f"{url}/embeddings",
            headers=headers,
            json=json_data,
        )
        r.raise_for_status()
        data = r.json()
        if "data" in data:
            return [elem["embedding"] for elem in data["data"]]
        else:
            raise ValueError(
                "Unexpected OpenAI embeddings response: missing 'data' key"
            )
    except Exception as e:
        log.exception(f"Error generating openai batch embeddings: {e}")
        return None


async def agenerate_openai_batch_embeddings(
    model: str,
    texts: list[str],
    url: str = "https://api.openai.com/v1",
    key: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"agenerate_openai_batch_embeddings:model {model} batch size: {len(texts)}"
        )
        form_data = {"input": texts, "model": model}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            form_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
            headers = include_user_info_headers(headers, user)

        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        ) as session:
            async with session.post(
                f"{url}/embeddings",
                headers=headers,
                json=form_data,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                r.raise_for_status()
                data = await r.json()
                if "data" in data:
                    return [item["embedding"] for item in data["data"]]
                else:
                    raise Exception("Something went wrong :/")
    except Exception as e:
        log.exception(f"Error generating openai batch embeddings: {e}")
        return None


def generate_azure_openai_batch_embeddings(
    model: str,
    texts: list[str],
    url: str,
    key: str = "",
    version: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"generate_azure_openai_batch_embeddings:deployment {model} batch size: {len(texts)}"
        )
        json_data = {"input": texts}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            json_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        url = f"{url}/openai/deployments/{model}/embeddings?api-version={version}"

        for _ in range(5):
            headers = {
                "Content-Type": "application/json",
                "api-key": key,
            }
            if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                headers = include_user_info_headers(headers, user)

            r = requests.post(
                url,
                headers=headers,
                json=json_data,
            )
            if r.status_code == 429:
                retry = float(r.headers.get("Retry-After", "1"))
                time.sleep(retry)
                continue
            r.raise_for_status()
            data = r.json()
            if "data" in data:
                return [elem["embedding"] for elem in data["data"]]
            else:
                raise Exception("Something went wrong :/")
        return None
    except Exception as e:
        log.exception(f"Error generating azure openai batch embeddings: {e}")
        return None


async def agenerate_azure_openai_batch_embeddings(
    model: str,
    texts: list[str],
    url: str,
    key: str = "",
    version: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"agenerate_azure_openai_batch_embeddings:deployment {model} batch size: {len(texts)}"
        )
        form_data = {"input": texts}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            form_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        full_url = f"{url}/openai/deployments/{model}/embeddings?api-version={version}"

        headers = {
            "Content-Type": "application/json",
            "api-key": key,
        }
        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
            headers = include_user_info_headers(headers, user)

        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        ) as session:
            async with session.post(
                full_url,
                headers=headers,
                json=form_data,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                r.raise_for_status()
                data = await r.json()
                if "data" in data:
                    return [item["embedding"] for item in data["data"]]
                else:
                    raise Exception("Something went wrong :/")
    except Exception as e:
        log.exception(f"Error generating azure openai batch embeddings: {e}")
        return None


def generate_ollama_batch_embeddings(
    model: str,
    texts: list[str],
    url: str,
    key: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"generate_ollama_batch_embeddings:model {model} batch size: {len(texts)}"
        )
        json_data = {"input": texts, "model": model}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            json_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
            headers = include_user_info_headers(headers, user)

        r = requests.post(
            f"{url}/api/embed",
            headers=headers,
            json=json_data,
        )
        r.raise_for_status()
        data = r.json()

        if "embeddings" in data:
            return data["embeddings"]
        else:
            raise ValueError(
                "Unexpected Ollama embeddings response: missing 'embeddings' key"
            )
    except Exception as e:
        log.exception(f"Error generating ollama batch embeddings: {e}")
        return None


async def agenerate_ollama_batch_embeddings(
    model: str,
    texts: list[str],
    url: str,
    key: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"agenerate_ollama_batch_embeddings:model {model} batch size: {len(texts)}"
        )
        form_data = {"input": texts, "model": model}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            form_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
            headers = include_user_info_headers(headers, user)

        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        ) as session:
            async with session.post(
                f"{url}/api/embed",
                headers=headers,
                json=form_data,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                r.raise_for_status()
                data = await r.json()
                if "embeddings" in data:
                    return data["embeddings"]
                else:
                    raise Exception("Something went wrong :/")
    except Exception as e:
        log.exception(f"Error generating ollama batch embeddings: {e}")
        return None


def get_embedding_function(
    embedding_engine,
    embedding_model,
    embedding_function,
    url,
    key,
    embedding_batch_size,
    azure_api_version=None,
    enable_async=True,
    concurrent_requests=0,
) -> Awaitable:
    if embedding_engine == "":
        # Sentence transformers: CPU-bound sync operation
        async def async_embedding_function(query, prefix=None, user=None):
            return await asyncio.to_thread(
                (
                    lambda query, prefix=None: embedding_function.encode(
                        query,
                        batch_size=int(embedding_batch_size),
                        **({"prompt": prefix} if prefix else {}),
                    ).tolist()
                ),
                query,
                prefix,
            )

        return async_embedding_function
    elif embedding_engine in ["ollama", "openai", "azure_openai"]:
        embedding_function = lambda query, prefix=None, user=None: generate_embeddings(
            engine=embedding_engine,
            model=embedding_model,
            text=query,
            prefix=prefix,
            url=url,
            key=key,
            user=user,
            azure_api_version=azure_api_version,
        )

        async def async_embedding_function(query, prefix=None, user=None):
            if isinstance(query, list):
                # Create batches
                batches = [
                    query[i : i + embedding_batch_size]
                    for i in range(0, len(query), embedding_batch_size)
                ]

                if enable_async:
                    log.debug(
                        f"generate_multiple_async: Processing {len(batches)} batches in parallel"
                    )
                    # Use semaphore to limit concurrent embedding API requests
                    # 0 = unlimited (no semaphore)
                    if concurrent_requests:
                        semaphore = asyncio.Semaphore(concurrent_requests)

                        async def generate_batch_with_semaphore(batch):
                            async with semaphore:
                                return await embedding_function(
                                    batch, prefix=prefix, user=user
                                )

                        tasks = [
                            generate_batch_with_semaphore(batch) for batch in batches
                        ]
                    else:
                        tasks = [
                            embedding_function(batch, prefix=prefix, user=user)
                            for batch in batches
                        ]
                    batch_results = await asyncio.gather(*tasks)
                else:
                    log.debug(
                        f"generate_multiple_async: Processing {len(batches)} batches sequentially"
                    )
                    batch_results = []
                    for batch in batches:
                        batch_results.append(
                            await embedding_function(batch, prefix=prefix, user=user)
                        )

                # Flatten results
                embeddings = []
                for batch_embeddings in batch_results:
                    if isinstance(batch_embeddings, list):
                        embeddings.extend(batch_embeddings)

                log.debug(
                    f"generate_multiple_async: Generated {len(embeddings)} embeddings from {len(batches)} parallel batches"
                )
                return embeddings
            else:
                return await embedding_function(query, prefix, user)

        return async_embedding_function
    else:
        raise ValueError(f"Unknown embedding engine: {embedding_engine}")


async def generate_embeddings(
    engine: str,
    model: str,
    text: Union[str, list[str]],
    prefix: Union[str, None] = None,
    **kwargs,
):
    url = kwargs.get("url", "")
    key = kwargs.get("key", "")
    user = kwargs.get("user")

    if prefix is not None and RAG_EMBEDDING_PREFIX_FIELD_NAME is None:
        if isinstance(text, list):
            text = [f"{prefix}{text_element}" for text_element in text]
        else:
            text = f"{prefix}{text}"

    if engine == "ollama":
        embeddings = await agenerate_ollama_batch_embeddings(
            **{
                "model": model,
                "texts": text if isinstance(text, list) else [text],
                "url": url,
                "key": key,
                "prefix": prefix,
                "user": user,
            }
        )
        return embeddings[0] if isinstance(text, str) else embeddings
    elif engine == "openai":
        embeddings = await agenerate_openai_batch_embeddings(
            model, text if isinstance(text, list) else [text], url, key, prefix, user
        )
        return embeddings[0] if isinstance(text, str) else embeddings
    elif engine == "azure_openai":
        azure_api_version = kwargs.get("azure_api_version", "")
        embeddings = await agenerate_azure_openai_batch_embeddings(
            model,
            text if isinstance(text, list) else [text],
            url,
            key,
            azure_api_version,
            prefix,
            user,
        )
        return embeddings[0] if isinstance(text, str) else embeddings


def get_reranking_function(reranking_engine, reranking_model, reranking_function):
    if reranking_function is None:
        return None
    if reranking_engine == "external":
        return lambda query, documents, user=None: reranking_function.predict(
            [(query, doc.page_content) for doc in documents], user=user
        )
    else:
        return lambda query, documents, user=None: reranking_function.predict(
            [(query, doc.page_content) for doc in documents]
        )


async def get_sources_from_items(
    request,
    items,
    queries,
    embedding_function,
    k,
    reranking_function,
    k_reranker,
    r,
    hybrid_bm25_weight,
    hybrid_search,
    full_context=False,
    user: Optional[UserModel] = None,
):
    log.debug(
        f"items: {items} {queries} {embedding_function} {reranking_function} {full_context}"
    )

    extracted_collections = []
    query_results = []
    full_context_max_chars = int(
        getattr(request.app.state.config, "RAG_FULL_CONTEXT_MAX_CHARS", 200000) or 0
    )

    for item in items:
        query_result = None
        collection_names = []
        force_hybrid_retrieval = False

        if item.get("type") == "text":
            # Raw Text
            # Used during temporary chat file uploads or web page & youtube attachements

            if item.get("context") == "full":
                if item.get("file"):
                    # if item has file data, use it
                    query_result = {
                        "documents": [
                            [item.get("file", {}).get("data", {}).get("content")]
                        ],
                        "metadatas": [[item.get("file", {}).get("meta", {})]],
                    }

            if query_result is None:
                # Fallback
                if item.get("collection_name"):
                    # If item has a collection name, use it
                    collection_names.append(item.get("collection_name"))
                elif item.get("file"):
                    # If item has file data, use it
                    query_result = {
                        "documents": [
                            [item.get("file", {}).get("data", {}).get("content")]
                        ],
                        "metadatas": [[item.get("file", {}).get("meta", {})]],
                    }
                else:
                    # Fallback to item content
                    query_result = {
                        "documents": [[item.get("content")]],
                        "metadatas": [
                            [{"file_id": item.get("id"), "name": item.get("name")}]
                        ],
                    }

        elif item.get("type") == "note":
            # Note Attached
            note = Notes.get_note_by_id(item.get("id"))

            if note and (
                user.role == "admin"
                or note.user_id == user.id
                or AccessGrants.has_access(
                    user_id=user.id,
                    resource_type="note",
                    resource_id=note.id,
                    permission="read",
                )
            ):
                # User has access to the note
                query_result = {
                    "documents": [[note.data.get("content", {}).get("md", "")]],
                    "metadatas": [[{"file_id": note.id, "name": note.title}]],
                }

        elif item.get("type") == "chat":
            # Chat Attached
            chat = Chats.get_chat_by_id(item.get("id"))

            if chat and (user.role == "admin" or chat.user_id == user.id):
                messages_map = chat.chat.get("history", {}).get("messages", {})
                message_id = chat.chat.get("history", {}).get("currentId")

                if messages_map and message_id:
                    # Reconstruct the message list in order
                    message_list = get_message_list(messages_map, message_id)
                    message_history = "\n".join(
                        [
                            f"#### {m.get('role', 'user').capitalize()}\n{m.get('content')}\n"
                            for m in message_list
                        ]
                    )

                    # User has access to the chat
                    query_result = {
                        "documents": [[message_history]],
                        "metadatas": [[{"file_id": chat.id, "name": chat.title}]],
                    }

        elif item.get("type") == "url":
            content, docs, _fetch_meta = get_content_from_url(request, item.get("url"))
            if docs:
                query_result = {
                    "documents": [[content]],
                    "metadatas": [[{"url": item.get("url"), "name": item.get("url")}]],
                }
        elif item.get("type") == "file":
            if (
                item.get("context") == "full"
                or request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL
            ):
                if item.get("file", {}).get("data", {}).get("content", ""):
                    # Manual Full Mode Toggle
                    # Used from chat file modal, we can assume that the file content will be available from item.get("file").get("data", {}).get("content")
                    query_result = {
                        "documents": [
                            [item.get("file", {}).get("data", {}).get("content", "")]
                        ],
                        "metadatas": [
                            [
                                {
                                    "file_id": item.get("id"),
                                    "name": item.get("name"),
                                    **item.get("file")
                                    .get("data", {})
                                    .get("metadata", {}),
                                }
                            ]
                        ],
                    }
                elif item.get("id"):
                    file_object = Files.get_file_by_id(item.get("id"))
                    if file_object:
                        query_result = {
                            "documents": [[file_object.data.get("content", "")]],
                            "metadatas": [
                                [
                                    {
                                        "file_id": item.get("id"),
                                        "name": file_object.filename,
                                        "source": file_object.filename,
                                    }
                                ]
                            ],
                        }
            else:
                # Fallback to collection names
                if item.get("legacy"):
                    collection_names.append(f"{item['id']}")
                else:
                    collection_names.append(f"file-{item['id']}")

        elif item.get("type") == "collection":
            # Manual Full Mode Toggle for Collection
            knowledge_base = Knowledges.get_knowledge_by_id(item.get("id"))

            if knowledge_base and (
                user.role == "admin"
                or knowledge_base.user_id == user.id
                or AccessGrants.has_access(
                    user_id=user.id,
                    resource_type="knowledge",
                    resource_id=knowledge_base.id,
                    permission="read",
                )
            ):
                if (
                    item.get("context") == "full"
                    or request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL
                ):
                    if knowledge_base and (
                        user.role == "admin"
                        or knowledge_base.user_id == user.id
                        or AccessGrants.has_access(
                            user_id=user.id,
                            resource_type="knowledge",
                            resource_id=knowledge_base.id,
                            permission="read",
                        )
                    ):
                        files = Knowledges.get_files_by_id(knowledge_base.id)

                        documents = []
                        metadatas = []
                        for file in files:
                            documents.append(file.data.get("content", ""))
                            metadatas.append(
                                {
                                    "file_id": file.id,
                                    "name": file.filename,
                                    "source": file.filename,
                                }
                            )

                        query_result = {
                            "documents": [documents],
                            "metadatas": [metadatas],
                        }
                else:
                    # Fallback to collection names
                    if item.get("legacy"):
                        collection_names = item.get("collection_names", [])
                    else:
                        collection_names.append(item["id"])

        elif item.get("docs"):
            # BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL
            query_result = {
                "documents": [[doc.get("content") for doc in item.get("docs")]],
                "metadatas": [[doc.get("metadata") for doc in item.get("docs")]],
            }
        elif item.get("collection_name"):
            # Direct Collection Name
            collection_names.append(item["collection_name"])
        elif item.get("collection_names"):
            # Collection Names List
            collection_names.extend(item["collection_names"])

        query_result_char_length = estimate_query_result_char_length(query_result)
        if (
            query_result is not None
            and full_context
            and full_context_max_chars > 0
            and query_result_char_length > full_context_max_chars
        ):
            # Full-context can exceed provider context limits on large attachments.
            # In that case, force deterministic+vector retrieval against indexed collections.
            if item.get("collection_name"):
                collection_names.append(item["collection_name"])
            if item.get("collection_names"):
                collection_names.extend(item["collection_names"])

            if item.get("type") == "file" and item.get("id"):
                if item.get("legacy"):
                    collection_names.append(f"{item['id']}")
                else:
                    collection_names.append(f"file-{item['id']}")
            elif item.get("type") == "collection" and item.get("id"):
                if item.get("legacy"):
                    collection_names.extend(item.get("collection_names", []))
                else:
                    collection_names.append(item["id"])

            if collection_names:
                query_result = None
                force_hybrid_retrieval = True
                log.info(
                    f"Switching from full-context to hybrid retrieval for item {item.get('id') or item.get('name') or item.get('type')}: "
                    + f"{query_result_char_length} chars exceeds max {full_context_max_chars} chars."
                )
            else:
                # If the item is not indexed (no collection available), trim full context
                # to keep injection under a bounded size.
                trimmed_documents = []
                remaining_chars = full_context_max_chars
                for doc in query_result.get("documents", [[]])[0]:
                    if not isinstance(doc, str) or remaining_chars <= 0:
                        continue
                    trimmed_documents.append(doc[:remaining_chars])
                    remaining_chars -= len(trimmed_documents[-1])

                if trimmed_documents:
                    query_result["documents"] = [trimmed_documents]
                    if query_result.get("metadatas"):
                        query_result["metadatas"] = [
                            query_result["metadatas"][0][: len(trimmed_documents)]
                        ]
                log.warning(
                    f"Trimming full-context for non-indexed item {item.get('id') or item.get('name') or item.get('type')}: "
                    + f"{query_result_char_length} chars exceeds max {full_context_max_chars} chars."
                )

        # If query_result is None
        # Fallback to collection names and vector search the collections
        if query_result is None and collection_names:
            # Preserve original order for deterministic prompt/context construction.
            extracted_set = set(extracted_collections)
            seen_collection_names = set()
            filtered_collection_names = []
            for collection_name in collection_names:
                if (
                    not collection_name
                    or collection_name in extracted_set
                    or collection_name in seen_collection_names
                ):
                    continue
                seen_collection_names.add(collection_name)
                filtered_collection_names.append(collection_name)

            collection_names = filtered_collection_names
            if not collection_names:
                log.debug(f"skipping {item} as it has already been extracted")
                continue

            try:
                if full_context and not force_hybrid_retrieval:
                    query_result = get_all_items_from_collections(collection_names)
                    query_result_char_length = estimate_query_result_char_length(
                        query_result
                    )
                    if (
                        full_context_max_chars > 0
                        and query_result_char_length > full_context_max_chars
                    ):
                        log.info(
                            f"Switching from full-context to hybrid retrieval for collections {collection_names}: "
                            + f"{query_result_char_length} chars exceeds max {full_context_max_chars} chars."
                        )
                        query_result = await query_collection_with_hybrid_fallback(
                            collection_names=collection_names,
                            queries=queries,
                            embedding_function=embedding_function,
                            k=k,
                            reranking_function=reranking_function,
                            k_reranker=k_reranker,
                            r=r,
                            hybrid_bm25_weight=hybrid_bm25_weight,
                            enable_hybrid_search=True,
                            enable_enriched_texts=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS,
                        )
                else:
                    query_result = await query_collection_with_hybrid_fallback(
                        collection_names=collection_names,
                        queries=queries,
                        embedding_function=embedding_function,
                        k=k,
                        reranking_function=reranking_function,
                        k_reranker=k_reranker,
                        r=r,
                        hybrid_bm25_weight=hybrid_bm25_weight,
                        enable_hybrid_search=(hybrid_search or force_hybrid_retrieval),
                        enable_enriched_texts=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS,
                    )
            except Exception as e:
                log.exception(e)

            extracted_collections.extend(collection_names)

        if query_result:
            if "data" in item:
                del item["data"]
            query_results.append({**query_result, "file": item})

    sources = []
    for query_result in query_results:
        try:
            if "documents" in query_result:
                if "metadatas" in query_result:
                    source = {
                        "source": query_result["file"],
                        "document": query_result["documents"][0],
                        "metadata": query_result["metadatas"][0],
                    }
                    if "distances" in query_result and query_result["distances"]:
                        source["distances"] = query_result["distances"][0]

                    sources.append(source)
        except Exception as e:
            log.exception(e)
    return sources


def get_model_path(model: str, update_model: bool = False):
    # Construct huggingface_hub kwargs with local_files_only to return the snapshot path
    cache_dir = os.getenv("SENTENCE_TRANSFORMERS_HOME")

    local_files_only = not update_model

    if OFFLINE_MODE:
        local_files_only = True

    snapshot_kwargs = {
        "cache_dir": cache_dir,
        "local_files_only": local_files_only,
    }

    log.debug(f"model: {model}")
    log.debug(f"snapshot_kwargs: {snapshot_kwargs}")

    # Inspiration from upstream sentence_transformers
    if (
        os.path.exists(model)
        or ("\\" in model or model.count("/") > 1)
        and local_files_only
    ):
        # If fully qualified path exists, return input, else set repo_id
        return model
    elif "/" not in model:
        # Set valid repo_id for model short-name
        model = "sentence-transformers" + "/" + model

    snapshot_kwargs["repo_id"] = model

    # Attempt to query the huggingface_hub library to determine the local path and/or to update
    try:
        model_repo_path = snapshot_download(**snapshot_kwargs)
        log.debug(f"model_repo_path: {model_repo_path}")
        return model_repo_path
    except Exception as e:
        log.exception(f"Cannot determine model snapshot path: {e}")
        if OFFLINE_MODE:
            raise
        return model


import operator
from typing import Optional, Sequence

from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document


class RerankCompressor(BaseDocumentCompressor):
    embedding_function: Any
    top_n: int
    reranking_function: Any
    r_score: float

    class Config:
        extra = "forbid"
        arbitrary_types_allowed = True

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        """Compress retrieved documents given the query context.

        Args:
            documents: The retrieved documents.
            query: The query context.
            callbacks: Optional callbacks to run during compression.

        Returns:
            The compressed documents.

        """
        return []

    async def acompress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        reranking = self.reranking_function is not None

        scores = None
        if reranking:
            scores = await asyncio.to_thread(self.reranking_function, query, documents)
        else:
            from sentence_transformers import util

            query_embedding = await self.embedding_function(
                query, RAG_EMBEDDING_QUERY_PREFIX
            )
            document_embedding = await self.embedding_function(
                [doc.page_content for doc in documents], RAG_EMBEDDING_CONTENT_PREFIX
            )
            scores = util.cos_sim(query_embedding, document_embedding)[0]

        if scores is not None:
            docs_with_scores = list(
                zip(
                    documents,
                    scores.tolist() if not isinstance(scores, list) else scores,
                )
            )
            if self.r_score:
                docs_with_scores = [
                    (d, s) for d, s in docs_with_scores if s >= self.r_score
                ]

            result = sorted(docs_with_scores, key=operator.itemgetter(1), reverse=True)
            final_results = []
            for doc, doc_score in result[: self.top_n]:
                metadata = doc.metadata
                metadata["score"] = doc_score
                doc = Document(
                    page_content=doc.page_content,
                    metadata=metadata,
                )
                final_results.append(doc)
            return final_results
        else:
            log.warning(
                "No valid scores found, check your reranking function. Returning original documents."
            )
            return documents
