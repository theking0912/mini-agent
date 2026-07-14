"""Reader: 个人知识库阅读器 — 书架 + 文档管理 + 段落级翻译对照"""

from .content_parser import analyze_url
from .reader_service import (
    list_collections,
    create_collection,
    delete_collection,
    rename_collection,
    list_documents,
    get_document,
    delete_document,
    analyze_and_store,
    get_sections_with_content,
    translate_paragraph,
    get_translation_progress,
    merge_to_document,
)

__all__ = [
    "analyze_url",
    "list_collections", "create_collection", "delete_collection", "rename_collection",
    "list_documents", "get_document", "delete_document",
    "analyze_and_store",
    "get_sections_with_content",
    "translate_paragraph",
    "get_translation_progress",
]
