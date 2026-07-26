from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy
import pytest
from pydantic import ValidationError

from material_agent.ml_screening.adapters import (
    FakeMLModelAdapter,
    FakeMLWorker,
)
from material_agent.ml_screening.models import (
    EvidenceLevel,
    MLNumericArtifactRef,
    MLPropertyValue,
    MLRelaxationResult,
    WorkerInputArtifact,
    WorkerProducedArtifact,
    WorkerResponse,
)
from material_agent.ml_screening.numerics import (
    ASE_GPA_IN_EV_ANGSTROM3,
    ase_voigt_to_symmetric_3x3,
    normalize_ase_stress_to_gpa,
    normalize_direct_stress_gpa,
)
from material_agent.ml_screening.worker_protocol import (
    capture_artifact_tree,
    parse_worker_stdout,
    validate_worker_inputs,
    validate_worker_outputs,
)


def _property_kwargs(ml_model, ml_candidate_factory) -> dict:
    candidate = ml_candidate_factory()
    return {
        "property_name": "mlip_potential_energy",
        "unit": "eV/atom",
        "evidence_level": EvidenceLevel.L1_RETRIEVED,
        "method": "fixture",
        "model_id": ml_model.model_id,
        "checkpoint_sha256": ml_model.checkpoint_sha256,
        "input_structure_id": candidate.source_structure.structure_id,
        "is_mock": True,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"value": -1.0, "artifact_ref": {
            "uri": "artifact://trajectory.npz",
            "sha256": "a" * 64,
            "size_bytes": 1,
            "media_type": "application/x-npz",
            "array_key": "energy",
            "dtype": "float64",
            "shape": [1],
            "allow_pickle": False,
        }},
    ],
)
def test_property_requires_exactly_one_value_source(
    changes,
    ml_model,
    ml_candidate_factory,
) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        MLPropertyValue(**_property_kwargs(ml_model, ml_candidate_factory), **changes)


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        [],
        [[0.0], []],
        True,
        "converged",
    ],
)
def test_property_rejects_non_finite_empty_or_non_numeric_values(
    value,
    ml_model,
    ml_candidate_factory,
) -> None:
    with pytest.raises(ValidationError):
        MLPropertyValue(
            **_property_kwargs(ml_model, ml_candidate_factory),
            value=value,
        )


