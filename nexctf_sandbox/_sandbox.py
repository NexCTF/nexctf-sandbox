"""The sandbox settings, and the microsandbox helper solutions run untrusted code with."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from functools import partial
from uuid import uuid4

from microsandbox import (
    Image,
    Network,
    NetworkProfile,
    RootDisk,
    Sandbox,
)
from nexctf.core import appconfig
from nexctf.plugins import ConfigCategory, ConfigDef, ConfigType

logger = logging.getLogger(__name__)

NETWORK_DISABLED = "disabled"  # no egress at all
NETWORK_INTERNET = "internet"  # public addresses and DNS, but nothing host-side
NETWORK_ALL = "all"  # unfiltered, including the host's own network
NETWORK_ACCESS_CHOICES = [NETWORK_DISABLED, NETWORK_INTERNET, NETWORK_ALL]

CONFIG = ConfigCategory(
    "Sandbox",
    (
        ConfigDef(
            key="base_image",
            label="Base image",
            default="python:3.14-slim",
            type=ConfigType.STRING,
            description="OCI image every microVM boots from. It must provide python3 on PATH.",
        ),
        ConfigDef(
            key="cpus",
            label="vCPUs",
            default=1,
            min=1,
            description="Virtual CPUs given to each microVM.",
        ),
        ConfigDef(
            key="memory_mib",
            label="Memory (MiB)",
            default=256,
            min=1,
            description="Guest RAM for each microVM. The root disk is added on top of this.",
        ),
        ConfigDef(
            key="root_disk_mib",
            label="Root disk (MiB)",
            default=64,
            min=1,
            description="Size of the tmpfs root disk. It is RAM-backed, so it is charged to guest memory.",
        ),
        ConfigDef(
            key="network_access",
            label="Network access",
            default=NETWORK_DISABLED,
            type=ConfigType.CHOICE,
            choices=NETWORK_ACCESS_CHOICES,
            description=(
                "'disabled': no egress at all.\n"
                "'internet': public addresses, plus the host's DNS resolver on port 53.\n"
                "'all': unfiltered, including everything on the host's own network."
            ),
        ),
        ConfigDef(
            key="max_concurrent",
            label="Max concurrent microVMs",
            default=8,
            min=1,
            description="Ceiling on microVMs running at once, across all challenges. Takes effect after a restart.",
        ),
    ),
    icon="box",
)

type ConfigValue = str | int | float | bool

_DEFAULTS: frozendict[str, ConfigValue] = frozendict(
    {def_.key: def_.default for def_ in CONFIG.defs}
)

_MAX_OUTPUT_BYTES = 64 * 1024
_RUN = (
    f"python3 /code.py >/out 2>/err; rc=$?; "
    f"head -c {_MAX_OUTPUT_BYTES} /out; head -c {_MAX_OUTPUT_BYTES} /err >&2; exit $rc"
)
_SLOTS: asyncio.Semaphore | None = None

MAX_PAYLOAD_CHARS = _MAX_OUTPUT_BYTES
MIN_TIMEOUT = 1
MAX_TIMEOUT = 30


def _slots(limit: int) -> asyncio.Semaphore:
    """Return the process-wide VM slot semaphore, building it on first use."""
    global _SLOTS
    if _SLOTS is None:
        _SLOTS = asyncio.Semaphore(limit)
    return _SLOTS


async def _settings() -> dict[str, ConfigValue]:
    """Resolve the admin-set VM settings, falling back to ``_DEFAULTS``.

    The host validates every override and falls back per key on its own; what
    can still fail is reaching it at all: Redis down, or no host settings to
    build a client from, as in the unit tests.
    """
    try:
        from nexctf.core.cache import get_client

        overrides = await appconfig.fetch_overrides(get_client())
    except Exception:
        logger.warning("sandbox.config fetch failed; using defaults", exc_info=True)
        return dict(_DEFAULTS)
    return {key: CONFIG.get(key, overrides) for key in _DEFAULTS}


def _network(access: ConfigValue) -> Network:
    """Build the guest's egress policy from the ``network_access`` setting."""
    if access == NETWORK_INTERNET:
        return Network.from_profiles(NetworkProfile.PUBLIC)
    if access == NETWORK_ALL:
        return Network.allow_all()
    if access != NETWORK_DISABLED:
        logger.warning("sandbox.network unknown access=%r; denying egress", access)
    return Network.none()


@asynccontextmanager
async def _ephemeral(cfg: dict[str, ConfigValue]):
    network = _network(cfg["network_access"])
    root_disk_mib = int(cfg["root_disk_mib"])
    name = f"nexctf-{uuid4().hex}"
    sb = None
    try:
        sb = await Sandbox.create(
            name,
            image=Image.oci(
                str(cfg["base_image"]), root_disk=RootDisk.tmpfs(root_disk_mib)
            ),
            cpus=int(cfg["cpus"]),
            memory=int(cfg["memory_mib"]) + root_disk_mib,
            network=network,
        )
        yield sb
    finally:
        try:
            if sb is not None:
                await sb.destroy(force=True)
            else:
                await Sandbox.remove(name)
        except Exception:
            logger.warning("sandbox.destroy failed name=%s", name, exc_info=True)


async def _exec(sb, code: str, stdin: str = "", *, timeout: int) -> tuple[int, str]:
    """Run *code* in an already-booted microVM. Raises ``ExecTimeoutError`` on timeout."""
    logger.info("sandbox.start stdin=%s timeout=%ds", bool(stdin), timeout)
    await sb.fs.write("/code.py", code.encode())
    result = await sb.shell(
        _RUN,
        stdin=stdin.encode() if stdin else None,
        timeout=float(timeout),
    )
    out = result.stdout_text
    logger.info(
        "sandbox.run exit_code=%d out=%dB err=%dB",
        result.exit_code,
        len(out),
        len(result.stderr_text),
    )
    return result.exit_code, out


@asynccontextmanager
async def python_runner():
    """Yield ``run(code, stdin, *, timeout) -> (exit_code, stdout)`` on one microVM."""
    cfg = await _settings()
    async with _slots(int(cfg["max_concurrent"])), _ephemeral(cfg) as sb:
        yield partial(_exec, sb)


async def run_python(code: str, stdin: str = "", *, timeout: int) -> tuple[int, str]:
    """Run Python *code* in its own isolated microVM.

    Returns ``(exit_code, stdout)``. Raises ``ExecTimeoutError`` on timeout.
    """
    async with python_runner() as run:
        return await run(code, stdin, timeout=timeout)
