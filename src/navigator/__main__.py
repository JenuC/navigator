"""Entry point.

Simulated (default)
-------------------
    uv run navigator

Real hardware via Micro-Manager 2
----------------------------------
    uv run navigator --hardware [--mm-settings path/to/settings.yaml]

    Requires:
      * Micro-Manager 2 open with the Python bridge enabled.
      * pycromanager installed: uv pip install -e ".[hardware]"

BH SPC-180NX photon counting
-----------------------------
    uv run navigator --spc            (hardware)
    uv run navigator --spc-sim        (SPCM-DLL simulation)

    Requires:
      * SPCM-DLL installed (BH SPCM software).
      * pybhspc installed: uv pip install -e ".[spc]"
    Flags may be combined with --hardware.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from imgui_bundle import immapp

from .app import App


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="navigator",
        description="XY(Z) stage navigator GUI",
    )
    p.add_argument(
        "--hardware",
        action="store_true",
        default=False,
        help="Connect to Micro-Manager 2 via pycromanager instead of simulation",
    )
    p.add_argument(
        "--mm-settings",
        metavar="PATH",
        default=None,
        help=(
            "Path to a microscope_control settings YAML file "
            "(optional; supplies z_stage name, axis limits, etc.)"
        ),
    )
    spc_grp = p.add_mutually_exclusive_group()
    spc_grp.add_argument(
        "--spc",
        action="store_true",
        default=False,
        help="Connect to BH SPC-180NX via pybhspc (hardware)",
    )
    spc_grp.add_argument(
        "--spc-sim",
        action="store_true",
        default=False,
        help="Use SPCM-DLL simulation of SPC-180NX (no hardware needed)",
    )
    p.add_argument(
        "--spc-ini",
        metavar="PATH",
        default=None,
        help=(
            "Path to a SPCM .ini file for SPC-180NX initialisation "
            "(e.g. spcm_SLIM.ini); if omitted, a minimal default is used"
        ),
    )
    return p


def _load_settings(path: str | None) -> dict:
    if path is None:
        return {}
    import yaml  # pycromanager install pulls in pyyaml transitively
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _connect_hardware(args: argparse.Namespace):
    """Return a PycroStage connected to MM2, or exit on failure."""
    try:
        from pycromanager import Core
    except ImportError:
        print(
            "[navigator] pycromanager is not installed.\n"
            "  Run:  uv pip install -e \".[hardware]\"\n"
            "  then try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    settings = _load_settings(args.mm_settings)

    print("[navigator] Connecting to Micro-Manager 2 …", flush=True)
    try:
        core = Core()
    except Exception as exc:
        print(
            f"[navigator] Failed to connect to MM2: {exc}\n"
            "  Is Micro-Manager 2 running with the Python bridge enabled?",
            file=sys.stderr,
        )
        sys.exit(1)

    from .pycro_stage import PycroStage
    stage = PycroStage(core, settings)
    x, y, z = stage.position
    print(f"[navigator] Connected.  Stage position: X={x:.1f}  Y={y:.1f}  Z={z:.2f} µm")
    return stage


def _connect_spc(args: argparse.Namespace):
    """Return an SPCModule, or exit on failure."""
    try:
        from .spc_module import SPCModule
    except ImportError as exc:
        print(
            f"[navigator] bh_spc not available: {exc}\n"
            "  Run:  uv pip install -e \".[spc]\"\n"
            "  then try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    simulate = args.spc_sim
    label = "sim" if simulate else "hardware"
    ini_path = args.spc_ini
    if ini_path is None and not simulate:
        default_ini = Path("spcm_SLIM.ini")
        if default_ini.exists():
            ini_path = default_ini
            print(f"[navigator] Using default SPC ini: {default_ini.resolve()}", flush=True)
    print(f"[navigator] Initializing SPC-180NX ({label}) …", flush=True)
    try:
        spc = SPCModule(mod_no=0, simulate=simulate, ini_path=ini_path)
    except Exception as exc:
        print(f"[navigator] SPC init failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("[navigator] SPC ready.")
    return spc


def main() -> None:
    args = _build_arg_parser().parse_args()

    stage = _connect_hardware(args) if args.hardware else None
    spc = _connect_spc(args) if (args.spc or args.spc_sim) else None
    app = App(stage=stage, spc=spc)

    title_parts = ["Stage Navigator"]
    title_parts.append("[MM2]" if args.hardware else "[stage-sim]")
    if spc is not None:
        title_parts.append("[SPC]" if args.spc else "[SPC-sim]")
    immapp.run(
        gui_function=app.render,
        window_title=" ".join(title_parts),
        window_size=(1400, 900),
        fps_idle=60,
    )

    # Clean shutdown — stop background threads before the interpreter finalizes
    if hasattr(stage, "shutdown"):
        stage.shutdown()
    if spc is not None:
        spc.shutdown()


if __name__ == "__main__":
    main()
