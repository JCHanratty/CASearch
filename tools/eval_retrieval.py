"""Retrieval evaluation framework for the contract dashboard.

Loads golden Q&A pairs and evaluates the search pipeline (FTS5 page search,
FTS5 chunk search, and optionally semantic search) using standard IR metrics:
Recall@K and Mean Reciprocal Rank (MRR).

Usage:
    python tools/eval_retrieval.py                  # run evaluation
    python tools/eval_retrieval.py --tune           # grid-search RRF weights
    python tools/eval_retrieval.py --limit 3        # only evaluate top-3
    python tools/eval_retrieval.py --semantic        # include semantic search
    python tools/eval_retrieval.py -v               # verbose per-question output
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so app modules can be imported
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Golden QA file path
# ---------------------------------------------------------------------------
GOLDEN_QA_PATH = PROJECT_ROOT / "tools" / "golden_qa_pairs.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class GoldenPair:
    """A single golden Q&A evaluation pair."""
    question: str
    expected_keywords: list[str]
    expected_topic: str
    difficulty: str


@dataclass
class RetrievalHit:
    """Unified representation of a search hit for evaluation."""
    text: str          # snippet / document text
    source: str        # "pages", "chunks", or "semantic"
    score: float = 0.0
    page: int = 0
    heading: Optional[str] = None


@dataclass
class QuestionResult:
    """Evaluation result for a single question."""
    question: str
    topic: str
    difficulty: str
    hits: list[RetrievalHit] = field(default_factory=list)
    recall_at_1: float = 0.0
    recall_at_3: float = 0.0
    recall_at_5: float = 0.0
    reciprocal_rank: float = 0.0
    first_hit_rank: int = 0  # 0 means no hit found


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_golden_pairs(path: Path = GOLDEN_QA_PATH) -> list[GoldenPair]:
    """Load golden Q&A pairs from JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        GoldenPair(
            question=entry["question"],
            expected_keywords=entry["expected_keywords"],
            expected_topic=entry["expected_topic"],
            difficulty=entry["difficulty"],
        )
        for entry in data
    ]


def text_contains_keyword(text: str, keyword: str) -> bool:
    """Check if *text* contains *keyword* (case-insensitive, word-boundary aware).

    For multi-word keywords (e.g. "sick leave") a simple substring check is
    used.  For single-word keywords a regex word-boundary match is used so
    that "pay" does not accidentally match inside "payment" unless the keyword
    is genuinely a prefix.
    """
    text_lower = text.lower()
    kw_lower = keyword.lower()

    # Multi-word: plain substring
    if " " in kw_lower:
        return kw_lower in text_lower

    # Single word: word-boundary or prefix match
    pattern = rf"\b{re.escape(kw_lower)}"
    return bool(re.search(pattern, text_lower))


def any_keyword_in_text(text: str, keywords: list[str]) -> bool:
    """Return True if *any* expected keyword appears in *text*."""
    return any(text_contains_keyword(text, kw) for kw in keywords)


def compute_recall_at_k(hits: list[RetrievalHit], keywords: list[str], k: int) -> float:
    """Recall@K: did at least one of the top-K results contain an expected keyword?

    Returns 1.0 if yes, 0.0 if no.
    """
    for hit in hits[:k]:
        combined = hit.text
        if hit.heading:
            combined = hit.heading + " " + combined
        if any_keyword_in_text(combined, keywords):
            return 1.0
    return 0.0


def compute_reciprocal_rank(hits: list[RetrievalHit], keywords: list[str]) -> tuple[float, int]:
    """MRR helper: return (1/rank, rank) for the first relevant hit, or (0, 0)."""
    for i, hit in enumerate(hits, start=1):
        combined = hit.text
        if hit.heading:
            combined = hit.heading + " " + combined
        if any_keyword_in_text(combined, keywords):
            return 1.0 / i, i
    return 0.0, 0


