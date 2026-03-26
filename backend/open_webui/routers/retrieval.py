import json
import logging
import mimetypes
import os
import shutil
import asyncio
import time

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, List, Optional, Sequence, Union

from fastapi import (
    Depends,
    FastAPI,
    Query,
    File,
    Form,
    HTTPException,
    UploadFile,
    Request,
    status,
    APIRouter,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
import tiktoken


from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    TokenTextSplitter,
    MarkdownHeaderTextSplitter,
)
from langchain_core.documents import Document

from open_webui.models.files import FileModel, FileUpdateForm, Files
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.models.knowledge import Knowledges
from open_webui.storage.provider import Storage
from open_webui.internal.db import get_session, get_db
from sqlalchemy.orm import Session


from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

# Document loaders
from open_webui.retrieval.loaders.main import Loader
from open_webui.retrieval.loaders.youtube import YoutubeLoader

# Web search engines
from open_webui.retrieval.web.main import SearchResult
from open_webui.retrieval.web.utils import get_web_loader
from open_webui.retrieval.web.ollama import search_ollama_cloud
from open_webui.retrieval.web.perplexity_search import search_perplexity_search
from open_webui.retrieval.web.brave import search_brave
from open_webui.retrieval.web.kagi import search_kagi
from open_webui.retrieval.web.mojeek import search_mojeek
from open_webui.retrieval.web.bocha import search_bocha
from open_webui.retrieval.web.duckduckgo import search_duckduckgo
from open_webui.retrieval.web.google_pse import search_google_pse
from open_webui.retrieval.web.jina_search import search_jina
from open_webui.retrieval.web.searchapi import search_searchapi
from open_webui.retrieval.web.serpapi import search_serpapi
from open_webui.retrieval.web.searxng import search_searxng
from open_webui.retrieval.web.yacy import search_yacy
from open_webui.retrieval.web.serper import search_serper
from open_webui.retrieval.web.serply import search_serply
from open_webui.retrieval.web.serpstack import search_serpstack
from open_webui.retrieval.web.tavily import search_tavily
from open_webui.retrieval.web.bing import search_bing
from open_webui.retrieval.web.azure import search_azure
from open_webui.retrieval.web.exa import search_exa
from open_webui.retrieval.web.perplexity import search_perplexity
from open_webui.retrieval.web.sougou import search_sougou
from open_webui.retrieval.web.firecrawl import search_firecrawl
from open_webui.retrieval.web.external import search_external
from open_webui.retrieval.web.yandex import search_yandex
from open_webui.retrieval.web.ydc import search_youcom
from open_webui.retrieval.web.planner import (
    PlannedQuery,
    WebSearchPlan,
    build_alternate_general_query,
    build_base_planned_queries,
    build_community_query,
    build_freshness_query,
    build_web_search_plan,
    build_targeted_query,
    canonicalize_url,
    clear_source_registry_caches,
    evaluate_intent_coverage,
    evaluate_signal_quality,
    infer_domain_source_type,
    is_fluff_query,
    load_source_registry,
    load_normalized_source_registry,
    normalize_domain,
    save_source_registry_payload,
    select_sources_for_topic,
    sanitize_query,
    validate_source_registry_payload,
)

