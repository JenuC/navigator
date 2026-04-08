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
"""

from __future__ import annotations

import argparse
import sys

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


def main() -> None:
    args = _build_arg_parser().parse_args()

    stage = _connect_hardware(args) if args.hardware else None
    app = App(stage=stage)

    immapp.run(
        gui_function=app.render,
        window_title="Stage Navigator" + (" [MM2]" if args.hardware else " [sim]"),
        window_size=(1400, 900),
        fps_idle=60,
    )

    # Clean shutdown — stop background threads before the interpreter finalizes
    if hasattr(stage, "shutdown"):
        stage.shutdown()


if __name__ == "__main__":
    main()
