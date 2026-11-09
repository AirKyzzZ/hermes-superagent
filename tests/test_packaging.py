import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUPTOOLS = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]


def test_every_subpackage_is_listed_for_the_wheel() -> None:
    on_disk = {".".join(path.parent.relative_to(ROOT).parts) for path in (ROOT / "superagent").rglob("__init__.py")}
    assert on_disk <= set(SETUPTOOLS["packages"])
