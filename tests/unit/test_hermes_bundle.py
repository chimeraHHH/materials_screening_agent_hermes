from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from material_agent.gateway import (
    InspirationRunRequestV1,
    inspiration_request_sha256,
)
from material_agent.integration.request_compiler import (
    HermesInspirationRequestCompiler,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    REPOSITORY_ROOT
    / "integrations/hermes/profiles/materials-inspiration/skills"
    / "materials-inspiration/SKILL.md"
)
SUPPORTED_REQUEST_SHA256 = (
    "0598117ef45e17ec44f328f3effff5722166e2df589b8695ff0ff1c5a25220c6"
)
MATERIALS_HUB_URL = "${MATERIAL_AGENT_MCP_BASE_URL}/materials/mcp"


def test_hermes_bundle_verifier_binds_nested_budget_guidance() -> None:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "integrations/hermes/scripts/verify_bundle.py"),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "Hermes bundle valid\n"


def test_hermes_bundle_verifier_fails_closed_on_shared_hub_url_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = REPOSITORY_ROOT / "integrations" / "hermes"
    copied = tmp_path / "hermes"
    shutil.copytree(source, copied, ignore=shutil.ignore_patterns("__pycache__"))
    config = copied / "profiles" / "materials-inspiration" / "config.yaml"
    config_text = config.read_text(encoding="utf-8")
    assert MATERIALS_HUB_URL in config_text
    config.write_text(
        config_text.replace(
            MATERIALS_HUB_URL,
            "http://127.0.0.1:9999/unmanaged/mcp",
        ),
        encoding="utf-8",
    )
    verifier_path = source / "scripts" / "verify_bundle.py"
    spec = importlib.util.spec_from_file_location("queued_bundle_verifier", verifier_path)
    assert spec is not None and spec.loader is not None
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    profile = copied / "profiles" / "materials-inspiration"
    monkeypatch.setattr(verifier, "HERMES_ROOT", copied)
    monkeypatch.setattr(verifier, "PROFILE_ROOT", profile)
    monkeypatch.setattr(
        verifier,
        "SKILL_PATH",
        profile / "skills" / "materials-inspiration" / "SKILL.md",
    )
    monkeypatch.setattr(
        verifier,
        "GATEWAY_CONTRACT_PATH",
        profile
        / "skills"
        / "materials-inspiration"
        / "references"
        / "gateway-contract.md",
    )
    monkeypatch.setattr(verifier, "SOUL_PATH", profile / "SOUL.md")
    monkeypatch.setattr(verifier, "HERMES_README_PATH", copied / "README.md")

    with pytest.raises(ValueError, match="shared loopback HTTP Hub"):
        verifier.verify()


def test_skill_example_compiles_the_complete_supported_public_request() -> None:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"Use this shape:\n\n   ```json\n(?P<payload>.*?)\n   ```",
        skill_text,
        flags=re.DOTALL,
    )
    assert match is not None
    example = json.loads(textwrap.dedent(match.group("payload")))
    request = InspirationRunRequestV1.model_validate_json(json.dumps(example))

    compiled = HermesInspirationRequestCompiler(
        max_retries_per_query=1
    ).compile(request)

    assert request.goal == (
        "Find a reviewable narrow-band mechanism using bounded public metadata."
    )
    assert compiled.policy.network_access is True
    assert compiled.target_tag_ids == ("electronic-flat-band",)
    assert compiled.expected_output_elements == ("Se", "Ti")
    assert compiled.physical_search_attempt_limit == 8
    assert inspiration_request_sha256(request) == SUPPORTED_REQUEST_SHA256