@pytest.mark.parametrize(
    ("property_name", "value", "unit"),
    [
        ("mlip_potential_energy", [-1.0], "eV/atom"),
        ("mlip_potential_energy", -1.0, "eV"),
        ("maximum_force", -0.01, "eV/angstrom"),
        ("stress", [0.0] * 6, "GPa"),
        (
            "stress",
            [[0.0, 1.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            "GPa",
        ),
    ],
)
def test_property_catalog_rejects_wrong_shape_or_unit(
    property_name,
    value,
    unit,
    ml_model,
    ml_candidate_factory,
) -> None:
    kwargs = _property_kwargs(ml_model, ml_candidate_factory)
    kwargs.update(property_name=property_name, unit=unit)
    with pytest.raises(ValidationError):
        MLPropertyValue(**kwargs, value=value)


def test_property_model_json_model_round_trip_preserves_contract(
    ml_model,
    ml_candidate_factory,
) -> None:
    prop = MLPropertyValue(
        **_property_kwargs(ml_model, ml_candidate_factory),
        value=-1.25,
    )
    assert MLPropertyValue.model_validate_json(prop.model_dump_json()) == prop
    assert "null" not in prop.model_dump_json().split('"value":', 1)[1].split(
        ",",
        1,
    )[0]


@pytest.mark.parametrize(
    ("property_name", "shape"),
    [
        ("forces", [4, 3]),
        ("stress_trajectory", [8, 3, 3]),
        ("site_magnetic_moments", [4]),
    ],
)
def test_numeric_artifact_catalog_accepts_only_fixed_shapes(
    property_name,
    shape,
    ml_model,
    ml_candidate_factory,
) -> None:
    units = {
        "forces": "eV/angstrom",
        "stress_trajectory": "GPa",
        "site_magnetic_moments": "mu_B",
    }
    ref = MLNumericArtifactRef(
        uri="artifact://numeric.npz",
        sha256="a" * 64,
        size_bytes=100,
        array_key="values",
        dtype="float64",
        shape=shape,
        allow_pickle=False,
    )
    prop = MLPropertyValue(
        **{
            **_property_kwargs(ml_model, ml_candidate_factory),
            "property_name": property_name,
            "unit": units[property_name],
        },
        artifact_ref=ref,
        num_sites=shape[0] if property_name != "stress_trajectory" else None,
        num_steps=shape[0] if property_name == "stress_trajectory" else None,
    )
    assert prop.artifact_ref == ref
    bad = ref.model_copy(update={"shape": [shape[0], 2]})
    with pytest.raises(ValidationError):
        MLPropertyValue(
            **{
                **_property_kwargs(ml_model, ml_candidate_factory),
                "property_name": property_name,
                "unit": units[property_name],
            },
            artifact_ref=bad,
            num_sites=shape[0] if property_name != "stress_trajectory" else None,
            num_steps=shape[0] if property_name == "stress_trajectory" else None,
        )


def test_stress_direct_and_ase_paths_agree_without_sign_flip() -> None:
    expected = [
        [1.0, -0.2, 0.3],
        [-0.2, 2.0, -0.4],
        [0.3, -0.4, 3.0],
    ]
    direct = normalize_direct_stress_gpa(expected)
    ase_voigt_ev_a3 = [
        1.0 * ASE_GPA_IN_EV_ANGSTROM3,
        2.0 * ASE_GPA_IN_EV_ANGSTROM3,
        3.0 * ASE_GPA_IN_EV_ANGSTROM3,
        -0.4 * ASE_GPA_IN_EV_ANGSTROM3,
        0.3 * ASE_GPA_IN_EV_ANGSTROM3,
        -0.2 * ASE_GPA_IN_EV_ANGSTROM3,
    ]
    assert ase_voigt_to_symmetric_3x3([1, 2, 3, -0.4, 0.3, -0.2]) == expected
    converted = normalize_ase_stress_to_gpa(ase_voigt_ev_a3)
    assert numpy.allclose(converted, direct, rtol=0, atol=1e-12)
    assert math.isclose(
        normalize_ase_stress_to_gpa(
            [[ASE_GPA_IN_EV_ANGSTROM3, 0, 0], [0, 0, 0], [0, 0, 0]]
        )[0][0],
        1.0,
    )


@pytest.mark.parametrize(
    "stress",
    [
        [0.0] * 6,
        [[0.0, 1.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        [[0.0, 0.0, 0.0], [0.0, float("nan"), 0.0], [0.0, 0.0, 0.0]],
    ],
)
def test_relaxation_rejects_invalid_stress(
    stress,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    plan = ml_plan_factory([ml_candidate_factory()])
    result = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    ).generate_artifacts(plan).manifest[0].candidate.relaxation_result
    assert result is not None
    payload = result.model_dump(mode="json")
    payload["final_stress_gpa_3x3"] = stress
    with pytest.raises(ValidationError):
        MLRelaxationResult.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute/file",
        "../escape",
        "safe/../escape",
        "./safe",
        "safe\\windows",
        " safe",
    ],
)
def test_worker_protocol_rejects_unsafe_json_paths(path) -> None:
    with pytest.raises(ValidationError):
        WorkerInputArtifact(
            artifact_uri="artifact://input",
            root_relative_path=path,
            sha256="a" * 64,
            size_bytes=1,
        )


def _filesystem_worker_case(
    root: Path,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
):
    candidate = ml_candidate_factory()
    candidate_payload = candidate.model_dump(mode="json")
    candidate_payload["source_structure"]["sha256"] = hashlib.sha256(
        b"structure"
    ).hexdigest()
    candidate = type(candidate).model_validate(candidate_payload)
    plan = ml_plan_factory([candidate])
    worker = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    )
    request = worker.request_for_candidate(
        plan,
        plan.inference_candidate_ids[0],
    )
    input_path = root / request.inputs[0].root_relative_path
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(b"structure")
    sandbox = root / request.output_sandbox_relative_path
    sandbox.mkdir(parents=True)
    request_payload = request.model_dump(mode="json")
    request_payload["inputs"][0]["size_bytes"] = len(b"structure")
    request = type(request).model_validate(request_payload)
    response = worker.run(request)
    return request, response, sandbox


def test_adapter_reloads_numeric_artifact_and_rebuilds_metadata(
    tmp_path,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    request, response, sandbox = _filesystem_worker_case(
        tmp_path,
        ml_candidate_factory,
        ml_plan_factory,
        ml_model,
        ml_health,
        ml_policy,
    )
    validate_worker_inputs(request, tmp_path)
    before = capture_artifact_tree(tmp_path)
    numeric_path = sandbox / "trajectory.npz"
    numpy.savez(numeric_path, stress=numpy.zeros((2, 3, 3)))
    relative = numeric_path.relative_to(tmp_path).as_posix()
    produced = WorkerProducedArtifact(
        root_relative_path=relative,
        sha256=hashlib.sha256(numeric_path.read_bytes()).hexdigest(),
        size_bytes=numeric_path.stat().st_size,
        media_type="application/x-npz",
        numeric_metadata={
            "array_key": "stress",
            "dtype": "float64",
            "shape": [2, 3, 3],
            "allow_pickle": False,
        },
    )
    verified_response = WorkerResponse(
        **{
            **response.model_dump(mode="json"),
            "produced_artifacts": [produced.model_dump(mode="json")],
        }
    )
    rebuilt = validate_worker_outputs(
        request=request,
        response=verified_response,
        artifact_root=tmp_path,
        before_snapshot=before,
    )
    assert rebuilt[0].shape == [2, 3, 3]
    assert rebuilt[0].dtype == "float64"
    assert rebuilt[0].allow_pickle is False

    forged = verified_response.model_dump(mode="json")
    forged["produced_artifacts"][0]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="hash"):
        validate_worker_outputs(
            request=request,
            response=WorkerResponse.model_validate(forged),
            artifact_root=tmp_path,
        )


def test_worker_symlink_escape_and_outside_write_are_rejected(
    tmp_path,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    request, response, sandbox = _filesystem_worker_case(
        tmp_path,
        ml_candidate_factory,
        ml_plan_factory,
        ml_model,
        ml_health,
        ml_policy,
    )
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.write_bytes(b"structure")
    link = tmp_path / "inputs" / "symlink.cif"
    link.symlink_to(outside)
    request_payload = request.model_dump(mode="json")
    request_payload["inputs"][0].update(
        root_relative_path="inputs/symlink.cif",
        sha256=hashlib.sha256(b"structure").hexdigest(),
        size_bytes=len(b"structure"),
    )
    with pytest.raises(ValueError, match="symlink"):
        validate_worker_inputs(
            type(request).model_validate(request_payload),
            tmp_path,
        )
    link.unlink()

    before = capture_artifact_tree(tmp_path)
    output = sandbox / "summary.json"
    output.write_bytes(b"{}")
    extra = tmp_path / "outside-sandbox.json"
    extra.write_bytes(b"{}")
    produced = WorkerProducedArtifact(
        root_relative_path=output.relative_to(tmp_path).as_posix(),
        sha256=hashlib.sha256(b"{}").hexdigest(),
        size_bytes=2,
        media_type="application/json",
    )
    checked = WorkerResponse(
        **{
            **response.model_dump(mode="json"),
            "produced_artifacts": [produced.model_dump(mode="json")],
        }
    )
    with pytest.raises(ValueError, match="outside"):
        validate_worker_outputs(
            request=request,
            response=checked,
            artifact_root=tmp_path,
            before_snapshot=before,
        )


def test_stdout_limit_and_authoritative_ledger_are_enforced(
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    plan = ml_plan_factory([ml_candidate_factory()])
    worker = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    )
    request = worker.request_for_candidate(
        plan,
        plan.inference_candidate_ids[0],
    )
    response = worker.run(request)
    encoded = response.model_dump_json().encode()
    assert parse_worker_stdout(
        encoded,
        max_stdout_bytes=len(encoded),
    ) == response
    with pytest.raises(ValueError, match="stdout"):
        parse_worker_stdout(encoded, max_stdout_bytes=len(encoded) - 1)
    with pytest.raises(ValidationError, match="authoritative"):
        WorkerResponse(
            **{
                **response.model_dump(mode="json"),
                "produced_artifacts": [
                    {
                        "root_relative_path": (
                            request.output_sandbox_relative_path
                            + "/operation-complete.json"
                        ),
                        "sha256": "a" * 64,
                        "size_bytes": 1,
                        "media_type": "application/json",
                        "numeric_metadata": None,
                    }
                ],
            }
        )


def test_worker_rejects_duplicate_paths_and_artifact_size_limits(
    tmp_path,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    request, response, sandbox = _filesystem_worker_case(
        tmp_path,
        ml_candidate_factory,
        ml_plan_factory,
        ml_model,
        ml_health,
        ml_policy,
    )
    request_payload = request.model_dump(mode="json")
    request_payload["inputs"].append(dict(request_payload["inputs"][0]))
    with pytest.raises(ValidationError, match="unique"):
        type(request).model_validate(request_payload)

    output = sandbox / "large.bin"
    output.write_bytes(b"1234")
    relative = output.relative_to(tmp_path).as_posix()
    produced = {
        "root_relative_path": relative,
        "sha256": hashlib.sha256(b"1234").hexdigest(),
        "size_bytes": 4,
        "media_type": "application/octet-stream",
        "numeric_metadata": None,
    }
    duplicate_response = response.model_dump(mode="json")
    duplicate_response["produced_artifacts"] = [produced, produced]
    with pytest.raises(ValidationError, match="unique"):
        WorkerResponse.model_validate(duplicate_response)

    limited_request = request.model_dump(mode="json")
    limited_request["limits"]["max_single_artifact_bytes"] = 3
    limited_request["limits"]["max_total_output_bytes"] = 3
    checked_response = response.model_dump(mode="json")
    checked_response["produced_artifacts"] = [produced]
    with pytest.raises(ValueError, match="single-artifact"):
        validate_worker_outputs(
            request=type(request).model_validate(limited_request),
            response=WorkerResponse.model_validate(checked_response),
            artifact_root=tmp_path,
        )


def test_worker_request_has_no_json_artifact_root(
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    plan = ml_plan_factory([ml_candidate_factory()])
    worker = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    )
    request = worker.request_for_candidate(
        plan,
        plan.inference_candidate_ids[0],
    )
    assert "artifact_root" not in type(request).model_fields
    with pytest.raises(ValidationError):
        type(request).model_validate(
            {**request.model_dump(mode="json"), "artifact_root": "/tmp/forged"}
        )


def test_adapter_never_loads_pickle_numeric_artifacts(
    tmp_path,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    request, response, sandbox = _filesystem_worker_case(
        tmp_path,
        ml_candidate_factory,
        ml_plan_factory,
        ml_model,
        ml_health,
        ml_policy,
    )
    path = sandbox / "object-array.npz"
    numpy.savez(path, values=numpy.array([{"unsafe": True}], dtype=object))
    produced = {
        "root_relative_path": path.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
        "media_type": "application/x-npz",
        "numeric_metadata": {
            "array_key": "values",
            "dtype": "float64",
            "shape": [1],
            "allow_pickle": False,
        },
    }
    payload = response.model_dump(mode="json")
    payload["produced_artifacts"] = [produced]
    with pytest.raises(ValueError):
        validate_worker_outputs(
            request=request,
            response=WorkerResponse.model_validate(payload),
            artifact_root=tmp_path,
        )
