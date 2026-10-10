"""Extractor registry. The pipeline asks for an extractor by name and never
imports a concrete class, so the AI part stays swappable."""
from __future__ import annotations
import os
from typing import Optional
from .base import Extractor, ExtractionResult
from .regex import RegexExtractor
__all__ = ["Extractor", "ExtractionResult", "RegexExtractor", "get_extractor"]
def get_extractor(name: Optional[str] = None, **kwargs) -> Extractor:
    name = (name or os.environ.get("EXTRACTOR", "regex")).strip().lower()
    if name == "regex":
        return RegexExtractor(**kwargs)
    raise ValueError(f"unknown extractor {name!r} (available: regex)")
