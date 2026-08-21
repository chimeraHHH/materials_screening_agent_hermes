"""Leaf source-policy contracts for formal flat-band benchmark cases.

This module is intentionally independent of the case-freezing and expert
modules.  It binds every source record used by a formal case to the frozen
catalog row, permitted roles, and public-release policy without creating an
import cycle.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import FlatBandBenchmarkCaseV1

ModelT = TypeVar("ModelT", bound=StrictModel)


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    """Revalidate serialized content, including adversarial model-copy data."""

    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _identity_values(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> tuple[str, str]:
    semantic = model.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    return deterministic_id(prefix, {sha_field: digest}), digest


def _assert_identity(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    identifier, digest = _identity_values(
        model, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    if getattr(model, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(model, id_field) != identifier:
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    identifier, digest = _identity_values(
        draft, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    return model_type.model_validate(
        {**values, id_field: identifier, sha_field: digest}
    )


class SourceCatalogDecision(StrEnum):
    INCLUDE = "INCLUDE"
    CONDITIONAL = "CONDITIONAL"
    EXCLUDE = "EXCLUDE"


class SourceUseRole(StrEnum):
    CASE_SEED = "case_seed"
    STRUCTURE = "structure"
    ELECTRONIC_STRUCTURE = "electronic_structure"
    MECHANISM_SEED = "mechanism_seed"
    LITERATURE_RETRIEVAL = "literature_retrieval"
    IDENTIFIER_RESOLUTION = "identifier_resolution"
    OPEN_ACCESS_STATUS = "open_access_status"
    SENSITIVITY_ONLY = "sensitivity_only"


# Exact canonical-row hashes and policy facts from source_catalog.jsonl whose
# catalog digest is frozen by FlatBandBenchmarkCaseV1.  Keeping the row hash in
# this executable registry prevents a caller from relabelling an EXCLUDE row as
# INCLUDE and merely content-addressing the lie.
_SOURCE_CATALOG_POLICY_V1: dict[
    str,
    tuple[str, SourceCatalogDecision, frozenset[SourceUseRole], frozenset[str]],
] = {
    "materials_flatband_database": (
        "7ec54dbe2ead2c19f046fed15f68b3ad6f3061a24773dff0d3a8c33317d257ad",
        SourceCatalogDecision.CONDITIONAL,
        frozenset(
            {
                SourceUseRole.CASE_SEED,
                SourceUseRole.ELECTRONIC_STRUCTURE,
                SourceUseRole.MECHANISM_SEED,
            }
        ),
        frozenset({"LicenseRef-NoExplicitDatabaseLicense"}),
    ),
    "icsd": (
        "a9c2b7d4a7aa44d15776e47b2c132982a4b4252bee57ea23212be80ecfead2fe",
        SourceCatalogDecision.EXCLUDE,
        frozenset({SourceUseRole.STRUCTURE}),
        frozenset({"LicenseRef-ICSD-Restricted"}),
    ),
    "crystal_net_flat_bands": (
        "28de3d5ed2e2c29dbc913dc84dc04a9692b509f125a8e3af26520ae6b3037b54",
        SourceCatalogDecision.INCLUDE,
        frozenset({SourceUseRole.CASE_SEED, SourceUseRole.MECHANISM_SEED}),
        frozenset({"CC-BY-4.0", "MIT"}),
    ),
    "jarvis_dft_wtb": (
        "c8a48a8dbdde710d9049ae15a271c35ffa3a95caca0208e21a55ce4c9f2a5fc2",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.CASE_SEED,
                SourceUseRole.STRUCTURE,
                SourceUseRole.ELECTRONIC_STRUCTURE,
            }
        ),
        frozenset({"CC-BY-4.0"}),
    ),
    "c2db": (
        "dc3b9109b55df7085564526a91af1e035b6b0d23932030e4b48cac3ca5374f7b",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.CASE_SEED,
                SourceUseRole.STRUCTURE,
                SourceUseRole.ELECTRONIC_STRUCTURE,
            }
        ),
        frozenset({"CC-BY-SA-4.0"}),
    ),
    "materials_project_core": (
        "9e36e4d3b7c20c1298e0485ae3a408ded10d0cdd054b73f87226b99f1806db7f",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.CASE_SEED,
                SourceUseRole.STRUCTURE,
                SourceUseRole.ELECTRONIC_STRUCTURE,
            }
        ),
        frozenset({"CC-BY-4.0"}),
    ),
    "cod": (
        "0d2428498bda8a7aee09a4ce52ca47509feb34b9e4d558e0220c2b8cbfacb30a",
        SourceCatalogDecision.INCLUDE,
        frozenset({SourceUseRole.CASE_SEED, SourceUseRole.STRUCTURE}),
        frozenset({"CC0-1.0"}),
    ),
    "nomad": (
        "fcf90baa58da2416e7e1473ca85fc8add57c01ce0b5d2c23356b909027a19b74",
        SourceCatalogDecision.CONDITIONAL,
        frozenset(
            {
                SourceUseRole.CASE_SEED,
                SourceUseRole.STRUCTURE,
                SourceUseRole.ELECTRONIC_STRUCTURE,
            }
        ),
        frozenset({"LicenseRef-PerUpload-Expected-CC-BY-4.0"}),
    ),
    "materials_cloud": (
        "4b7a223626c2e6cab162523509403e57bbc50661077e5f0690744665dde2fd2f",
        SourceCatalogDecision.CONDITIONAL,
        frozenset(
            {
                SourceUseRole.CASE_SEED,
                SourceUseRole.STRUCTURE,
                SourceUseRole.ELECTRONIC_STRUCTURE,
            }
        ),
        frozenset({"LicenseRef-PerRecord-Mixed", "CC-BY-SA-4.0"}),
    ),
    "aflow": (
        "e2bf366e8c8287fa964b6d0101e8a0b354acb09507147b6c21ad945991b9706a",
        SourceCatalogDecision.EXCLUDE,
        frozenset({SourceUseRole.SENSITIVITY_ONLY}),
        frozenset({"LicenseRef-AFLOW-Scientific-Academic-NonCommercial"}),
    ),
    "twodmatpedia": (
        "2be05995126a07fccbbd68cc46cf493aa7849135ad497da1efd3753db8ce002c",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.CASE_SEED,
                SourceUseRole.STRUCTURE,
                SourceUseRole.ELECTRONIC_STRUCTURE,
            }
        ),
        frozenset({"CC-BY-4.0", "CC0-1.0"}),
    ),
    "elf_flatband_2d": (
        "8e0f6822417bb4d2a4da6a68759a2c6d752e31ef29c09d074628b801f69e13d6",
        SourceCatalogDecision.INCLUDE,
        frozenset({SourceUseRole.CASE_SEED, SourceUseRole.MECHANISM_SEED}),
        frozenset({"MIT"}),
    ),
    "struct2flat": (
        "4e47ce21e784c5d5e5867363c686242f520cabf1aa0632e8dc707ab9f35f8a1b",
        SourceCatalogDecision.INCLUDE,
        frozenset({SourceUseRole.CASE_SEED, SourceUseRole.MECHANISM_SEED}),
        frozenset({"MIT"}),
    ),
    "crossref": (
        "97b098f64f929b836e346fbd87b43f815110c58937a6e94cc87662232d0cde14",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.LITERATURE_RETRIEVAL,
                SourceUseRole.IDENTIFIER_RESOLUTION,
            }
        ),
        frozenset({"CC0-1.0", "LicenseRef-Abstract-PerPublisher"}),
    ),
    "openalex": (
        "f9cfc627276338ffdc11c86afc700129b66f6f677dec96f170445c131b90415f",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.LITERATURE_RETRIEVAL,
                SourceUseRole.IDENTIFIER_RESOLUTION,
                SourceUseRole.OPEN_ACCESS_STATUS,
            }
        ),
        frozenset({"CC0-1.0"}),
    ),
    "openaire": (
        "c9fd3e91fe55c8dd3d80bb578c2e37768c482eeeb1699e4bdd90bf436429b173",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.LITERATURE_RETRIEVAL,
                SourceUseRole.IDENTIFIER_RESOLUTION,
                SourceUseRole.OPEN_ACCESS_STATUS,
            }
        ),
        frozenset({"CC-BY-4.0"}),
    ),
    "arxiv": (
        "aa0ff8feb3cfeae163afca09c8b9ed836f79ff0dd8df81922b7d849affa4df5a",
        SourceCatalogDecision.INCLUDE,
        frozenset(
            {
                SourceUseRole.LITERATURE_RETRIEVAL,
                SourceUseRole.IDENTIFIER_RESOLUTION,
            }
        ),
        frozenset({"CC0-1.0", "LicenseRef-Eprint-PerWork"}),
    ),
    "core": (
        "f18813ac7ca3622e1dba79e7e1e5f58e3ba7dac7f5ec7c7f853c05bd94156bab",
        SourceCatalogDecision.CONDITIONAL,
        frozenset({SourceUseRole.SENSITIVITY_ONLY}),
        frozenset({"LicenseRef-CORE-Mixed-CurrentTerms"}),
    ),
    "semantic_scholar": (
        "1b27cb1789427f425bfdc930ff69747fcc9b70c6c266ce903a5623938c31585b",
        SourceCatalogDecision.EXCLUDE,
        frozenset({SourceUseRole.SENSITIVITY_ONLY}),
        frozenset({"LicenseRef-S2-Internal-NonCommercial-NoThirdPartySharing"}),
    ),
    "europe_pmc": (
        "2f58dfdcb58a3bfa469c940192fe2b965283aac7a6b1b64677caf230e42add1d",
        SourceCatalogDecision.CONDITIONAL,
        frozenset(
            {SourceUseRole.OPEN_ACCESS_STATUS, SourceUseRole.SENSITIVITY_ONLY}
        ),
        frozenset({"LicenseRef-PerWork-Mixed"}),
    ),
}


_RECORD_LEVEL_COMPATIBLE_LICENSES = frozenset(
    {
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "MIT",
        "ODbL-1.0",
        "ODC-BY-1.0",
    }
)


class CaseSourcePolicyAttestationV2(StrictModel):
    """Record-level replay token for one source used by one frozen case.

    This is an internal, content-addressed policy assertion rather than a legal
    opinion or an external rights-holder signature.  The fields for a future
    CONDITIONAL-record approval flow remain reserved, but the formal Pilot is
    INCLUDE-only and this validator rejects every CONDITIONAL or EXCLUDE row.
    """

    schema_version: Literal["flatband-case-source-policy-attestation-v2"] = (
        "flatband-case-source-policy-attestation-v2"
    )
    attestation_id: Identifier
    attestation_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    source_id: Identifier
    source_record_id: Identifier
    source_record_raw_sha256: Sha256
    source_catalog_row_sha256: Sha256
    catalog_decision: SourceCatalogDecision
    usage_roles: Annotated[
        tuple[SourceUseRole, ...], Field(min_length=1, max_length=8)
    ]
    record_license_expression: Annotated[str, Field(min_length=2, max_length=96)]
    record_license_compatibility_sha256: Sha256
    record_provenance_token_sha256: Sha256
    public_fields_release_allowed: bool
    structure_payload_release_allowed: bool
    conditional_gate_token_sha256: Sha256 | None = None
    conditional_public_release_authorization_sha256: Sha256 | None = None
    external_legal_attestation_present: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_attestation(self) -> CaseSourcePolicyAttestationV2:
        role_values = tuple(item.value for item in self.usage_roles)
        if role_values != tuple(sorted(set(role_values))):
            raise ValueError("source usage roles must be sorted and unique")
        if self.structure_payload_release_allowed and (
            SourceUseRole.STRUCTURE not in self.usage_roles
        ):
            raise ValueError(
                "structure payload permission requires the structure usage role"
            )
        if self.structure_payload_release_allowed and not (
            self.public_fields_release_allowed
        ):
            raise ValueError(
                "structure payload permission requires public record fields"
            )
        policy = _SOURCE_CATALOG_POLICY_V1.get(self.source_id)
        if policy is None:
            raise ValueError("source is absent from the frozen source catalog policy")
        row_sha256, decision, allowed_roles, catalog_licenses = policy
        if self.source_catalog_row_sha256 != row_sha256:
            raise ValueError("source catalog row SHA-256 does not replay")
        if self.catalog_decision is not decision:
            raise ValueError("source catalog decision does not replay")
        if not set(self.usage_roles) <= allowed_roles:
            raise ValueError("source usage role is not allowed by the catalog row")
        if decision is SourceCatalogDecision.EXCLUDE:
            raise ValueError("EXCLUDE source cannot enter a frozen case policy")
        elif decision is SourceCatalogDecision.CONDITIONAL:
            raise ValueError(
                "formal Pilot is INCLUDE-only; CONDITIONAL source approval "
                "artifacts are unavailable"
            )
        else:
            if self.record_license_expression not in catalog_licenses:
                raise ValueError(
                    "INCLUDE record license is outside the frozen catalog scopes"
                )
            if self.conditional_gate_token_sha256 is not None or (
                self.conditional_public_release_authorization_sha256 is not None
            ):
                raise ValueError("INCLUDE source cannot carry a conditional override")
        _assert_identity(
            self,
            id_field="attestation_id",
            sha_field="attestation_sha256",
            prefix="case-source-policy-v2",
        )
        return self


def build_case_source_policy_attestation_v2(
    *,
    case: FlatBandBenchmarkCaseV1,
    source_id: str,
    source_record_id: str,
    usage_roles: tuple[SourceUseRole, ...],
    record_license_compatibility_sha256: str,
    record_provenance_token_sha256: str,
    public_fields_release_allowed: bool,
    structure_payload_release_allowed: bool,
    conditional_gate_token_sha256: str | None = None,
    conditional_public_release_authorization_sha256: str | None = None,
) -> CaseSourcePolicyAttestationV2:
    """Build a policy token from an exact source record already in the case."""

    full_case = _revalidate(case, FlatBandBenchmarkCaseV1)
    matches = tuple(
        item
        for item in full_case.source_records
        if (item.source_id, item.source_record_id) == (source_id, source_record_id)
    )
    if len(matches) != 1:
        raise ValueError("source policy attestation requires one exact case source")
    record = matches[0]
    if record.raw_sha256 is None:
        raise ValueError("source policy attestation requires a frozen raw SHA-256")
    policy = _SOURCE_CATALOG_POLICY_V1.get(source_id)
    if policy is None:
        raise ValueError("source is absent from the frozen source catalog policy")
    row_sha256, decision, _allowed_roles, _catalog_licenses = policy
    ordered_roles = tuple(sorted(usage_roles, key=lambda item: item.value))
    return _build_identified(
        CaseSourcePolicyAttestationV2,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="case-source-policy-v2",
        values={
            "case_id": full_case.case_id,
            "case_sha256": full_case.case_sha256,
            "source_id": record.source_id,
            "source_record_id": record.source_record_id,
            "source_record_raw_sha256": record.raw_sha256,
            "source_catalog_row_sha256": row_sha256,
            "catalog_decision": decision,
            "usage_roles": ordered_roles,
            "record_license_expression": record.license_expression,
            "record_license_compatibility_sha256": (
                record_license_compatibility_sha256
            ),
            "record_provenance_token_sha256": record_provenance_token_sha256,
            "public_fields_release_allowed": public_fields_release_allowed,
            "structure_payload_release_allowed": (
                structure_payload_release_allowed
            ),
            "conditional_gate_token_sha256": conditional_gate_token_sha256,
            "conditional_public_release_authorization_sha256": (
                conditional_public_release_authorization_sha256
            ),
        },
    )


def assert_case_source_policy_v2(
    *,
    case: FlatBandBenchmarkCaseV1,
    attestations: tuple[CaseSourcePolicyAttestationV2, ...],
) -> None:
    """Replay exact source coverage, structure provenance, and seed membership."""

    full_case = _revalidate(case, FlatBandBenchmarkCaseV1)
    policies = tuple(
        sorted(
            (
                _revalidate(item, CaseSourcePolicyAttestationV2)
                for item in attestations
            ),
            key=lambda item: (item.source_id, item.source_record_id),
        )
    )
    record_by_key = {
        (item.source_id, item.source_record_id): item
        for item in full_case.source_records
    }
    policy_by_key = {
        (item.source_id, item.source_record_id): item for item in policies
    }
    if len(policy_by_key) != len(policies) or set(policy_by_key) != set(record_by_key):
        raise ValueError(
            "source policy attestations do not exactly cover case sources"
        )
    for key, record in record_by_key.items():
        policy = policy_by_key[key]
        if (
            policy.case_id,
            policy.case_sha256,
            policy.source_record_raw_sha256,
            policy.record_license_expression,
            policy.public_fields_release_allowed,
        ) != (
            full_case.case_id,
            full_case.case_sha256,
            record.raw_sha256,
            record.license_expression,
            record.public_redistribution_allowed,
        ):
            raise ValueError(
                "source policy attestation differs from its exact case record"
            )
    structure_policies = tuple(
        item
        for item in policies
        if SourceUseRole.STRUCTURE in item.usage_roles
    )
    if not structure_policies:
        raise ValueError("case has no catalog-approved structure source")
    for evidence in full_case.seed_evidence:
        key = (
            evidence.source_record.source_id,
            evidence.source_record.source_record_id,
        )
        if record_by_key.get(key) != evidence.source_record:
            raise ValueError(
                "seed evidence source record is not an exact candidate source"
            )
    if full_case.public_release_allowed:
        if not all(item.public_fields_release_allowed for item in policies):
            raise ValueError("public case has non-releasable source fields")
        if not all(
            item.structure_payload_release_allowed for item in structure_policies
        ):
            raise ValueError("public case has a non-releasable structure source")


__all__ = [
    "_RECORD_LEVEL_COMPATIBLE_LICENSES",
    "_SOURCE_CATALOG_POLICY_V1",
    "CaseSourcePolicyAttestationV2",
    "SourceCatalogDecision",
    "SourceUseRole",
    "assert_case_source_policy_v2",
    "build_case_source_policy_attestation_v2",
]
