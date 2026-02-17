import re

from app.services.search import build_fts_query


def test_build_fts_query_prefixes_words():
    query = "Spruce Grove Sick Time"
    fts_q = build_fts_query(query, mode="and")

    # Expect prefix tokens for individual words
    assert "spruce*" in fts_q
    assert "grove*" in fts_q
    assert "sick*" in fts_q
    assert "time*" in fts_q

    # Ensure we did not produce invalid quoted-prefix patterns like "word"*
    assert not re.search(r'"\w+"\*', fts_q)
