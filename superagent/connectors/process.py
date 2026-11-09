from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Sequence

from . import ConnectorError

BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "XDG_CONFIG_HOME", "XDG_DATA_HOME")


class CommandFailed(ConnectorError):
    def __init__(self, message: str, stdout: str) -> None:
        super().__init__(message)
        self.stdout = stdout


def run_json(argv: Sequence[str], env_names: Sequence[str], timeout: float, stdin: str | None = None) -> object:
    stdout = run_text(argv, env_names, timeout, stdin)
    try:
        return json.loads(stdout)
    except ValueError as error:
        raise ConnectorError(f"{argv[0]} did not print JSON: {error}") from error


def run_text(
    argv: Sequence[str], env_names: Sequence[str], timeout: float, stdin: str | None = None, cwd: str | None = None
) -> str:
    if timeout <= 0:
        raise ConnectorError(f"no time left to run {argv[0]}")
    env = {name: os.environ[name] for name in (*BASE_ENV, *env_names) if name in os.environ}
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except OSError as error:
        raise ConnectorError(f"{argv[0]} could not run: {error}") from error
    try:
        stdout, stderr = process.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.communicate()
        raise ConnectorError(f"{argv[0]} took longer than {timeout:g}s") from error
    if process.returncode != 0:
        raise CommandFailed(f"{argv[0]} exited {process.returncode}: {stderr.strip()[-200:]}", stdout)
    return stdout
