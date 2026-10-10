"""Extractor registry. The pipeline asks for an extractor by name and never
imports a concrete class, so the AI part stays swappable.
Names: "regex" (default, or $EXTRACTOR) and "llm". The llm backend comes from
$LLM_BACKEND. Only "mock" exists; a real local model client is future work.
"""
from __future__ import annotations
import os
from typing import Optional
from .base import Extractor, ExtractionResult
from .regex import RegexExtractor
__all__ = ["Extractor", "ExtractionResult", "RegexExtractor", "get_extractor"]
def _make_llm(data_dir, client=None, **kwargs):
    from .llm import LLMExtractor
    if client is None:
        backend = os.environ.get("LLM_BACKEND", "mock").strip().lower()
        if backend != "mock":
            raise ValueError(f"unknown LLM_BACKEND {backend!r}: only 'mock' is implemented "
                             "(a real local model client is future work)")
        from .mock_llm import oracle_client_from_dataset
        client = oracle_client_from_dataset(data_dir or os.environ.get("LLM_MOCK_DATA", "data/sample"))
    return LLMExtractor(client, **kwargs)
def get_extractor(name: Optional[str] = None, data_dir=None, **kwargs) -> Extractor:
    name = (name or os.environ.get("EXTRACTOR", "regex")).strip().lower()
    if name == "regex":
        return RegexExtractor(**kwargs)
    if name == "llm":
        return _make_llm(data_dir, **kwargs)
    raise ValueError(f"unknown extractor {name!r} (available: regex, llm)")
