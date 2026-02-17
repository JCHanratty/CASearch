"""Semantic search service using sentence-transformers and ChromaDB.

This module provides dense vector embeddings for semantic similarity search,
replacing the TF-IDF approach with transformer-based embeddings.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

from app.db import get_db
from app.models import SearchResult
from app.settings import settings

# Lazy imports to avoid slow startup
_chroma_client = None
_embedding_model = None
_collection = None

# Model configuration
# BGE-base provides better retrieval quality for legal/contract documents (768 dimensions)
# Supports query/passage prefixes for asymmetric retrieval
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
COLLECTION_NAME = "contract_chunks_v2"  # New collection for new embedding model

logger = logging.getLogger(__name__)


@dataclass
class SemanticSearchResult:
    """Result from semantic similarity search."""
    file_id: int
    chunk_id: Optional[int]
    page_number: int
    filename: str
    file_path: str
    text: str
    heading: Optional[str]
    score: float  # Distance (lower is better) or similarity (higher is better)


def _get_chroma_path() -> Path:
    """Get path to the ChromaDB storage directory."""
    chroma_dir = settings.INDEX_DIR / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chroma_dir


def _get_embedding_model():
    """Lazily load the sentence-transformers model."""
    global _embedding_model

    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
            _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
            logger.info("Embedding model loaded successfully")
        except Exception as e:
            logger.error(f"Error loading embedding model: {e}")
            raise

    return _embedding_model


def _get_chroma_client():
    """Lazily initialize the ChromaDB client."""
    global _chroma_client

    if _chroma_client is None:
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            chroma_path = _get_chroma_path()
            logger.info(f"Initializing ChromaDB at: {chroma_path}")

            _chroma_client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                )
            )
            logger.info("ChromaDB client initialized")
        except Exception as e:
            logger.error(f"Error initializing ChromaDB: {e}")
            raise

    return _chroma_client


def _get_collection():
    """Get or create the ChromaDB collection."""
    global _collection

    if _collection is None:
        client = _get_chroma_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}  # Use cosine similarity
        )

    return _collection


def embed_text(text: str, is_query: bool = False) -> list[float]:
    """
    Convert text to a dense vector embedding.

    Args:
        text: Text to embed
        is_query: If True, add query prefix for asymmetric retrieval (BGE model)

    Returns:
        List of floats representing the embedding vector
    """
    model = _get_embedding_model()

    # BGE models perform better with query/passage prefixes
    if "bge" in EMBEDDING_MODEL.lower():
        prefix = "query: " if is_query else "passage: "
        text = prefix + text

    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def embed_texts_batch(texts: list[str], is_query: bool = False) -> list[list[float]]:
    """
    Embed multiple texts in a batch for efficiency.

    Args:
        texts: List of texts to embed
        is_query: If True, add query prefix for asymmetric retrieval

    Returns:
        List of embedding vectors
    """
    if not texts:
        return []

    # BGE models perform better with query/passage prefixes
    if "bge" in EMBEDDING_MODEL.lower():
        prefix = "query: " if is_query else "passage: "
        texts = [prefix + t for t in texts]

    model = _get_embedding_model()
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return [e.tolist() for e in embeddings]


def add_chunk_embedding(
    chunk_id: int,
    file_id: int,
    text: str,
    heading: Optional[str] = None,
    page_start: int = 1,
    page_end: int = 1,
    filename: str = "",
    file_path: str = "",
) -> bool:
    """
    Add a chunk embedding to the vector store.

    Args:
        chunk_id: Unique identifier for the chunk
        file_id: ID of the source file
        text: Text content to embed
        heading: Optional section heading
        page_start: Starting page number
        page_end: Ending page number
        filename: Name of the source file
        file_path: Path to the source file

    Returns:
        True if added successfully
    """
    try:
        collection = _get_collection()
        embedding = embed_text(text)

        # Create unique ID
        doc_id = f"chunk_{file_id}_{chunk_id}"

        # Metadata for filtering and display
        metadata = {
            "file_id": file_id,
            "chunk_id": chunk_id,
            "page_start": page_start,
            "page_end": page_end,
            "filename": filename,
            "file_path": file_path,
        }
        if heading:
            metadata["heading"] = heading

        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text[:1000]],  # Store truncated text for retrieval
            metadatas=[metadata],
        )
        return True

    except Exception as e:
        logger.error(f"Error adding chunk embedding: {e}")
        return False


def add_page_embedding(
    page_id: int,
    file_id: int,
    page_number: int,
    text: str,
    filename: str = "",
    file_path: str = "",
) -> bool:
    """
    Add a page embedding to the vector store.

    Args:
        page_id: Unique identifier for the page
        file_id: ID of the source file
        page_number: Page number (1-indexed)
        text: Text content to embed
        filename: Name of the source file
        file_path: Path to the source file

    Returns:
        True if added successfully
    """
    try:
        collection = _get_collection()
        embedding = embed_text(text)

        # Create unique ID
        doc_id = f"page_{file_id}_{page_id}"

        metadata = {
            "file_id": file_id,
            "page_id": page_id,
            "page_number": page_number,
            "filename": filename,
            "file_path": file_path,
            "is_page": True,  # Distinguish from chunks
        }

        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text[:1000]],
            metadatas=[metadata],
        )
        return True

    except Exception as e:
        logger.error(f"Error adding page embedding: {e}")
        return False


def search_semantic(
    query: str,
    limit: int = 10,
    file_id: Optional[int] = None,
    chunks_only: bool = False,
) -> list[SemanticSearchResult]:
    """
    Search for semantically similar content.

    Args:
        query: Search query text
        limit: Maximum number of results
        file_id: Optional file ID to restrict search
        chunks_only: If True, only search chunks (not pages)

    Returns:
        List of SemanticSearchResult objects sorted by similarity
    """
    try:
        collection = _get_collection()

        # Check if collection is empty
        if collection.count() == 0:
            return []

        # Embed the query (with query prefix for BGE)
        query_embedding = embed_text(query, is_query=True)

        # Build filter
        where_filter = None
        if file_id is not None:
            where_filter = {"file_id": file_id}

        # Query ChromaDB
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        # Convert to SemanticSearchResult objects
        search_results = []

        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 0
                document = results["documents"][0][i] if results["documents"] else ""

                # Skip pages if chunks_only
                if chunks_only and metadata.get("is_page"):
                    continue

                # Convert distance to similarity (cosine distance to similarity)
                # ChromaDB returns distance, lower is better
                # Similarity = 1 - distance for cosine
                similarity = max(0, 1 - distance)

                # Determine page number
                page_num = metadata.get("page_number") or metadata.get("page_start", 1)

                search_results.append(SemanticSearchResult(
                    file_id=metadata.get("file_id", 0),
                    chunk_id=metadata.get("chunk_id"),
                    page_number=page_num,
                    filename=metadata.get("filename", ""),
                    file_path=metadata.get("file_path", ""),
                    text=document,
                    heading=metadata.get("heading"),
                    score=similarity,
                ))

        return search_results

    except Exception as e:
        logger.error(f"Error in semantic search: {e}")
        return []


def delete_file_embeddings(file_id: int) -> bool:
    """
    Delete all embeddings for a specific file.

    Args:
        file_id: ID of the file to delete embeddings for

    Returns:
        True if deleted successfully
    """
    try:
        collection = _get_collection()

        # ChromaDB requires IDs for deletion, so we need to find them first
        # Query for all documents with this file_id
        results = collection.get(
            where={"file_id": file_id},
            include=[],
        )

        if results and results["ids"]:
            collection.delete(ids=results["ids"])
            logger.info(f"Deleted {len(results['ids'])} embeddings for file {file_id}")

        return True

    except Exception as e:
        logger.error(f"Error deleting file embeddings: {e}")
        return False


def rebuild_semantic_index(
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    use_chunks: bool = True,
) -> dict:
    """
    Rebuild the entire semantic index from database content.

    Args:
        progress_callback: Optional callback(current, total, message)
        use_chunks: If True, index chunks; if False, index pages

    Returns:
        Dict with rebuild statistics
    """
    try:
        # Clear existing collection
        client = _get_chroma_client()
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass  # Collection might not exist

        global _collection
        _collection = None  # Force recreation
        collection = _get_collection()

        with get_db() as conn:
            if use_chunks:
                # Index semantic chunks
                rows = conn.execute("""
                    SELECT c.id as chunk_id, c.file_id, c.text, c.heading,
                           c.page_start, c.page_end, f.filename, f.path
                    FROM document_chunks c
                    JOIN files f ON c.file_id = f.id
                    WHERE f.status = 'indexed' AND c.text IS NOT NULL AND length(c.text) > 0
                    ORDER BY f.id, c.chunk_number
                """).fetchall()
            else:
                # Index pages
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
                "items_indexed": 0,
                "message": "No content found to index"
            }

        total = len(rows)
        if progress_callback:
            progress_callback(0, total, "Starting semantic indexing...")

        # Process in batches for efficiency
        batch_size = 32
        indexed_count = 0

        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch = rows[batch_start:batch_end]

            # Prepare batch data
            ids = []
            texts = []
            metadatas = []

            for row in batch:
                if use_chunks:
                    doc_id = f"chunk_{row['file_id']}_{row['chunk_id']}"
                    metadata = {
                        "file_id": row["file_id"],
                        "chunk_id": row["chunk_id"],
                        "page_start": row["page_start"],
                        "page_end": row["page_end"],
                        "filename": row["filename"],
                        "file_path": row["path"],
                    }
                    if row["heading"]:
                        metadata["heading"] = row["heading"]
                else:
                    doc_id = f"page_{row['file_id']}_{row['page_id']}"
                    metadata = {
                        "file_id": row["file_id"],
                        "page_id": row["page_id"],
                        "page_number": row["page_number"],
                        "filename": row["filename"],
                        "file_path": row["path"],
                        "is_page": True,
                    }

                ids.append(doc_id)
                texts.append(row["text"][:2000])  # Limit text length for embedding
                metadatas.append(metadata)

            # Batch embed
            embeddings = embed_texts_batch(texts)

            # Add to collection
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=[t[:1000] for t in texts],  # Store truncated
                metadatas=metadatas,
            )

            indexed_count += len(batch)

            if progress_callback:
                progress_callback(
                    indexed_count, total,
                    f"Indexed {indexed_count}/{total} items..."
                )

        return {
            "success": True,
            "items_indexed": indexed_count,
            "index_type": "chunks" if use_chunks else "pages",
            "message": f"Successfully indexed {indexed_count} items"
        }

    except Exception as e:
        logger.error(f"Error rebuilding semantic index: {e}")
        return {
            "success": False,
            "items_indexed": 0,
            "message": f"Error: {str(e)}"
        }


def get_semantic_index_stats() -> dict:
    """
    Get statistics about the semantic index.

    Returns:
        Dict with index statistics
    """
    try:
        collection = _get_collection()

        stats = {
            "index_exists": True,
            "items_indexed": collection.count(),
            "embedding_model": EMBEDDING_MODEL,
            "collection_name": COLLECTION_NAME,
        }

        # Get storage size
        chroma_path = _get_chroma_path()
        if chroma_path.exists():
            total_size = sum(f.stat().st_size for f in chroma_path.rglob("*") if f.is_file())
            stats["index_size_mb"] = round(total_size / (1024 * 1024), 2)

        return stats

    except Exception as e:
        return {
            "index_exists": False,
            "items_indexed": 0,
            "error": str(e),
        }


def semantic_to_search_result(result: SemanticSearchResult) -> SearchResult:
    """
    Convert a SemanticSearchResult to a SearchResult for compatibility.

    Args:
        result: SemanticSearchResult to convert

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


