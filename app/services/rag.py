"""RAG (Retrieval Augmented Generation) service with TF-IDF vector store."""

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.db import get_db
from app.models import SearchResult
from app.settings import settings


@dataclass
class VectorSearchResult:
    """Result from vector similarity search."""
    file_id: int
    page_id: int
    page_number: int
    filename: str
    file_path: str
    text: str
    score: float  # Cosine similarity score (0-1, higher is better)


# Global vectorizer and index storage
_vectorizer: Optional[TfidfVectorizer] = None
_embeddings_matrix: Optional[np.ndarray] = None
_page_metadata: list[dict] = []  # Maps matrix row index to page info


def _get_index_path() -> Path:
    """Get path to the vector index file."""
    index_dir = settings.INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)
    return index_dir / "tfidf_index.pkl"


def _load_index() -> bool:
    """
    Load the TF-IDF index from disk.

    Returns:
        True if index was loaded successfully, False otherwise
    """
    global _vectorizer, _embeddings_matrix, _page_metadata

    index_path = _get_index_path()
    if not index_path.exists():
        return False

    try:
        with open(index_path, "rb") as f:
            data = pickle.load(f)
            _vectorizer = data["vectorizer"]
            _embeddings_matrix = data["embeddings"]
            _page_metadata = data["metadata"]
        return True
    except Exception as e:
        print(f"Error loading vector index: {e}")
        return False


def _save_index() -> bool:
    """
    Save the TF-IDF index to disk.

    Returns:
        True if saved successfully, False otherwise
    """
    global _vectorizer, _embeddings_matrix, _page_metadata

    if _vectorizer is None or _embeddings_matrix is None:
        return False

    index_path = _get_index_path()
    try:
        with open(index_path, "wb") as f:
            pickle.dump({
                "vectorizer": _vectorizer,
                "embeddings": _embeddings_matrix,
                "metadata": _page_metadata,
            }, f)
        return True
    except Exception as e:
        print(f"Error saving vector index: {e}")
        return False


def embed_text(text: str) -> list[float]:
    """
    Convert text to a TF-IDF vector embedding.

    Args:
        text: Text to embed

    Returns:
        List of floats representing the TF-IDF vector
    """
    global _vectorizer

    if _vectorizer is None:
        # Try to load from disk
        if not _load_index():
            # No index exists, return empty vector
            return []

    if _vectorizer is None:
        return []

    try:
        # Transform the text using the fitted vectorizer
        vector = _vectorizer.transform([text])
        return vector.toarray()[0].tolist()
    except Exception as e:
        print(f"Error embedding text: {e}")
        return []


def add_page_embedding(file_id: int, page_id: int, text: str) -> bool:
    """
    Add a page embedding to the vector store.

    Note: For TF-IDF, we need to rebuild the entire index when adding new documents
    because the vocabulary and IDF weights change. This function stores the text
    in the database for later batch indexing.

    Args:
        file_id: ID of the file
        page_id: ID of the page in pdf_pages table
        text: Text content to embed

    Returns:
        True if stored successfully
    """
    with get_db() as conn:
        # Store the text for later embedding (or update if exists)
        try:
            conn.execute("""
                INSERT INTO page_embeddings (page_id, text_hash, embedding_json)
                VALUES (?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    text_hash = excluded.text_hash,
                    embedding_json = excluded.embedding_json
            """, (page_id, hash(text), ""))  # Empty embedding - will be filled on rebuild
            return True
        except Exception as e:
            print(f"Error storing page for embedding: {e}")
            return False


def search_similar(query: str, limit: int = 10, file_id: Optional[int] = None) -> list[VectorSearchResult]:
    """
    Search for pages similar to the query using vector similarity.

    Args:
        query: Search query text
        limit: Maximum number of results
        file_id: Optional file ID to restrict search to

    Returns:
        List of VectorSearchResult objects sorted by similarity (highest first)
    """
    global _vectorizer, _embeddings_matrix, _page_metadata

    # Ensure index is loaded
    if _vectorizer is None or _embeddings_matrix is None:
        if not _load_index():
            return []

    if _vectorizer is None or _embeddings_matrix is None or len(_page_metadata) == 0:
        return []

    try:
        # Embed the query
        query_vector = _vectorizer.transform([query])

        # Calculate cosine similarity with all documents
        similarities = cosine_similarity(query_vector, _embeddings_matrix)[0]

        # Get top results
        results = []

        # Create index-score pairs and sort by score descending
        scored_indices = [(i, similarities[i]) for i in range(len(similarities))]
        scored_indices.sort(key=lambda x: x[1], reverse=True)

        for idx, score in scored_indices:
            if score <= 0:
                continue  # Skip zero/negative similarity

            meta = _page_metadata[idx]

            # Filter by file_id if specified
            if file_id is not None and meta["file_id"] != file_id:
                continue

            results.append(VectorSearchResult(
                file_id=meta["file_id"],
                page_id=meta["page_id"],
                page_number=meta["page_number"],
                filename=meta["filename"],
                file_path=meta["file_path"],
                text=meta["text"][:200],  # Snippet
                score=float(score),
            ))

            if len(results) >= limit:
                break

        return results

    except Exception as e:
        print(f"Error in vector search: {e}")
        return []


