from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).parents[1] / "fixtures/contracts/agent03-v1"


def test_agent03_fixture_manifest_is_reproducible_and_non_scientific() -> None:
    manifest = json.loads((FIXTURE / "fixture_manifest.json").read_text())
    assert manifest["is_mock"] is True
    for item in manifest["files"]:
        path = FIXTURE / item["path"]
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == item["sha256"]
        assert not path.is_absolute() or str(path).startswith(str(FIXTURE))
        payload = content.decode()
        assert "band_gap" not in payload
        assert "total_energy" not in payload
        assert "magnetic_moment" not in payload
        assert "L3_DFT_VALIDATED" not in payload
        assert "traceback" not in payload.lower()
        assert "POTCAR" not in payload


def test_agent03_fixture_has_no_machine_paths_or_credentials() -> None:
    text = "\n".join(
        path.read_text()
        for path in FIXTURE.rglob("*")
        if path.is_file()
    )
    assert "/Users/" not in text
    assert "/home/" not in text
    assert "MP_API_KEY" not in text
    assert "password" not in text.lower()
