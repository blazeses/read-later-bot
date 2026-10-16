import uuid


def compute_rrf_score(ranks_per_system: dict[str, int], k: int = 60) -> float:
    return sum(1.0 / (k + rank) for rank in ranks_per_system.values())


def test_rrf_ranking_logic():
    k = 60
    doc_both = compute_rrf_score({"vector": 2, "text": 3}, k=k)
    doc_vec_only = compute_rrf_score({"vector": 1}, k=k)
    doc_text_only = compute_rrf_score({"text": 1}, k=k)

    # A document appearing in top-3 of both rankers should score higher
    # than a document appearing #1 in only one ranker
    # 1/(60+2) + 1/(60+3) = 1/62 + 1/63 ~= 0.016129 + 0.015873 = 0.032002
    # 1/(60+1) = 1/61 ~= 0.016393
    assert doc_both > doc_vec_only
    assert doc_both > doc_text_only
