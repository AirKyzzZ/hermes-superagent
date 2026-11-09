from __future__ import annotations

import re
import tomllib
from pathlib import Path

from superagent.connectors import connector_types, parse_specs

GUIDE = Path(__file__).parents[1] / "docs" / "connectors.md"
BLOCKS = re.findall(r"```toml\n(.*?)```", GUIDE.read_text(encoding="utf-8"), re.S)


def test_every_block_in_the_guide_is_a_valid_config() -> None:
    assert BLOCKS
    for block in BLOCKS:
        assert parse_specs(tomllib.loads(block)["connectors"])


def test_the_guide_shows_every_connector_type() -> None:
    shown = {spec.type for block in BLOCKS for spec in parse_specs(tomllib.loads(block)["connectors"])}
    assert shown == set(connector_types())
