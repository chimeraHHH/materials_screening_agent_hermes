from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import replace

import pytest

from material_agent.inspiration.semantic_embedding import (
    LocalBundleVerificationV1,
    LocalSemanticEmbeddingAdapter,
    LocalSemanticModelIdentityV1,
    SemanticEmbeddingCacheEntryV1,
    SemanticEmbeddingError,
    SemanticPassageInputV1,
    build_production_semantic_adapter,
)


def _identity(*, suffix: str = "a", synthetic: bool = True) -> LocalSemanticModelIdentityV1:
    return LocalSemanticModelIdentityV1(
        provider_id="synthetic-local-provider",
        model_id="synthetic-passage-model",
        model_revision=f"revision-{suffix}",
        tokenizer_sha256="1" * 64,
        config_sha256=suffix * 64,
        model_bundle_sha256="3" * 64,
        dependency_lock_sha256="4" * 64,
        license_id="test-only-license",
        license_sha256="5" * 64,
        dimension=8,
        synthetic=synthetic,
    )


class SyntheticLocalProvider:
    """Explicit engineering fixture; it is not a scientific model."""

    def __init__(
        self,
        *,
        identity: LocalSemanticModelIdentityV1 | None = None,
        vector: Sequence[float] = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    ) -> None:
        self._identity = identity or _identity()
        self.vector = vector
        self.embed_calls = 0
        self.observed_inputs: list[bytes] = []
        self.verification = LocalBundleVerificationV1(
            tokenizer_sha256=self._identity.tokenizer_sha256,
            config_sha256=self._identity.config_sha256,
            model_bundle_sha256=self._identity.model_bundle_sha256,
            dependency_lock_sha256=self._identity.dependency_lock_sha256,
            license_sha256=self._identity.license_sha256,
            observed_dimension=self._identity.dimension,
            loaded_from_local_path=True,
            dependency_check_passed=True,
            license_check_passed=True,
        )

    @property
    def identity(self) -> LocalSemanticModelIdentityV1:
        return self._identity

    def verify_local_bundle(self) -> LocalBundleVerificationV1:
        return self.verification

    def count_tokens(self, canonical_input: bytes) -> int:
        payload = json.loads(canonical_input)
        return sum(
            len(str(value).split())
            for key, value in payload.items()
            if key != "normalized_tags"
        ) + sum(len(tag.split()) for tag in payload["normalized_tags"])

    def embed(self, canonical_input: bytes) -> Sequence[float]:
        self.embed_calls += 1
        self.observed_inputs.append(canonical_input)
        return self.vector


def _input(*, passage: str = "Localized modes suppress dispersion.") -> SemanticPassageInputV1:
    return SemanticPassageInputV1(
        title="  Cross-domain   mechanism  ",
        section_heading=" Results ",
        selected_passage=passage,
        normalized_tags=("Flat-Band", "local-mode", "flat-band"),
    )


def test_passage_only_input_replay_and_cache_are_bound_to_exact_model_identity() -> None:
    provider = SyntheticLocalProvider()
    cache: dict[str, SemanticEmbeddingCacheEntryV1] = {}
    adapter = LocalSemanticEmbeddingAdapter(
        provider=provider,
        cache=cache,
        allow_synthetic=True,
    )

    first = adapter.embed_one(_input(), max_input_tokens=20)
    replay = adapter.embed_one(_input(), max_input_tokens=20)

    assert first.cache_hit is False
    assert replay.cache_hit is True
    assert provider.embed_calls == 1
    assert first.vector == replay.vector
    assert first.vector_sha256 == replay.vector_sha256
    assert first.cache_key_sha256 == replay.cache_key_sha256
    assert math.sqrt(math.fsum(value * value for value in first.vector)) == pytest.approx(
        1.0,
        abs=1e-6,
    )
    assert json.loads(provider.observed_inputs[0]) == {
        "normalized_tags": ["flat-band", "local-mode"],
        "section_heading": "Results",
        "selected_passage": "Localized modes suppress dispersion.",
        "title": "Cross-domain mechanism",
    }
    changed_input = adapter.embed_one(
        _input(passage="Localized modes enhance dispersion."),
        max_input_tokens=20,
    )
    assert changed_input.embedding_input_sha256 != first.embedding_input_sha256
    assert changed_input.cache_key_sha256 != first.cache_key_sha256

    other = LocalSemanticEmbeddingAdapter(
        provider=SyntheticLocalProvider(identity=_identity(suffix="b")),
        cache=cache,
        allow_synthetic=True,
    ).embed_one(_input(), max_input_tokens=20)
    assert other.model_identity.identity_sha256 != first.model_identity.identity_sha256
    assert other.cache_key_sha256 != first.cache_key_sha256


