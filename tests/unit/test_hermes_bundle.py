from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

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
    "bd9841221e60480915da9641b4db52bbf200d6c71d596f3c1f5727384284d244"
)


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
    assert compiled.physical_search_attempt_limit == 6
    assert inspiration_request_sha256(request) == SUPPORTED_REQUEST_SHA256
