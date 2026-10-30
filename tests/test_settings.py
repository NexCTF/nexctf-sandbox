"""Tests for the admin-tunable microVM settings."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from nexctf_sandbox import _sandbox

from .conftest import _settings_returning


async def test_defaults_are_used_when_the_host_config_is_unreachable(
    fake_vm,
) -> None:
    """Outside the host app there is no Redis to read settings from — boot anyway."""
    await _sandbox.run_python("print('hi')", timeout=5)

    defaults = _sandbox._DEFAULTS
    memory = int(defaults["memory_mib"]) + int(defaults["root_disk_mib"])
    assert fake_vm.boot_kwargs["cpus"] == defaults["cpus"]
    assert fake_vm.boot_kwargs["memory"] == memory


async def test_admin_settings_reach_the_vm(monkeypatch, fake_vm) -> None:
    monkeypatch.setattr(
        _sandbox,
        "_settings",
        _settings_returning(cpus=4, memory_mib=512, root_disk_mib=128),
    )

    await _sandbox.run_python("print('hi')", timeout=5)

    assert fake_vm.boot_kwargs["cpus"] == 4
    assert (
        fake_vm.boot_kwargs["memory"] == 512 + 128
    )  # guest RAM plus the tmpfs root disk


async def test_root_disk_is_never_dropped_from_the_memory_budget(
    monkeypatch, fake_vm
) -> None:
    """Regression guard: ``memory=`` is guest RAM *plus* the RAM-backed root disk.

    Passing ``memory_mib`` straight through would under-provision the VM and OOM
    it the moment an admin raises the root disk.
    """
    monkeypatch.setattr(
        _sandbox, "_settings", _settings_returning(memory_mib=256, root_disk_mib=512)
    )

    await _sandbox.run_python("print('hi')", timeout=5)

    assert fake_vm.boot_kwargs["memory"] == 256 + 512


async def test_base_image_is_applied(monkeypatch, fake_vm) -> None:
    monkeypatch.setattr(
        _sandbox, "_settings", _settings_returning(base_image="python:3.13-slim")
    )

    await _sandbox.run_python("print('hi')", timeout=5)

    assert "python:3.13-slim" in repr(fake_vm.boot_kwargs["image"])


async def test_the_vm_has_no_egress_by_default(monkeypatch, fake_vm) -> None:
    """Submitted code is untrusted, so the network is off until an admin opens it."""
    monkeypatch.setattr(_sandbox, "_settings", _settings_returning())

    await _sandbox.run_python("print('hi')", timeout=5)

    assert fake_vm.boot_kwargs["network"] == _sandbox.Network.none()


async def test_internet_access_reaches_the_public_internet_only(
    monkeypatch, fake_vm
) -> None:
    """'internet' must not hand submitted code the host's own network: Redis and
    the Postgres holding the flags sit on it. Only ``public`` (plus DNS) is
    granted — that is what separates it from 'all'."""
    monkeypatch.setattr(
        _sandbox,
        "_settings",
        _settings_returning(network_access=_sandbox.NETWORK_INTERNET),
    )

    await _sandbox.run_python("print('hi')", timeout=5)

    policy = fake_vm.boot_kwargs["network"]._to_dict()["custom_policy"]
    assert policy["default_egress"] == "deny"  # 'all' would make this "allow"
    # The DNS rules are pinned to port 53 on the host; the rest is the real grant.
    granted = {r["destination"] for r in policy["rules"] if r.get("port") != "53"}
    assert granted == {"public"}


async def test_all_access_is_unfiltered(monkeypatch, fake_vm) -> None:
    """The escape hatch, for an admin who wants the guest on the host's network."""
    monkeypatch.setattr(
        _sandbox, "_settings", _settings_returning(network_access=_sandbox.NETWORK_ALL)
    )

    await _sandbox.run_python("print('hi')", timeout=5)

    assert fake_vm.boot_kwargs["network"] == _sandbox.Network.allow_all()


def test_every_choice_maps_to_a_policy() -> None:
    """The ConfigDef offers these three, so each has to resolve to its own policy —
    a value with no branch would fall through to the deny default silently."""
    policies = [_sandbox._network(a) for a in _sandbox.NETWORK_ACCESS_CHOICES]

    # Network holds a dict, so it is unhashable — compare on the repr instead.
    assert len({repr(p) for p in policies}) == len(_sandbox.NETWORK_ACCESS_CHOICES)
    assert _sandbox._network(_sandbox.NETWORK_DISABLED) == _sandbox.Network.none()


def test_an_unknown_access_value_denies_rather_than_widens() -> None:
    """Not reachable through an override, but if a choice is ever added without
    its branch it must fail closed."""
    assert _sandbox._network("everything") == _sandbox.Network.none()