# ---------------------------------------------------------------------------
# Search pipeline wrappers
# ---------------------------------------------------------------------------
def run_page_search(query: str, limit: int) -> list[RetrievalHit]:
    """Run FTS5 page-level search and return unified hits."""
    from app.services.search import search_pages

    results = search_pages(query, limit=limit, mode="and", fallback_to_or=True)
    return [
        RetrievalHit(
            text=r.snippet,
            source="pages",
            score=r.score,
            page=r.page_number,
        )
        for r in results
    ]


def run_chunk_search(query: str, limit: int) -> list[RetrievalHit]:
    """Run FTS5 chunk-level search and return unified hits."""
    from app.services.search import search_chunks

    results = search_chunks(query, limit=limit, mode="and", fallback_to_or=True)
    return [
        RetrievalHit(
            text=r.snippet,
            source="chunks",
            score=r.score,
            page=r.page_start,
            heading=r.heading,
        )
        for r in results
    ]


def run_semantic_search(query: str, limit: int) -> list[RetrievalHit]:
    """Run ChromaDB semantic search and return unified hits."""
    try:
        from app.services.semantic_search import search_semantic

        results = search_semantic(query, limit=limit)
        return [
            RetrievalHit(
                text=r.text,
                source="semantic",
                score=r.score,
                page=r.page_number,
                heading=r.heading,
            )
            for r in results
        ]
    except Exception as exc:
        print(f"  [WARN] Semantic search unavailable: {exc}")
        return []


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------
def fuse_rrf(
    ranked_lists: list[list[RetrievalHit]],
    weights: Optional[list[float]] = None,
    k: int = 60,
    limit: int = 10,
) -> list[RetrievalHit]:
    """Reciprocal Rank Fusion across multiple ranked result lists.

    Args:
        ranked_lists: List of ranked hit lists from different retrievers.
        weights: Per-list weight multipliers (default 1.0 each).
        k: RRF constant (default 60).
        limit: How many fused results to return.

    Returns:
        Fused and re-ranked list of RetrievalHit.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)

    # Score by (text snippet hash) to deduplicate
    scores: dict[str, float] = {}
    hit_map: dict[str, RetrievalHit] = {}

    for lst, weight in zip(ranked_lists, weights):
        for rank, hit in enumerate(lst, start=1):
            # Use a content fingerprint as dedup key
            key = f"{hit.source}:{hit.page}:{hit.text[:80]}"
            rrf_score = weight / (k + rank)
            scores[key] = scores.get(key, 0.0) + rrf_score
            if key not in hit_map:
                hit_map[key] = hit

    # Sort by fused score descending
    sorted_keys = sorted(scores, key=scores.__getitem__, reverse=True)

    fused = []
    for key in sorted_keys[:limit]:
        hit = hit_map[key]
        hit.score = scores[key]
        fused.append(hit)

    return fused


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def evaluate(
    pairs: list[GoldenPair],
    use_semantic: bool = False,
    rrf_weights: Optional[list[float]] = None,
    fetch_limit: int = 10,
    verbose: bool = False,
) -> list[QuestionResult]:
    """Run the full evaluation over all golden pairs.

    Args:
        pairs: Golden Q&A pairs to evaluate.
        use_semantic: Whether to include ChromaDB semantic search.
        rrf_weights: Weights for [pages, chunks, semantic] in RRF fusion.
                     If None, defaults to [1.0, 1.2] (or [1.0, 1.2, 1.0] with semantic).
        fetch_limit: Number of results to fetch per retriever.
        verbose: Print per-question details.

    Returns:
        List of QuestionResult, one per golden pair.
    """
    if rrf_weights is None:
        rrf_weights = [1.0, 1.2, 1.0] if use_semantic else [1.0, 1.2]

    # Optionally expand queries via the synonym service
    try:
        from app.services.synonyms import expand_query
        has_synonyms = True
    except Exception:
        has_synonyms = False

    results: list[QuestionResult] = []

    for idx, pair in enumerate(pairs, start=1):
        # Determine all query variants (original + synonyms)
        queries = [pair.question]
        if has_synonyms:
            try:
                expanded = expand_query(pair.question)
                for variant in expanded:
                    if variant not in queries:
                        queries.append(variant)
            except Exception:
                pass

        # Gather results across query variants, keeping best per retriever
        all_page_hits: list[RetrievalHit] = []
        all_chunk_hits: list[RetrievalHit] = []
        all_semantic_hits: list[RetrievalHit] = []

        for q in queries:
            all_page_hits.extend(run_page_search(q, limit=fetch_limit))
            all_chunk_hits.extend(run_chunk_search(q, limit=fetch_limit))
            if use_semantic:
                all_semantic_hits.extend(run_semantic_search(q, limit=fetch_limit))

        # Deduplicate within each retriever (keep first occurrence = best rank)
        def dedup(hits: list[RetrievalHit]) -> list[RetrievalHit]:
            seen: set[str] = set()
            out: list[RetrievalHit] = []
            for h in hits:
                key = f"{h.page}:{h.text[:80]}"
                if key not in seen:
                    seen.add(key)
                    out.append(h)
            return out

        all_page_hits = dedup(all_page_hits)
        all_chunk_hits = dedup(all_chunk_hits)
        all_semantic_hits = dedup(all_semantic_hits)

        # Fuse via RRF
        lists_to_fuse = [all_page_hits, all_chunk_hits]
        weights_to_use = list(rrf_weights[:2])
        if use_semantic and all_semantic_hits:
            lists_to_fuse.append(all_semantic_hits)
            weights_to_use.append(rrf_weights[2] if len(rrf_weights) > 2 else 1.0)

        fused = fuse_rrf(lists_to_fuse, weights=weights_to_use, limit=fetch_limit)

        # Compute metrics
        r1 = compute_recall_at_k(fused, pair.expected_keywords, 1)
        r3 = compute_recall_at_k(fused, pair.expected_keywords, 3)
        r5 = compute_recall_at_k(fused, pair.expected_keywords, 5)
        rr, first_rank = compute_reciprocal_rank(fused, pair.expected_keywords)

        qr = QuestionResult(
            question=pair.question,
            topic=pair.expected_topic,
            difficulty=pair.difficulty,
            hits=fused,
            recall_at_1=r1,
            recall_at_3=r3,
            recall_at_5=r5,
            reciprocal_rank=rr,
            first_hit_rank=first_rank,
        )
        results.append(qr)

        if verbose:
            status = "HIT" if r1 > 0 else ("hit@3" if r3 > 0 else ("hit@5" if r5 > 0 else "MISS"))
            print(f"  [{idx:2d}/{len(pairs)}] [{status:5s}] {pair.question}")
            if fused:
                snippet_preview = fused[0].text[:100].replace("\n", " ")
                print(f"           Top result (p{fused[0].page}): {snippet_preview}...")
            if first_rank == 0:
                print(f"           Expected keywords: {pair.expected_keywords}")

    return results


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------
@dataclass
class AggregateMetrics:
    """Aggregate metrics across all evaluated questions."""
    total: int = 0
    recall_at_1: float = 0.0
    recall_at_3: float = 0.0
    recall_at_5: float = 0.0
    mrr: float = 0.0
    # Per-difficulty breakdown
    easy_recall_5: float = 0.0
    medium_recall_5: float = 0.0
    hard_recall_5: float = 0.0


def aggregate(results: list[QuestionResult]) -> AggregateMetrics:
    """Compute aggregate metrics from per-question results."""
    n = len(results)
    if n == 0:
        return AggregateMetrics()

    metrics = AggregateMetrics(total=n)
    metrics.recall_at_1 = sum(r.recall_at_1 for r in results) / n
    metrics.recall_at_3 = sum(r.recall_at_3 for r in results) / n
    metrics.recall_at_5 = sum(r.recall_at_5 for r in results) / n
    metrics.mrr = sum(r.reciprocal_rank for r in results) / n

    # Per-difficulty
    for diff in ("easy", "medium", "hard"):
        subset = [r for r in results if r.difficulty == diff]
        if subset:
            val = sum(r.recall_at_5 for r in subset) / len(subset)
            setattr(metrics, f"{diff}_recall_5", val)

    return metrics


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------
def print_results_table(results: list[QuestionResult], metrics: AggregateMetrics) -> None:
    """Print a formatted evaluation results table."""
    sep = "=" * 100

    print(f"\n{sep}")
    print("  RETRIEVAL EVALUATION RESULTS")
    print(sep)
    print(f"  {'#':<4} {'Diff':<7} {'R@1':<5} {'R@3':<5} {'R@5':<5} {'RR':<6} {'Rank':<5} {'Topic'}")
    print(f"  {'-'*4} {'-'*7} {'-'*5} {'-'*5} {'-'*5} {'-'*6} {'-'*5} {'-'*30}")

    for i, r in enumerate(results, start=1):
        rank_str = str(r.first_hit_rank) if r.first_hit_rank > 0 else "-"
        print(
            f"  {i:<4} {r.difficulty:<7} "
            f"{r.recall_at_1:<5.1f} {r.recall_at_3:<5.1f} {r.recall_at_5:<5.1f} "
            f"{r.reciprocal_rank:<6.3f} {rank_str:<5} {r.topic}"
        )

    print(sep)
    print(f"\n  AGGREGATE METRICS ({metrics.total} questions)")
    print(f"  {'-'*50}")
    print(f"  Recall@1:  {metrics.recall_at_1:.1%}")
    print(f"  Recall@3:  {metrics.recall_at_3:.1%}")
    print(f"  Recall@5:  {metrics.recall_at_5:.1%}")
    print(f"  MRR:       {metrics.mrr:.3f}")
    print()
    print(f"  Per-difficulty Recall@5:")
    print(f"    Easy:    {metrics.easy_recall_5:.1%}")
    print(f"    Medium:  {metrics.medium_recall_5:.1%}")
    print(f"    Hard:    {metrics.hard_recall_5:.1%}")

    # Flag misses
    misses = [r for r in results if r.recall_at_5 == 0]
    if misses:
        print(f"\n  MISSES (0 relevant in top 5):")
        for r in misses:
            print(f"    - [{r.difficulty}] {r.question}")
    else:
        print(f"\n  All questions had at least one relevant result in top 5.")

    print(sep)
    print()


# ---------------------------------------------------------------------------
# Grid search for RRF weights
# ---------------------------------------------------------------------------
def grid_search_rrf(
    pairs: list[GoldenPair],
    use_semantic: bool = False,
    fetch_limit: int = 10,
) -> None:
    """Grid-search over RRF weight combinations to find the best config.

    Searches over page_weight in [0.5, 1.0, 1.5] and chunk_weight in
    [0.5, 1.0, 1.2, 1.5, 2.0]. If semantic is enabled, also searches
    semantic_weight in [0.5, 1.0, 1.5].
    """
    page_weights = [0.5, 1.0, 1.5]
    chunk_weights = [0.5, 1.0, 1.2, 1.5, 2.0]
    semantic_weights = [0.5, 1.0, 1.5] if use_semantic else [0.0]

    best_mrr = -1.0
    best_weights: list[float] = []
    best_metrics: Optional[AggregateMetrics] = None
    all_configs: list[tuple[list[float], AggregateMetrics]] = []

    total_combos = len(page_weights) * len(chunk_weights) * len(semantic_weights)
    print(f"\n  Grid search: {total_combos} weight combinations ...")
    print(f"  {'Page':<7} {'Chunk':<7} {'Semantic':<9} {'R@1':<7} {'R@3':<7} {'R@5':<7} {'MRR':<7}")
    print(f"  {'-'*7} {'-'*7} {'-'*9} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")

    combo_num = 0
    for pw, cw, sw in product(page_weights, chunk_weights, semantic_weights):
        combo_num += 1
        weights = [pw, cw] if not use_semantic else [pw, cw, sw]

        qr = evaluate(
            pairs,
            use_semantic=use_semantic,
            rrf_weights=weights,
            fetch_limit=fetch_limit,
            verbose=False,
        )
        m = aggregate(qr)
        all_configs.append((weights, m))

        print(
            f"  {pw:<7.1f} {cw:<7.1f} {sw:<9.1f} "
            f"{m.recall_at_1:<7.1%} {m.recall_at_3:<7.1%} {m.recall_at_5:<7.1%} {m.mrr:<7.3f}"
        )

        if m.mrr > best_mrr:
            best_mrr = m.mrr
            best_weights = weights
            best_metrics = m

    print(f"\n  BEST CONFIG (by MRR):")
    if use_semantic:
        print(f"    page_weight={best_weights[0]}, chunk_weight={best_weights[1]}, semantic_weight={best_weights[2]}")
    else:
        print(f"    page_weight={best_weights[0]}, chunk_weight={best_weights[1]}")
    print(f"    Recall@1={best_metrics.recall_at_1:.1%}  Recall@3={best_metrics.recall_at_3:.1%}  "
          f"Recall@5={best_metrics.recall_at_5:.1%}  MRR={best_metrics.mrr:.3f}")

    # Also show best by Recall@5
    best_r5_config = max(all_configs, key=lambda x: (x[1].recall_at_5, x[1].mrr))
    if best_r5_config[0] != best_weights:
        w = best_r5_config[0]
        m = best_r5_config[1]
        print(f"\n  BEST CONFIG (by Recall@5):")
        if use_semantic:
            print(f"    page_weight={w[0]}, chunk_weight={w[1]}, semantic_weight={w[2]}")
        else:
            print(f"    page_weight={w[0]}, chunk_weight={w[1]}")
        print(f"    Recall@1={m.recall_at_1:.1%}  Recall@3={m.recall_at_3:.1%}  "
              f"Recall@5={m.recall_at_5:.1%}  MRR={m.mrr:.3f}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Evaluate the contract dashboard retrieval pipeline against golden Q&A pairs."
    )
    parser.add_argument(
        "--tune", action="store_true",
        help="Grid-search RRF weights for best Recall/MRR.",
    )
    parser.add_argument(
        "--semantic", action="store_true",
        help="Include ChromaDB semantic search in the pipeline.",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Number of results to fetch per retriever (default: 10).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print per-question details during evaluation.",
    )
    parser.add_argument(
        "--pairs", type=str, default=None,
        help="Path to a custom golden Q&A JSON file (default: tools/golden_qa_pairs.json).",
    )
    parser.add_argument(
        "--difficulty", type=str, default=None, choices=["easy", "medium", "hard"],
        help="Only evaluate questions of this difficulty level.",
    )

    args = parser.parse_args()

    # Load golden pairs
    qa_path = Path(args.pairs) if args.pairs else GOLDEN_QA_PATH
    if not qa_path.exists():
        print(f"ERROR: Golden QA file not found: {qa_path}")
        sys.exit(1)

    pairs = load_golden_pairs(qa_path)
    print(f"\n  Loaded {len(pairs)} golden Q&A pairs from {qa_path.name}")

    # Filter by difficulty if requested
    if args.difficulty:
        pairs = [p for p in pairs if p.difficulty == args.difficulty]
        print(f"  Filtered to {len(pairs)} '{args.difficulty}' questions")

    if not pairs:
        print("  No questions to evaluate. Exiting.")
        sys.exit(0)

    print(f"  Search mode: FTS5 pages + FTS5 chunks" + (" + semantic" if args.semantic else ""))
    print(f"  Fetch limit: {args.limit} results per retriever")

    if args.tune:
        # Grid search mode
        start = time.time()
        grid_search_rrf(pairs, use_semantic=args.semantic, fetch_limit=args.limit)
        elapsed = time.time() - start
        print(f"  Grid search completed in {elapsed:.1f}s")
    else:
        # Standard evaluation
        start = time.time()
        results = evaluate(
            pairs,
            use_semantic=args.semantic,
            fetch_limit=args.limit,
            verbose=args.verbose,
        )
        elapsed = time.time() - start

        metrics = aggregate(results)
        print_results_table(results, metrics)
        print(f"  Evaluation completed in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
