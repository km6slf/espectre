# SPDX-License-Identifier: GPL-3.0-only
# Commercial licensing available under separate agreement; see LICENSING.md.
"""
ESPectre - CLI ESPHome

ESPHome frontend wrappers.

Author: Francesco Pace <francesco.pace@gmail.com>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .build_artifacts import print_build_artifact_metadata
from .common import (
    Fore,
    REPO_ROOT,
    Style,
    resolve_serial_port,
)
from .idf import flash_factory_image, flash_prebuilt_idf_build
from .targets import IDF_TARGET_BY_CHIP, resolve_esphome_config

ACTION_MAP = {
    "build": "compile",
    "flash": "upload",
    "config": "config",
    "monitor": "logs",
}

ESPHOME_COMMAND_PREFIX = [
    "esphome",
    "--toolchain",
    "esp-idf",
    "-s",
    "component_source",
    "local",
]


def detect_espectre_project_version() -> str:
    """Resolve the canonical firmware version through the shared repository script."""
    version_script = REPO_ROOT / ".github" / "scripts" / "detect_git_version.py"
    try:
        process = subprocess.Popen(
            [sys.executable, str(version_script)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        stdout, _ = process.communicate()
    except OSError:
        return ""
    return stdout.strip() if process.returncode == 0 else ""


def esphome_build_root(config_path: Path) -> Path:
    """Return the config-specific root passed to ESPHome for local builds."""
    return config_path.parent / ".esphome" / "build" / config_path.stem


def esphome_command_environment(config_path: Path) -> dict[str, str]:
    """Isolate each repository config while preserving the caller environment."""
    environment = os.environ.copy()
    environment["ESPHOME_BUILD_PATH"] = str(esphome_build_root(config_path))
    if not environment.get("ESPECTRE_GIT_VERSION"):
        version = detect_espectre_project_version()
        if version:
            environment["ESPECTRE_GIT_VERSION"] = version
    return environment


def resolve_esphome_build_artifact(config_path: Path) -> Path:
    """Return the application image produced for an ESPHome config."""
    storage_path = config_path.parent / ".esphome" / "storage" / f"{config_path.name}.json"
    try:
        storage = json.loads(storage_path.read_text(encoding="utf-8"))
        build_path = storage.get("build_path")
        if isinstance(build_path, str) and build_path:
            stored_artifact = Path(build_path) / "build" / "espectre.bin"
            if stored_artifact.is_file():
                return stored_artifact
    except (OSError, ValueError, json.JSONDecodeError):
        pass

    candidates = [
        path
        for path in (config_path.parent / ".esphome" / "build").rglob("build/espectre.bin")
        if path.is_file()
    ]
    if not candidates:
        raise FileNotFoundError(
            f"ESPHome build artifact not found for {config_path}"
        )
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _is_network_device(device: str | None) -> bool:
    """Return whether --device names a hostname, IP address, or URL rather than a serial port."""
    if not device:
        return False
    if device.startswith("/") or device.lower().startswith("com"):
        return False
    return True


def probe_flash_manufacturer(port: str) -> int | None:
    """Probe the SPI flash manufacturer ID byte on a serial port."""
    try:
        import esptool
        esp = esptool.cmds.detect_chip(port)
        flash_id = esp.flash_id()
        esp.hard_reset()
        return (flash_id >> 16) & 0xFF
    except Exception:
        return None


def run_esphome_command(args) -> None:
    """Run an ESPHome action against the resolved repository config."""
    device = getattr(args, "device", None)
    if getattr(args, "chip", None) in {"s3", None} and getattr(args, "config", None) is None and not _is_network_device(device):
        try:
            probe_port = resolve_serial_port(device, chip=args.chip or "s3", frontend="esphome", purpose="probe")
            if probe_port:
                mfg = probe_flash_manufacturer(probe_port)
                if mfg == 0xC2:
                    print(f"{Fore.YELLOW}ℹ️  Detected Macronix MX25L SPI flash (0xC2). Auto-selecting s3-macronix profile (40MHz DIO / internal SRAM).{Style.RESET_ALL}")
                    args.chip = "s3-macronix"
        except Exception:
            pass

    try:
        config_path = resolve_esphome_config(args.chip, args.config)
    except ValueError as e:
        print(f"{Fore.RED}❌ {e}{Style.RESET_ALL}")
        raise SystemExit(1)

    if not config_path.exists():
        print(f"{Fore.RED}❌ ESPHome config not found: {config_path}{Style.RESET_ALL}")
        raise SystemExit(1)

    action = ACTION_MAP[args.esphome_command]
    commands: list[list[str]] = []
    if args.esphome_command == "build":
        if getattr(args, "clean_all", False):
            commands.append([*ESPHOME_COMMAND_PREFIX, "clean-all", str(config_path)])
        elif getattr(args, "clean", False):
            commands.append([*ESPHOME_COMMAND_PREFIX, "clean", str(config_path)])

    command = [*ESPHOME_COMMAND_PREFIX, action, str(config_path)]
    device = getattr(args, "device", None)
    if args.esphome_command in {"flash", "monitor"} and not _is_network_device(device):
        if args.esphome_command == "flash":
            if args.chip is None:
                print(f"{Fore.RED}❌ --chip is required for serial flash.{Style.RESET_ALL}")
                raise SystemExit(1)
            chip = args.chip
            device = resolve_serial_port(
                device,
                chip=chip,
                frontend="esphome",
                purpose="flash",
            )
        else:
            device = resolve_serial_port(
                device,
                chip=getattr(args, "chip", None),
                frontend="esphome",
                purpose="monitor",
            )
    if args.esphome_command == "flash" and not _is_network_device(device):
        chip = args.chip
        try:
            if getattr(args, "firmware", None):
                flash_factory_image(
                    Path(args.firmware).resolve(),
                    device,
                    IDF_TARGET_BY_CHIP[chip],
                    chip=chip,
                    erase=bool(getattr(args, "erase", False)),
                )
            else:
                flash_prebuilt_idf_build(
                    resolve_esphome_build_artifact(config_path).parent,
                    device,
                    IDF_TARGET_BY_CHIP[chip],
                    chip=chip,
                    erase=bool(getattr(args, "erase", False)),
                )
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"{Fore.RED}❌ Error flashing ESPHome firmware: {exc}{Style.RESET_ALL}")
            raise SystemExit(getattr(exc, "returncode", 1)) from exc
        return
    if device:
        command.extend(["--device", device])
    if getattr(args, "firmware", None):
        command.extend(["--file", args.firmware])
    commands.append(command)

    try:
        display_path = config_path.relative_to(REPO_ROOT)
    except ValueError:
        display_path = config_path
    print(f"{Fore.CYAN}Config: {display_path}{Style.RESET_ALL}")
    for command in commands:
        print(f"{Fore.CYAN}Command: {' '.join(command)}{Style.RESET_ALL}")
    try:
        for command in commands:
            subprocess.run(
                command,
                check=True,
                env=esphome_command_environment(config_path),
            )
    except FileNotFoundError:
        print(f"{Fore.RED}❌ esphome not found. Install it in the project environment first.{Style.RESET_ALL}")
        raise SystemExit(1)
    except subprocess.CalledProcessError as e:
        print(f"{Fore.RED}❌ ESPHome command failed with exit code {e.returncode}{Style.RESET_ALL}")
        raise SystemExit(e.returncode)
    if args.esphome_command == "build" and bool(getattr(args, "json", False)):
        try:
            artifact = resolve_esphome_build_artifact(config_path)
            print_build_artifact_metadata(
                frontend="esphome",
                chip=getattr(args, "chip", None),
                artifact=artifact,
            )
        except FileNotFoundError as exc:
            print(f"{Fore.RED}❌ {exc}{Style.RESET_ALL}")
            raise SystemExit(1) from exc
