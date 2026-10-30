"""Shared fixtures for the nexctf_sandbox tests."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from nexctf.plugins.loader import plugin_key
from nexctf.plugins.testing import register_plugin

import nexctf_sandbox
from nexctf_sandbox import _sandbox

# Under the key the host's loader derives from the distribution name, so the
# settings land where they would in production.
register_plugin(nexctf_sandbox.plugin, plugin_key("nexctf-sandbox"))


def _returns(exit_code: int, stdout: str = "") -> Callable:
    """Build an async run_python stub that always returns the given exit code and stdout."""

    async def _fake(code: str, stdin: str = "", *, timeout: int) -> tuple[int, str]:
        return exit_code, stdout

    return _fake


def _raises(exc: Exception) -> Callable:
    """Build an async run_python stub that always raises *exc*."""

    async def _fake(code: str, stdin: str = "", *, timeout: int) -> tuple[int, str]:
        raise exc

    return _fake


def _capturing() -> tuple[dict[str, str], Callable]:
    """Build a run_python stub that exits 0, and the dict it records code and stdin in."""
    captured: dict[str, str] = {}

    async def _fake(code: str, stdin: str = "", *, timeout: int) -> tuple[int, str]:
        captured.update(code=code, stdin=stdin)
        return 0, ""

    return captured, _fake


def _settings_returning(**values) -> Callable:
    """Build a ``_settings`` stub returning the defaults with *values* overridden."""
    merged = dict(_sandbox._DEFAULTS) | values

    async def _fake() -> dict:
        return merged

    return _fake


class _FakeShellResult:
    def __init__(self, exit_code: int, stdout_text: str, stderr_text: str = "") -> None:
        self.exit_code = exit_code
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text


class _FakeFs:
    async def write(self, path: str, data: bytes) -> None:
        pass


class _FakeSandbox:
    def __init__(self) -> None:
        self.fs = _FakeFs()
        self.shell_kwargs: dict | None = None
        self.destroyed: list[bool] = []
        self.boot_kwargs: dict = {}  # what Sandbox.create was last called with
        self.removed: list[str] = []  # names passed to Sandbox.remove

    async def shell(self, cmd: str, *, stdin=None, timeout=None):
        self.shell_kwargs = {"cmd": cmd, "stdin": stdin, "timeout": timeout}
        return _FakeShellResult(0, "ok")

    async def destroy(self, *, force: bool = False, timeout=None) -> None:
        self.destroyed.append(force)


@pytest.fixture
def fake_vm(monkeypatch: pytest.MonkeyPatch) -> _FakeSandbox:
    """Swap Sandbox.create/remove for fakes; every boot returns this one VM."""
    vm = _FakeSandbox()

    async def _create(name, **kwargs):
        vm.boot_kwargs.update(kwargs)
        return vm

    async def _remove(name) -> None:
        vm.removed.append(name)

    monkeypatch.setattr(_sandbox.Sandbox, "create", _create)
    monkeypatch.setattr(_sandbox.Sandbox, "remove", _remove)
    return vm


@pytest.fixture
def patch_run_python(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[object, Callable], None]:
    """Return a setter that swaps ``run_python`` in a solution module for a stub.

    Usage::

        patch_run_python(runner, fake_run_python)

    where ``fake_run_python(code, stdin="", *, timeout) -> tuple[int, str]``.
    """

    def _patch(module: object, fake: Callable) -> None:
        monkeypatch.setattr(module, "run_python", fake)

    return _patch


@pytest.fixture
def patch_python_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[object, Callable], SimpleNamespace]:
    """Like :func:`patch_run_python`, for modules that reuse one VM across runs.

    The stub has the same signature; it is just handed out by a context manager
    instead of being called directly. The setter returns a namespace whose
    ``boots`` counts how many VMs were opened.
    """

    def _patch(module: object, fake: Callable) -> SimpleNamespace:
        stats = SimpleNamespace(boots=0)

        @asynccontextmanager
        async def _runner():
            stats.boots += 1
            yield fake

        monkeypatch.setattr(module, "python_runner", _runner)
        return stats

    return _patch
