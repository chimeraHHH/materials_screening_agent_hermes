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
from material_agent.integration.hermes_service import (
    HERMES_FIXTURE_CONSTRAINTS,
    HERMES_FIXTURE_GOAL,
    HermesFixturePreparer,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    REPOSITORY_ROOT
    / "integrations/hermes/profiles/materials-inspiration/skills"
    / "materials-inspiration/SKILL.md"
)
PILOT_REQUEST_SHA256 = (
    "e45c8f641a21411c0cb60c78ee9597243cabd17a954ee769c897876008147cba"
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


def test_skill_example_is_the_complete_supported_fixture_request() -> None:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"Use this shape:\n\n   ```json\n(?P<payload>.*?)\n   ```",
        skill_text,
        flags=re.DOTALL,
    )
    assert match is not None
    example = json.loads(textwrap.dedent(match.group("payload")))
    request = InspirationRunRequestV1.model_validate_json(json.dumps(example))

    assert request.goal == HERMES_FIXTURE_GOAL
    assert request.constraints == HERMES_FIXTURE_CONSTRAINTS
    assert HermesFixturePreparer.supports(request)
    assert inspiration_request_sha256(request) == PILOT_REQUEST_SHA256
