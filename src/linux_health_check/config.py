"""Configuration: defaults, loading from JSON and strict validation.

Unknown keys are rejected so that a typo ("crti") fails loudly instead of
silently falling back to a default threshold.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .rules import parse_port_spec

DEFAULTS: dict = {
    "cpu": {
        "sample_seconds": 0.5,
        "usage_warn": 85,
        "usage_crit": 95,
        "load_per_core_warn": 1.0,
        "load_per_core_crit": 2.0,
    },
    "memory": {"warn": 85, "crit": 95, "swap_warn": 50, "swap_crit": 80},
    "disk": {
        "space_warn": 80,
        "space_crit": 90,
        "inode_warn": 80,
        "inode_crit": 90,
        "exclude_fstypes": [
            "autofs",
            "binfmt_misc",
            "bpf",
            "cgroup",
            "cgroup2",
            "configfs",
            "debugfs",
            "devpts",
            "devtmpfs",
            "efivarfs",
            "fusectl",
            "hugetlbfs",
            "mqueue",
            "nsfs",
            "overlay",
            "proc",
            "pstore",
            "ramfs",
            "rpc_pipefs",
            "securityfs",
            "squashfs",
            "sysfs",
            "tmpfs",
            "tracefs",
            "fuse.portal",
            "fuse.gvfsd-fuse",
        ],
        "exclude_mount_prefixes": ["/snap/", "/var/lib/docker/", "/var/lib/containers/"],
    },
    "services": {"units": [], "not_found": "warning"},
    "ports": {"expected": [], "allowed": None},
    "boot": {"recent_boot_warn_minutes": 15, "max_uptime_days": None},
}

# (section, warn key, crit key, maximum value or None)
THRESHOLD_PAIRS = [
    ("cpu", "usage_warn", "usage_crit", 100),
    ("cpu", "load_per_core_warn", "load_per_core_crit", None),
    ("memory", "warn", "crit", 100),
    ("memory", "swap_warn", "swap_crit", 100),
    ("disk", "space_warn", "space_crit", 100),
    ("disk", "inode_warn", "inode_crit", 100),
]


class ConfigError(Exception):
    pass


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _merge(base: dict, override: dict, where: str = "") -> None:
    for key, value in override.items():
        if key not in base:
            raise ConfigError(f"unknown key '{where}{key}'")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"'{where}{key}' must be an object")
            _merge(base[key], value, f"{where}{key}.")
        else:
            base[key] = value


def _check_string_list(value: Any, name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"'{name}' must be a list of strings")


def validate(cfg: dict) -> None:
    for section, warn_key, crit_key, maximum in THRESHOLD_PAIRS:
        values = {}
        for key in (warn_key, crit_key):
            value = cfg[section][key]
            name = f"{section}.{key}"
            if value is None:
                continue
            if not _is_number(value) or value < 0:
                raise ConfigError(f"'{name}' must be a non-negative number or null")
            if maximum is not None and value > maximum:
                raise ConfigError(f"'{name}' must be at most {maximum}")
            values[key] = value
        if len(values) == 2 and values[warn_key] > values[crit_key]:
            raise ConfigError(
                f"'{section}.{warn_key}' must not be greater than '{section}.{crit_key}'"
            )

    sample = cfg["cpu"]["sample_seconds"]
    if not _is_number(sample) or not 0 <= sample <= 10:
        raise ConfigError("'cpu.sample_seconds' must be a number between 0 and 10")

    _check_string_list(cfg["disk"]["exclude_fstypes"], "disk.exclude_fstypes")
    _check_string_list(cfg["disk"]["exclude_mount_prefixes"], "disk.exclude_mount_prefixes")
    _check_string_list(cfg["services"]["units"], "services.units")
    if cfg["services"]["not_found"] not in ("ok", "warning", "critical"):
        raise ConfigError("'services.not_found' must be one of: ok, warning, critical")

    _check_string_list(cfg["ports"]["expected"], "ports.expected")
    if cfg["ports"]["allowed"] is not None:
        _check_string_list(cfg["ports"]["allowed"], "ports.allowed")
    for spec in cfg["ports"]["expected"] + (cfg["ports"]["allowed"] or []):
        try:
            parse_port_spec(spec)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None

    boot = cfg["boot"]
    for key in ("recent_boot_warn_minutes", "max_uptime_days"):
        value = boot[key]
        if value is not None and (not _is_number(value) or value <= 0):
            raise ConfigError(f"'boot.{key}' must be a positive number or null")


def load_config(path: str | None = None) -> dict:
    """Return the defaults, overridden by the JSON file at path if given."""
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from None
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise ConfigError(f"{path} must contain a JSON object")
        _merge(cfg, data)
    validate(cfg)
    return cfg
