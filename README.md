# NexCTF Sandbox

A [NexCTF](https://github.com/NexCTF/NexCTF) plugin that checks answers by running Python: a code runner that tests players' programs against test cases, and a script checker that validates answers with a function you write. Every run happens in a throwaway [microsandbox](https://github.com/superradcompany/microsandbox) microVM, booted for the submission and removed right after.

[![CI](https://github.com/NexCTF/nexctf-sandbox/actions/workflows/ci.yml/badge.svg)](https://github.com/NexCTF/nexctf-sandbox/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/nexctf-sandbox.svg)](https://pypi.org/project/nexctf-sandbox/)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Python 3.15](https://img.shields.io/badge/python-3.15-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Requirements

- NexCTF 0.12.5 or a later 0.12.x, see [Compatibility](#compatibility)
- A Linux host with KVM: the NexCTF process that checks submissions needs read and write access to `/dev/kvm`

## Quick Start

Add the plugin to NexCTF's `NEXCTF_PLUGINS` in its `.env`, along with the host's `kvm` group id; NexCTF installs the plugin from PyPI and applies its migrations on start:

```bash
NEXCTF_PLUGINS=nexctf-sandbox==1.1.0
KVM_GID=993  # stat -c %g /dev/kvm
```

The NexCTF process that checks submissions boots the microVMs, so it needs read and write access to `/dev/kvm`. Nothing else: the network setting is enforced by microsandbox's own network stack, with no extra capability or root.

With NexCTF's `compose.yml`, that goes on the `app` service, e.g. in a `compose.override.yml`. `/dev/kvm` keeps the host's `root:kvm` ownership and `0660` mode inside the container, so the image's unprivileged user has to join the `kvm` group:

```yaml
services:
  app:
    devices:
      - /dev/kvm
    group_add:
      - "${KVM_GID:?set KVM_GID to the host's kvm group id}"
```

Restart NexCTF, then, on a question, pick the solution type **runner** or **script**.

## Features

### 🏃 Code runner
- Runs the player's Python 3 code against up to 20 test cases, feeding each one's input on stdin
- Accepted only when every case's stdout matches the expected output, whitespace stripped
- A crash or a non-zero exit fails the case
- All the cases of a submission share one microVM, run one after the other
- For questions with a **code** input

### ✅ Script checker
- Validates the answer with your own Python function, `check(answer, team_id, team_fields) -> bool`
- Hands the checker the team's custom fields, typed, private ones included: per-team flags and seeds work out of the box
- The starter checker lists the team custom fields that exist
- Checkers written without `team_fields` keep working as they are
- For questions with an **input**, **text** or **code** input

A run that outlasts its timeout is not counted as a wrong answer: the player is told the check timed out and can submit again, and the admin event feed records a `solution.timeout`.

## Writing a checker

The checker is a function named `check` that returns `True` to accept the answer. `team_id` is the team's id as a string, or `None`; `team_fields` maps each team custom field name to its value (`int`, `bool` or `str`), and a key is missing when the team left the field empty. Declare `team_fields` only if you need it:

```python
import hashlib


def check(answer: str, team_id: str | None, team_fields: dict) -> bool:
    seed = team_fields.get("seed", "")
    flag = "FLAG{" + hashlib.sha256(seed.encode()).hexdigest()[:16] + "}"
    return answer.strip() == flag
```

The checker runs in its own microVM, with the base image's Python and the network access set below.

## Settings

Under **Settings → Plugins → Sandbox**:

| Setting | Key | Default |
|---|---|---|
| Base image | `base_image` | `python:3.14-slim`; any OCI image with `python3` on `PATH` |
| vCPUs | `cpus` | 1 |
| Memory | `memory_mib` | 256 MiB, plus the root disk |
| Root disk | `root_disk_mib` | 64 MiB, in RAM |
| Network access | `network_access` | `disabled`; `internet` for public addresses and the host's DNS, `all` for unfiltered egress, the host's own network included |
| Max concurrent microVMs | `max_concurrent` | 8 per NexCTF API worker process, across all challenges, so `WORKERS` × 8 in all; takes effect after a restart |

The vCPUs, memory, root disk and max concurrent microVMs settings must be at least 1.

Each setting can also come from NexCTF's environment, as `NEXCTF_NEXCTF_SANDBOX_` followed by its key in capitals, e.g. `NEXCTF_NEXCTF_SANDBOX_BASE_IMAGE=python:3.13-slim`. A value saved under Settings takes precedence over it.