from open_webui.retrieval.utils import (
    get_content_from_url,
    get_embedding_function,
    get_reranking_function,
    get_model_path,
    query_collection,
    query_collection_with_hybrid_search,
    query_doc,
    query_doc_with_hybrid_search,
)
from open_webui.retrieval.vector.utils import filter_metadata
from open_webui.utils.misc import (
    calculate_sha256_string,
    sanitize_text_for_db,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_permission

from open_webui.config import (
    ENV,
    RAG_EMBEDDING_MODEL_AUTO_UPDATE,
    RAG_EMBEDDING_MODEL_TRUST_REMOTE_CODE,
    RAG_RERANKING_MODEL_AUTO_UPDATE,
    RAG_RERANKING_MODEL_TRUST_REMOTE_CODE,
    UPLOAD_DIR,
    DEFAULT_LOCALE,
    RAG_EMBEDDING_CONTENT_PREFIX,
    RAG_EMBEDDING_QUERY_PREFIX,
)
from open_webui.env import (
    DEVICE_TYPE,
    DOCKER,
    RAG_EMBEDDING_TIMEOUT,
    SENTENCE_TRANSFORMERS_BACKEND,
    SENTENCE_TRANSFORMERS_MODEL_KWARGS,
    SENTENCE_TRANSFORMERS_CROSS_ENCODER_BACKEND,
    SENTENCE_TRANSFORMERS_CROSS_ENCODER_MODEL_KWARGS,
    SENTENCE_TRANSFORMERS_CROSS_ENCODER_SIGMOID_ACTIVATION_FUNCTION,
)

from open_webui.constants import ERROR_MESSAGES

log = logging.getLogger(__name__)

##########################################
#
# Utility functions
#
##########################################


def get_ef(
    engine: str,
    embedding_model: str,
    auto_update: bool = RAG_EMBEDDING_MODEL_AUTO_UPDATE,
):
    ef = None
    if embedding_model and engine == "":
        from sentence_transformers import SentenceTransformer

        try:
            ef = SentenceTransformer(
                get_model_path(embedding_model, auto_update),
                device=DEVICE_TYPE,
                trust_remote_code=RAG_EMBEDDING_MODEL_TRUST_REMOTE_CODE,
                backend=SENTENCE_TRANSFORMERS_BACKEND,
                model_kwargs=SENTENCE_TRANSFORMERS_MODEL_KWARGS,
            )
        except Exception as e:
            log.debug(f"Error loading SentenceTransformer: {e}")

    return ef


def get_rf(
    engine: str = "",
    reranking_model: Optional[str] = None,
    external_reranker_url: str = "",
    external_reranker_api_key: str = "",
    external_reranker_timeout: str = "",
    auto_update: bool = RAG_RERANKING_MODEL_AUTO_UPDATE,
):
    rf = None
    # Convert timeout string to int or None (system default)
    timeout_value = (
        int(external_reranker_timeout) if external_reranker_timeout else None
    )
    if reranking_model:
        if any(model in reranking_model for model in ["jinaai/jina-colbert-v2"]):
            try:
                from open_webui.retrieval.models.colbert import ColBERT

                rf = ColBERT(
                    get_model_path(reranking_model, auto_update),
                    env="docker" if DOCKER else None,
                )

            except Exception as e:
                log.error(f"ColBERT: {e}")
                raise Exception(ERROR_MESSAGES.DEFAULT(e))
        else:
            if engine == "external":
                try:
                    from open_webui.retrieval.models.external import ExternalReranker

                    rf = ExternalReranker(
                        url=external_reranker_url,
                        api_key=external_reranker_api_key,
                        model=reranking_model,
                        timeout=timeout_value,
                    )
                except Exception as e:
                    log.error(f"ExternalReranking: {e}")
                    raise Exception(ERROR_MESSAGES.DEFAULT(e))
            else:
                import sentence_transformers
                import torch

                try:
                    rf = sentence_transformers.CrossEncoder(
                        get_model_path(reranking_model, auto_update),
                        device=DEVICE_TYPE,
                        trust_remote_code=RAG_RERANKING_MODEL_TRUST_REMOTE_CODE,
                        backend=SENTENCE_TRANSFORMERS_CROSS_ENCODER_BACKEND,
                        model_kwargs=SENTENCE_TRANSFORMERS_CROSS_ENCODER_MODEL_KWARGS,
                        activation_fn=(
                            torch.nn.Sigmoid()
                            if SENTENCE_TRANSFORMERS_CROSS_ENCODER_SIGMOID_ACTIVATION_FUNCTION
                            else None
                        ),
                    )
                except Exception as e:
                    log.error(f"CrossEncoder: {e}")
                    raise Exception(ERROR_MESSAGES.DEFAULT("CrossEncoder error"))

                # Safely adjust pad_token_id if missing as some models do not have this in config
                try:
                    model_cfg = getattr(rf, "model", None)
                    if model_cfg and hasattr(model_cfg, "config"):
                        cfg = model_cfg.config
                        if getattr(cfg, "pad_token_id", None) is None:
                            # Fallback to eos_token_id when available
                            eos = getattr(cfg, "eos_token_id", None)
                            if eos is not None:
                                cfg.pad_token_id = eos
                                log.debug(
                                    f"Missing pad_token_id detected; set to eos_token_id={eos}"
                                )
                            else:
                                log.warning(
                                    "Neither pad_token_id nor eos_token_id present in model config"
                                )
                except Exception as e2:
                    log.warning(f"Failed to adjust pad_token_id on CrossEncoder: {e2}")

    return rf


##########################################
#
# API routes
#
##########################################


router = APIRouter()


class CollectionNameForm(BaseModel):
    collection_name: Optional[str] = None


class ProcessUrlForm(CollectionNameForm):
    url: str


class SearchForm(BaseModel):
    queries: List[str]
    plan: Optional[dict[str, Any]] = None


@router.get("/")
async def get_status(request: Request):
    return {
        "status": True,
        "CHUNK_SIZE": request.app.state.config.CHUNK_SIZE,
        "CHUNK_OVERLAP": request.app.state.config.CHUNK_OVERLAP,
        "RAG_TEMPLATE": request.app.state.config.RAG_TEMPLATE,
        "RAG_EMBEDDING_ENGINE": request.app.state.config.RAG_EMBEDDING_ENGINE,
        "RAG_EMBEDDING_MODEL": request.app.state.config.RAG_EMBEDDING_MODEL,
        "RAG_RERANKING_MODEL": request.app.state.config.RAG_RERANKING_MODEL,
        "RAG_EMBEDDING_BATCH_SIZE": request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
        "ENABLE_ASYNC_EMBEDDING": request.app.state.config.ENABLE_ASYNC_EMBEDDING,
        "RAG_EMBEDDING_CONCURRENT_REQUESTS": request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
    }


@router.get("/embedding")
async def get_embedding_config(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        "RAG_EMBEDDING_ENGINE": request.app.state.config.RAG_EMBEDDING_ENGINE,
        "RAG_EMBEDDING_MODEL": request.app.state.config.RAG_EMBEDDING_MODEL,
        "RAG_EMBEDDING_BATCH_SIZE": request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
        "ENABLE_ASYNC_EMBEDDING": request.app.state.config.ENABLE_ASYNC_EMBEDDING,
        "RAG_EMBEDDING_CONCURRENT_REQUESTS": request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
        "openai_config": {
            "url": request.app.state.config.RAG_OPENAI_API_BASE_URL,
            "key": request.app.state.config.RAG_OPENAI_API_KEY,
        },
        "ollama_config": {
            "url": request.app.state.config.RAG_OLLAMA_BASE_URL,
            "key": request.app.state.config.RAG_OLLAMA_API_KEY,
        },
        "azure_openai_config": {
            "url": request.app.state.config.RAG_AZURE_OPENAI_BASE_URL,
            "key": request.app.state.config.RAG_AZURE_OPENAI_API_KEY,
            "version": request.app.state.config.RAG_AZURE_OPENAI_API_VERSION,
        },
    }


class OpenAIConfigForm(BaseModel):
    url: str
    key: str


class OllamaConfigForm(BaseModel):
    url: str
    key: str


class AzureOpenAIConfigForm(BaseModel):
    url: str
    key: str
    version: str


class EmbeddingModelUpdateForm(BaseModel):
    openai_config: Optional[OpenAIConfigForm] = None
    ollama_config: Optional[OllamaConfigForm] = None
    azure_openai_config: Optional[AzureOpenAIConfigForm] = None
    RAG_EMBEDDING_ENGINE: str
    RAG_EMBEDDING_MODEL: str
    RAG_EMBEDDING_BATCH_SIZE: Optional[int] = 1
    ENABLE_ASYNC_EMBEDDING: Optional[bool] = True
    RAG_EMBEDDING_CONCURRENT_REQUESTS: Optional[int] = 0


def unload_embedding_model(request: Request):
    if request.app.state.config.RAG_EMBEDDING_ENGINE == "":
        # unloads current internal embedding model and clears VRAM cache
        request.app.state.ef = None
        request.app.state.EMBEDDING_FUNCTION = None
        import gc

        gc.collect()
        if DEVICE_TYPE == "cuda":
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()


@router.post("/embedding/update")
async def update_embedding_config(
    request: Request, form_data: EmbeddingModelUpdateForm, user=Depends(get_admin_user)
):
    log.info(
        f"Updating embedding model: {request.app.state.config.RAG_EMBEDDING_MODEL} to {form_data.RAG_EMBEDDING_MODEL}"
    )
    unload_embedding_model(request)
    try:
        request.app.state.config.RAG_EMBEDDING_ENGINE = form_data.RAG_EMBEDDING_ENGINE
        request.app.state.config.RAG_EMBEDDING_MODEL = form_data.RAG_EMBEDDING_MODEL
        request.app.state.config.RAG_EMBEDDING_BATCH_SIZE = (
            form_data.RAG_EMBEDDING_BATCH_SIZE
        )
        request.app.state.config.ENABLE_ASYNC_EMBEDDING = (
            form_data.ENABLE_ASYNC_EMBEDDING
        )
        request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS = (
            form_data.RAG_EMBEDDING_CONCURRENT_REQUESTS
        )

        if request.app.state.config.RAG_EMBEDDING_ENGINE in [
            "ollama",
            "openai",
            "azure_openai",
        ]:
            if form_data.openai_config is not None:
                request.app.state.config.RAG_OPENAI_API_BASE_URL = (
                    form_data.openai_config.url
                )
                request.app.state.config.RAG_OPENAI_API_KEY = (
                    form_data.openai_config.key
                )

            if form_data.ollama_config is not None:
                request.app.state.config.RAG_OLLAMA_BASE_URL = (
                    form_data.ollama_config.url
                )
                request.app.state.config.RAG_OLLAMA_API_KEY = (
                    form_data.ollama_config.key
                )

            if form_data.azure_openai_config is not None:
                request.app.state.config.RAG_AZURE_OPENAI_BASE_URL = (
                    form_data.azure_openai_config.url
                )
                request.app.state.config.RAG_AZURE_OPENAI_API_KEY = (
                    form_data.azure_openai_config.key
                )
                request.app.state.config.RAG_AZURE_OPENAI_API_VERSION = (
                    form_data.azure_openai_config.version
                )

        request.app.state.ef = get_ef(
            request.app.state.config.RAG_EMBEDDING_ENGINE,
            request.app.state.config.RAG_EMBEDDING_MODEL,
        )

        request.app.state.EMBEDDING_FUNCTION = get_embedding_function(
            request.app.state.config.RAG_EMBEDDING_ENGINE,
            request.app.state.config.RAG_EMBEDDING_MODEL,
            request.app.state.ef,
            (
                request.app.state.config.RAG_OPENAI_API_BASE_URL
                if request.app.state.config.RAG_EMBEDDING_ENGINE == "openai"
                else (
                    request.app.state.config.RAG_OLLAMA_BASE_URL
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == "ollama"
                    else request.app.state.config.RAG_AZURE_OPENAI_BASE_URL
                )
            ),
            (
                request.app.state.config.RAG_OPENAI_API_KEY
                if request.app.state.config.RAG_EMBEDDING_ENGINE == "openai"
                else (
                    request.app.state.config.RAG_OLLAMA_API_KEY
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == "ollama"
                    else request.app.state.config.RAG_AZURE_OPENAI_API_KEY
                )
            ),
            request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
            azure_api_version=(
                request.app.state.config.RAG_AZURE_OPENAI_API_VERSION
                if request.app.state.config.RAG_EMBEDDING_ENGINE == "azure_openai"
                else None
            ),
            enable_async=request.app.state.config.ENABLE_ASYNC_EMBEDDING,
            concurrent_requests=request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
        )

        return {
            "status": True,
            "RAG_EMBEDDING_ENGINE": request.app.state.config.RAG_EMBEDDING_ENGINE,
            "RAG_EMBEDDING_MODEL": request.app.state.config.RAG_EMBEDDING_MODEL,
            "RAG_EMBEDDING_BATCH_SIZE": request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
            "ENABLE_ASYNC_EMBEDDING": request.app.state.config.ENABLE_ASYNC_EMBEDDING,
            "RAG_EMBEDDING_CONCURRENT_REQUESTS": request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
            "openai_config": {
                "url": request.app.state.config.RAG_OPENAI_API_BASE_URL,
                "key": request.app.state.config.RAG_OPENAI_API_KEY,
            },
            "ollama_config": {
                "url": request.app.state.config.RAG_OLLAMA_BASE_URL,
                "key": request.app.state.config.RAG_OLLAMA_API_KEY,
            },
            "azure_openai_config": {
                "url": request.app.state.config.RAG_AZURE_OPENAI_BASE_URL,
                "key": request.app.state.config.RAG_AZURE_OPENAI_API_KEY,
                "version": request.app.state.config.RAG_AZURE_OPENAI_API_VERSION,
            },
        }
    except Exception as e:
        log.exception(f"Problem updating embedding model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.get("/config")
async def get_rag_config(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        # RAG settings
        "RAG_TEMPLATE": request.app.state.config.RAG_TEMPLATE,
        "TOP_K": request.app.state.config.TOP_K,
        "BYPASS_EMBEDDING_AND_RETRIEVAL": request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,
        "RAG_FULL_CONTEXT": request.app.state.config.RAG_FULL_CONTEXT,
        "RAG_FULL_CONTEXT_MAX_CHARS": request.app.state.config.RAG_FULL_CONTEXT_MAX_CHARS,
        # Hybrid search settings
        "ENABLE_RAG_HYBRID_SEARCH": request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
        "ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS": request.app.state.config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS,
        "TOP_K_RERANKER": request.app.state.config.TOP_K_RERANKER,
        "RELEVANCE_THRESHOLD": request.app.state.config.RELEVANCE_THRESHOLD,
        "HYBRID_BM25_WEIGHT": request.app.state.config.HYBRID_BM25_WEIGHT,
        # Content extraction settings
        "CONTENT_EXTRACTION_ENGINE": request.app.state.config.CONTENT_EXTRACTION_ENGINE,
        "PDF_EXTRACT_IMAGES": request.app.state.config.PDF_EXTRACT_IMAGES,
        "PDF_LOADER_MODE": request.app.state.config.PDF_LOADER_MODE,
        "DATALAB_MARKER_API_KEY": request.app.state.config.DATALAB_MARKER_API_KEY,
        "DATALAB_MARKER_API_BASE_URL": request.app.state.config.DATALAB_MARKER_API_BASE_URL,
        "DATALAB_MARKER_ADDITIONAL_CONFIG": request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG,
        "DATALAB_MARKER_SKIP_CACHE": request.app.state.config.DATALAB_MARKER_SKIP_CACHE,
        "DATALAB_MARKER_FORCE_OCR": request.app.state.config.DATALAB_MARKER_FORCE_OCR,
        "DATALAB_MARKER_PAGINATE": request.app.state.config.DATALAB_MARKER_PAGINATE,
        "DATALAB_MARKER_STRIP_EXISTING_OCR": request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR,
        "DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION": request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
        "DATALAB_MARKER_FORMAT_LINES": request.app.state.config.DATALAB_MARKER_FORMAT_LINES,
        "DATALAB_MARKER_USE_LLM": request.app.state.config.DATALAB_MARKER_USE_LLM,
        "DATALAB_MARKER_OUTPUT_FORMAT": request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT,
        "EXTERNAL_DOCUMENT_LOADER_URL": request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL,
        "EXTERNAL_DOCUMENT_LOADER_API_KEY": request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
        "TIKA_SERVER_URL": request.app.state.config.TIKA_SERVER_URL,
        "DOCLING_SERVER_URL": request.app.state.config.DOCLING_SERVER_URL,
        "DOCLING_API_KEY": request.app.state.config.DOCLING_API_KEY,
        "DOCLING_PARAMS": request.app.state.config.DOCLING_PARAMS,
        "DOCUMENT_INTELLIGENCE_ENDPOINT": request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        "DOCUMENT_INTELLIGENCE_KEY": request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
        "DOCUMENT_INTELLIGENCE_MODEL": request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL,
        "MISTRAL_OCR_API_BASE_URL": request.app.state.config.MISTRAL_OCR_API_BASE_URL,
        "MISTRAL_OCR_API_KEY": request.app.state.config.MISTRAL_OCR_API_KEY,
        # MinerU settings
        "MINERU_API_MODE": request.app.state.config.MINERU_API_MODE,
        "MINERU_API_URL": request.app.state.config.MINERU_API_URL,
        "MINERU_API_KEY": request.app.state.config.MINERU_API_KEY,
        "MINERU_API_TIMEOUT": request.app.state.config.MINERU_API_TIMEOUT,
        "MINERU_PARAMS": request.app.state.config.MINERU_PARAMS,
        # Reranking settings
        "RAG_RERANKING_MODEL": request.app.state.config.RAG_RERANKING_MODEL,
        "RAG_RERANKING_ENGINE": request.app.state.config.RAG_RERANKING_ENGINE,
        "RAG_EXTERNAL_RERANKER_URL": request.app.state.config.RAG_EXTERNAL_RERANKER_URL,
        "RAG_EXTERNAL_RERANKER_API_KEY": request.app.state.config.RAG_EXTERNAL_RERANKER_API_KEY,
        "RAG_EXTERNAL_RERANKER_TIMEOUT": request.app.state.config.RAG_EXTERNAL_RERANKER_TIMEOUT,
        # Chunking settings
        "TEXT_SPLITTER": request.app.state.config.TEXT_SPLITTER,
        "ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER": request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER,
        "CHUNK_SIZE": request.app.state.config.CHUNK_SIZE,
        "CHUNK_MIN_SIZE_TARGET": request.app.state.config.CHUNK_MIN_SIZE_TARGET,
        "CHUNK_OVERLAP": request.app.state.config.CHUNK_OVERLAP,
        # File upload settings
        "FILE_MAX_SIZE": request.app.state.config.FILE_MAX_SIZE,
        "FILE_MAX_COUNT": request.app.state.config.FILE_MAX_COUNT,
        "FILE_IMAGE_COMPRESSION_WIDTH": request.app.state.config.FILE_IMAGE_COMPRESSION_WIDTH,
        "FILE_IMAGE_COMPRESSION_HEIGHT": request.app.state.config.FILE_IMAGE_COMPRESSION_HEIGHT,
        "ALLOWED_FILE_EXTENSIONS": request.app.state.config.ALLOWED_FILE_EXTENSIONS,
        # Integration settings
        "ENABLE_GOOGLE_DRIVE_INTEGRATION": request.app.state.config.ENABLE_GOOGLE_DRIVE_INTEGRATION,
        "ENABLE_ONEDRIVE_INTEGRATION": request.app.state.config.ENABLE_ONEDRIVE_INTEGRATION,
        # Web search settings
        "web": {
            "ENABLE_WEB_SEARCH": request.app.state.config.ENABLE_WEB_SEARCH,
            "WEB_SEARCH_ENGINE": request.app.state.config.WEB_SEARCH_ENGINE,
            "WEB_SEARCH_TRUST_ENV": request.app.state.config.WEB_SEARCH_TRUST_ENV,
            "WEB_SEARCH_RESULT_COUNT": request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            "WEB_SEARCH_CONCURRENT_REQUESTS": request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS,
            "WEB_SEARCH_LOCAL_FIRST": request.app.state.config.WEB_SEARCH_LOCAL_FIRST,
            "WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS": request.app.state.config.WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS,
            "WEB_SEARCH_BRAVE_FALLBACK": request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK,
            "WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES": request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES,
            "WEB_SEARCH_BRAVE_MIN_INTERVAL_MS": request.app.state.config.WEB_SEARCH_BRAVE_MIN_INTERVAL_MS,
            "ENABLE_WEB_SEARCH_PLANNER": request.app.state.config.ENABLE_WEB_SEARCH_PLANNER,
            "ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER": request.app.state.config.ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER,
            "WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES": request.app.state.config.WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES,
            "WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES": request.app.state.config.WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES,
            "WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE": request.app.state.config.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE,
            "WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE": request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE,
            "WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS": request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS,
            "WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE": request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE,
            "WEB_SEARCH_PLANNER_PLATEAU_DELTA": request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_DELTA,
            "WEB_SEARCH_PLANNER_PLATEAU_STREAK": request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_STREAK,
            "WEB_SEARCH_PLANNER_MODE": request.app.state.config.WEB_SEARCH_PLANNER_MODE,
            "WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES,
            "WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS,
            "WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS,
            "WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS,
            "WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE,
            "WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD": request.app.state.config.WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD,
            "ENABLE_WEB_SEARCH_EVIDENCE_SATURATION": request.app.state.config.ENABLE_WEB_SEARCH_EVIDENCE_SATURATION,
            "WEB_SEARCH_EVIDENCE_MAX_TOKENS": request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_TOKENS,
            "WEB_SEARCH_EVIDENCE_CHUNK_TOKENS": request.app.state.config.WEB_SEARCH_EVIDENCE_CHUNK_TOKENS,
            "WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE": request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE,
            "WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS,
            "WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS,
            "WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE,
            "WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS,
            "WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS,
            "WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS,
            "WEB_LOADER_CONCURRENT_REQUESTS": request.app.state.config.WEB_LOADER_CONCURRENT_REQUESTS,
            "WEB_SEARCH_DOMAIN_FILTER_LIST": request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            "BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL": request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL,
            "BYPASS_WEB_SEARCH_WEB_LOADER": request.app.state.config.BYPASS_WEB_SEARCH_WEB_LOADER,
            "OLLAMA_CLOUD_WEB_SEARCH_API_KEY": request.app.state.config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY,
            "SEARXNG_QUERY_URL": request.app.state.config.SEARXNG_QUERY_URL,
            "SEARXNG_LANGUAGE": request.app.state.config.SEARXNG_LANGUAGE,
            "YACY_QUERY_URL": request.app.state.config.YACY_QUERY_URL,
            "YACY_USERNAME": request.app.state.config.YACY_USERNAME,
            "YACY_PASSWORD": request.app.state.config.YACY_PASSWORD,
            "GOOGLE_PSE_API_KEY": request.app.state.config.GOOGLE_PSE_API_KEY,
            "GOOGLE_PSE_ENGINE_ID": request.app.state.config.GOOGLE_PSE_ENGINE_ID,
            "BRAVE_SEARCH_API_KEY": request.app.state.config.BRAVE_SEARCH_API_KEY,
            "KAGI_SEARCH_API_KEY": request.app.state.config.KAGI_SEARCH_API_KEY,
            "MOJEEK_SEARCH_API_KEY": request.app.state.config.MOJEEK_SEARCH_API_KEY,
            "BOCHA_SEARCH_API_KEY": request.app.state.config.BOCHA_SEARCH_API_KEY,
            "SERPSTACK_API_KEY": request.app.state.config.SERPSTACK_API_KEY,
            "SERPSTACK_HTTPS": request.app.state.config.SERPSTACK_HTTPS,
            "SERPER_API_KEY": request.app.state.config.SERPER_API_KEY,
            "SERPLY_API_KEY": request.app.state.config.SERPLY_API_KEY,
            "DDGS_BACKEND": request.app.state.config.DDGS_BACKEND,
            "TAVILY_API_KEY": request.app.state.config.TAVILY_API_KEY,
            "SEARCHAPI_API_KEY": request.app.state.config.SEARCHAPI_API_KEY,
            "SEARCHAPI_ENGINE": request.app.state.config.SEARCHAPI_ENGINE,
            "SERPAPI_API_KEY": request.app.state.config.SERPAPI_API_KEY,
            "SERPAPI_ENGINE": request.app.state.config.SERPAPI_ENGINE,
            "JINA_API_KEY": request.app.state.config.JINA_API_KEY,
            "JINA_API_BASE_URL": request.app.state.config.JINA_API_BASE_URL,
            "BING_SEARCH_V7_ENDPOINT": request.app.state.config.BING_SEARCH_V7_ENDPOINT,
            "BING_SEARCH_V7_SUBSCRIPTION_KEY": request.app.state.config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            "EXA_API_KEY": request.app.state.config.EXA_API_KEY,
            "PERPLEXITY_API_KEY": request.app.state.config.PERPLEXITY_API_KEY,
            "PERPLEXITY_MODEL": request.app.state.config.PERPLEXITY_MODEL,
            "PERPLEXITY_SEARCH_CONTEXT_USAGE": request.app.state.config.PERPLEXITY_SEARCH_CONTEXT_USAGE,
            "PERPLEXITY_SEARCH_API_URL": request.app.state.config.PERPLEXITY_SEARCH_API_URL,
            "SOUGOU_API_SID": request.app.state.config.SOUGOU_API_SID,
            "SOUGOU_API_SK": request.app.state.config.SOUGOU_API_SK,
            "WEB_LOADER_ENGINE": request.app.state.config.WEB_LOADER_ENGINE,
            "WEB_LOADER_TIMEOUT": request.app.state.config.WEB_LOADER_TIMEOUT,
            "ENABLE_WEB_LOADER_SSL_VERIFICATION": request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            "PLAYWRIGHT_WS_URL": request.app.state.config.PLAYWRIGHT_WS_URL,
            "PLAYWRIGHT_TIMEOUT": request.app.state.config.PLAYWRIGHT_TIMEOUT,
            "PLAYWRIGHT_REMOVE_SELECTORS": request.app.state.config.PLAYWRIGHT_REMOVE_SELECTORS,
            "FIRECRAWL_API_KEY": request.app.state.config.FIRECRAWL_API_KEY,
            "FIRECRAWL_API_BASE_URL": request.app.state.config.FIRECRAWL_API_BASE_URL,
            "FIRECRAWL_TIMEOUT": request.app.state.config.FIRECRAWL_TIMEOUT,
            "TAVILY_EXTRACT_DEPTH": request.app.state.config.TAVILY_EXTRACT_DEPTH,
            "EXTERNAL_WEB_SEARCH_URL": request.app.state.config.EXTERNAL_WEB_SEARCH_URL,
            "EXTERNAL_WEB_SEARCH_API_KEY": request.app.state.config.EXTERNAL_WEB_SEARCH_API_KEY,
            "EXTERNAL_WEB_LOADER_URL": request.app.state.config.EXTERNAL_WEB_LOADER_URL,
            "EXTERNAL_WEB_LOADER_API_KEY": request.app.state.config.EXTERNAL_WEB_LOADER_API_KEY,
            "YOUTUBE_LOADER_LANGUAGE": request.app.state.config.YOUTUBE_LOADER_LANGUAGE,
            "YOUTUBE_LOADER_PROXY_URL": request.app.state.config.YOUTUBE_LOADER_PROXY_URL,
            "YOUTUBE_LOADER_TRANSLATION": request.app.state.YOUTUBE_LOADER_TRANSLATION,
            "YANDEX_WEB_SEARCH_URL": request.app.state.config.YANDEX_WEB_SEARCH_URL,
            "YANDEX_WEB_SEARCH_API_KEY": request.app.state.config.YANDEX_WEB_SEARCH_API_KEY,
            "YANDEX_WEB_SEARCH_CONFIG": request.app.state.config.YANDEX_WEB_SEARCH_CONFIG,
            "YOUCOM_API_KEY": request.app.state.config.YOUCOM_API_KEY,
        },
    }


class WebConfig(BaseModel):
    ENABLE_WEB_SEARCH: Optional[bool] = None
    WEB_SEARCH_ENGINE: Optional[str] = None
    WEB_SEARCH_TRUST_ENV: Optional[bool] = None
    WEB_SEARCH_RESULT_COUNT: Optional[int] = None
    WEB_SEARCH_CONCURRENT_REQUESTS: Optional[int] = None
    WEB_SEARCH_LOCAL_FIRST: Optional[bool] = None
    WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS: Optional[int] = None
    WEB_SEARCH_BRAVE_FALLBACK: Optional[bool] = None
    WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES: Optional[int] = None
    WEB_SEARCH_BRAVE_MIN_INTERVAL_MS: Optional[int] = None
    ENABLE_WEB_SEARCH_PLANNER: Optional[bool] = None
    ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER: Optional[bool] = None
    WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES: Optional[int] = None
    WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES: Optional[int] = None
    WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE: Optional[int] = None
    WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE: Optional[float] = None
    WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS: Optional[int] = None
    WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE: Optional[float] = None
    WEB_SEARCH_PLANNER_PLATEAU_DELTA: Optional[float] = None
    WEB_SEARCH_PLANNER_PLATEAU_STREAK: Optional[int] = None
    WEB_SEARCH_PLANNER_MODE: Optional[str] = None
    WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES: Optional[int] = None
    WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS: Optional[int] = None
    WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS: Optional[int] = None
    WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS: Optional[int] = None
    WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE: Optional[float] = None
    WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD: Optional[bool] = None
    ENABLE_WEB_SEARCH_EVIDENCE_SATURATION: Optional[bool] = None
    WEB_SEARCH_EVIDENCE_MAX_TOKENS: Optional[int] = None
    WEB_SEARCH_EVIDENCE_CHUNK_TOKENS: Optional[int] = None
    WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE: Optional[int] = None
    WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS: Optional[int] = None
    WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS: Optional[int] = None
    WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE: Optional[float] = None
    WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS: Optional[int] = None
    WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS: Optional[int] = None
    WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS: Optional[int] = None
    WEB_LOADER_CONCURRENT_REQUESTS: Optional[int] = None
    WEB_SEARCH_DOMAIN_FILTER_LIST: Optional[List[str]] = []
    BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL: Optional[bool] = None
    BYPASS_WEB_SEARCH_WEB_LOADER: Optional[bool] = None
    OLLAMA_CLOUD_WEB_SEARCH_API_KEY: Optional[str] = None
    SEARXNG_QUERY_URL: Optional[str] = None
    SEARXNG_LANGUAGE: Optional[str] = None
    YACY_QUERY_URL: Optional[str] = None
    YACY_USERNAME: Optional[str] = None
    YACY_PASSWORD: Optional[str] = None
    GOOGLE_PSE_API_KEY: Optional[str] = None
    GOOGLE_PSE_ENGINE_ID: Optional[str] = None
    BRAVE_SEARCH_API_KEY: Optional[str] = None
    KAGI_SEARCH_API_KEY: Optional[str] = None
    MOJEEK_SEARCH_API_KEY: Optional[str] = None
    BOCHA_SEARCH_API_KEY: Optional[str] = None
    SERPSTACK_API_KEY: Optional[str] = None
    SERPSTACK_HTTPS: Optional[bool] = None
    SERPER_API_KEY: Optional[str] = None
    SERPLY_API_KEY: Optional[str] = None
    DDGS_BACKEND: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None
    SEARCHAPI_API_KEY: Optional[str] = None
    SEARCHAPI_ENGINE: Optional[str] = None
    SERPAPI_API_KEY: Optional[str] = None
    SERPAPI_ENGINE: Optional[str] = None
    JINA_API_KEY: Optional[str] = None
    JINA_API_BASE_URL: Optional[str] = None
    BING_SEARCH_V7_ENDPOINT: Optional[str] = None
    BING_SEARCH_V7_SUBSCRIPTION_KEY: Optional[str] = None
    EXA_API_KEY: Optional[str] = None
    PERPLEXITY_API_KEY: Optional[str] = None
    PERPLEXITY_MODEL: Optional[str] = None
    PERPLEXITY_SEARCH_CONTEXT_USAGE: Optional[str] = None
    PERPLEXITY_SEARCH_API_URL: Optional[str] = None
    SOUGOU_API_SID: Optional[str] = None
    SOUGOU_API_SK: Optional[str] = None
    WEB_LOADER_ENGINE: Optional[str] = None
    WEB_LOADER_TIMEOUT: Optional[str] = None
    ENABLE_WEB_LOADER_SSL_VERIFICATION: Optional[bool] = None
    PLAYWRIGHT_WS_URL: Optional[str] = None
    PLAYWRIGHT_TIMEOUT: Optional[int] = None
    PLAYWRIGHT_REMOVE_SELECTORS: Optional[List[str]] = None
    FIRECRAWL_API_KEY: Optional[str] = None
    FIRECRAWL_API_BASE_URL: Optional[str] = None
    FIRECRAWL_TIMEOUT: Optional[str] = None
    TAVILY_EXTRACT_DEPTH: Optional[str] = None
    EXTERNAL_WEB_SEARCH_URL: Optional[str] = None
    EXTERNAL_WEB_SEARCH_API_KEY: Optional[str] = None
    EXTERNAL_WEB_LOADER_URL: Optional[str] = None
    EXTERNAL_WEB_LOADER_API_KEY: Optional[str] = None
    YOUTUBE_LOADER_LANGUAGE: Optional[List[str]] = None
    YOUTUBE_LOADER_PROXY_URL: Optional[str] = None
    YOUTUBE_LOADER_TRANSLATION: Optional[str] = None
    YANDEX_WEB_SEARCH_URL: Optional[str] = None
    YANDEX_WEB_SEARCH_API_KEY: Optional[str] = None
    YANDEX_WEB_SEARCH_CONFIG: Optional[str] = None
    YOUCOM_API_KEY: Optional[str] = None


class ConfigForm(BaseModel):
    # RAG settings
    RAG_TEMPLATE: Optional[str] = None
    TOP_K: Optional[int] = None
    BYPASS_EMBEDDING_AND_RETRIEVAL: Optional[bool] = None
    RAG_FULL_CONTEXT: Optional[bool] = None
    RAG_FULL_CONTEXT_MAX_CHARS: Optional[int] = None

    # Hybrid search settings
    ENABLE_RAG_HYBRID_SEARCH: Optional[bool] = None
    ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS: Optional[bool] = None
    TOP_K_RERANKER: Optional[int] = None
    RELEVANCE_THRESHOLD: Optional[float] = None
    HYBRID_BM25_WEIGHT: Optional[float] = None

    # Content extraction settings
    CONTENT_EXTRACTION_ENGINE: Optional[str] = None
    PDF_EXTRACT_IMAGES: Optional[bool] = None
    PDF_LOADER_MODE: Optional[str] = None

    DATALAB_MARKER_API_KEY: Optional[str] = None
    DATALAB_MARKER_API_BASE_URL: Optional[str] = None
    DATALAB_MARKER_ADDITIONAL_CONFIG: Optional[str] = None
    DATALAB_MARKER_SKIP_CACHE: Optional[bool] = None
    DATALAB_MARKER_FORCE_OCR: Optional[bool] = None
    DATALAB_MARKER_PAGINATE: Optional[bool] = None
    DATALAB_MARKER_STRIP_EXISTING_OCR: Optional[bool] = None
    DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION: Optional[bool] = None
    DATALAB_MARKER_FORMAT_LINES: Optional[bool] = None
    DATALAB_MARKER_USE_LLM: Optional[bool] = None
    DATALAB_MARKER_OUTPUT_FORMAT: Optional[str] = None

    EXTERNAL_DOCUMENT_LOADER_URL: Optional[str] = None
    EXTERNAL_DOCUMENT_LOADER_API_KEY: Optional[str] = None

    TIKA_SERVER_URL: Optional[str] = None
    DOCLING_SERVER_URL: Optional[str] = None
    DOCLING_API_KEY: Optional[str] = None
    DOCLING_PARAMS: Optional[dict] = None
    DOCUMENT_INTELLIGENCE_ENDPOINT: Optional[str] = None
    DOCUMENT_INTELLIGENCE_KEY: Optional[str] = None
    DOCUMENT_INTELLIGENCE_MODEL: Optional[str] = None
    MISTRAL_OCR_API_BASE_URL: Optional[str] = None
    MISTRAL_OCR_API_KEY: Optional[str] = None

    # MinerU settings
    MINERU_API_MODE: Optional[str] = None
    MINERU_API_URL: Optional[str] = None
    MINERU_API_KEY: Optional[str] = None
    MINERU_API_TIMEOUT: Optional[str] = None
    MINERU_PARAMS: Optional[dict] = None

    # Reranking settings
    RAG_RERANKING_MODEL: Optional[str] = None
    RAG_RERANKING_ENGINE: Optional[str] = None
    RAG_EXTERNAL_RERANKER_URL: Optional[str] = None
    RAG_EXTERNAL_RERANKER_API_KEY: Optional[str] = None
    RAG_EXTERNAL_RERANKER_TIMEOUT: Optional[str] = None

    # Chunking settings
    TEXT_SPLITTER: Optional[str] = None
    ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER: Optional[bool] = None
    CHUNK_SIZE: Optional[int] = None
    CHUNK_MIN_SIZE_TARGET: Optional[int] = None
    CHUNK_OVERLAP: Optional[int] = None

    # File upload settings
    FILE_MAX_SIZE: Optional[Union[int, str]] = None
    FILE_MAX_COUNT: Optional[Union[int, str]] = None
    FILE_IMAGE_COMPRESSION_WIDTH: Optional[Union[int, str]] = None
    FILE_IMAGE_COMPRESSION_HEIGHT: Optional[Union[int, str]] = None
    ALLOWED_FILE_EXTENSIONS: Optional[List[str]] = None

    # Integration settings
    ENABLE_GOOGLE_DRIVE_INTEGRATION: Optional[bool] = None
    ENABLE_ONEDRIVE_INTEGRATION: Optional[bool] = None

    # Web search settings
    web: Optional[WebConfig] = None


class SourceRegistryUpdateForm(BaseModel):
    registry: dict[str, Any]


@router.get("/web/search/planner/source-registry")
async def get_web_search_source_registry(user=Depends(get_admin_user)):
    try:
        clear_source_registry_caches()
        registry = load_source_registry()
        validation = validate_source_registry_payload(registry)
        return {
            "status": True,
            "registry": registry,
            "validation": validation,
        }
    except Exception as e:
        log.exception("Failed to load source registry")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/web/search/planner/source-registry")
async def update_web_search_source_registry(
    form_data: SourceRegistryUpdateForm,
    user=Depends(get_admin_user),
):
    try:
        validation = save_source_registry_payload(form_data.registry)
        registry = load_source_registry()
        return {
            "status": True,
            "registry": registry,
            "validation": validation,
        }
    except Exception as e:
        log.exception("Failed to update source registry")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/config/update")
async def update_rag_config(
    request: Request, form_data: ConfigForm, user=Depends(get_admin_user)
):
    # RAG settings
    request.app.state.config.RAG_TEMPLATE = (
        form_data.RAG_TEMPLATE
        if form_data.RAG_TEMPLATE is not None
        else request.app.state.config.RAG_TEMPLATE
    )
    request.app.state.config.TOP_K = (
        form_data.TOP_K
        if form_data.TOP_K is not None
        else request.app.state.config.TOP_K
    )
    request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL = (
        form_data.BYPASS_EMBEDDING_AND_RETRIEVAL
        if form_data.BYPASS_EMBEDDING_AND_RETRIEVAL is not None
        else request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL
    )
    request.app.state.config.RAG_FULL_CONTEXT = (
        form_data.RAG_FULL_CONTEXT
        if form_data.RAG_FULL_CONTEXT is not None
        else request.app.state.config.RAG_FULL_CONTEXT
    )
    request.app.state.config.RAG_FULL_CONTEXT_MAX_CHARS = (
        form_data.RAG_FULL_CONTEXT_MAX_CHARS
        if form_data.RAG_FULL_CONTEXT_MAX_CHARS is not None
        else request.app.state.config.RAG_FULL_CONTEXT_MAX_CHARS
    )

    # Hybrid search settings
    request.app.state.config.ENABLE_RAG_HYBRID_SEARCH = (
        form_data.ENABLE_RAG_HYBRID_SEARCH
        if form_data.ENABLE_RAG_HYBRID_SEARCH is not None
        else request.app.state.config.ENABLE_RAG_HYBRID_SEARCH
    )
    request.app.state.config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS = (
        form_data.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS
        if form_data.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS is not None
        else request.app.state.config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS
    )

    request.app.state.config.TOP_K_RERANKER = (
        form_data.TOP_K_RERANKER
        if form_data.TOP_K_RERANKER is not None
        else request.app.state.config.TOP_K_RERANKER
    )
    request.app.state.config.RELEVANCE_THRESHOLD = (
        form_data.RELEVANCE_THRESHOLD
        if form_data.RELEVANCE_THRESHOLD is not None
        else request.app.state.config.RELEVANCE_THRESHOLD
    )
    request.app.state.config.HYBRID_BM25_WEIGHT = (
        form_data.HYBRID_BM25_WEIGHT
        if form_data.HYBRID_BM25_WEIGHT is not None
        else request.app.state.config.HYBRID_BM25_WEIGHT
    )

    # Content extraction settings
    request.app.state.config.CONTENT_EXTRACTION_ENGINE = (
        form_data.CONTENT_EXTRACTION_ENGINE
        if form_data.CONTENT_EXTRACTION_ENGINE is not None
        else request.app.state.config.CONTENT_EXTRACTION_ENGINE
    )
    request.app.state.config.PDF_EXTRACT_IMAGES = (
        form_data.PDF_EXTRACT_IMAGES
        if form_data.PDF_EXTRACT_IMAGES is not None
        else request.app.state.config.PDF_EXTRACT_IMAGES
    )
    request.app.state.config.PDF_LOADER_MODE = (
        form_data.PDF_LOADER_MODE
        if form_data.PDF_LOADER_MODE is not None
        else request.app.state.config.PDF_LOADER_MODE
    )
    request.app.state.config.DATALAB_MARKER_API_KEY = (
        form_data.DATALAB_MARKER_API_KEY
        if form_data.DATALAB_MARKER_API_KEY is not None
        else request.app.state.config.DATALAB_MARKER_API_KEY
    )
    request.app.state.config.DATALAB_MARKER_API_BASE_URL = (
        form_data.DATALAB_MARKER_API_BASE_URL
        if form_data.DATALAB_MARKER_API_BASE_URL is not None
        else request.app.state.config.DATALAB_MARKER_API_BASE_URL
    )
    request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG = (
        form_data.DATALAB_MARKER_ADDITIONAL_CONFIG
        if form_data.DATALAB_MARKER_ADDITIONAL_CONFIG is not None
        else request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG
    )
    request.app.state.config.DATALAB_MARKER_SKIP_CACHE = (
        form_data.DATALAB_MARKER_SKIP_CACHE
        if form_data.DATALAB_MARKER_SKIP_CACHE is not None
        else request.app.state.config.DATALAB_MARKER_SKIP_CACHE
    )
    request.app.state.config.DATALAB_MARKER_FORCE_OCR = (
        form_data.DATALAB_MARKER_FORCE_OCR
        if form_data.DATALAB_MARKER_FORCE_OCR is not None
        else request.app.state.config.DATALAB_MARKER_FORCE_OCR
    )
    request.app.state.config.DATALAB_MARKER_PAGINATE = (
        form_data.DATALAB_MARKER_PAGINATE
        if form_data.DATALAB_MARKER_PAGINATE is not None
        else request.app.state.config.DATALAB_MARKER_PAGINATE
    )
    request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR = (
        form_data.DATALAB_MARKER_STRIP_EXISTING_OCR
        if form_data.DATALAB_MARKER_STRIP_EXISTING_OCR is not None
        else request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR
    )
    request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION = (
        form_data.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION
        if form_data.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION is not None
        else request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION
    )
    request.app.state.config.DATALAB_MARKER_FORMAT_LINES = (
        form_data.DATALAB_MARKER_FORMAT_LINES
        if form_data.DATALAB_MARKER_FORMAT_LINES is not None
        else request.app.state.config.DATALAB_MARKER_FORMAT_LINES
    )
    request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT = (
        form_data.DATALAB_MARKER_OUTPUT_FORMAT
        if form_data.DATALAB_MARKER_OUTPUT_FORMAT is not None
        else request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT
    )
    request.app.state.config.DATALAB_MARKER_USE_LLM = (
        form_data.DATALAB_MARKER_USE_LLM
        if form_data.DATALAB_MARKER_USE_LLM is not None
        else request.app.state.config.DATALAB_MARKER_USE_LLM
    )
    request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL = (
        form_data.EXTERNAL_DOCUMENT_LOADER_URL
        if form_data.EXTERNAL_DOCUMENT_LOADER_URL is not None
        else request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL
    )
    request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY = (
        form_data.EXTERNAL_DOCUMENT_LOADER_API_KEY
        if form_data.EXTERNAL_DOCUMENT_LOADER_API_KEY is not None
        else request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY
    )
    request.app.state.config.TIKA_SERVER_URL = (
        form_data.TIKA_SERVER_URL
        if form_data.TIKA_SERVER_URL is not None
        else request.app.state.config.TIKA_SERVER_URL
    )
    request.app.state.config.DOCLING_SERVER_URL = (
        form_data.DOCLING_SERVER_URL
        if form_data.DOCLING_SERVER_URL is not None
        else request.app.state.config.DOCLING_SERVER_URL
    )
    request.app.state.config.DOCLING_API_KEY = (
        form_data.DOCLING_API_KEY
        if form_data.DOCLING_API_KEY is not None
        else request.app.state.config.DOCLING_API_KEY
    )
    request.app.state.config.DOCLING_PARAMS = (
        form_data.DOCLING_PARAMS
        if form_data.DOCLING_PARAMS is not None
        else request.app.state.config.DOCLING_PARAMS
    )
    request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT = (
        form_data.DOCUMENT_INTELLIGENCE_ENDPOINT
        if form_data.DOCUMENT_INTELLIGENCE_ENDPOINT is not None
        else request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT
    )
    request.app.state.config.DOCUMENT_INTELLIGENCE_KEY = (
        form_data.DOCUMENT_INTELLIGENCE_KEY
        if form_data.DOCUMENT_INTELLIGENCE_KEY is not None
        else request.app.state.config.DOCUMENT_INTELLIGENCE_KEY
    )
    request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL = (
        form_data.DOCUMENT_INTELLIGENCE_MODEL
        if form_data.DOCUMENT_INTELLIGENCE_MODEL is not None
        else request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL
    )

    request.app.state.config.MISTRAL_OCR_API_BASE_URL = (
        form_data.MISTRAL_OCR_API_BASE_URL
        if form_data.MISTRAL_OCR_API_BASE_URL is not None
        else request.app.state.config.MISTRAL_OCR_API_BASE_URL
    )
    request.app.state.config.MISTRAL_OCR_API_KEY = (
        form_data.MISTRAL_OCR_API_KEY
        if form_data.MISTRAL_OCR_API_KEY is not None
        else request.app.state.config.MISTRAL_OCR_API_KEY
    )

    # MinerU settings
    request.app.state.config.MINERU_API_MODE = (
        form_data.MINERU_API_MODE
        if form_data.MINERU_API_MODE is not None
        else request.app.state.config.MINERU_API_MODE
    )
    request.app.state.config.MINERU_API_URL = (
        form_data.MINERU_API_URL
        if form_data.MINERU_API_URL is not None
        else request.app.state.config.MINERU_API_URL
    )
    request.app.state.config.MINERU_API_KEY = (
        form_data.MINERU_API_KEY
        if form_data.MINERU_API_KEY is not None
        else request.app.state.config.MINERU_API_KEY
    )
    request.app.state.config.MINERU_API_TIMEOUT = (
        form_data.MINERU_API_TIMEOUT
        if form_data.MINERU_API_TIMEOUT is not None
        else request.app.state.config.MINERU_API_TIMEOUT
    )
    request.app.state.config.MINERU_PARAMS = (
        form_data.MINERU_PARAMS
        if form_data.MINERU_PARAMS is not None
        else request.app.state.config.MINERU_PARAMS
    )

    # Reranking settings
    if request.app.state.config.RAG_RERANKING_ENGINE == "":
        # Unloading the internal reranker and clear VRAM memory
        request.app.state.rf = None
        request.app.state.RERANKING_FUNCTION = None
        import gc

        gc.collect()
        if DEVICE_TYPE == "cuda":
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    request.app.state.config.RAG_RERANKING_ENGINE = (
        form_data.RAG_RERANKING_ENGINE
        if form_data.RAG_RERANKING_ENGINE is not None
        else request.app.state.config.RAG_RERANKING_ENGINE
    )

    request.app.state.config.RAG_EXTERNAL_RERANKER_URL = (
        form_data.RAG_EXTERNAL_RERANKER_URL
        if form_data.RAG_EXTERNAL_RERANKER_URL is not None
        else request.app.state.config.RAG_EXTERNAL_RERANKER_URL
    )

    request.app.state.config.RAG_EXTERNAL_RERANKER_API_KEY = (
        form_data.RAG_EXTERNAL_RERANKER_API_KEY
        if form_data.RAG_EXTERNAL_RERANKER_API_KEY is not None
        else request.app.state.config.RAG_EXTERNAL_RERANKER_API_KEY
    )

    request.app.state.config.RAG_EXTERNAL_RERANKER_TIMEOUT = (
        form_data.RAG_EXTERNAL_RERANKER_TIMEOUT
        if form_data.RAG_EXTERNAL_RERANKER_TIMEOUT is not None
        else request.app.state.config.RAG_EXTERNAL_RERANKER_TIMEOUT
    )

    log.info(
        f"Updating reranking model: {request.app.state.config.RAG_RERANKING_MODEL} to {form_data.RAG_RERANKING_MODEL}"
    )
    try:
        request.app.state.config.RAG_RERANKING_MODEL = (
            form_data.RAG_RERANKING_MODEL
            if form_data.RAG_RERANKING_MODEL is not None
            else request.app.state.config.RAG_RERANKING_MODEL
        )

        try:
            if (
                request.app.state.config.ENABLE_RAG_HYBRID_SEARCH
                and not request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL
            ):
                request.app.state.rf = get_rf(
                    request.app.state.config.RAG_RERANKING_ENGINE,
                    request.app.state.config.RAG_RERANKING_MODEL,
                    request.app.state.config.RAG_EXTERNAL_RERANKER_URL,
                    request.app.state.config.RAG_EXTERNAL_RERANKER_API_KEY,
                    request.app.state.config.RAG_EXTERNAL_RERANKER_TIMEOUT,
                )

                request.app.state.RERANKING_FUNCTION = get_reranking_function(
                    request.app.state.config.RAG_RERANKING_ENGINE,
                    request.app.state.config.RAG_RERANKING_MODEL,
                    request.app.state.rf,
                )
        except Exception as e:
            log.error(f"Error loading reranking model: {e}")
            request.app.state.config.ENABLE_RAG_HYBRID_SEARCH = False
    except Exception as e:
        log.exception(f"Problem updating reranking model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )

    # Chunking settings
    request.app.state.config.TEXT_SPLITTER = (
        form_data.TEXT_SPLITTER
        if form_data.TEXT_SPLITTER is not None
        else request.app.state.config.TEXT_SPLITTER
    )
    request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER = (
        form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER
        if form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER is not None
        else request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER
    )
    request.app.state.config.CHUNK_SIZE = (
        form_data.CHUNK_SIZE
        if form_data.CHUNK_SIZE is not None
        else request.app.state.config.CHUNK_SIZE
    )
    request.app.state.config.CHUNK_MIN_SIZE_TARGET = (
        form_data.CHUNK_MIN_SIZE_TARGET
        if form_data.CHUNK_MIN_SIZE_TARGET is not None
        else request.app.state.config.CHUNK_MIN_SIZE_TARGET
    )
    request.app.state.config.CHUNK_OVERLAP = (
        form_data.CHUNK_OVERLAP
        if form_data.CHUNK_OVERLAP is not None
        else request.app.state.config.CHUNK_OVERLAP
    )

    # File upload settings
    # Empty string means "clear to None" (unlimited/no compression),
    # None means "don't change", int means "set to this value"
    if form_data.FILE_MAX_SIZE is not None:
        request.app.state.config.FILE_MAX_SIZE = (
            None if form_data.FILE_MAX_SIZE == "" else form_data.FILE_MAX_SIZE
        )
    if form_data.FILE_MAX_COUNT is not None:
        request.app.state.config.FILE_MAX_COUNT = (
            None if form_data.FILE_MAX_COUNT == "" else form_data.FILE_MAX_COUNT
        )
    if form_data.FILE_IMAGE_COMPRESSION_WIDTH is not None:
        request.app.state.config.FILE_IMAGE_COMPRESSION_WIDTH = (
            None
            if form_data.FILE_IMAGE_COMPRESSION_WIDTH == ""
            else form_data.FILE_IMAGE_COMPRESSION_WIDTH
        )
    if form_data.FILE_IMAGE_COMPRESSION_HEIGHT is not None:
        request.app.state.config.FILE_IMAGE_COMPRESSION_HEIGHT = (
            None
            if form_data.FILE_IMAGE_COMPRESSION_HEIGHT == ""
            else form_data.FILE_IMAGE_COMPRESSION_HEIGHT
        )

    request.app.state.config.ALLOWED_FILE_EXTENSIONS = (
        form_data.ALLOWED_FILE_EXTENSIONS
        if form_data.ALLOWED_FILE_EXTENSIONS is not None
        else request.app.state.config.ALLOWED_FILE_EXTENSIONS
    )

    # Integration settings
    request.app.state.config.ENABLE_GOOGLE_DRIVE_INTEGRATION = (
        form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION
        if form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION is not None
        else request.app.state.config.ENABLE_GOOGLE_DRIVE_INTEGRATION
    )
    request.app.state.config.ENABLE_ONEDRIVE_INTEGRATION = (
        form_data.ENABLE_ONEDRIVE_INTEGRATION
        if form_data.ENABLE_ONEDRIVE_INTEGRATION is not None
        else request.app.state.config.ENABLE_ONEDRIVE_INTEGRATION
    )

    if form_data.web is not None:
        # Web search settings
        request.app.state.config.ENABLE_WEB_SEARCH = form_data.web.ENABLE_WEB_SEARCH
        request.app.state.config.WEB_SEARCH_ENGINE = form_data.web.WEB_SEARCH_ENGINE
        request.app.state.config.WEB_SEARCH_TRUST_ENV = (
            form_data.web.WEB_SEARCH_TRUST_ENV
        )
        request.app.state.config.WEB_SEARCH_RESULT_COUNT = (
            form_data.web.WEB_SEARCH_RESULT_COUNT
        )
        request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS = (
            form_data.web.WEB_SEARCH_CONCURRENT_REQUESTS
        )
        request.app.state.config.WEB_SEARCH_LOCAL_FIRST = (
            form_data.web.WEB_SEARCH_LOCAL_FIRST
            if form_data.web.WEB_SEARCH_LOCAL_FIRST is not None
            else request.app.state.config.WEB_SEARCH_LOCAL_FIRST
        )
        request.app.state.config.WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS = (
            form_data.web.WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS
            if form_data.web.WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS is not None
            else request.app.state.config.WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS
        )
        request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK = (
            form_data.web.WEB_SEARCH_BRAVE_FALLBACK
            if form_data.web.WEB_SEARCH_BRAVE_FALLBACK is not None
            else request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK
        )
        request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES = (
            form_data.web.WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES
            if form_data.web.WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES is not None
            else request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES
        )
        request.app.state.config.WEB_SEARCH_BRAVE_MIN_INTERVAL_MS = (
            form_data.web.WEB_SEARCH_BRAVE_MIN_INTERVAL_MS
            if form_data.web.WEB_SEARCH_BRAVE_MIN_INTERVAL_MS is not None
            else request.app.state.config.WEB_SEARCH_BRAVE_MIN_INTERVAL_MS
        )
        request.app.state.config.ENABLE_WEB_SEARCH_PLANNER = (
            form_data.web.ENABLE_WEB_SEARCH_PLANNER
            if form_data.web.ENABLE_WEB_SEARCH_PLANNER is not None
            else request.app.state.config.ENABLE_WEB_SEARCH_PLANNER
        )
        request.app.state.config.ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER = (
            form_data.web.ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER
            if form_data.web.ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER is not None
            else request.app.state.config.ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER
        )
        request.app.state.config.WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES = (
            form_data.web.WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES
            if form_data.web.WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES
        )
        request.app.state.config.WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES = (
            form_data.web.WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES
            if form_data.web.WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES
        )
        request.app.state.config.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE = (
            form_data.web.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE
            if form_data.web.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE
            is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE
        )
        request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE = (
            form_data.web.WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE
            if form_data.web.WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE
        )
        request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS = (
            form_data.web.WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS
            if form_data.web.WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS
        )
        request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE = (
            form_data.web.WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE
            if form_data.web.WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE
        )
        request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_DELTA = (
            form_data.web.WEB_SEARCH_PLANNER_PLATEAU_DELTA
            if form_data.web.WEB_SEARCH_PLANNER_PLATEAU_DELTA is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_DELTA
        )
        request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_STREAK = (
            form_data.web.WEB_SEARCH_PLANNER_PLATEAU_STREAK
            if form_data.web.WEB_SEARCH_PLANNER_PLATEAU_STREAK is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_STREAK
        )
        request.app.state.config.WEB_SEARCH_PLANNER_MODE = (
            form_data.web.WEB_SEARCH_PLANNER_MODE
            if form_data.web.WEB_SEARCH_PLANNER_MODE is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_MODE
        )
        request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES = (
            form_data.web.WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES
            if form_data.web.WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES
        )
        request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS = (
            form_data.web.WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS
            if form_data.web.WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS
        )
        request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS = (
            form_data.web.WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS
            if form_data.web.WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS
        )
        request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS = (
            form_data.web.WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS
            if form_data.web.WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS
            is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS
        )
        request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE = (
            form_data.web.WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE
            if form_data.web.WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE
        )
        request.app.state.config.WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD = (
            form_data.web.WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD
            if form_data.web.WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD is not None
            else request.app.state.config.WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD
        )
        request.app.state.config.ENABLE_WEB_SEARCH_EVIDENCE_SATURATION = (
            form_data.web.ENABLE_WEB_SEARCH_EVIDENCE_SATURATION
            if form_data.web.ENABLE_WEB_SEARCH_EVIDENCE_SATURATION is not None
            else request.app.state.config.ENABLE_WEB_SEARCH_EVIDENCE_SATURATION
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_TOKENS = (
            form_data.web.WEB_SEARCH_EVIDENCE_MAX_TOKENS
            if form_data.web.WEB_SEARCH_EVIDENCE_MAX_TOKENS is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_TOKENS
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_CHUNK_TOKENS = (
            form_data.web.WEB_SEARCH_EVIDENCE_CHUNK_TOKENS
            if form_data.web.WEB_SEARCH_EVIDENCE_CHUNK_TOKENS is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_CHUNK_TOKENS
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE = (
            form_data.web.WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE
            if form_data.web.WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS = (
            form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS
            if form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS = (
            form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS
            if form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE = (
            form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE
            if form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS = (
            form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS
            if form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS = (
            form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS
            if form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS
        )
        request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS = (
            form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS
            if form_data.web.WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS is not None
            else request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS
        )
        request.app.state.config.WEB_LOADER_CONCURRENT_REQUESTS = (
            form_data.web.WEB_LOADER_CONCURRENT_REQUESTS
        )
        request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST = (
            form_data.web.WEB_SEARCH_DOMAIN_FILTER_LIST
        )
        request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL = (
            form_data.web.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL
        )
        request.app.state.config.BYPASS_WEB_SEARCH_WEB_LOADER = (
            form_data.web.BYPASS_WEB_SEARCH_WEB_LOADER
        )
        request.app.state.config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY = (
            form_data.web.OLLAMA_CLOUD_WEB_SEARCH_API_KEY
        )
        request.app.state.config.SEARXNG_QUERY_URL = form_data.web.SEARXNG_QUERY_URL
        request.app.state.config.SEARXNG_LANGUAGE = form_data.web.SEARXNG_LANGUAGE
        request.app.state.config.YACY_QUERY_URL = form_data.web.YACY_QUERY_URL
        request.app.state.config.YACY_USERNAME = form_data.web.YACY_USERNAME
        request.app.state.config.YACY_PASSWORD = form_data.web.YACY_PASSWORD
        request.app.state.config.GOOGLE_PSE_API_KEY = form_data.web.GOOGLE_PSE_API_KEY
        request.app.state.config.GOOGLE_PSE_ENGINE_ID = (
            form_data.web.GOOGLE_PSE_ENGINE_ID
        )
        request.app.state.config.BRAVE_SEARCH_API_KEY = (
            form_data.web.BRAVE_SEARCH_API_KEY
        )
        request.app.state.config.KAGI_SEARCH_API_KEY = form_data.web.KAGI_SEARCH_API_KEY
        request.app.state.config.MOJEEK_SEARCH_API_KEY = (
            form_data.web.MOJEEK_SEARCH_API_KEY
        )
        request.app.state.config.BOCHA_SEARCH_API_KEY = (
            form_data.web.BOCHA_SEARCH_API_KEY
        )
        request.app.state.config.SERPSTACK_API_KEY = form_data.web.SERPSTACK_API_KEY
        request.app.state.config.SERPSTACK_HTTPS = form_data.web.SERPSTACK_HTTPS
        request.app.state.config.SERPER_API_KEY = form_data.web.SERPER_API_KEY
        request.app.state.config.SERPLY_API_KEY = form_data.web.SERPLY_API_KEY
        request.app.state.config.DDGS_BACKEND = form_data.web.DDGS_BACKEND
        request.app.state.config.TAVILY_API_KEY = form_data.web.TAVILY_API_KEY
        request.app.state.config.SEARCHAPI_API_KEY = form_data.web.SEARCHAPI_API_KEY
        request.app.state.config.SEARCHAPI_ENGINE = form_data.web.SEARCHAPI_ENGINE
        request.app.state.config.SERPAPI_API_KEY = form_data.web.SERPAPI_API_KEY
        request.app.state.config.SERPAPI_ENGINE = form_data.web.SERPAPI_ENGINE
        request.app.state.config.JINA_API_KEY = form_data.web.JINA_API_KEY
        request.app.state.config.JINA_API_BASE_URL = form_data.web.JINA_API_BASE_URL
        request.app.state.config.BING_SEARCH_V7_ENDPOINT = (
            form_data.web.BING_SEARCH_V7_ENDPOINT
        )
        request.app.state.config.BING_SEARCH_V7_SUBSCRIPTION_KEY = (
            form_data.web.BING_SEARCH_V7_SUBSCRIPTION_KEY
        )
        request.app.state.config.EXA_API_KEY = form_data.web.EXA_API_KEY
        request.app.state.config.PERPLEXITY_API_KEY = form_data.web.PERPLEXITY_API_KEY
        request.app.state.config.PERPLEXITY_MODEL = form_data.web.PERPLEXITY_MODEL
        request.app.state.config.PERPLEXITY_SEARCH_CONTEXT_USAGE = (
            form_data.web.PERPLEXITY_SEARCH_CONTEXT_USAGE
        )
        request.app.state.config.PERPLEXITY_SEARCH_API_URL = (
            form_data.web.PERPLEXITY_SEARCH_API_URL
        )
        request.app.state.config.SOUGOU_API_SID = form_data.web.SOUGOU_API_SID
        request.app.state.config.SOUGOU_API_SK = form_data.web.SOUGOU_API_SK

        # Web loader settings
        request.app.state.config.WEB_LOADER_ENGINE = form_data.web.WEB_LOADER_ENGINE
        request.app.state.config.WEB_LOADER_TIMEOUT = form_data.web.WEB_LOADER_TIMEOUT

        request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION = (
            form_data.web.ENABLE_WEB_LOADER_SSL_VERIFICATION
        )
        request.app.state.config.PLAYWRIGHT_WS_URL = form_data.web.PLAYWRIGHT_WS_URL
        request.app.state.config.PLAYWRIGHT_TIMEOUT = form_data.web.PLAYWRIGHT_TIMEOUT
        request.app.state.config.PLAYWRIGHT_REMOVE_SELECTORS = (
            form_data.web.PLAYWRIGHT_REMOVE_SELECTORS
        )
        request.app.state.config.FIRECRAWL_API_KEY = form_data.web.FIRECRAWL_API_KEY
        request.app.state.config.FIRECRAWL_API_BASE_URL = (
            form_data.web.FIRECRAWL_API_BASE_URL
        )
        request.app.state.config.FIRECRAWL_TIMEOUT = form_data.web.FIRECRAWL_TIMEOUT
        request.app.state.config.EXTERNAL_WEB_SEARCH_URL = (
            form_data.web.EXTERNAL_WEB_SEARCH_URL
        )
        request.app.state.config.EXTERNAL_WEB_SEARCH_API_KEY = (
            form_data.web.EXTERNAL_WEB_SEARCH_API_KEY
        )
        request.app.state.config.EXTERNAL_WEB_LOADER_URL = (
            form_data.web.EXTERNAL_WEB_LOADER_URL
        )
        request.app.state.config.EXTERNAL_WEB_LOADER_API_KEY = (
            form_data.web.EXTERNAL_WEB_LOADER_API_KEY
        )
        request.app.state.config.TAVILY_EXTRACT_DEPTH = (
            form_data.web.TAVILY_EXTRACT_DEPTH
        )
        request.app.state.config.YOUTUBE_LOADER_LANGUAGE = (
            form_data.web.YOUTUBE_LOADER_LANGUAGE
        )
        request.app.state.config.YOUTUBE_LOADER_PROXY_URL = (
            form_data.web.YOUTUBE_LOADER_PROXY_URL
        )
        request.app.state.YOUTUBE_LOADER_TRANSLATION = (
            form_data.web.YOUTUBE_LOADER_TRANSLATION
        )
        request.app.state.config.YANDEX_WEB_SEARCH_URL = (
            form_data.web.YANDEX_WEB_SEARCH_URL
        )
        request.app.state.config.YANDEX_WEB_SEARCH_API_KEY = (
            form_data.web.YANDEX_WEB_SEARCH_API_KEY
        )
        request.app.state.config.YANDEX_WEB_SEARCH_CONFIG = (
            form_data.web.YANDEX_WEB_SEARCH_CONFIG
        )
        request.app.state.config.YOUCOM_API_KEY = form_data.web.YOUCOM_API_KEY

    return {
        "status": True,
        # RAG settings
        "RAG_TEMPLATE": request.app.state.config.RAG_TEMPLATE,
        "TOP_K": request.app.state.config.TOP_K,
        "BYPASS_EMBEDDING_AND_RETRIEVAL": request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,
        "RAG_FULL_CONTEXT": request.app.state.config.RAG_FULL_CONTEXT,
        "RAG_FULL_CONTEXT_MAX_CHARS": request.app.state.config.RAG_FULL_CONTEXT_MAX_CHARS,
        # Hybrid search settings
        "ENABLE_RAG_HYBRID_SEARCH": request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
        "TOP_K_RERANKER": request.app.state.config.TOP_K_RERANKER,
        "RELEVANCE_THRESHOLD": request.app.state.config.RELEVANCE_THRESHOLD,
        "HYBRID_BM25_WEIGHT": request.app.state.config.HYBRID_BM25_WEIGHT,
        # Content extraction settings
        "CONTENT_EXTRACTION_ENGINE": request.app.state.config.CONTENT_EXTRACTION_ENGINE,
        "PDF_EXTRACT_IMAGES": request.app.state.config.PDF_EXTRACT_IMAGES,
        "PDF_LOADER_MODE": request.app.state.config.PDF_LOADER_MODE,
        "DATALAB_MARKER_API_KEY": request.app.state.config.DATALAB_MARKER_API_KEY,
        "DATALAB_MARKER_API_BASE_URL": request.app.state.config.DATALAB_MARKER_API_BASE_URL,
        "DATALAB_MARKER_ADDITIONAL_CONFIG": request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG,
        "DATALAB_MARKER_SKIP_CACHE": request.app.state.config.DATALAB_MARKER_SKIP_CACHE,
        "DATALAB_MARKER_FORCE_OCR": request.app.state.config.DATALAB_MARKER_FORCE_OCR,
        "DATALAB_MARKER_PAGINATE": request.app.state.config.DATALAB_MARKER_PAGINATE,
        "DATALAB_MARKER_STRIP_EXISTING_OCR": request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR,
        "DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION": request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
        "DATALAB_MARKER_USE_LLM": request.app.state.config.DATALAB_MARKER_USE_LLM,
        "DATALAB_MARKER_OUTPUT_FORMAT": request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT,
        "EXTERNAL_DOCUMENT_LOADER_URL": request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL,
        "EXTERNAL_DOCUMENT_LOADER_API_KEY": request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
        "TIKA_SERVER_URL": request.app.state.config.TIKA_SERVER_URL,
        "DOCLING_SERVER_URL": request.app.state.config.DOCLING_SERVER_URL,
        "DOCLING_API_KEY": request.app.state.config.DOCLING_API_KEY,
        "DOCLING_PARAMS": request.app.state.config.DOCLING_PARAMS,
        "DOCUMENT_INTELLIGENCE_ENDPOINT": request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        "DOCUMENT_INTELLIGENCE_KEY": request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
        "DOCUMENT_INTELLIGENCE_MODEL": request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL,
        "MISTRAL_OCR_API_BASE_URL": request.app.state.config.MISTRAL_OCR_API_BASE_URL,
        "MISTRAL_OCR_API_KEY": request.app.state.config.MISTRAL_OCR_API_KEY,
        # MinerU settings
        "MINERU_API_MODE": request.app.state.config.MINERU_API_MODE,
        "MINERU_API_URL": request.app.state.config.MINERU_API_URL,
        "MINERU_API_KEY": request.app.state.config.MINERU_API_KEY,
        "MINERU_API_TIMEOUT": request.app.state.config.MINERU_API_TIMEOUT,
        "MINERU_PARAMS": request.app.state.config.MINERU_PARAMS,
        # Reranking settings
        "RAG_RERANKING_MODEL": request.app.state.config.RAG_RERANKING_MODEL,
        "RAG_RERANKING_ENGINE": request.app.state.config.RAG_RERANKING_ENGINE,
        "RAG_EXTERNAL_RERANKER_URL": request.app.state.config.RAG_EXTERNAL_RERANKER_URL,
        "RAG_EXTERNAL_RERANKER_API_KEY": request.app.state.config.RAG_EXTERNAL_RERANKER_API_KEY,
        "RAG_EXTERNAL_RERANKER_TIMEOUT": request.app.state.config.RAG_EXTERNAL_RERANKER_TIMEOUT,
        # Chunking settings
        "TEXT_SPLITTER": request.app.state.config.TEXT_SPLITTER,
        "CHUNK_SIZE": request.app.state.config.CHUNK_SIZE,
        "CHUNK_MIN_SIZE_TARGET": request.app.state.config.CHUNK_MIN_SIZE_TARGET,
        "ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER": request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER,
        "CHUNK_OVERLAP": request.app.state.config.CHUNK_OVERLAP,
        # File upload settings
        "FILE_MAX_SIZE": request.app.state.config.FILE_MAX_SIZE,
        "FILE_MAX_COUNT": request.app.state.config.FILE_MAX_COUNT,
        "FILE_IMAGE_COMPRESSION_WIDTH": request.app.state.config.FILE_IMAGE_COMPRESSION_WIDTH,
        "FILE_IMAGE_COMPRESSION_HEIGHT": request.app.state.config.FILE_IMAGE_COMPRESSION_HEIGHT,
        "ALLOWED_FILE_EXTENSIONS": request.app.state.config.ALLOWED_FILE_EXTENSIONS,
        # Integration settings
        "ENABLE_GOOGLE_DRIVE_INTEGRATION": request.app.state.config.ENABLE_GOOGLE_DRIVE_INTEGRATION,
        "ENABLE_ONEDRIVE_INTEGRATION": request.app.state.config.ENABLE_ONEDRIVE_INTEGRATION,
        # Web search settings
        "web": {
            "ENABLE_WEB_SEARCH": request.app.state.config.ENABLE_WEB_SEARCH,
            "WEB_SEARCH_ENGINE": request.app.state.config.WEB_SEARCH_ENGINE,
            "WEB_SEARCH_TRUST_ENV": request.app.state.config.WEB_SEARCH_TRUST_ENV,
            "WEB_SEARCH_RESULT_COUNT": request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            "WEB_SEARCH_CONCURRENT_REQUESTS": request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS,
            "WEB_SEARCH_LOCAL_FIRST": request.app.state.config.WEB_SEARCH_LOCAL_FIRST,
            "WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS": request.app.state.config.WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS,
            "WEB_SEARCH_BRAVE_FALLBACK": request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK,
            "WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES": request.app.state.config.WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES,
            "WEB_SEARCH_BRAVE_MIN_INTERVAL_MS": request.app.state.config.WEB_SEARCH_BRAVE_MIN_INTERVAL_MS,
            "ENABLE_WEB_SEARCH_PLANNER": request.app.state.config.ENABLE_WEB_SEARCH_PLANNER,
            "ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER": request.app.state.config.ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER,
            "WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES": request.app.state.config.WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES,
            "WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES": request.app.state.config.WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES,
            "WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE": request.app.state.config.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE,
            "WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE": request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE,
            "WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS": request.app.state.config.WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS,
            "WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE": request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE,
            "WEB_SEARCH_PLANNER_PLATEAU_DELTA": request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_DELTA,
            "WEB_SEARCH_PLANNER_PLATEAU_STREAK": request.app.state.config.WEB_SEARCH_PLANNER_PLATEAU_STREAK,
            "WEB_SEARCH_PLANNER_MODE": request.app.state.config.WEB_SEARCH_PLANNER_MODE,
            "WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES,
            "WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS,
            "WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS,
            "WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS,
            "WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE": request.app.state.config.WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE,
            "WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD": request.app.state.config.WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD,
            "ENABLE_WEB_SEARCH_EVIDENCE_SATURATION": request.app.state.config.ENABLE_WEB_SEARCH_EVIDENCE_SATURATION,
            "WEB_SEARCH_EVIDENCE_MAX_TOKENS": request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_TOKENS,
            "WEB_SEARCH_EVIDENCE_CHUNK_TOKENS": request.app.state.config.WEB_SEARCH_EVIDENCE_CHUNK_TOKENS,
            "WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE": request.app.state.config.WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE,
            "WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS,
            "WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS,
            "WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE,
            "WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS,
            "WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS,
            "WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS": request.app.state.config.WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS,
            "WEB_LOADER_CONCURRENT_REQUESTS": request.app.state.config.WEB_LOADER_CONCURRENT_REQUESTS,
            "WEB_SEARCH_DOMAIN_FILTER_LIST": request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            "BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL": request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL,
            "BYPASS_WEB_SEARCH_WEB_LOADER": request.app.state.config.BYPASS_WEB_SEARCH_WEB_LOADER,
            "OLLAMA_CLOUD_WEB_SEARCH_API_KEY": request.app.state.config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY,
            "SEARXNG_QUERY_URL": request.app.state.config.SEARXNG_QUERY_URL,
            "SEARXNG_LANGUAGE": request.app.state.config.SEARXNG_LANGUAGE,
            "YACY_QUERY_URL": request.app.state.config.YACY_QUERY_URL,
            "YACY_USERNAME": request.app.state.config.YACY_USERNAME,
            "YACY_PASSWORD": request.app.state.config.YACY_PASSWORD,
            "GOOGLE_PSE_API_KEY": request.app.state.config.GOOGLE_PSE_API_KEY,
            "GOOGLE_PSE_ENGINE_ID": request.app.state.config.GOOGLE_PSE_ENGINE_ID,
            "BRAVE_SEARCH_API_KEY": request.app.state.config.BRAVE_SEARCH_API_KEY,
            "KAGI_SEARCH_API_KEY": request.app.state.config.KAGI_SEARCH_API_KEY,
            "MOJEEK_SEARCH_API_KEY": request.app.state.config.MOJEEK_SEARCH_API_KEY,
            "BOCHA_SEARCH_API_KEY": request.app.state.config.BOCHA_SEARCH_API_KEY,
            "SERPSTACK_API_KEY": request.app.state.config.SERPSTACK_API_KEY,
            "SERPSTACK_HTTPS": request.app.state.config.SERPSTACK_HTTPS,
            "SERPER_API_KEY": request.app.state.config.SERPER_API_KEY,
            "SERPLY_API_KEY": request.app.state.config.SERPLY_API_KEY,
            "TAVILY_API_KEY": request.app.state.config.TAVILY_API_KEY,
            "SEARCHAPI_API_KEY": request.app.state.config.SEARCHAPI_API_KEY,
            "SEARCHAPI_ENGINE": request.app.state.config.SEARCHAPI_ENGINE,
            "SERPAPI_API_KEY": request.app.state.config.SERPAPI_API_KEY,
            "SERPAPI_ENGINE": request.app.state.config.SERPAPI_ENGINE,
            "JINA_API_KEY": request.app.state.config.JINA_API_KEY,
            "JINA_API_BASE_URL": request.app.state.config.JINA_API_BASE_URL,
            "BING_SEARCH_V7_ENDPOINT": request.app.state.config.BING_SEARCH_V7_ENDPOINT,
            "BING_SEARCH_V7_SUBSCRIPTION_KEY": request.app.state.config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            "EXA_API_KEY": request.app.state.config.EXA_API_KEY,
            "PERPLEXITY_API_KEY": request.app.state.config.PERPLEXITY_API_KEY,
            "PERPLEXITY_MODEL": request.app.state.config.PERPLEXITY_MODEL,
            "PERPLEXITY_SEARCH_CONTEXT_USAGE": request.app.state.config.PERPLEXITY_SEARCH_CONTEXT_USAGE,
            "PERPLEXITY_SEARCH_API_URL": request.app.state.config.PERPLEXITY_SEARCH_API_URL,
            "SOUGOU_API_SID": request.app.state.config.SOUGOU_API_SID,
            "SOUGOU_API_SK": request.app.state.config.SOUGOU_API_SK,
            "WEB_LOADER_ENGINE": request.app.state.config.WEB_LOADER_ENGINE,
            "WEB_LOADER_TIMEOUT": request.app.state.config.WEB_LOADER_TIMEOUT,
            "ENABLE_WEB_LOADER_SSL_VERIFICATION": request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            "PLAYWRIGHT_WS_URL": request.app.state.config.PLAYWRIGHT_WS_URL,
            "PLAYWRIGHT_TIMEOUT": request.app.state.config.PLAYWRIGHT_TIMEOUT,
            "PLAYWRIGHT_REMOVE_SELECTORS": request.app.state.config.PLAYWRIGHT_REMOVE_SELECTORS,
            "FIRECRAWL_API_KEY": request.app.state.config.FIRECRAWL_API_KEY,
            "FIRECRAWL_API_BASE_URL": request.app.state.config.FIRECRAWL_API_BASE_URL,
            "FIRECRAWL_TIMEOUT": request.app.state.config.FIRECRAWL_TIMEOUT,
            "TAVILY_EXTRACT_DEPTH": request.app.state.config.TAVILY_EXTRACT_DEPTH,
            "EXTERNAL_WEB_SEARCH_URL": request.app.state.config.EXTERNAL_WEB_SEARCH_URL,
            "EXTERNAL_WEB_SEARCH_API_KEY": request.app.state.config.EXTERNAL_WEB_SEARCH_API_KEY,
            "EXTERNAL_WEB_LOADER_URL": request.app.state.config.EXTERNAL_WEB_LOADER_URL,
            "EXTERNAL_WEB_LOADER_API_KEY": request.app.state.config.EXTERNAL_WEB_LOADER_API_KEY,
            "YOUTUBE_LOADER_LANGUAGE": request.app.state.config.YOUTUBE_LOADER_LANGUAGE,
            "YOUTUBE_LOADER_PROXY_URL": request.app.state.config.YOUTUBE_LOADER_PROXY_URL,
            "YOUTUBE_LOADER_TRANSLATION": request.app.state.YOUTUBE_LOADER_TRANSLATION,
            "YANDEX_WEB_SEARCH_URL": request.app.state.config.YANDEX_WEB_SEARCH_URL,
            "YANDEX_WEB_SEARCH_API_KEY": request.app.state.config.YANDEX_WEB_SEARCH_API_KEY,
            "YANDEX_WEB_SEARCH_CONFIG": request.app.state.config.YANDEX_WEB_SEARCH_CONFIG,
            "YOUCOM_API_KEY": request.app.state.config.YOUCOM_API_KEY,
        },
    }


####################################
#
# Document process and retrieval
#
####################################


def can_merge_chunks(a: Document, b: Document) -> bool:
    if a.metadata.get("source") != b.metadata.get("source"):
        return False

    a_file_id = a.metadata.get("file_id")
    b_file_id = b.metadata.get("file_id")

    if a_file_id is not None and b_file_id is not None:
        return a_file_id == b_file_id

    return True


def merge_docs_to_target_size(
    request: Request,
    chunks: list[Document],
) -> list[Document]:
    """
    Best-effort normalization of chunk sizes.

    Attempts to grow small chunks up to a desired minimum size,
    without exceeding the maximum size or crossing source/file
    boundaries.
    """
    min_chunk_size_target = request.app.state.config.CHUNK_MIN_SIZE_TARGET
    max_chunk_size = request.app.state.config.CHUNK_SIZE

    if min_chunk_size_target <= 0:
        return chunks

    measure_chunk_size = len
    if request.app.state.config.TEXT_SPLITTER == "token":
        encoding = tiktoken.get_encoding(
            str(request.app.state.config.TIKTOKEN_ENCODING_NAME)
        )
        measure_chunk_size = lambda text: len(encoding.encode(text))

    processed_chunks: list[Document] = []

    current_chunk: Document | None = None
    current_content: str = ""

    for next_chunk in chunks:
        if current_chunk is None:
            current_chunk = next_chunk
            current_content = next_chunk.page_content
            continue  # First chunk initialization

        proposed_content = f"{current_content}\n\n{next_chunk.page_content}"

        can_merge = (
            can_merge_chunks(current_chunk, next_chunk)
            and measure_chunk_size(current_content) < min_chunk_size_target
            and measure_chunk_size(proposed_content) <= max_chunk_size
        )

        if can_merge:
            current_content = proposed_content
        else:
            processed_chunks.append(
                Document(
                    page_content=current_content,
                    metadata={**current_chunk.metadata},
                )
            )
            current_chunk = next_chunk
            current_content = next_chunk.page_content

    if current_chunk is not None:
        processed_chunks.append(
            Document(
                page_content=current_content,
                metadata={**current_chunk.metadata},
            )
        )

    return processed_chunks


def save_docs_to_vector_db(
    request: Request,
    docs,
    collection_name,
    metadata: Optional[dict] = None,
    overwrite: bool = False,
    split: bool = True,
    add: bool = False,
    user=None,
) -> bool:
    def _get_docs_info(docs: list[Document]) -> str:
        docs_info = set()

        # Trying to select relevant metadata identifying the document.
        for doc in docs:
            metadata = getattr(doc, "metadata", {})
            doc_name = metadata.get("name", "")
            if not doc_name:
                doc_name = metadata.get("title", "")
            if not doc_name:
                doc_name = metadata.get("source", "")
            if doc_name:
                docs_info.add(doc_name)

        return ", ".join(docs_info)

    log.debug(
        f"save_docs_to_vector_db: document {_get_docs_info(docs)} {collection_name}"
    )

    # Check if entries with the same hash (metadata.hash) already exist
    if metadata and "hash" in metadata:
        result = VECTOR_DB_CLIENT.query(
            collection_name=collection_name,
            filter={"hash": metadata["hash"]},
        )

        if result is not None and result.ids and len(result.ids) > 0:
            existing_doc_ids = result.ids[0]
            if existing_doc_ids:
                # Check if the existing document belongs to the same file
                # If same file_id, this is a re-add/reindex - allow it
                # If different file_id, this is a duplicate - block it
                existing_file_id = None
                if result.metadatas and result.metadatas[0]:
                    existing_file_id = result.metadatas[0][0].get("file_id")

                if existing_file_id != metadata.get("file_id"):
                    log.info(f"Document with hash {metadata['hash']} already exists")
                    raise ValueError(ERROR_MESSAGES.DUPLICATE_CONTENT)

    if split:
        if request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER:
            log.info("Using markdown header text splitter")
            # Define headers to split on - covering most common markdown header levels
            markdown_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=[
                    ("#", "Header 1"),
                    ("##", "Header 2"),
                    ("###", "Header 3"),
                    ("####", "Header 4"),
                    ("#####", "Header 5"),
                    ("######", "Header 6"),
                ],
                strip_headers=False,  # Keep headers in content for context
            )

            split_docs = []
            for doc in docs:
                split_docs.extend(
                    [
                        Document(
                            page_content=split_chunk.page_content,
                            metadata={**doc.metadata},
                        )
                        for split_chunk in markdown_splitter.split_text(
                            doc.page_content
                        )
                    ]
                )

            docs = split_docs
            if request.app.state.config.CHUNK_MIN_SIZE_TARGET > 0:
                docs = merge_docs_to_target_size(request, docs)

        if request.app.state.config.TEXT_SPLITTER in ["", "character"]:
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=request.app.state.config.CHUNK_SIZE,
                chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                add_start_index=True,
            )
            docs = text_splitter.split_documents(docs)
        elif request.app.state.config.TEXT_SPLITTER == "token":
            log.info(
                f"Using token text splitter: {request.app.state.config.TIKTOKEN_ENCODING_NAME}"
            )

            tiktoken.get_encoding(str(request.app.state.config.TIKTOKEN_ENCODING_NAME))
            text_splitter = TokenTextSplitter(
                encoding_name=str(request.app.state.config.TIKTOKEN_ENCODING_NAME),
                chunk_size=request.app.state.config.CHUNK_SIZE,
                chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                add_start_index=True,
            )
            docs = text_splitter.split_documents(docs)
        else:
            raise ValueError(ERROR_MESSAGES.DEFAULT("Invalid text splitter"))

    if len(docs) == 0:
        raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)

    texts = [sanitize_text_for_db(doc.page_content) for doc in docs]
    metadatas = [
        {
            **doc.metadata,
            **(metadata if metadata else {}),
            "embedding_config": {
                "engine": request.app.state.config.RAG_EMBEDDING_ENGINE,
                "model": request.app.state.config.RAG_EMBEDDING_MODEL,
            },
        }
        for doc in docs
    ]

    try:
        if VECTOR_DB_CLIENT.has_collection(collection_name=collection_name):
            log.info(f"collection {collection_name} already exists")

            if overwrite:
                VECTOR_DB_CLIENT.delete_collection(collection_name=collection_name)
                log.info(f"deleting existing collection {collection_name}")
            elif add is False:
                log.info(
                    f"collection {collection_name} already exists, overwrite is False and add is False"
                )
                return True

        log.info(f"generating embeddings for {collection_name}")
        embedding_function = get_embedding_function(
            request.app.state.config.RAG_EMBEDDING_ENGINE,
            request.app.state.config.RAG_EMBEDDING_MODEL,
            request.app.state.ef,
            (
                request.app.state.config.RAG_OPENAI_API_BASE_URL
                if request.app.state.config.RAG_EMBEDDING_ENGINE == "openai"
                else (
                    request.app.state.config.RAG_OLLAMA_BASE_URL
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == "ollama"
                    else request.app.state.config.RAG_AZURE_OPENAI_BASE_URL
                )
            ),
            (
                request.app.state.config.RAG_OPENAI_API_KEY
                if request.app.state.config.RAG_EMBEDDING_ENGINE == "openai"
                else (
                    request.app.state.config.RAG_OLLAMA_API_KEY
                    if request.app.state.config.RAG_EMBEDDING_ENGINE == "ollama"
                    else request.app.state.config.RAG_AZURE_OPENAI_API_KEY
                )
            ),
            request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
            azure_api_version=(
                request.app.state.config.RAG_AZURE_OPENAI_API_VERSION
                if request.app.state.config.RAG_EMBEDDING_ENGINE == "azure_openai"
                else None
            ),
            enable_async=request.app.state.config.ENABLE_ASYNC_EMBEDDING,
            concurrent_requests=request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
        )

        # Run async embedding in sync context using the main event loop
        # This allows the main loop to stay responsive to health checks during long operations
        embedding_timeout = RAG_EMBEDDING_TIMEOUT

        future = asyncio.run_coroutine_threadsafe(
            embedding_function(
                list(map(lambda x: x.replace("\n", " "), texts)),
                prefix=RAG_EMBEDDING_CONTENT_PREFIX,
                user=user,
            ),
            request.app.state.main_loop,
        )
        embeddings = future.result(timeout=embedding_timeout)
        log.info(f"embeddings generated {len(embeddings)} for {len(texts)} items")

        items = [
            {
                "id": str(uuid.uuid4()),
                "text": text,
                "vector": embeddings[idx],
                "metadata": metadatas[idx],
            }
            for idx, text in enumerate(texts)
        ]

        log.info(f"adding to collection {collection_name}")
        VECTOR_DB_CLIENT.insert(
            collection_name=collection_name,
            items=items,
        )

        log.info(f"added {len(items)} items to collection {collection_name}")
        return True
    except Exception as e:
        log.exception(e)
        raise e


class ProcessFileForm(BaseModel):
    file_id: str
    content: Optional[str] = None
    collection_name: Optional[str] = None


@router.post("/process/file")
def process_file(
    request: Request,
    form_data: ProcessFileForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    """
    Process a file and save its content to the vector database.
    Process a file and save its content to the vector database.
    Note: granular session management is used to prevent connection pool exhaustion.
    The session is committed before external API calls, and updates use a fresh session.
    """
    if user.role == "admin":
        file = Files.get_file_by_id(form_data.file_id, db=db)
    else:
        file = Files.get_file_by_id_and_user_id(form_data.file_id, user.id, db=db)

    if file:
        try:

            collection_name = form_data.collection_name

            if collection_name is None:
                collection_name = f"file-{file.id}"

            if form_data.content:
                # Update the content in the file
                # Usage: /files/{file_id}/data/content/update, /files/ (audio file upload pipeline)

                try:
                    # /files/{file_id}/data/content/update
                    VECTOR_DB_CLIENT.delete_collection(
                        collection_name=f"file-{file.id}"
                    )
                except:
                    # Audio file upload pipeline
                    pass

                docs = [
                    Document(
                        page_content=form_data.content.replace("<br/>", "\n"),
                        metadata={
                            **file.meta,
                            "name": file.filename,
                            "created_by": file.user_id,
                            "file_id": file.id,
                            "source": file.filename,
                        },
                    )
                ]

                text_content = form_data.content
            elif form_data.collection_name:
                # Check if the file has already been processed and save the content
                # Usage: /knowledge/{id}/file/add, /knowledge/{id}/file/update

                result = VECTOR_DB_CLIENT.query(
                    collection_name=f"file-{file.id}", filter={"file_id": file.id}
                )

                if result is not None and len(result.ids[0]) > 0:
                    docs = [
                        Document(
                            page_content=result.documents[0][idx],
                            metadata=result.metadatas[0][idx],
                        )
                        for idx, id in enumerate(result.ids[0])
                    ]
                else:
                    docs = [
                        Document(
                            page_content=file.data.get("content", ""),
                            metadata={
                                **file.meta,
                                "name": file.filename,
                                "created_by": file.user_id,
                                "file_id": file.id,
                                "source": file.filename,
                            },
                        )
                    ]

                text_content = file.data.get("content", "")
            else:
                # Process the file and save the content
                # Usage: /files/
                file_path = file.path
                if file_path:
                    file_path = Storage.get_file(file_path)
                    loader = Loader(
                        engine=request.app.state.config.CONTENT_EXTRACTION_ENGINE,
                        user=user,
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
                    docs = loader.load(
                        file.filename, file.meta.get("content_type"), file_path
                    )

                    docs = [
                        Document(
                            page_content=doc.page_content,
                            metadata={
                                **filter_metadata(doc.metadata),
                                "name": file.filename,
                                "created_by": file.user_id,
                                "file_id": file.id,
                                "source": file.filename,
                            },
                        )
                        for doc in docs
                    ]
                else:
                    docs = [
                        Document(
                            page_content=file.data.get("content", ""),
                            metadata={
                                **file.meta,
                                "name": file.filename,
                                "created_by": file.user_id,
                                "file_id": file.id,
                                "source": file.filename,
                            },
                        )
                    ]
                text_content = " ".join([doc.page_content for doc in docs])

            log.debug(f"text_content: {text_content}")
            Files.update_file_data_by_id(
                file.id,
                {"content": text_content},
                db=db,
            )
            hash = calculate_sha256_string(text_content)

            if request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL:
                Files.update_file_data_by_id(file.id, {"status": "completed"}, db=db)
                Files.update_file_hash_by_id(file.id, hash, db=db)
                return {
                    "status": True,
                    "collection_name": None,
                    "filename": file.filename,
                    "content": text_content,
                }
            else:
                try:
                    # Commit any pending changes before the slow embedding step.
                    # Note: file is already a Pydantic model (not ORM), so no expunge needed.
                    db.commit()

                    # External embedding API takes time (5-60s+).
                    # Subsequent updates use fresh sessions via get_db().
                    result = save_docs_to_vector_db(
                        request,
                        docs=docs,
                        collection_name=collection_name,
                        metadata={
                            "file_id": file.id,
                            "name": file.filename,
                            "hash": hash,
                        },
                        add=(True if form_data.collection_name else False),
                        user=user,
                    )
                    log.info(f"added {len(docs)} items to collection {collection_name}")

                    if result:
                        # Fresh session for the final update.
                        with get_db() as session:
                            Files.update_file_metadata_by_id(
                                file.id,
                                {
                                    "collection_name": collection_name,
                                },
                                db=session,
                            )

                            Files.update_file_data_by_id(
                                file.id,
                                {"status": "completed"},
                                db=session,
                            )
                            Files.update_file_hash_by_id(file.id, hash, db=session)

                            return {
                                "status": True,
                                "collection_name": collection_name,
                                "filename": file.filename,
                                "content": text_content,
                            }
                    else:
                        raise Exception("Error saving document to vector database")
                except Exception as e:
                    raise e

        except Exception as e:
            log.exception(e)
            # Fresh session for error status update.
            with get_db() as session:
                Files.update_file_data_by_id(
                    file.id,
                    {"status": "failed"},
                    db=session,
                )
                # Clear the hash so the file can be re-uploaded after fixing the issue
                Files.update_file_hash_by_id(file.id, None, db=session)

            if "No pandoc was found" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.PANDOC_NOT_INSTALLED,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e),
                )

    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )


class ProcessTextForm(BaseModel):
    name: str
    content: str
    collection_name: Optional[str] = None


@router.post("/process/text")
async def process_text(
    request: Request,
    form_data: ProcessTextForm,
    user=Depends(get_verified_user),
):
    collection_name = form_data.collection_name
    if collection_name is None:
        collection_name = calculate_sha256_string(form_data.content)

    docs = [
        Document(
            page_content=form_data.content,
            metadata={"name": form_data.name, "created_by": user.id},
        )
    ]
    text_content = form_data.content
    log.debug(f"text_content: {text_content}")

    result = await run_in_threadpool(
        save_docs_to_vector_db, request, docs, collection_name, user=user
    )
    if result:
        return {
            "status": True,
            "collection_name": collection_name,
            "content": text_content,
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT(),
        )


@router.post("/process/youtube")
@router.post("/process/web")
async def process_web(
    request: Request,
    form_data: ProcessUrlForm,
    process: bool = Query(True, description="Whether to process and save the content"),
    overwrite: bool = Query(
        True, description="Whether to overwrite existing collection"
    ),
    user=Depends(get_verified_user),
):
    try:
        content, docs = await run_in_threadpool(
            get_content_from_url, request, form_data.url
        )
        log.debug(f"text_content: {content}")

        if process:
            collection_name = form_data.collection_name
            if not collection_name:
                collection_name = calculate_sha256_string(form_data.url)[:63]

            if not request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL:
                await run_in_threadpool(
                    save_docs_to_vector_db,
                    request,
                    docs,
                    collection_name,
                    overwrite=overwrite,
                    add=(not overwrite),
                    user=user,
                )
            else:
                collection_name = None

            return {
                "status": True,
                "collection_name": collection_name,
                "filename": form_data.url,
                "file": {
                    "data": {
                        "content": content,
                    },
                    "meta": {
                        "name": form_data.url,
                        "source": form_data.url,
                    },
                },
            }
        else:
            return {
                "status": True,
                "content": content,
            }
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


def search_web(
    request: Request, engine: str, query: str, user=None
) -> list[SearchResult]:
    """Search the web using a search engine and return the results as a list of SearchResult objects.
    Will look for a search engine API key in environment variables in the following order:
    - SEARXNG_QUERY_URL
    - YACY_QUERY_URL + YACY_USERNAME + YACY_PASSWORD
    - GOOGLE_PSE_API_KEY + GOOGLE_PSE_ENGINE_ID
    - BRAVE_SEARCH_API_KEY
    - KAGI_SEARCH_API_KEY
    - MOJEEK_SEARCH_API_KEY
    - BOCHA_SEARCH_API_KEY
    - SERPSTACK_API_KEY
    - SERPER_API_KEY
    - SERPLY_API_KEY
    - TAVILY_API_KEY
    - EXA_API_KEY
    - PERPLEXITY_API_KEY
    - SOUGOU_API_SID + SOUGOU_API_SK
    - SEARCHAPI_API_KEY + SEARCHAPI_ENGINE (by default `google`)
    - SERPAPI_API_KEY + SERPAPI_ENGINE (by default `google`)
    Args:
        query (str): The query to search for
    """

    # TODO: add playwright to search the web
    if engine == "ollama_cloud":
        return search_ollama_cloud(
            "https://ollama.com",
            request.app.state.config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == "perplexity_search":
        if request.app.state.config.PERPLEXITY_API_KEY:
            return search_perplexity_search(
                request.app.state.config.PERPLEXITY_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                request.app.state.config.PERPLEXITY_SEARCH_API_URL,
                user,
            )
        else:
            raise Exception("No PERPLEXITY_API_KEY found in environment variables")
    elif engine == "searxng":
        if request.app.state.config.SEARXNG_QUERY_URL:
            searxng_kwargs = {"language": request.app.state.config.SEARXNG_LANGUAGE}
            return search_searxng(
                request.app.state.config.SEARXNG_QUERY_URL,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                **searxng_kwargs,
            )
        else:
            raise Exception("No SEARXNG_QUERY_URL found in environment variables")
    elif engine == "yacy":
        if request.app.state.config.YACY_QUERY_URL:
            return search_yacy(
                request.app.state.config.YACY_QUERY_URL,
                request.app.state.config.YACY_USERNAME,
                request.app.state.config.YACY_PASSWORD,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No YACY_QUERY_URL found in environment variables")
    elif engine == "google_pse":
        if (
            request.app.state.config.GOOGLE_PSE_API_KEY
            and request.app.state.config.GOOGLE_PSE_ENGINE_ID
        ):
            return search_google_pse(
                request.app.state.config.GOOGLE_PSE_API_KEY,
                request.app.state.config.GOOGLE_PSE_ENGINE_ID,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                referer=request.app.state.config.WEBUI_URL,
            )
        else:
            raise Exception(
                "No GOOGLE_PSE_API_KEY or GOOGLE_PSE_ENGINE_ID found in environment variables"
            )
    elif engine == "brave":
        if request.app.state.config.BRAVE_SEARCH_API_KEY:
            return search_brave(
                request.app.state.config.BRAVE_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No BRAVE_SEARCH_API_KEY found in environment variables")
    elif engine == "kagi":
        if request.app.state.config.KAGI_SEARCH_API_KEY:
            return search_kagi(
                request.app.state.config.KAGI_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No KAGI_SEARCH_API_KEY found in environment variables")
    elif engine == "mojeek":
        if request.app.state.config.MOJEEK_SEARCH_API_KEY:
            return search_mojeek(
                request.app.state.config.MOJEEK_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No MOJEEK_SEARCH_API_KEY found in environment variables")
    elif engine == "bocha":
        if request.app.state.config.BOCHA_SEARCH_API_KEY:
            return search_bocha(
                request.app.state.config.BOCHA_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No BOCHA_SEARCH_API_KEY found in environment variables")
    elif engine == "serpstack":
        if request.app.state.config.SERPSTACK_API_KEY:
            return search_serpstack(
                request.app.state.config.SERPSTACK_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                https_enabled=request.app.state.config.SERPSTACK_HTTPS,
            )
        else:
            raise Exception("No SERPSTACK_API_KEY found in environment variables")
    elif engine == "serper":
        if request.app.state.config.SERPER_API_KEY:
            return search_serper(
                request.app.state.config.SERPER_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SERPER_API_KEY found in environment variables")
    elif engine == "serply":
        if request.app.state.config.SERPLY_API_KEY:
            return search_serply(
                request.app.state.config.SERPLY_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                filter_list=request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SERPLY_API_KEY found in environment variables")
    elif engine == "duckduckgo":
        return search_duckduckgo(
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            concurrent_requests=request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS,
            backend=request.app.state.config.DDGS_BACKEND,
        )
    elif engine == "tavily":
        if request.app.state.config.TAVILY_API_KEY:
            return search_tavily(
                request.app.state.config.TAVILY_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No TAVILY_API_KEY found in environment variables")
    elif engine == "exa":
        if request.app.state.config.EXA_API_KEY:
            return search_exa(
                request.app.state.config.EXA_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No EXA_API_KEY found in environment variables")
    elif engine == "searchapi":
        if request.app.state.config.SEARCHAPI_API_KEY:
            return search_searchapi(
                request.app.state.config.SEARCHAPI_API_KEY,
                request.app.state.config.SEARCHAPI_ENGINE,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SEARCHAPI_API_KEY found in environment variables")
    elif engine == "serpapi":
        if request.app.state.config.SERPAPI_API_KEY:
            return search_serpapi(
                request.app.state.config.SERPAPI_API_KEY,
                request.app.state.config.SERPAPI_ENGINE,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SERPAPI_API_KEY found in environment variables")
    elif engine == "jina":
        return search_jina(
            request.app.state.config.JINA_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.JINA_API_BASE_URL,
        )
    elif engine == "bing":
        return search_bing(
            request.app.state.config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            request.app.state.config.BING_SEARCH_V7_ENDPOINT,
            str(DEFAULT_LOCALE),
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == "azure":
        if (
            request.app.state.config.AZURE_AI_SEARCH_API_KEY
            and request.app.state.config.AZURE_AI_SEARCH_ENDPOINT
            and request.app.state.config.AZURE_AI_SEARCH_INDEX_NAME
        ):
            return search_azure(
                request.app.state.config.AZURE_AI_SEARCH_API_KEY,
                request.app.state.config.AZURE_AI_SEARCH_ENDPOINT,
                request.app.state.config.AZURE_AI_SEARCH_INDEX_NAME,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception(
                "AZURE_AI_SEARCH_API_KEY, AZURE_AI_SEARCH_ENDPOINT, and AZURE_AI_SEARCH_INDEX_NAME are required for Azure AI Search"
            )
    elif engine == "exa":
        return search_exa(
            request.app.state.config.EXA_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == "perplexity":
        return search_perplexity(
            request.app.state.config.PERPLEXITY_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            model=request.app.state.config.PERPLEXITY_MODEL,
            search_context_usage=request.app.state.config.PERPLEXITY_SEARCH_CONTEXT_USAGE,
        )
    elif engine == "sougou":
        if (
            request.app.state.config.SOUGOU_API_SID
            and request.app.state.config.SOUGOU_API_SK
        ):
            return search_sougou(
                request.app.state.config.SOUGOU_API_SID,
                request.app.state.config.SOUGOU_API_SK,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception(
                "No SOUGOU_API_SID or SOUGOU_API_SK found in environment variables"
            )
    elif engine == "firecrawl":
        return search_firecrawl(
            request.app.state.config.FIRECRAWL_API_BASE_URL,
            request.app.state.config.FIRECRAWL_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == "external":
        return search_external(
            request,
            request.app.state.config.EXTERNAL_WEB_SEARCH_URL,
            request.app.state.config.EXTERNAL_WEB_SEARCH_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            user=user,
        )
    elif engine == "yandex":
        return search_yandex(
            request,
            request.app.state.config.YANDEX_WEB_SEARCH_URL,
            request.app.state.config.YANDEX_WEB_SEARCH_API_KEY,
            request.app.state.config.YANDEX_WEB_SEARCH_CONFIG,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            user=user,
        )
    elif engine == "youcom":
        return search_youcom(
            request.app.state.config.YOUCOM_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    else:
        raise Exception("No search engine API key found in environment variables")


def _search_result_to_item(result: SearchResult) -> dict[str, Any]:
    return {
        "link": result.link,
        "title": result.title,
        "snippet": result.snippet,
    }


def _flatten_search_results(
    search_results: list[list[SearchResult]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for result in search_results:
        if not result:
            continue
        for item in result:
            if item and item.link:
                items.append(_search_result_to_item(item))
    return items


def _dedupe_items_by_canonical_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        url = item.get("link", "")
        canonical = canonicalize_url(url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        deduped.append(item)
    return deduped


def _apply_recency_hint(query: str, recency_days: Optional[int]) -> str:
    if recency_days is None:
        return query

    try:
        normalized_days = int(recency_days)
    except Exception:
        return query

    if normalized_days <= 0:
        return query

    return sanitize_query(f"{query} last {normalized_days} days")


async def _wait_for_brave_fallback_slot(request: Request, min_interval_ms: int) -> None:
    if min_interval_ms <= 0:
        return

    now = time.monotonic()
    last = getattr(request.app.state, "_WEB_SEARCH_BRAVE_FALLBACK_LAST_TS", None)
    if isinstance(last, (float, int)):
        remaining = (float(min_interval_ms) / 1000.0) - (now - float(last))
        if remaining > 0:
            await asyncio.sleep(remaining)

    request.app.state._WEB_SEARCH_BRAVE_FALLBACK_LAST_TS = time.monotonic()


COARSE_CATEGORY_ORDER = [
    "software",
    "medicine",
    "legal",
    "science",
    "news",
    "shopping",
    "general",
]

COARSE_CATEGORY_TOPICS: dict[str, list[str]] = {
    "software": ["software_apis_devops", "ai_ml_local_llm"],
    "medicine": ["medicine_health"],
    "legal": ["legal_compliance"],
    "science": ["science_academic"],
    "news": ["news_current_events"],
    "shopping": ["general"],
    "general": ["general"],
}

COARSE_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "software": (
        "api",
        "sdk",
        "library",
        "programming",
        "docker",
        "kubernetes",
        "python",
        "javascript",
        "error",
        "traceback",
        "bug",
    ),
    "medicine": (
        "medicine",
        "medical",
        "drug",
        "antibiotic",
        "pharmacology",
        "disease",
        "clinical",
        "patient",
    ),
    "legal": (
        "law",
        "legal",
        "regulation",
        "regulatory",
        "compliance",
        "statute",
        "directive",
        "gdpr",
    ),
    "science": (
        "study",
        "paper",
        "journal",
        "research",
        "experiment",
        "chemistry",
        "physics",
        "biology",
    ),
    "news": (
        "news",
        "today",
        "latest",
        "current",
        "recent",
        "breaking",
    ),
    "shopping": (
        "buy",
        "price",
        "pricing",
        "best",
        "compare",
        "deal",
        "shop",
        "shopping",
        "recommend",
        "review",
    ),
}

COARSE_CONFIDENCE_HIGH = 0.75
CITATION_TRUST_MIN = 0.72
TIME_SCOPE_EVERGREEN = "evergreen"
TIME_SCOPE_RECENT = "recent"
TIME_SCOPE_BREAKING = "breaking"
TIME_SCOPE_ALLOWED = {
    TIME_SCOPE_EVERGREEN,
    TIME_SCOPE_RECENT,
    TIME_SCOPE_BREAKING,
}
DEFAULT_RECENT_RECENCY_DAYS = 365
DEFAULT_BREAKING_RECENCY_DAYS = 30
FOCUSED_DOMAIN_WINDOW_MIN = 3
FOCUSED_DOMAIN_WINDOW_MAX = 6
FOCUSED_DOMAIN_WINDOW_DEFAULT = 4
FOCUSED_SEARCH_SESSION_TTL_SECONDS = 1800

_FOCUSED_SEARCH_SESSIONS: dict[str, dict[str, Any]] = {}
_FOCUSED_SEARCH_SESSIONS_BY_SCOPE: dict[str, set[str]] = {}


def _focused_scope_key(metadata: Optional[dict[str, Any]]) -> str:
    payload = metadata or {}
    chat_id = str(payload.get("chat_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    if not chat_id or not message_id:
        return ""
    return f"{chat_id}:{message_id}"


def _reset_focused_scope(scope_key: str) -> None:
    if not scope_key:
        return
    existing = _FOCUSED_SEARCH_SESSIONS_BY_SCOPE.pop(scope_key, set())
    for session_id in existing:
        _FOCUSED_SEARCH_SESSIONS.pop(session_id, None)


def _prune_stale_focused_sessions() -> None:
    now = int(time.time())
    stale_ids = [
        session_id
        for session_id, payload in _FOCUSED_SEARCH_SESSIONS.items()
        if now - int(payload.get("created_at", 0) or 0) > FOCUSED_SEARCH_SESSION_TTL_SECONDS
    ]
    for session_id in stale_ids:
        _invalidate_focused_search_session(session_id)


def _create_focused_search_session(
    *,
    metadata: Optional[dict[str, Any]],
    query: str,
) -> str:
    _prune_stale_focused_sessions()
    scope_key = _focused_scope_key(metadata)
    if scope_key:
        _reset_focused_scope(scope_key)

    session_id = f"fss_{uuid.uuid4().hex[:24]}"
    _FOCUSED_SEARCH_SESSIONS[session_id] = {
        "query": query,
        "scope": scope_key,
        "created_at": int(time.time()),
    }

    if scope_key:
        _FOCUSED_SEARCH_SESSIONS_BY_SCOPE.setdefault(scope_key, set()).add(session_id)

    return session_id


def _get_focused_search_session(
    *,
    search_session_id: Optional[str],
    metadata: Optional[dict[str, Any]],
    query: str,
) -> Optional[dict[str, Any]]:
    _prune_stale_focused_sessions()
    session_id = str(search_session_id or "").strip()
    if not session_id:
        return None

    session = _FOCUSED_SEARCH_SESSIONS.get(session_id)
    if not session:
        return None

    expected_scope = str(session.get("scope") or "")
    current_scope = _focused_scope_key(metadata)
    if expected_scope and expected_scope != current_scope:
        _FOCUSED_SEARCH_SESSIONS.pop(session_id, None)
        if expected_scope in _FOCUSED_SEARCH_SESSIONS_BY_SCOPE:
            _FOCUSED_SEARCH_SESSIONS_BY_SCOPE[expected_scope].discard(session_id)
        return None

    if str(session.get("query") or "") != query:
        return None

    return session


def _invalidate_focused_search_session(
    search_session_id: Optional[str],
) -> None:
    session_id = str(search_session_id or "").strip()
    if not session_id:
        return
    session = _FOCUSED_SEARCH_SESSIONS.pop(session_id, None)
    if not session:
        return
    scope_key = str(session.get("scope") or "")
    if scope_key in _FOCUSED_SEARCH_SESSIONS_BY_SCOPE:
        _FOCUSED_SEARCH_SESSIONS_BY_SCOPE[scope_key].discard(session_id)
        if not _FOCUSED_SEARCH_SESSIONS_BY_SCOPE[scope_key]:
            _FOCUSED_SEARCH_SESSIONS_BY_SCOPE.pop(scope_key, None)


def _clamp_domain_window_size(value: Optional[int]) -> int:
    try:
        parsed = int(value or FOCUSED_DOMAIN_WINDOW_DEFAULT)
    except Exception:
        parsed = FOCUSED_DOMAIN_WINDOW_DEFAULT
    return max(FOCUSED_DOMAIN_WINDOW_MIN, min(FOCUSED_DOMAIN_WINDOW_MAX, parsed))


def _decode_domain_cursor(cursor: Optional[str]) -> int:
    token = str(cursor or "").strip()
    if not token:
        return 0
    try:
        value = int(token)
    except Exception:
        return 0
    return max(0, value)


def _compact_category_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in options:
        category = str(item.get("category") or "").strip()
        if not category:
            continue
        compact.append(
            {
                "id": category,
                "category": category,
                "label": category,
                "domain_count": int(item.get("domain_count", 0) or 0),
                "has_local_domains": bool(item.get("has_local_domains", False)),
            }
        )
    return compact


def _compact_domain_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in options:
        domain = normalize_domain(str(item.get("domain") or ""))
        if not domain:
            continue
        compact.append(
            {
                "id": domain,
                "domain": domain,
                "trust": round(float(item.get("trust", 0.0) or 0.0), 4),
                "is_local": bool(item.get("is_local", False)),
                "source_type": str(item.get("source_type") or ""),
            }
        )
    return compact


def _paginate_domain_options(
    options: list[dict[str, Any]],
    *,
    cursor: Optional[str],
    window_size: int,
) -> tuple[list[dict[str, Any]], Optional[str], int, int]:
    total = len(options)
    if total == 0:
        return [], None, 0, 0

    start = _decode_domain_cursor(cursor)
    if start >= total:
        start = max(0, total - window_size)
    end = min(total, start + window_size)
    window = options[start:end]
    next_cursor = str(end) if end < total else None
    return window, next_cursor, len(window), total


def _topics_for_category(category: str) -> list[str]:
    return COARSE_CATEGORY_TOPICS.get(category, ["general"])


def _normalize_category(value: str) -> str:
    candidate = sanitize_query(value or "").lower().replace(" ", "_")
    if candidate not in COARSE_CATEGORY_TOPICS:
        return ""
    return candidate


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _normalize_positive_int(value: Any) -> Optional[int]:
    try:
        normalized = int(value)
    except Exception:
        return None
    if normalized <= 0:
        return None
    return normalized


def _normalize_time_scope(value: Optional[str]) -> str:
    candidate = sanitize_query(value or "").lower()
    if candidate in TIME_SCOPE_ALLOWED:
        return candidate
    return ""


def _build_time_scope_options(
    *,
    plan_time_sensitive: bool,
    recency_days_hint: Optional[int],
) -> list[dict[str, Any]]:
    hinted_days = _normalize_positive_int(recency_days_hint)
    recent_days = hinted_days or DEFAULT_RECENT_RECENCY_DAYS
    breaking_days = min(recent_days, DEFAULT_BREAKING_RECENCY_DAYS)
    recommended_scope = (
        TIME_SCOPE_RECENT if plan_time_sensitive else TIME_SCOPE_EVERGREEN
    )

    return [
        {
            "scope": TIME_SCOPE_EVERGREEN,
            "summary": "Evergreen facts. Do not constrain by recent days.",
            "recency_days": None,
            "recommended": recommended_scope == TIME_SCOPE_EVERGREEN,
        },
        {
            "scope": TIME_SCOPE_RECENT,
            "summary": "Recent developments when freshness matters.",
            "recency_days": recent_days,
            "recommended": recommended_scope == TIME_SCOPE_RECENT,
        },
        {
            "scope": TIME_SCOPE_BREAKING,
            "summary": "Breaking updates only.",
            "recency_days": breaking_days,
            "recommended": False,
        },
    ]


def _compute_effective_recency_days(
    *,
    selected_time_scope: str,
    recency_days_hint: Optional[int],
    plan_time_sensitive: bool,
) -> tuple[Optional[int], str]:
    scope = selected_time_scope
    if scope not in TIME_SCOPE_ALLOWED:
        scope = TIME_SCOPE_RECENT if plan_time_sensitive else TIME_SCOPE_EVERGREEN

    hinted_days = _normalize_positive_int(recency_days_hint)
    if scope == TIME_SCOPE_EVERGREEN:
        return None, "scope_evergreen"

    if scope == TIME_SCOPE_BREAKING:
        if hinted_days is not None:
            return min(hinted_days, DEFAULT_BREAKING_RECENCY_DAYS), "scope_breaking"
        return DEFAULT_BREAKING_RECENCY_DAYS, "scope_breaking_default"

    if hinted_days is not None:
        return hinted_days, "scope_recent_hint"
    return DEFAULT_RECENT_RECENCY_DAYS, "scope_recent_default"


def _coarse_route_category(
    query: str, topic_hint: Optional[str] = None
) -> dict[str, Any]:
    text = f"{query} {topic_hint or ''}".lower()
    scores: dict[str, int] = {}
    for category in COARSE_CATEGORY_ORDER:
        if category == "general":
            continue
        score = 0
        for keyword in COARSE_CATEGORY_KEYWORDS.get(category, ()):
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                score += 1
        scores[category] = score

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_category = "general"
    best_score = 0
    second_score = 0
    if ranked:
        best_category, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0

    if best_score <= 0:
        confidence = 0.35
        best_category = "general"
    elif best_score >= 3 and (best_score - second_score) >= 2:
        confidence = 0.90
    elif best_score >= 2 and (best_score - second_score) >= 1:
        confidence = 0.82
    elif best_score >= 2:
        confidence = 0.72
    elif second_score == 0:
        confidence = 0.70
    else:
        confidence = 0.58

    return {
        "category": best_category,
        "confidence": round(confidence, 2),
        "ambiguous": confidence < COARSE_CONFIDENCE_HIGH,
        "scores": scores,
    }


def _build_broader_discovery_queries(
    *,
    cleaned_query: str,
    plan: WebSearchPlan,
    effective_recency_days: Optional[int],
    max_queries: int,
) -> list[str]:
    candidates = [
        plan.base_exact_query,
        plan.base_general_query,
        cleaned_query,
    ]
    queries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        query = sanitize_query(_apply_recency_hint(candidate, effective_recency_days))
        if not query or query in seen:
            continue
        seen.add(query)
        queries.append(query)
        if len(queries) >= max_queries:
            break
    return queries


def _category_summaries() -> dict[str, str]:
    return {
        "software": "APIs, SDKs, docs, debugging, dev tooling.",
        "medicine": "Clinical and pharmacology information with medical sources.",
        "legal": "Regulations, statutes, and compliance guidance.",
        "science": "Research papers, journals, and scientific references.",
        "news": "Current events and time-sensitive developments.",
        "shopping": "Product/price discovery (currently mapped to general).",
        "general": "Fallback for broad or mixed topics.",
    }


def _build_category_options(include_community: bool = False) -> list[dict[str, Any]]:
    summaries = _category_summaries()
    normalized_sources = load_normalized_source_registry()
    options: list[dict[str, Any]] = []

    for category in COARSE_CATEGORY_ORDER:
        topic_set = set(_topics_for_category(category))
        domains: set[str] = set()
        local_domains: set[str] = set()
        for source in normalized_sources:
            if source.topic not in topic_set:
                continue
            if not include_community and source.family == "community":
                continue
            if not source.allow_site_constraint:
                continue
            domains.add(source.domain)
            if source.is_local:
                local_domains.add(source.domain)

        options.append(
            {
                "category": category,
                "summary": summaries.get(category, ""),
                "topics": sorted(topic_set),
                "domain_count": len(domains),
                "local_domain_count": len(local_domains),
                "has_local_domains": bool(local_domains),
            }
        )

    return options


def _build_domain_options_for_categories(
    categories: list[str],
    *,
    include_community: bool,
    local_first: bool,
    time_sensitive: bool,
) -> list[dict[str, Any]]:
    if not categories:
        return []

    category_for_topic: dict[str, str] = {}
    for category in categories:
        for topic in _topics_for_category(category):
            category_for_topic[topic] = category

    normalized_sources = load_normalized_source_registry()
    ranked: list[tuple[float, int, str, Any]] = []
    for source in normalized_sources:
        category = category_for_topic.get(source.topic)
        if not category:
            continue
        if not include_community and source.family == "community":
            continue
        if not source.allow_site_constraint:
            continue

        score = float(source.trust_score)
        if local_first and source.is_local:
            score += 0.35
        if source.prefer_for_exact_facts:
            score += 0.12
        if time_sensitive and source.prefer_for_time_sensitive:
            score += 0.12

        ranked.append((score, source.default_priority, category, source))

    ranked.sort(key=lambda item: (-item[0], item[1], item[3].domain))

    seen_domains: set[str] = set()
    options: list[dict[str, Any]] = []
    for _, _, category, source in ranked:
        domain = normalize_domain(source.domain)
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        options.append(
            {
                "domain": domain,
                "category": category,
                "topic": source.topic,
                "source_type": source.source_type,
                "trust_tier": source.trust_tier,
                "trust": round(float(source.trust_score), 4),
                "is_local": bool(source.is_local),
                "access": source.access,
                "freshness_profile": source.freshness_profile,
                "default_priority": source.default_priority,
            }
        )

    return options


def _build_step_payload(
    *,
    query: str,
    phase: str,
    next_action: str,
    coarse_route: dict[str, Any],
    category_options: list[dict[str, Any]],
    domain_options: list[dict[str, Any]],
    selected_categories: list[str],
    selected_domains: list[str],
    time_scope_options: list[dict[str, Any]],
    selected_time_scope: str,
    effective_recency_days: Optional[int] = None,
    recency_policy_reason: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    message: Optional[str] = None,
    errors: Optional[list[str]] = None,
    unavailable_categories: Optional[list[dict[str, Any]]] = None,
    search_session_id: Optional[str] = None,
    cursor: Optional[str] = None,
    next_cursor: Optional[str] = None,
    domain_options_shown: Optional[int] = None,
    domain_options_total: Optional[int] = None,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "next_action": next_action,
        "query": query,
        "coarse_route": coarse_route,
        "category_options": category_options,
        "domain_options": domain_options,
        "time_scope_options": time_scope_options,
        "selected_categories": selected_categories,
        "selected_domains": selected_domains,
        "selected_time_scope": selected_time_scope,
        "effective_recency_days": effective_recency_days,
        "recency_policy_reason": recency_policy_reason,
        "fallback_reason": fallback_reason,
        "search_session_id": search_session_id,
        "cursor": cursor,
        "next_cursor": next_cursor,
        "domain_options_shown": domain_options_shown,
        "domain_options_total": domain_options_total,
        "errors": errors or [],
        "unavailable_categories": unavailable_categories or [],
        "message": message or "",
        "queries": [],
        "items": [],
        "evidence_items": [],
        "citation_items": [],
        "candidate_count": 0,
        "evidence_count": 0,
        "citation_count": 0,
        "coverage_complete": False,
        "quality_score": 0.0,
        "local_phase_executed": False,
        "brave_fallback_used": False,
        "topic": "general",
        "local_primary_hits": 0,
        "trusted_domains": 0,
    }


async def execute_strong_source_search(
    request: Request,
    *,
    query: str,
    user=None,
    max_queries: int = 3,
    mode: str = "search",
    search_session_id: Optional[str] = None,
    cursor: Optional[str] = None,
    selected_categories: Optional[list[str]] = None,
    selected_domains: Optional[list[str]] = None,
    selected_domain_ids: Optional[list[str]] = None,
    selected_time_scope: Optional[str] = None,
    max_domains: int = 4,
    domain_window_size: int = 4,
    include_full_options: bool = False,
    topic_hint: Optional[str] = None,
    recency_days: Optional[int] = None,
    include_community: bool = False,
    event_emitter=None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    cfg = request.app.state.config
    cleaned_query = sanitize_query(query)
    if not cleaned_query:
        raise ValueError("Query is empty")

    mode = sanitize_query(mode or "search").lower() or "search"
    if mode not in {"search", "list_categories", "list_domains"}:
        mode = "search"

    bounded_max_queries = max(1, min(6, int(max_queries or 3)))
    bounded_max_domains = max(1, min(4, int(max_domains or 4)))
    local_first = bool(getattr(cfg, "WEB_SEARCH_LOCAL_FIRST", True))
    local_min_primary_hits = max(
        1, int(getattr(cfg, "WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS", 2) or 2)
    )
    brave_fallback_enabled = bool(getattr(cfg, "WEB_SEARCH_BRAVE_FALLBACK", True))
    brave_fallback_max_queries = max(
        1, int(getattr(cfg, "WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES", 2) or 2)
    )
    brave_min_interval_ms = max(
        0, int(getattr(cfg, "WEB_SEARCH_BRAVE_MIN_INTERVAL_MS", 1000) or 1000)
    )
    planner_stop_score = float(
        getattr(cfg, "WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE", 0.66)
    )
    debug_tool_journey = bool(
        ((metadata or {}).get("params", {}) or {}).get("debug_tool_journey", False)
    )
    include_full_option_payload = bool(include_full_options or debug_tool_journey)
    bounded_domain_window = _clamp_domain_window_size(domain_window_size)

    planner_context = (
        sanitize_query(topic_hint or "", max_length=512) if topic_hint else None
    )
    plan = build_web_search_plan(
        cleaned_query,
        conversation_context=planner_context,
        max_targeted_domains=max(
            bounded_max_queries,
            int(
                getattr(cfg, "WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE", 4) or 4
            ),
        ),
        local_first=local_first,
    )

    async def emit_focus_status(payload: dict[str, Any]) -> None:
        if not event_emitter:
            return
        try:
            await event_emitter({"type": "status", "data": payload})
        except Exception:
            log.debug("Failed to emit focused search status event", exc_info=True)

    def domain_urls(domains: list[str], limit: int = 8) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for domain in domains:
            normalized = (domain or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            urls.append(f"https://{normalized}")
            if len(urls) >= limit:
                break
        return urls

    coarse_route = _coarse_route_category(cleaned_query, planner_context)
    all_category_options = _build_category_options(include_community=include_community)
    category_domain_counts = {
        str(item.get("category") or ""): int(item.get("domain_count", 0) or 0)
        for item in all_category_options
    }
    category_options_full = [
        item
        for item in all_category_options
        if int(item.get("domain_count", 0) or 0) > 0
    ]
    unavailable_categories_full = [
        item
        for item in all_category_options
        if int(item.get("domain_count", 0) or 0) <= 0
    ]
    category_options = (
        category_options_full
        if include_full_option_payload
        else _compact_category_options(category_options_full)
    )
    unavailable_categories = (
        unavailable_categories_full
        if include_full_option_payload
        else _compact_category_options(unavailable_categories_full)
    )

    normalized_categories = _unique_ordered(
        [
            _normalize_category(category)
            for category in (selected_categories or [])
            if _normalize_category(category)
        ]
    )
    normalized_domains = _unique_ordered(
        [
            normalize_domain(str(domain))
            for domain in (selected_domains or [])
            if normalize_domain(str(domain))
        ]
    )
    normalized_domain_ids = _unique_ordered(
        [
            normalize_domain(str(domain_id))
            for domain_id in (selected_domain_ids or [])
            if normalize_domain(str(domain_id))
        ]
    )
    normalized_time_scope = _normalize_time_scope(selected_time_scope)
    if not normalized_time_scope:
        normalized_time_scope = (
            TIME_SCOPE_RECENT if plan.time_sensitive else TIME_SCOPE_EVERGREEN
        )
    time_scope_options = _build_time_scope_options(
        plan_time_sensitive=plan.time_sensitive,
        recency_days_hint=recency_days,
    )
    effective_recency_days, recency_policy_reason = _compute_effective_recency_days(
        selected_time_scope=normalized_time_scope,
        recency_days_hint=recency_days,
        plan_time_sensitive=plan.time_sensitive,
    )

    provided_session_id = str(search_session_id or "").strip()
    session_scope_key = _focused_scope_key(metadata)
    enforce_session_scope = bool(session_scope_key)
    resolved_session = _get_focused_search_session(
        search_session_id=provided_session_id,
        metadata=metadata,
        query=cleaned_query,
    )
    has_selection_inputs = bool(
        normalized_categories or normalized_domains or normalized_domain_ids
    )

    if mode == "list_categories":
        active_search_session_id = (
            _create_focused_search_session(
                metadata=metadata,
                query=cleaned_query,
            )
            if enforce_session_scope
            else provided_session_id
        )
    else:
        if enforce_session_scope and resolved_session is None:
            if mode == "search" and has_selection_inputs:
                active_search_session_id = _create_focused_search_session(
                    metadata=metadata,
                    query=cleaned_query,
                )
                await emit_focus_status(
                    {
                        "action": "web_search",
                        "description": "Focused search session expired. Recovering with existing selections",
                        "done": True,
                        "plan": {"mode": "focused_local_first"},
                        "planner": {
                            "mode": "focused_local_first",
                            "fallback_reason": "session_auto_recovered",
                        },
                    }
                )
            elif mode == "search" and not has_selection_inputs and not provided_session_id:
                active_search_session_id = _create_focused_search_session(
                    metadata=metadata,
                    query=cleaned_query,
                )
            else:
                restart_session_id = _create_focused_search_session(
                    metadata=metadata,
                    query=cleaned_query,
                )
                await emit_focus_status(
                    {
                        "action": "web_search",
                        "description": "Focused search session expired. Restarting category selection",
                        "done": True,
                        "plan": {"mode": "focused_local_first"},
                        "planner": {
                            "mode": "focused_local_first",
                            "fallback_reason": "session_expired",
                        },
                    }
                )
                return _build_step_payload(
                    query=cleaned_query,
                    phase="awaiting_category_selection",
                    next_action="restart_selection",
                    coarse_route=coarse_route,
                    category_options=category_options,
                    domain_options=[],
                    selected_categories=[],
                    selected_domains=[],
                    time_scope_options=time_scope_options,
                    selected_time_scope=normalized_time_scope,
                    effective_recency_days=effective_recency_days,
                    recency_policy_reason=recency_policy_reason,
                    fallback_reason="session_expired",
                    unavailable_categories=unavailable_categories,
                    search_session_id=restart_session_id,
                    message=(
                        "Focused search session is missing or expired. "
                        "Restart category selection with this new session."
                    ),
                )
        else:
            active_search_session_id = provided_session_id

    async def run_broader_discovery(
        *,
        fallback_reason: str,
        selected_categories_for_payload: list[str],
        message: str,
    ) -> dict[str, Any]:
        await emit_focus_status(
            {
                "action": "web_search",
                "description": (
                    "No curated domains for this category; running broader discovery now"
                ),
                "done": False,
                "plan": {
                    "mode": "focused_local_first",
                    "topic": plan.topic,
                    "selected_domains": [],
                    "selected_time_scope": normalized_time_scope,
                },
                "planner": {
                    "mode": "focused_local_first",
                    "fallback_reason": fallback_reason,
                    "effective_recency_days": effective_recency_days,
                },
            }
        )

        broader_queries = _build_broader_discovery_queries(
            cleaned_query=cleaned_query,
            plan=plan,
            effective_recency_days=effective_recency_days,
            max_queries=min(brave_fallback_max_queries, bounded_max_queries),
        )

        fallback_engine = (
            str(getattr(cfg, "WEB_SEARCH_ENGINE", "") or "").strip().lower()
        )
        if not fallback_engine:
            fallback_engine = "duckduckgo"

        broader_search_results: list[list[SearchResult]] = []
        executed_queries: list[str] = []
        for planned_query in broader_queries:
            try:
                if fallback_engine == "brave":
                    await _wait_for_brave_fallback_slot(request, brave_min_interval_ms)
                query_results = await run_in_threadpool(
                    search_web,
                    request,
                    fallback_engine,
                    planned_query,
                    user,
                )
                broader_search_results.append(query_results)
                executed_queries.append(planned_query)
            except Exception as exc:
                log.warning(
                    "Broader discovery query failed for '%s': %s", planned_query, exc
                )

        combined_items = _dedupe_items_by_canonical_url(
            _flatten_search_results(broader_search_results)
        )
        combined_quality = evaluate_signal_quality(combined_items, plan)
        combined_intent_coverage = evaluate_intent_coverage(
            combined_quality["scored_items"], plan
        )

        quality_score = float(combined_quality.get("avg_top_score", 0.0) or 0.0)
        trusted_domains = int(combined_quality.get("trusted_unique_domains", 0) or 0)
        coverage_complete = bool(
            combined_intent_coverage.get("complete", True)
            and quality_score >= planner_stop_score
            and trusted_domains >= 1
        )

        result_count = max(1, int(cfg.WEB_SEARCH_RESULT_COUNT or 5))
        candidate_scored_items = combined_quality.get("scored_items", [])[
            : result_count * 4
        ]
        evidence_scored_items = candidate_scored_items[: result_count * 2]

        def _materialize_item(item: dict[str, Any]) -> dict[str, Any]:
            link = item.get("link")
            domain = item.get("domain")
            source_type = infer_domain_source_type(domain or "", plan)
            return {
                "title": item.get("title"),
                "link": link,
                "snippet": item.get("snippet"),
                "domain": domain,
                "quality": round(float(item.get("quality", 0.0) or 0.0), 4),
                "trust": round(float(item.get("trust", 0.0) or 0.0), 4),
                "source_type": source_type,
            }

        items = [
            _materialize_item(item) for item in candidate_scored_items if item.get("link")
        ]
        evidence_items = [
            _materialize_item(item) for item in evidence_scored_items if item.get("link")
        ]
        citation_items = [
            item
            for item in evidence_items
            if float(item.get("trust", 0.0) or 0.0) >= CITATION_TRUST_MIN
            and str(item.get("source_type", "") or "") != "community"
        ]
        citation_items = _dedupe_items_by_canonical_url(citation_items)

        evidence_adequate = bool(
            coverage_complete and trusted_domains >= 2 and len(citation_items) >= 3
        )
        adequacy_reason = (
            "sufficient_snippet_evidence"
            if evidence_adequate
            else "insufficient_snippet_evidence"
        )
        recommended_urls = [item.get("link") for item in citation_items[:3] if item.get("link")]

        payload = {
            "phase": "completed",
            "next_action": "answer" if evidence_adequate else "read_selected_sources",
            "category_options": category_options if include_full_option_payload else [],
            "domain_options": [],
            "selected_categories": selected_categories_for_payload,
            "time_scope_options": time_scope_options,
            "selected_time_scope": normalized_time_scope,
            "effective_recency_days": effective_recency_days,
            "recency_policy_reason": recency_policy_reason,
            "queries": executed_queries,
            "items": items,
            "evidence_items": evidence_items,
            "citation_items": citation_items,
            "candidate_count": len(items),
            "evidence_count": len(evidence_items),
            "citation_count": len(citation_items),
            "selected_domains": [],
            "coverage_complete": coverage_complete,
            "quality_score": round(quality_score, 4),
            "local_phase_executed": False,
            "brave_fallback_used": True,
            "fallback_reason": fallback_reason,
            "topic": plan.topic,
            "local_primary_hits": 0,
            "trusted_domains": trusted_domains,
            "message": message,
            "unavailable_categories": unavailable_categories,
            "search_session_id": active_search_session_id,
            "evidence_adequate": evidence_adequate,
            "adequacy_reason": adequacy_reason,
            "recommended_urls": recommended_urls,
        }

        await emit_focus_status(
            {
                "action": "web_search",
                "description": "Focused search completed with broader fallback",
                "done": True,
                "plan": {
                    "mode": "focused_local_first",
                    "topic": plan.topic,
                    "selected_domains": [],
                    "selected_time_scope": normalized_time_scope,
                },
                "planner": {
                    "mode": "focused_local_first",
                    "executed_queries": list(executed_queries),
                    "final_trusted_domains": trusted_domains,
                    "fallback_reason": fallback_reason,
                    "candidate_count": len(items),
                    "evidence_count": len(evidence_items),
                    "citation_count": len(citation_items),
                    "selected_time_scope": normalized_time_scope,
                    "effective_recency_days": effective_recency_days,
                    "recency_policy_reason": recency_policy_reason,
                    "show_debug_metrics": debug_tool_journey,
                    **(
                        {"final_score": round(quality_score, 4)}
                        if debug_tool_journey
                        else {}
                    ),
                },
                "items": citation_items,
                "urls": [],
            }
        )
        _invalidate_focused_search_session(active_search_session_id)
        return payload

    if mode == "list_categories":
        message = "Choose 1-2 categories, then call mode=list_domains."
        next_action = "select_categories"
        if not category_options:
            message = (
                "No curated categories with selectable domains are available right now. "
                "Proceed with broader web discovery."
            )
            next_action = "broader_search"
        payload = _build_step_payload(
            query=cleaned_query,
            phase="awaiting_category_selection",
            next_action=next_action,
            coarse_route=coarse_route,
            category_options=category_options,
            domain_options=[],
            selected_categories=normalized_categories,
            selected_domains=normalized_domains,
            time_scope_options=time_scope_options,
            selected_time_scope=normalized_time_scope,
            effective_recency_days=effective_recency_days,
            recency_policy_reason=recency_policy_reason,
            unavailable_categories=unavailable_categories,
            search_session_id=active_search_session_id,
            message=message,
        )
        await emit_focus_status(
            {
                "action": "web_search",
                "description": "Focused search: category shortlist prepared",
                "done": True,
                "plan": {"mode": "focused_local_first"},
                "planner": {"mode": "focused_local_first"},
            }
        )
        return payload

    if mode == "list_domains":
        if not normalized_categories:
            if coarse_route["confidence"] >= COARSE_CONFIDENCE_HIGH:
                normalized_categories = [coarse_route["category"]]
            else:
                return _build_step_payload(
                    query=cleaned_query,
                    phase="awaiting_category_selection",
                    next_action="select_categories",
                    coarse_route=coarse_route,
                    category_options=category_options,
                    domain_options=[],
                    selected_categories=[],
                    selected_domains=[],
                    time_scope_options=time_scope_options,
                    selected_time_scope=normalized_time_scope,
                    effective_recency_days=effective_recency_days,
                    recency_policy_reason=recency_policy_reason,
                    unavailable_categories=unavailable_categories,
                    search_session_id=active_search_session_id,
                    message="Category selection required before domain selection.",
                )

        if len(normalized_categories) > 2:
            normalized_categories = normalized_categories[:2]

        domain_options_full = _build_domain_options_for_categories(
            normalized_categories,
            include_community=include_community,
            local_first=local_first,
            time_sensitive=plan.time_sensitive,
        )

        if not domain_options_full:
            payload = _build_step_payload(
                query=cleaned_query,
                phase="awaiting_category_selection",
                next_action="reselect_category",
                coarse_route=coarse_route,
                category_options=category_options,
                domain_options=[],
                selected_categories=normalized_categories,
                selected_domains=[],
                time_scope_options=time_scope_options,
                selected_time_scope=normalized_time_scope,
                effective_recency_days=effective_recency_days,
                recency_policy_reason=recency_policy_reason,
                fallback_reason="no_curated_domains_in_category",
                unavailable_categories=unavailable_categories,
                search_session_id=active_search_session_id,
                message=(
                    "No curated domains exist for the selected category yet. "
                    "Choose another category or allow broader web discovery."
                ),
            )
            payload["missing_curated_domains_for"] = normalized_categories
            return payload

        domain_options_public_all = (
            domain_options_full
            if include_full_option_payload
            else _compact_domain_options(domain_options_full)
        )
        paged_domain_options, next_domain_cursor, shown_count, total_count = (
            _paginate_domain_options(
                domain_options_public_all,
                cursor=cursor,
                window_size=bounded_domain_window,
            )
        )

        return _build_step_payload(
            query=cleaned_query,
            phase="awaiting_domain_selection",
            next_action="select_domains",
            coarse_route=coarse_route,
            category_options=category_options,
            domain_options=paged_domain_options,
            selected_categories=normalized_categories,
            selected_domains=[],
            time_scope_options=time_scope_options,
            selected_time_scope=normalized_time_scope,
            effective_recency_days=effective_recency_days,
            recency_policy_reason=recency_policy_reason,
            unavailable_categories=unavailable_categories,
            search_session_id=active_search_session_id,
            cursor=str(_decode_domain_cursor(cursor)),
            next_cursor=next_domain_cursor,
            domain_options_shown=shown_count,
            domain_options_total=total_count,
            message=(
                "Choose 1-4 domains and a time scope, then call mode=search. "
                "Use cursor to page domain options when needed."
            ),
        )

    # mode == "search"
    if not normalized_categories:
        if coarse_route["confidence"] >= COARSE_CONFIDENCE_HIGH:
            inferred_category = coarse_route["category"]
            if int(category_domain_counts.get(inferred_category, 0) or 0) <= 0:
                return await run_broader_discovery(
                    fallback_reason="no_curated_domains_for_inferred_category",
                    selected_categories_for_payload=[inferred_category],
                    message=(
                        "No curated domains exist for the inferred category; broader "
                        "discovery was executed automatically."
                    ),
                )
            normalized_categories = [inferred_category]
        else:
            payload = _build_step_payload(
                query=cleaned_query,
                phase="awaiting_category_selection",
                next_action="select_categories",
                coarse_route=coarse_route,
                category_options=category_options,
                domain_options=[],
                selected_categories=[],
                selected_domains=[],
                time_scope_options=time_scope_options,
                selected_time_scope=normalized_time_scope,
                effective_recency_days=effective_recency_days,
                recency_policy_reason=recency_policy_reason,
                unavailable_categories=unavailable_categories,
                search_session_id=active_search_session_id,
                message="Category selection required before focused search.",
            )
            await emit_focus_status(
                {
                    "action": "web_search",
                    "description": "Focused search: awaiting category selection",
                    "done": True,
                    "plan": {"mode": "focused_local_first"},
                    "planner": {"mode": "focused_local_first"},
                }
            )
            return payload

    if len(normalized_categories) > 2:
        normalized_categories = normalized_categories[:2]

    domain_options_full = _build_domain_options_for_categories(
        normalized_categories,
        include_community=include_community,
        local_first=local_first,
        time_sensitive=plan.time_sensitive,
    )
    domain_allowlist = {item["domain"] for item in domain_options_full}
    domain_options_public_all = (
        domain_options_full
        if include_full_option_payload
        else _compact_domain_options(domain_options_full)
    )
    paged_domain_options, next_domain_cursor, shown_count, total_count = (
        _paginate_domain_options(
            domain_options_public_all,
            cursor=cursor,
            window_size=bounded_domain_window,
        )
    )

    if normalized_domain_ids and not normalized_domains:
        normalized_domains = [
            domain for domain in normalized_domain_ids if domain in domain_allowlist
        ]

    if not domain_options_full and not normalized_domains:
        return await run_broader_discovery(
            fallback_reason="no_curated_domains_in_category",
            selected_categories_for_payload=normalized_categories,
            message=(
                "No curated domains exist for this category; broader discovery "
                "was executed automatically."
            ),
        )

    if not normalized_domains:
        payload = _build_step_payload(
            query=cleaned_query,
            phase="awaiting_domain_selection",
            next_action="select_domains",
            coarse_route=coarse_route,
            category_options=category_options,
            domain_options=paged_domain_options,
            selected_categories=normalized_categories,
            selected_domains=[],
            time_scope_options=time_scope_options,
            selected_time_scope=normalized_time_scope,
            effective_recency_days=effective_recency_days,
            recency_policy_reason=recency_policy_reason,
            search_session_id=active_search_session_id,
            cursor=str(_decode_domain_cursor(cursor)),
            next_cursor=next_domain_cursor,
            domain_options_shown=shown_count,
            domain_options_total=total_count,
            message="Domain selection required before focused search.",
        )
        await emit_focus_status(
            {
                "action": "web_search",
                "description": "Focused search: awaiting domain selection",
                "done": True,
                "plan": {
                    "mode": "focused_local_first",
                    "selected_domains": [],
                },
                "planner": {"mode": "focused_local_first"},
            }
        )
        return payload

    validation_errors: list[str] = []
    if len(normalized_domains) > bounded_max_domains:
        validation_errors.append(f"too_many_domains:max={bounded_max_domains}")
    invalid_domains = [
        domain for domain in normalized_domains if domain not in domain_allowlist
    ]
    if invalid_domains:
        validation_errors.append("invalid_domains")

    if validation_errors:
        payload = _build_step_payload(
            query=cleaned_query,
            phase="awaiting_domain_selection",
            next_action="fix_domain_selection",
            coarse_route=coarse_route,
            category_options=category_options,
            domain_options=paged_domain_options,
            selected_categories=normalized_categories,
            selected_domains=normalized_domains,
            time_scope_options=time_scope_options,
            selected_time_scope=normalized_time_scope,
            effective_recency_days=effective_recency_days,
            recency_policy_reason=recency_policy_reason,
            search_session_id=active_search_session_id,
            cursor=str(_decode_domain_cursor(cursor)),
            next_cursor=next_domain_cursor,
            domain_options_shown=shown_count,
            domain_options_total=total_count,
            errors=validation_errors,
            message="Domain selection is invalid. Pick 1-4 domains from domain_options.",
        )
        payload["invalid_domains"] = invalid_domains
        await emit_focus_status(
            {
                "action": "web_search",
                "description": "Focused search: invalid domain selection",
                "done": True,
                "plan": {
                    "mode": "focused_local_first",
                    "selected_domains": normalized_domains,
                },
                "planner": {
                    "mode": "focused_local_first",
                    "fallback_reason": "invalid_domain_selection",
                },
            }
        )
        return payload

    normalized_domains = normalized_domains[:bounded_max_domains]
    selected_domain_meta = {
        item["domain"]: item
        for item in domain_options_full
        if item["domain"] in set(normalized_domains)
    }
    local_domains = [
        domain
        for domain in normalized_domains
        if selected_domain_meta.get(domain, {}).get("is_local")
    ]

    await emit_focus_status(
        {
            "action": "web_search",
            "description": "Focused search: running targeted queries",
            "query": cleaned_query,
            "done": False,
                "plan": {
                    "mode": "focused_local_first",
                    "topic": plan.topic,
                    "selected_domains": normalized_domains,
                    "selected_time_scope": normalized_time_scope,
                },
                "planner": {
                    "mode": "focused_local_first",
                    "effective_recency_days": effective_recency_days,
                },
                "urls": domain_urls(normalized_domains),
            }
        )

    used_queries: set[str] = set()
    executed_queries: list[str] = []
    local_search_results: list[list[SearchResult]] = []
    fallback_search_results: list[list[SearchResult]] = []

    local_queries: list[str] = []
    phase_a_domains = local_domains if local_first else list(normalized_domains)
    for domain in phase_a_domains[:bounded_max_queries]:
        candidate = _apply_recency_hint(
            build_targeted_query(plan, domain), effective_recency_days
        )
        candidate = sanitize_query(candidate)
        if not candidate or candidate in used_queries:
            continue
        used_queries.add(candidate)
        local_queries.append(candidate)

    for planned_query in local_queries:
        try:
            query_results = await run_in_threadpool(
                search_web,
                request,
                cfg.WEB_SEARCH_ENGINE,
                planned_query,
                user,
            )
            local_search_results.append(query_results)
            executed_queries.append(planned_query)
        except Exception as exc:
            log.warning(
                "Local strong-source query failed for '%s': %s", planned_query, exc
            )

    local_items = _dedupe_items_by_canonical_url(
        _flatten_search_results(local_search_results)
    )
    local_quality = evaluate_signal_quality(local_items, plan)
    local_intent_coverage = evaluate_intent_coverage(
        local_quality["scored_items"], plan
    )

    local_primary_domains = {
        item.get("domain", "")
        for item in local_quality.get("scored_items", [])[:10]
        if item.get("domain", "") in set(local_domains)
        and float(item.get("trust", 0.0) or 0.0) >= 0.75
    }
    local_quality_score = float(local_quality.get("avg_top_score", 0.0) or 0.0)
    required_primary_hits = max(1, min(local_min_primary_hits, len(normalized_domains)))
    local_coverage_complete = bool(
        local_intent_coverage.get("complete", True)
        and local_quality_score >= planner_stop_score
        and len(local_primary_domains) >= required_primary_hits
    )

    brave_fallback_used = False
    fallback_reason: Optional[str] = None

    if local_first and not local_domains:
        fallback_reason = "no_local_domains_in_category"
    elif not local_queries:
        fallback_reason = "no_primary_domains_for_phase_a"
    elif not local_intent_coverage.get("complete", True):
        fallback_reason = "insufficient_intent_coverage"
    elif len(local_primary_domains) < required_primary_hits:
        fallback_reason = "insufficient_local_primary_hits"
    elif local_quality_score < planner_stop_score:
        fallback_reason = "insufficient_local_quality"

    if not local_coverage_complete and brave_fallback_enabled:
        await emit_focus_status(
            {
                "action": "web_search",
                "description": "Focused search did not return enough evidence, trying broader search now",
                "done": False,
                "plan": {
                    "mode": "focused_local_first",
                    "topic": plan.topic,
                    "selected_domains": normalized_domains,
                    "selected_time_scope": normalized_time_scope,
                },
                "planner": {
                    "mode": "focused_local_first",
                    "executed_queries": list(executed_queries),
                    "fallback_reason": fallback_reason,
                    "effective_recency_days": effective_recency_days,
                },
                "urls": domain_urls(normalized_domains),
            }
        )

        fallback_queries: list[str] = []
        local_domain_set = set(local_domains)
        non_local_domains = [
            domain for domain in normalized_domains if domain not in local_domain_set
        ]

        for domain in non_local_domains:
            candidate = _apply_recency_hint(
                build_targeted_query(plan, domain), effective_recency_days
            )
            candidate = sanitize_query(candidate)
            if not candidate or candidate in used_queries:
                continue
            used_queries.add(candidate)
            fallback_queries.append(candidate)
            if len(fallback_queries) >= brave_fallback_max_queries:
                break

        if not fallback_queries:
            fallback_query = _apply_recency_hint(
                plan.base_exact_query, effective_recency_days
            )
            fallback_query = sanitize_query(fallback_query)
            if fallback_query and fallback_query not in used_queries:
                used_queries.add(fallback_query)
                fallback_queries.append(fallback_query)

        fallback_engine = (
            str(getattr(cfg, "WEB_SEARCH_ENGINE", "") or "").strip().lower()
        )
        if not fallback_engine:
            fallback_engine = "duckduckgo"

        for planned_query in fallback_queries[:brave_fallback_max_queries]:
            try:
                if fallback_engine == "brave":
                    await _wait_for_brave_fallback_slot(request, brave_min_interval_ms)
                query_results = await run_in_threadpool(
                    search_web,
                    request,
                    fallback_engine,
                    planned_query,
                    user,
                )
                fallback_search_results.append(query_results)
                executed_queries.append(planned_query)
            except Exception as exc:
                if fallback_reason is None:
                    fallback_reason = "broader_fallback_error"
                log.warning(
                    "Broader fallback query failed for '%s': %s", planned_query, exc
                )

        brave_fallback_used = bool(fallback_search_results)
        if brave_fallback_used and fallback_reason is None:
            fallback_reason = "local_evidence_incomplete"

    combined_items = _dedupe_items_by_canonical_url(
        _flatten_search_results(local_search_results + fallback_search_results)
    )
    combined_quality = evaluate_signal_quality(combined_items, plan)
    combined_intent_coverage = evaluate_intent_coverage(
        combined_quality["scored_items"], plan
    )

    quality_score = float(combined_quality.get("avg_top_score", 0.0) or 0.0)
    trusted_domains = int(combined_quality.get("trusted_unique_domains", 0) or 0)
    coverage_complete = bool(
        combined_intent_coverage.get("complete", True)
        and quality_score >= planner_stop_score
        and trusted_domains >= required_primary_hits
    )

    result_count = max(1, int(cfg.WEB_SEARCH_RESULT_COUNT or 5))
    candidate_scored_items = combined_quality.get("scored_items", [])[
        : result_count * 4
    ]
    evidence_scored_items = candidate_scored_items[: result_count * 2]

    def _materialize_item(item: dict[str, Any]) -> dict[str, Any]:
        link = item.get("link")
        domain = item.get("domain")
        source_type = infer_domain_source_type(domain or "", plan)
        return {
            "title": item.get("title"),
            "link": link,
            "snippet": item.get("snippet"),
            "domain": domain,
            "quality": round(float(item.get("quality", 0.0) or 0.0), 4),
            "trust": round(float(item.get("trust", 0.0) or 0.0), 4),
            "source_type": source_type,
        }

    items = [
        _materialize_item(item) for item in candidate_scored_items if item.get("link")
    ]
    evidence_items = [
        {
            **_materialize_item(item),
        }
        for item in evidence_scored_items
        if item.get("link")
    ]
    citation_items = [
        item
        for item in evidence_items
        if float(item.get("trust", 0.0) or 0.0) >= CITATION_TRUST_MIN
        and str(item.get("source_type", "") or "") != "community"
    ]
    citation_items = _dedupe_items_by_canonical_url(citation_items)

    evidence_adequate = bool(
        coverage_complete and trusted_domains >= 2 and len(citation_items) >= 3
    )
    adequacy_reason = (
        "sufficient_snippet_evidence"
        if evidence_adequate
        else "insufficient_snippet_evidence"
    )
    recommended_urls = [item.get("link") for item in citation_items[:3] if item.get("link")]

    payload = {
        "phase": "completed",
        "next_action": "answer" if evidence_adequate else "read_selected_sources",
        "category_options": category_options if include_full_option_payload else [],
        "domain_options": domain_options_public_all if include_full_option_payload else [],
        "selected_categories": normalized_categories,
        "time_scope_options": time_scope_options,
        "selected_time_scope": normalized_time_scope,
        "effective_recency_days": effective_recency_days,
        "recency_policy_reason": recency_policy_reason,
        "queries": executed_queries,
        "items": items,
        "evidence_items": evidence_items,
        "citation_items": citation_items,
        "candidate_count": len(items),
        "evidence_count": len(evidence_items),
        "citation_count": len(citation_items),
        "selected_domains": normalized_domains,
        "coverage_complete": coverage_complete,
        "quality_score": round(quality_score, 4),
        "local_phase_executed": bool(local_queries),
        "brave_fallback_used": brave_fallback_used,
        "fallback_reason": fallback_reason,
        "topic": plan.topic,
        "local_primary_hits": len(local_primary_domains),
        "trusted_domains": trusted_domains,
        "search_session_id": active_search_session_id,
        "evidence_adequate": evidence_adequate,
        "adequacy_reason": adequacy_reason,
        "recommended_urls": recommended_urls,
    }

    planner_payload: dict[str, Any] = {
        "mode": "focused_local_first",
        "executed_queries": list(executed_queries),
        "final_trusted_domains": trusted_domains,
        "fallback_reason": fallback_reason,
        "candidate_count": len(items),
        "evidence_count": len(evidence_items),
        "citation_count": len(citation_items),
        "selected_time_scope": normalized_time_scope,
        "effective_recency_days": effective_recency_days,
        "recency_policy_reason": recency_policy_reason,
        "show_debug_metrics": debug_tool_journey,
    }
    if debug_tool_journey:
        planner_payload["final_score"] = round(quality_score, 4)

    await emit_focus_status(
        {
            "action": "web_search",
            "description": (
                "Focused search completed with broader fallback"
                if brave_fallback_used
                else "Focused search completed"
            ),
            "done": True,
            "plan": {
                "mode": "focused_local_first",
                "topic": plan.topic,
                "selected_domains": normalized_domains,
                "selected_time_scope": normalized_time_scope,
            },
            "planner": planner_payload,
            "items": citation_items,
            "urls": domain_urls(normalized_domains),
        }
    )

    _invalidate_focused_search_session(active_search_session_id)

    return payload


async def _execute_web_search_with_planner(
    request: Request, form_data: SearchForm, user=None
) -> tuple[list[list[SearchResult]], list[str], dict[str, Any]]:
    if not form_data.plan:
        raise ValueError("Planner payload is missing")

    plan = WebSearchPlan.model_validate(form_data.plan)

    cfg = request.app.state.config
    min_total_queries = max(1, int(cfg.WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES or 3))
    max_total_queries = max(
        min_total_queries, int(cfg.WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES or 10)
    )
    max_targeted_domains = max(
        0, int(cfg.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE or 4)
    )

    if max_targeted_domains and len(plan.selected_domains) > max_targeted_domains:
        plan_payload = (
            plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()  # type: ignore[attr-defined]
        )
        selected_domains = plan.selected_domains[:max_targeted_domains]
        selected_sources = [
            source
            for source in plan.selected_sources
            if source.domain in set(selected_domains)
        ]
        plan_payload["selected_domains"] = selected_domains
        plan_payload["selected_sources"] = selected_sources
        plan = WebSearchPlan.model_validate(plan_payload)

    primary_stop_score = float(cfg.WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE or 0.66)
    primary_stop_trusted_domains = int(
        cfg.WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS or 3
    )
    plateau_floor = float(cfg.WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE or 0.56)
    plateau_delta = float(cfg.WEB_SEARCH_PLANNER_PLATEAU_DELTA or 0.02)
    plateau_streak_limit = int(cfg.WEB_SEARCH_PLANNER_PLATEAU_STREAK or 2)
    planner_mode = (
        str(
            getattr(cfg, "WEB_SEARCH_PLANNER_MODE", plan.mode or "hybrid_rewriter")
        ).strip()
        or "hybrid_rewriter"
    )
    enable_intent_coverage_guard = bool(
        getattr(cfg, "WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD", True)
    )

    allowed_targeted_domain_set = {
        (domain or "").strip().lower() for domain in plan.selected_domains
    }
    pending_queries = (
        [
            candidate
            for candidate in plan.planned_queries
            if (
                candidate.kind != "targeted"
                or not candidate.domain
                or (candidate.domain.strip().lower() in allowed_targeted_domain_set)
            )
        ]
        if plan.planned_queries
        else build_base_planned_queries(plan, targeted_slots=3)
    )
    targeted_in_pending = {
        (candidate.domain or "").strip().lower()
        for candidate in pending_queries
        if candidate.kind == "targeted" and candidate.domain
    }
    remaining_targeted_domains = [
        domain
        for domain in plan.selected_domains
        if domain.strip().lower() not in targeted_in_pending
    ]

    used_query_strings: set[str] = set()
    executed_queries: list[str] = []
    search_results: list[list[SearchResult]] = []
    score_history: list[float] = []
    trusted_history: list[int] = []
    intent_coverage_history: list[dict[str, Any]] = []

    prev_score: Optional[float] = None
    plateau_streak_count = 0
    stop_reason = "budget_exhausted"

    freshness_used = False
    community_used = False
    alternate_general_used = False

    while len(executed_queries) < max_total_queries:
        candidate: Optional[PlannedQuery] = None
        if pending_queries:
            candidate = pending_queries.pop(0)
        else:
            weak_signal = True
            if score_history:
                weak_signal = (
                    score_history[-1] < primary_stop_score
                    or trusted_history[-1] < primary_stop_trusted_domains
                )

            if remaining_targeted_domains:
                domain = remaining_targeted_domains.pop(0)
                candidate = PlannedQuery(
                    kind="targeted",
                    domain=domain,
                    query=build_targeted_query(plan, domain),
                )
            elif plan.time_sensitive and not freshness_used:
                freshness_used = True
                candidate = PlannedQuery(
                    kind="freshness", query=build_freshness_query(plan)
                )
            elif (plan.community_requested or weak_signal) and not community_used:
                community_used = True
                candidate = PlannedQuery(
                    kind="community", query=build_community_query(plan)
                )
            elif not alternate_general_used:
                alternate_general_used = True
                candidate = PlannedQuery(
                    kind="alternate_general", query=build_alternate_general_query(plan)
                )

        if candidate is None:
            stop_reason = "no_more_candidates"
            break

        query = sanitize_query(candidate.query)
        if not query or is_fluff_query(query):
            continue
        if query in used_query_strings:
            continue

        used_query_strings.add(query)
        query_results = await run_in_threadpool(
            search_web,
            request,
            request.app.state.config.WEB_SEARCH_ENGINE,
            query,
            user,
        )
        search_results.append(query_results)
        executed_queries.append(query)

        deduped_items = _dedupe_items_by_canonical_url(
            _flatten_search_results(search_results)
        )
        quality = evaluate_signal_quality(deduped_items, plan)
        avg_score = float(quality["avg_top_score"])
        trusted_domains = int(quality["trusted_unique_domains"])
        intent_coverage = evaluate_intent_coverage(quality["scored_items"], plan)

        score_history.append(avg_score)
        trusted_history.append(trusted_domains)
        intent_coverage_history.append(intent_coverage)

        if prev_score is not None:
            delta = avg_score - prev_score
            if avg_score >= plateau_floor and delta < plateau_delta:
                plateau_streak_count += 1
            else:
                plateau_streak_count = 0
        prev_score = avg_score

        if len(executed_queries) >= min_total_queries:
            if (
                avg_score >= primary_stop_score
                and trusted_domains >= primary_stop_trusted_domains
                and (
                    not enable_intent_coverage_guard
                    or intent_coverage.get("complete", True)
                )
            ):
                stop_reason = "quality_threshold_met"
                break

            if plateau_streak_count >= plateau_streak_limit:
                stop_reason = "quality_plateau"
                break

    planner_metrics = {
        "mode": plan.mode or planner_mode,
        "rewriter_model_used": plan.rewriter_model_used,
        "rewriter_fallback_used": plan.rewriter_fallback_used,
        "rewriter_retry_count": plan.rewriter_retry_count,
        "fallback_reason": plan.fallback_reason,
        "stop_reason": stop_reason,
        "executed_queries": executed_queries,
        "scores": score_history,
        "trusted_domains": trusted_history,
        "final_score": score_history[-1] if score_history else 0.0,
        "final_trusted_domains": trusted_history[-1] if trusted_history else 0,
        "intent_coverage_history": intent_coverage_history,
        "enable_intent_coverage_guard": enable_intent_coverage_guard,
        "min_total_queries": min_total_queries,
        "max_total_queries": max_total_queries,
    }
    return search_results, executed_queries, planner_metrics


@router.post("/process/web/search")
async def process_web_search(
    request: Request, form_data: SearchForm, user=Depends(get_verified_user)
):
    if not request.app.state.config.ENABLE_WEB_SEARCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if user.role != "admin" and not has_permission(
        user.id, "features.web_search", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    urls: list[str] = []
    result_items: list[SearchResult] = []
    search_results: list[list[SearchResult]] = []
    planner_metrics = None
    effective_queries = list(form_data.queries)

    try:
        logging.debug(
            f"trying to web search with {request.app.state.config.WEB_SEARCH_ENGINE, form_data.queries}"
        )

        if form_data.plan and request.app.state.config.ENABLE_WEB_SEARCH_PLANNER:
            search_results, effective_queries, planner_metrics = (
                await _execute_web_search_with_planner(request, form_data, user)
            )
        else:
            # Use semaphore to limit concurrent requests based on WEB_SEARCH_CONCURRENT_REQUESTS
            # 0 or None = unlimited (previous behavior), positive number = limited concurrency
            # Set to 1 for sequential execution (rate-limited APIs like Brave free tier)
            concurrent_limit = request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS

            if concurrent_limit:
                # Limited concurrency with semaphore
                semaphore = asyncio.Semaphore(concurrent_limit)

                async def search_query_with_semaphore(query):
                    async with semaphore:
                        return await run_in_threadpool(
                            search_web,
                            request,
                            request.app.state.config.WEB_SEARCH_ENGINE,
                            query,
                            user,
                        )

                search_tasks = [
                    search_query_with_semaphore(query) for query in form_data.queries
                ]
            else:
                # Unlimited parallel execution (previous behavior)
                search_tasks = [
                    run_in_threadpool(
                        search_web,
                        request,
                        request.app.state.config.WEB_SEARCH_ENGINE,
                        query,
                        user,
                    )
                    for query in form_data.queries
                ]

            search_results = await asyncio.gather(*search_tasks)

        for result in search_results:
            if result:
                for item in result:
                    if item and item.link:
                        result_items.append(item)
                        urls.append(item.link)

        urls = list(dict.fromkeys(urls))
        log.debug(f"urls: {urls}")

    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.WEB_SEARCH_ERROR(e),
        )

    if len(urls) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.DEFAULT("No results found from web search"),
        )

    try:
        if request.app.state.config.BYPASS_WEB_SEARCH_WEB_LOADER:
            search_results = [
                item for result in search_results for item in result if result
            ]

            docs = [
                Document(
                    page_content=result.snippet,
                    metadata={
                        "source": result.link,
                        "title": result.title,
                        "snippet": result.snippet,
                        "link": result.link,
                    },
                )
                for result in search_results
                if hasattr(result, "snippet") and result.snippet is not None
            ]
        else:
            loader = get_web_loader(
                urls,
                verify_ssl=request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
                requests_per_second=request.app.state.config.WEB_LOADER_CONCURRENT_REQUESTS,
                trust_env=request.app.state.config.WEB_SEARCH_TRUST_ENV,
            )
            docs = await loader.aload()

        urls = [
            doc.metadata.get("source") for doc in docs if doc.metadata.get("source")
        ]  # only keep the urls returned by the loader
        result_items = [
            dict(item) for item in result_items if item.link in urls
        ]  # only keep the search results that have been loaded

        if request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL:
            return {
                "status": True,
                "collection_name": None,
                "filenames": urls,
                "items": result_items,
                "queries": effective_queries,
                "planner": planner_metrics,
                "docs": [
                    {
                        "content": doc.page_content,
                        "metadata": doc.metadata,
                    }
                    for doc in docs
                ],
                "loaded_count": len(docs),
            }
        else:
            # Create a single collection for all documents
            collection_name = (
                f"web-search-{calculate_sha256_string('-'.join(effective_queries))}"[
                    :63
                ]
            )

            try:
                await run_in_threadpool(
                    save_docs_to_vector_db,
                    request,
                    docs,
                    collection_name,
                    overwrite=True,
                    user=user,
                )
            except Exception as e:
                log.debug(f"error saving docs: {e}")

            return {
                "status": True,
                "collection_names": [collection_name],
                "items": result_items,
                "filenames": urls,
                "queries": effective_queries,
                "planner": planner_metrics,
                "loaded_count": len(docs),
            }
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


def _validate_collection_access(collection_names: list[str], user) -> None:
    """
    Prevent users from querying collections they don't own.
    Enforces ownership on user-memory-* and file-* collections.
    Admins bypass this check.
    """
    if user.role == "admin":
        return

    for name in collection_names:
        if name.startswith("user-memory-") and name != f"user-memory-{user.id}":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        elif name.startswith("file-"):
            file_id = name[len("file-") :]
            if not has_access_to_file(
                file_id=file_id,
                access_type="read",
                user=user,
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
                )


class QueryDocForm(BaseModel):
    collection_name: str
    query: str
    k: Optional[int] = None
    k_reranker: Optional[int] = None
    r: Optional[float] = None
    hybrid: Optional[bool] = None


@router.post("/query/doc")
async def query_doc_handler(
    request: Request,
    form_data: QueryDocForm,
    user=Depends(get_verified_user),
):
    _validate_collection_access([form_data.collection_name], user)

    try:
        if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH and (
            form_data.hybrid is None or form_data.hybrid
        ):
            collection_results = {}
            collection_results[form_data.collection_name] = VECTOR_DB_CLIENT.get(
                collection_name=form_data.collection_name
            )
            return await query_doc_with_hybrid_search(
                collection_name=form_data.collection_name,
                collection_result=collection_results[form_data.collection_name],
                query=form_data.query,
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
                reranking_function=(
                    (
                        lambda query, documents: request.app.state.RERANKING_FUNCTION(
                            query, documents, user=user
                        )
                    )
                    if request.app.state.RERANKING_FUNCTION
                    else None
                ),
                k_reranker=form_data.k_reranker
                or request.app.state.config.TOP_K_RERANKER,
                r=(
                    form_data.r
                    if form_data.r
                    else request.app.state.config.RELEVANCE_THRESHOLD
                ),
                hybrid_bm25_weight=(
                    form_data.hybrid_bm25_weight
                    if form_data.hybrid_bm25_weight
                    else request.app.state.config.HYBRID_BM25_WEIGHT
                ),
                user=user,
            )
        else:
            query_embedding = await request.app.state.EMBEDDING_FUNCTION(
                form_data.query, prefix=RAG_EMBEDDING_QUERY_PREFIX, user=user
            )
            return query_doc(
                collection_name=form_data.collection_name,
                query_embedding=query_embedding,
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
                user=user,
            )
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


class QueryCollectionsForm(BaseModel):
    collection_names: list[str]
    query: str
    k: Optional[int] = None
    k_reranker: Optional[int] = None
    r: Optional[float] = None
    hybrid: Optional[bool] = None
    hybrid_bm25_weight: Optional[float] = None
    enable_enriched_texts: Optional[bool] = None


@router.post("/query/collection")
async def query_collection_handler(
    request: Request,
    form_data: QueryCollectionsForm,
    user=Depends(get_verified_user),
):
    _validate_collection_access(form_data.collection_names, user)

    try:
        if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH and (
            form_data.hybrid is None or form_data.hybrid
        ):
            return await query_collection_with_hybrid_search(
                collection_names=form_data.collection_names,
                queries=[form_data.query],
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
                reranking_function=(
                    (
                        lambda query, documents: request.app.state.RERANKING_FUNCTION(
                            query, documents, user=user
                        )
                    )
                    if request.app.state.RERANKING_FUNCTION
                    else None
                ),
                k_reranker=form_data.k_reranker
                or request.app.state.config.TOP_K_RERANKER,
                r=(
                    form_data.r
                    if form_data.r
                    else request.app.state.config.RELEVANCE_THRESHOLD
                ),
                hybrid_bm25_weight=(
                    form_data.hybrid_bm25_weight
                    if form_data.hybrid_bm25_weight
                    else request.app.state.config.HYBRID_BM25_WEIGHT
                ),
                enable_enriched_texts=(
                    form_data.enable_enriched_texts
                    if form_data.enable_enriched_texts is not None
                    else request.app.state.config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS
                ),
            )
        else:
            return await query_collection(
                collection_names=form_data.collection_names,
                queries=[form_data.query],
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
            )

    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


####################################
#
# Vector DB operations
#
####################################


class DeleteForm(BaseModel):
    collection_name: str
    file_id: str


@router.post("/delete")
def delete_entries_from_collection(
    form_data: DeleteForm,
    user=Depends(get_admin_user),
    db: Session = Depends(get_session),
):
    try:
        if VECTOR_DB_CLIENT.has_collection(collection_name=form_data.collection_name):
            file = Files.get_file_by_id(form_data.file_id, db=db)
            if not file:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
            hash = file.hash

            VECTOR_DB_CLIENT.delete(
                collection_name=form_data.collection_name,
                metadata={"hash": hash},
            )
            return {"status": True}
        else:
            return {"status": False}
    except Exception as e:
        log.exception(e)
        return {"status": False}


@router.post("/reset/db")
def reset_vector_db(user=Depends(get_admin_user), db: Session = Depends(get_session)):
    VECTOR_DB_CLIENT.reset()
    Knowledges.delete_all_knowledge(db=db)


@router.post("/reset/uploads")
def reset_upload_dir(user=Depends(get_admin_user)) -> bool:
    folder = f"{UPLOAD_DIR}"
    try:
        # Check if the directory exists
        if os.path.exists(folder):
            # Iterate over all the files and directories in the specified directory
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)  # Remove the file or link
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)  # Remove the directory
                except Exception as e:
                    log.exception(f"Failed to delete {file_path}. Reason: {e}")
        else:
            log.warning(f"The directory {folder} does not exist")
    except Exception as e:
        log.exception(f"Failed to process the directory {folder}. Reason: {e}")
    return True


if ENV == "dev":

    @router.get("/ef/{text}")
    async def get_embeddings(request: Request, text: Optional[str] = "Hello World!"):
        return {
            "result": await request.app.state.EMBEDDING_FUNCTION(
                text, prefix=RAG_EMBEDDING_QUERY_PREFIX
            )
        }


class BatchProcessFilesForm(BaseModel):
    files: List[FileModel]
    collection_name: str


class BatchProcessFilesResult(BaseModel):
    file_id: str
    status: str
    error: Optional[str] = None


class BatchProcessFilesResponse(BaseModel):
    results: List[BatchProcessFilesResult]
    errors: List[BatchProcessFilesResult]


@router.post("/process/files/batch")
async def process_files_batch(
    request: Request,
    form_data: BatchProcessFilesForm,
    user=Depends(get_verified_user),
) -> BatchProcessFilesResponse:
    """
    Process a batch of files and save them to the vector database.

    NOTE: We intentionally do NOT use Depends(get_session) here.
    The save_docs_to_vector_db() call makes external embedding API calls which
    can take 5-60+ seconds for batch operations. Database operations after
    embedding (Files.update_file_by_id) manage their own short-lived sessions.
    """

    collection_name = form_data.collection_name

    file_results: List[BatchProcessFilesResult] = []
    file_errors: List[BatchProcessFilesResult] = []
    file_updates: List[FileUpdateForm] = []

    # Prepare all documents first
    all_docs: List[Document] = []

    for file in form_data.files:
        try:
            # Ownership check: verify the requesting user owns the file or is an admin
            db_file = Files.get_file_by_id(file.id)
            if not db_file:
                file_errors.append(
                    BatchProcessFilesResult(
                        file_id=file.id,
                        status="failed",
                        error="File not found",
                    )
                )
                continue
            if db_file.user_id != user.id and user.role != "admin":
                file_errors.append(
                    BatchProcessFilesResult(
                        file_id=file.id,
                        status="failed",
                        error="Permission denied: not file owner",
                    )
                )
                continue

            text_content = file.data.get("content", "")
            docs: List[Document] = [
                Document(
                    page_content=text_content.replace("<br/>", "\n"),
                    metadata={
                        **file.meta,
                        "name": file.filename,
                        "created_by": file.user_id,
                        "file_id": file.id,
                        "source": file.filename,
                    },
                )
            ]

            all_docs.extend(docs)

            file_updates.append(
                FileUpdateForm(
                    hash=calculate_sha256_string(text_content),
                    data={"content": text_content},
                )
            )
            file_results.append(
                BatchProcessFilesResult(file_id=file.id, status="prepared")
            )

        except Exception as e:
            log.error(f"process_files_batch: Error processing file {file.id}: {str(e)}")
            file_errors.append(
                BatchProcessFilesResult(file_id=file.id, status="failed", error=str(e))
            )

    # Save all documents in one batch
    if all_docs:
        try:
            await run_in_threadpool(
                save_docs_to_vector_db,
                request,
                all_docs,
                collection_name,
                add=True,
                user=user,
            )

            # Update all files with collection name
            for file_update, file_result in zip(file_updates, file_results):
                Files.update_file_by_id(id=file_result.file_id, form_data=file_update)
                file_result.status = "completed"

        except Exception as e:
            log.error(
                f"process_files_batch: Error saving documents to vector DB: {str(e)}"
            )
            for file_result in file_results:
                file_result.status = "failed"
                file_errors.append(
                    BatchProcessFilesResult(
                        file_id=file_result.file_id, status="failed", error=str(e)
                    )
                )

    return BatchProcessFilesResponse(results=file_results, errors=file_errors)
