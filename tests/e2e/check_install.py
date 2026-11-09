from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECT_SCRIPT = """
import importlib.metadata as metadata
import sys
from pathlib import Path

import superagent
import superagent.agents
import superagent.connectors
import superagent.plugin

site = Path(sys.prefix).resolve()
assert site in Path(superagent.__file__).resolve().parents, superagent.__file__
assert callable(superagent.plugin.register)
for script in (superagent.agents.HOOK_SCRIPT, superagent.agents.PROBE_SCRIPT, superagent.agents.RELAUNCH_SCRIPT):
    assert script.is_file(), script
print("installed", metadata.version("hermes-superagent"), "from", Path(superagent.__file__).parent)
"""


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(command)} exited {done.returncode}\n{done.stdout}\n{done.stderr}")
    return done.stdout


def buildable_source(source: str, work: Path) -> str:
    if not Path(source).is_dir():
        return source
    skip = shutil.ignore_patterns(".git", ".claude", ".venv", "build", "dist", "*.egg-info", "__pycache__")
    return str(shutil.copytree(source, work / "source", ignore=skip))


def check(tool: str, source: str, work: Path) -> None:
    venv = work / tool
    bin_dir = venv / "bin"
    if tool == "pip":
        run([sys.executable, "-m", "venv", str(venv)], work)
        run([str(bin_dir / "pip"), "install", "--quiet", source], work)
    else:
        run(["uv", "venv", "--quiet", str(venv)], work)
        run(["uv", "pip", "install", "--quiet", "--python", str(bin_dir / "python"), source], work)

    clean_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    print(tool, run([str(bin_dir / "python"), "-c", EXPECT_SCRIPT], work, clean_env).strip())

    home = work / f"{tool}-home"
    run([str(bin_dir / "superagent"), "--home", str(home), "setup"], work, clean_env)
    if not (home / "superagent.toml").is_file():
        raise SystemExit(f"setup wrote no superagent.toml under {home}")
    run([str(bin_dir / "superagent"), "--home", str(home), "status"], work, clean_env)
    print(tool, "console script ran setup and status")

    demo = run([str(bin_dir / "superagent-demo")], work, clean_env)
    if "late report refused" not in demo or "still open: 0" not in demo:
        raise SystemExit(f"superagent-demo did not reach its last scenes\n{demo}")
    print(tool, "superagent-demo ran every scene")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the package into clean venvs and use it away from the source")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()

    work = (args.work or Path(tempfile.mkdtemp(prefix="superagent-install-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    source = buildable_source(args.source, work)
    for tool in ("pip", "uv"):
        check(tool, source, work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
