"""
ad_combination_embeddings — text combination embeddings for dynamic ads (AdStac.kr).

For a dynamic ad with multiple values per slot, embeds every Cartesian
combination as a single text vector: {"headline": "...", "primary_text": "..."}.
No image, no metadata — pure text combinations.

Usage
-----
from ad_combination_embeddings import (
    embed_all_combinations,
    get_embeddings_for_source,
    count_embeddings_for_source,
    combination_count,
)
import asyncio

# Dry-run: how many combinations will this produce?
n = combination_count(components)   # e.g. 4 headlines × 3 bodies × 2 descs = 24

# Embed all combinations (real API calls)
stored = asyncio.run(embed_all_combinations(
    source_id="my_ad_001",
    components=components,
))

# Retrieve
embeddings = get_embeddings_for_source("my_ad_001")
# [{"combination_key": '{"headline":"Wool Socks",...}',
#   "combination": {"headline": "Wool Socks", ...},
#   "vector": np.ndarray,
#   "model": "embed-v-4-0",
#   "embedded_at": "..."}, ...]
"""

from ad_combination_embeddings.combinations import combination_count
from ad_combination_embeddings.pipeline import embed_all_combinations
from ad_combination_embeddings.storage import (
    count_embeddings_for_source,
    get_embeddings_for_source,
)

__all__ = [
    "embed_all_combinations",
    "get_embeddings_for_source",
    "count_embeddings_for_source",
    "combination_count",
]