def _override(key: str, value: str) -> dict[str, str]:
    """A stored override for *key*, under the key the host registered it at."""
    return {f"{_sandbox.CONFIG.key}.{key}": value}


def test_a_bad_override_never_reaches_the_policy() -> None:
    """The guard the branch above backs up: the host rejects a non-choice override
    and hands back the code default instead of the raw string."""
    resolved = _sandbox.CONFIG.get(
        "network_access", _override("network_access", "yes-please")
    )

    assert resolved == _sandbox.NETWORK_DISABLED


@pytest.mark.parametrize(
    "key", ["cpus", "memory_mib", "root_disk_mib", "max_concurrent"]
)
def test_a_zero_size_never_reaches_the_vm(key: str) -> None:
    """0 vCPUs or 0 MiB cannot boot, and 0 slots blocks every submission forever,
    so the host refuses an override below 1 and resolves the code default instead."""
    resolved = _sandbox.CONFIG.get(key, _override(key, "0"))

    assert resolved == _sandbox._DEFAULTS[key]


def test_max_concurrent_sizes_the_semaphore(monkeypatch) -> None:
    """The semaphore is built once, from the setting, on first use."""
    monkeypatch.setattr(_sandbox, "_SLOTS", None)

    assert _sandbox._slots(3)._value == 3
    # Built once: a later, different limit does not resize it. This is why the
    # setting is documented as taking effect only after a restart.
    assert _sandbox._slots(9)._value == 3


def _install_fake_host(monkeypatch, *, fetch_overrides) -> None:
    """Stand in for Redis: a client that is never used, and *fetch_overrides*."""
    monkeypatch.setitem(
        sys.modules, "nexctf.core.cache", SimpleNamespace(get_client=lambda: object())
    )
    monkeypatch.setattr(_sandbox.appconfig, "fetch_overrides", fetch_overrides)


async def test_stored_overrides_reach_the_settings(monkeypatch) -> None:
    async def _stored(_redis) -> dict:
        return _override("cpus", "4")

    _install_fake_host(monkeypatch, fetch_overrides=_stored)

    assert await _sandbox._settings() == dict(_sandbox._DEFAULTS) | {"cpus": 4}


async def test_a_redis_failure_falls_back_to_the_defaults(monkeypatch) -> None:
    """A transient outage must not fail the submission."""

    async def _boom(_redis) -> dict:
        raise ConnectionError("redis is down")

    _install_fake_host(monkeypatch, fetch_overrides=_boom)

    assert await _sandbox._settings() == dict(_sandbox._DEFAULTS)


async def test_a_redis_failure_keeps_the_env_settings(monkeypatch) -> None:
    """The host resolves ENV under the stored overrides; losing Redis must not
    drop an operator's NEXCTF_* setting, such as a pinned base image."""

    async def _boom(_redis) -> dict:
        raise ConnectionError("redis is down")

    _install_fake_host(monkeypatch, fetch_overrides=_boom)
    env_key = f"NEXCTF_{_sandbox.CONFIG.key}.base_image".upper().replace(".", "_")
    monkeypatch.setenv(env_key, "python:3.13-slim")

    assert (await _sandbox._settings())["base_image"] == "python:3.13-slim"


def test_the_timeout_and_payload_limits_are_not_settings() -> None:
    """They are baked into DB CHECK constraints and Pydantic bounds at import time,
    so they must not become runtime settings — Postgres would reject the value."""
    assert not {"timeout", "max_timeout", "min_timeout", "max_output_bytes"} & set(
        _sandbox._DEFAULTS
    )


def test_base_image_is_registered_as_a_plain_string() -> None:
    """A str default cannot be inferred, so the ConfigDef must say so explicitly."""
    from nexctf.core.appconfig import get_def
    from nexctf.plugins import ConfigType

    def_ = get_def(f"{_sandbox.CONFIG.key}.base_image")
    assert def_.type is ConfigType.STRING


async def test_every_access_setting_boots_with_its_policy(monkeypatch, fake_vm) -> None:
    """microsandbox enforces the policy in its own userspace network stack, so no
    setting depends on a host capability — each one must boot, with its own policy.
    The plugin used to refuse 'disabled' and 'internet' without CAP_NET_ADMIN."""
    for access in _sandbox.NETWORK_ACCESS_CHOICES:
        monkeypatch.setattr(
            _sandbox, "_settings", _settings_returning(network_access=access)
        )

        await _sandbox.run_python("print('hi')", timeout=5)

        assert fake_vm.boot_kwargs["network"] == _sandbox._network(access)
