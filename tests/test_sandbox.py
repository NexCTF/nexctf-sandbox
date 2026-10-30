"""Tests for the shared microsandbox helper (``_sandbox.run_python``)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from nexctf_sandbox import _sandbox

from .conftest import _FakeSandbox, _FakeShellResult


async def test_run_python_sends_stdin_as_bytes(fake_vm) -> None:
    """microsandbox reads a bare str as a stdin mode name, not data — regression guard
    for a bug where plain str stdin raised ``ValueError: unknown stdin mode``."""
    await _sandbox.run_python("print('hi')", "hello world", timeout=5)

    assert fake_vm.shell_kwargs is not None
    assert fake_vm.shell_kwargs["stdin"] == b"hello world"


async def test_run_python_sends_none_for_empty_stdin(fake_vm) -> None:
    await _sandbox.run_python("print('hi')", "", timeout=5)

    assert fake_vm.shell_kwargs is not None
    assert fake_vm.shell_kwargs["stdin"] is None


def test_output_is_capped_in_the_guest() -> None:
    """Untrusted code can print faster than the timeout ends and the whole payload
    lands in the API process, so both streams are truncated before they come back."""
    assert _sandbox._RUN.count(f"head -c {_sandbox._MAX_OUTPUT_BYTES}") == 2
    assert "exit $rc" in _sandbox._RUN  # the program's own exit code, not head's


async def test_sandbox_is_destroyed_not_just_killed(fake_vm) -> None:
    """kill() leaves the registration and its disk on the host — the VM must be
    removed too, or every submission leaks host disk permanently."""
    await _sandbox.run_python("print('hi')", timeout=5)

    # Forced: nothing in the guest is worth a graceful stop.
    assert fake_vm.destroyed == [True]
    assert fake_vm.removed == []


async def test_sandbox_is_destroyed_when_the_run_raises(monkeypatch, fake_vm) -> None:
    async def _boom(*args, **kwargs):
        raise RuntimeError("exec blew up")

    monkeypatch.setattr(fake_vm, "shell", _boom)

    with pytest.raises(RuntimeError):
        await _sandbox.run_python("print('hi')", timeout=5)

    assert fake_vm.destroyed == [True]


async def test_sandbox_is_removed_by_name_when_create_fails(
    monkeypatch, fake_vm
) -> None:
    """A create that fails partway can leave a record behind, with no handle to it."""

    async def _fail(name, **kwargs):
        raise RuntimeError("boot failed")

    monkeypatch.setattr(_sandbox.Sandbox, "create", _fail)

    with pytest.raises(RuntimeError):
        await _sandbox.run_python("print('hi')", timeout=5)

    assert len(fake_vm.removed) == 1
    assert fake_vm.removed[0].startswith("nexctf-")


async def test_concurrent_runs_are_capped(monkeypatch, fake_vm) -> None:
    """Nothing else limits how many microVMs exist at once — each is a vCPU plus
    _MEMORY_MIB, and rate limiting is per user, not per host."""
    live = 0
    peak = 0

    class _SlowSandbox(_FakeSandbox):
        async def shell(self, cmd, *, stdin=None, timeout=None):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0)
            live -= 1
            return await super().shell(cmd, stdin=stdin, timeout=timeout)

    async def _fake_create(name, **kwargs):
        return _SlowSandbox()

    monkeypatch.setattr(_sandbox.Sandbox, "create", _fake_create)
    monkeypatch.setattr(_sandbox, "_SLOTS", asyncio.Semaphore(2))

    await asyncio.gather(*(_sandbox.run_python("x", timeout=5) for _ in range(10)))

    assert peak == 2


async def test_payloads_are_never_logged(monkeypatch, caplog, fake_vm) -> None:
    """For script solutions the checker source is where the flag lives, and a checker
    that raises echoes it onto stderr — so payloads are not logged at any level."""

    async def _shell(cmd, *, stdin=None, timeout=None):
        return _FakeShellResult(0, "FLAG{secret}", "FLAG{secret}")

    monkeypatch.setattr(fake_vm, "shell", _shell)

    with caplog.at_level(logging.DEBUG, logger=_sandbox.__name__):
        await _sandbox.run_python("print('hi')", timeout=5)

    assert "FLAG{secret}" not in caplog.text