# Cross-encoder re-ranker (lazy loaded)
_reranker = None
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def _get_reranker():
    """Lazily load the cross-encoder re-ranking model."""
    global _reranker

    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            logger.info(f"Loading re-ranker model: {RERANKER_MODEL}")
            _reranker = CrossEncoder(RERANKER_MODEL)
            logger.info("Re-ranker model loaded successfully")
        except Exception as e:
            logger.error(f"Error loading re-ranker model: {e}")
            raise

    return _reranker


def search_semantic_with_rerank(
    query: str,
    limit: int = 10,
    file_id: Optional[int] = None,
    initial_limit: int = 50,
) -> list[SemanticSearchResult]:
    """
    Two-stage retrieval: fast bi-encoder retrieval then accurate cross-encoder re-ranking.

    Args:
        query: Search query text
        limit: Maximum number of final results
        file_id: Optional file ID to restrict search
        initial_limit: Number of candidates to retrieve before re-ranking

    Returns:
        List of SemanticSearchResult objects sorted by re-ranked score
    """
    # Stage 1: Fast bi-encoder retrieval
    candidates = search_semantic(query, limit=initial_limit, file_id=file_id)

    if not candidates or len(candidates) <= limit:
        return candidates[:limit]

    try:
        # Stage 2: Cross-encoder re-ranking
        reranker = _get_reranker()

        # Create query-document pairs for re-ranking
        pairs = [(query, c.text) for c in candidates]
        scores = reranker.predict(pairs)

        # Re-sort by cross-encoder scores
        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        # Update scores and return top results
        results = []
        for candidate, score in scored[:limit]:
            candidate.score = float(score)
            results.append(candidate)

        return results

    except Exception as e:
        logger.error(f"Error in re-ranking, falling back to bi-encoder results: {e}")
        return candidates[:limit]
