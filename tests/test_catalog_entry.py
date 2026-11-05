import re
import struct
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ENTRY = yaml.safe_load((ROOT / "docs/catalog-entry.yaml").read_text(encoding="utf-8"))
MANIFEST = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_entry_capabilities_match_the_manifest() -> None:
    capabilities = ENTRY["capabilities"]
    assert capabilities["provides_tools"] == MANIFEST["provides_tools"]
    assert capabilities["provides_hooks"] == MANIFEST["provides_hooks"]


def test_versions_and_floor_agree() -> None:
    assert ENTRY["name"] == MANIFEST["name"]
    assert ENTRY["version"] == MANIFEST["version"] == PROJECT["version"]
    assert ENTRY["requires_hermes"] == MANIFEST["requires_hermes"]


def test_entry_description_carries_the_manifest_disclosure() -> None:
    disclosure = MANIFEST["description"].split("Disclosure:")[1].split("macOS and Linux")[0].strip()
    assert disclosure in ENTRY["description"]


def test_cover_is_two_to_one_and_pinned_with_the_entry() -> None:
    match = re.fullmatch(
        r"https://raw\.githubusercontent\.com/AirKyzzZ/hermes-superagent/(\w{40})/(.+)", ENTRY["image"]
    )
    assert match is not None
    assert match.group(1) == ENTRY["sha"]
    header = (ROOT / match.group(2)).read_bytes()[16:24]
    width, height = struct.unpack(">II", header)
    assert width == 2 * height
