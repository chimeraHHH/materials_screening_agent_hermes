from __future__ import annotations

import pytest

from material_agent.research.flatband_metrics import duplicate_rate_at_5


def test_duplicate_rate_at_5_accepts_the_full_top_five_boundary() -> None:
    assert duplicate_rate_at_5(("same", "same", "b", "c", "d")) == 0.25


def test_duplicate_rate_at_5_rejects_more_than_five_clusters() -> None:
    with pytest.raises(ValueError, match="at most five"):
        duplicate_rate_at_5(("a", "a", "b", "c", "d", "e"))