def test_production_is_explicitly_unavailable_and_synthetic_never_auto_promotes() -> None:
    with pytest.raises(SemanticEmbeddingError) as unavailable:
        build_production_semantic_adapter()
    assert unavailable.value.code == "SEMANTIC_PRODUCTION_UNAVAILABLE"

    with pytest.raises(SemanticEmbeddingError) as synthetic:
        build_production_semantic_adapter(provider=SyntheticLocalProvider())
    assert synthetic.value.code == "SYNTHETIC_PROVIDER_FORBIDDEN"


def test_bundle_drift_dependency_license_and_local_only_checks_fail_closed() -> None:
    provider = SyntheticLocalProvider()
    adapter = LocalSemanticEmbeddingAdapter(
        provider=provider,
        allow_synthetic=True,
    )
    provider.verification = replace(
        provider.verification,
        model_bundle_sha256="f" * 64,
    )
    with pytest.raises(SemanticEmbeddingError) as drift:
        adapter.embed_one(_input(), max_input_tokens=20)
    assert drift.value.code == "MODEL_BUNDLE_DRIFT"

    for override, code in (
        ({"dependency_check_passed": False}, "DEPENDENCY_CHECK_FAILED"),
        ({"license_check_passed": False}, "LICENSE_CHECK_FAILED"),
        ({"loaded_from_local_path": False}, "REMOTE_PROVIDER_FORBIDDEN"),
    ):
        checked = SyntheticLocalProvider()
        checked.verification = replace(checked.verification, **override)
        with pytest.raises(SemanticEmbeddingError) as error:
            LocalSemanticEmbeddingAdapter(
                provider=checked,
                allow_synthetic=True,
            )
        assert error.value.code == code

    identity_drift = SyntheticLocalProvider()
    drift_adapter = LocalSemanticEmbeddingAdapter(
        provider=identity_drift,
        allow_synthetic=True,
    )
    identity_drift._identity = _identity(suffix="b")
    with pytest.raises(SemanticEmbeddingError) as changed_identity:
        drift_adapter.embed_one(_input(), max_input_tokens=20)
    assert changed_identity.value.code == "PROVIDER_IDENTITY_DRIFT"


@pytest.mark.parametrize(
    ("vector", "code"),
    (
        ((1.0,) * 7, "PROVIDER_DIMENSION_MISMATCH"),
        ((float("nan"), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), "PROVIDER_NON_FINITE_VECTOR"),
        ((0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), "PROVIDER_NOT_L2_NORMALIZED"),
    ),
)
def test_dimension_finite_and_l2_output_validation(
    vector: Sequence[float],
    code: str,
) -> None:
    adapter = LocalSemanticEmbeddingAdapter(
        provider=SyntheticLocalProvider(vector=vector),
        allow_synthetic=True,
    )
    with pytest.raises(SemanticEmbeddingError) as error:
        adapter.embed_one(_input(), max_input_tokens=20)
    assert error.value.code == code


def test_token_budgets_and_corrupt_cache_entries_fail_closed() -> None:
    provider = SyntheticLocalProvider()
    cache: dict[str, SemanticEmbeddingCacheEntryV1] = {}
    adapter = LocalSemanticEmbeddingAdapter(
        provider=provider,
        cache=cache,
        allow_synthetic=True,
    )
    with pytest.raises(SemanticEmbeddingError) as token_error:
        adapter.embed_one(_input(), max_input_tokens=2)
    assert token_error.value.code == "MAX_INPUT_TOKENS_EXCEEDED"

    result = adapter.embed_one(_input(), max_input_tokens=20)
    cache[result.cache_key_sha256] = SemanticEmbeddingCacheEntryV1(
        cache_key_sha256="f" * 64,
        model_identity_sha256=result.model_identity.identity_sha256,
        embedding_input_sha256=result.embedding_input_sha256,
        dimension=result.dimension,
        input_token_count=result.input_token_count,
        vector=result.vector,
    )
    with pytest.raises(SemanticEmbeddingError) as cache_error:
        adapter.embed_one(_input(), max_input_tokens=20)
    assert cache_error.value.code == "CACHE_IDENTITY_MISMATCH"