def rebuild_vector_index(progress_callback=None) -> dict:
    """
    Rebuild the entire TF-IDF vector index from all indexed pages.

    Args:
        progress_callback: Optional callback function(current, total, message)

    Returns:
        Dict with rebuild statistics
    """
    global _vectorizer, _embeddings_matrix, _page_metadata

    with get_db() as conn:
        # Get all indexed pages with their text
        rows = conn.execute("""
            SELECT p.id as page_id, p.file_id, p.page_number, p.text,
                   f.filename, f.path
            FROM pdf_pages p
            JOIN files f ON p.file_id = f.id
            WHERE f.status = 'indexed' AND p.text IS NOT NULL AND length(p.text) > 0
            ORDER BY f.id, p.page_number
        """).fetchall()

        if not rows:
            return {
                "success": False,
                "pages_indexed": 0,
                "message": "No indexed pages found"
            }

        total = len(rows)
        if progress_callback:
            progress_callback(0, total, "Starting TF-IDF vectorization...")

        # Extract texts and metadata
        texts = []
        _page_metadata = []

        for i, row in enumerate(rows):
            texts.append(row["text"])
            _page_metadata.append({
                "page_id": row["page_id"],
                "file_id": row["file_id"],
                "page_number": row["page_number"],
                "filename": row["filename"],
                "file_path": row["path"],
                "text": row["text"],
            })

            if progress_callback and (i + 1) % 100 == 0:
                progress_callback(i + 1, total, f"Preparing texts: {i + 1}/{total}")

        if progress_callback:
            progress_callback(total, total, "Building TF-IDF matrix...")

        # Create and fit the TF-IDF vectorizer
        _vectorizer = TfidfVectorizer(
            max_features=10000,  # Limit vocabulary size for performance
            stop_words="english",
            ngram_range=(1, 2),  # Use unigrams and bigrams
            min_df=1,  # Minimum document frequency
            max_df=0.95,  # Maximum document frequency (ignore too common terms)
            sublinear_tf=True,  # Apply sublinear TF scaling
        )

        try:
            _embeddings_matrix = _vectorizer.fit_transform(texts)

            if progress_callback:
                progress_callback(total, total, "Saving index to disk...")

            # Save the index
            _save_index()

            # Update page_embeddings table to mark pages as indexed
            for meta in _page_metadata:
                conn.execute("""
                    INSERT INTO page_embeddings (page_id, text_hash, embedding_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(page_id) DO UPDATE SET
                        text_hash = excluded.text_hash,
                        embedding_json = excluded.embedding_json
                """, (meta["page_id"], hash(meta["text"]), "indexed"))

            return {
                "success": True,
                "pages_indexed": total,
                "vocabulary_size": len(_vectorizer.vocabulary_),
                "message": f"Successfully indexed {total} pages"
            }

        except Exception as e:
            return {
                "success": False,
                "pages_indexed": 0,
                "message": f"Error building index: {str(e)}"
            }


def get_vector_index_stats() -> dict:
    """
    Get statistics about the vector index.

    Returns:
        Dict with index statistics
    """
    global _vectorizer, _embeddings_matrix, _page_metadata

    # Ensure index is loaded
    if _vectorizer is None:
        _load_index()

    index_path = _get_index_path()

    stats = {
        "index_exists": index_path.exists(),
        "index_loaded": _vectorizer is not None,
        "pages_indexed": len(_page_metadata) if _page_metadata else 0,
        "vocabulary_size": len(_vectorizer.vocabulary_) if _vectorizer else 0,
    }

    if index_path.exists():
        stats["index_size_mb"] = round(index_path.stat().st_size / (1024 * 1024), 2)

    return stats


def vector_search_to_search_result(result: VectorSearchResult) -> SearchResult:
    """
    Convert a VectorSearchResult to a SearchResult for compatibility.

    Args:
        result: VectorSearchResult to convert

    Returns:
        SearchResult object
    """
    return SearchResult(
        file_id=result.file_id,
        file_path=result.file_path,
        filename=result.filename,
        page_number=result.page_number,
        snippet=result.text,
        score=result.score,
    )
