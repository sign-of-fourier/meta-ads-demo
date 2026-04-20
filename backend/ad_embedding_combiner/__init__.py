"""
ad_embedding_combiner — combines text + image embeddings for GPR/BO.

Truncates each modality to a fixed dimension then concatenates.
TEXT_DIM and IMAGE_DIM in combiner.py are the only knobs to turn.

Usage
-----
from ad_embedding_combiner import combine, output_dim, TEXT_DIM, IMAGE_DIM

vec = combine(text_vector, image_vector)   # shape: (TEXT_DIM + IMAGE_DIM,)
"""

from ad_embedding_combiner.combiner import (
    IMAGE_DIM,
    TEXT_DIM,
    combine,
    output_dim,
    truncate_pad,
)

__all__ = ["combine", "truncate_pad", "output_dim", "TEXT_DIM", "IMAGE_DIM"]
