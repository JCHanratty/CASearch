"""Pydantic models for data transfer."""

from datetime import datetime
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


class FileInfo(BaseModel):
    """Information about a PDF file."""
    id: int
    path: str
    filename: str
    sha256: str
    mtime: float
    size: int
    status: str
    last_error: Optional[str] = None
    pages: Optional[int] = None
    extracted_at: Optional[str] = None
    created_at: Optional[str] = None
    public_read: bool = False
    employer_name: Optional[str] = None
    union_local: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    region: Optional[str] = None
    short_name: Optional[str] = None


class PageText(BaseModel):
    """Extracted text from a PDF page."""
    page_number: int
    text: str


class SearchResult(BaseModel):
    """A single search result with citation info."""
    file_id: int
    file_path: str
    filename: str
    page_number: int
    snippet: str
    score: float


class Citation(BaseModel):
    """A citation reference from Q&A."""
    file_id: int
    file_path: str
    filename: str
    page_number: int
    cited_text: str


class QAResponse(BaseModel):
    """Response from the Q&A service."""
    answer: str
    citations: list[Citation]
    no_evidence: bool = False
    retrieval_method: Optional[str] = None
    synonyms_used: Optional[dict] = None
    retrieval_diagnostics: Optional[dict] = None
    verification_warnings: Optional[list[str]] = None


class CompareResult(BaseModel):
    """Result from document comparison."""
    doc_a: dict
    doc_b: dict
    matches_a: list[dict] = []
    matches_b: list[dict] = []


class DashboardStats(BaseModel):
    """Statistics for the dashboard."""
    total_files: int
    indexed_files: int
    error_files: int
    total_pages: int
    pending_files: int
    last_scan: Optional[str] = None
