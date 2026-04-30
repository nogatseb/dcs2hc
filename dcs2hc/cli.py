"""Command-line interface for building HC aircraft library JSON files."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import BuildReport, build_from_directory, library_to_dict


def _print_report(report: BuildReport, lib_dict: dict, *, verbose: bool) -> None:
    n_cmds = len(lib_dict.get("commands", []))
    n_axes = len(lib_dict.get("axes", []))
    print(f"  commands: {n_cmds}")
    print(f"  axes:     {n_axes}")
    if report.skipped_clickables:
        print(f"  skipped clickables: {report.skipped_clickables}")
    if report.cockpit_only_in_default:
        print(f"  cockpit commands added from default.lua: {report.cockpit_only_in_default}")
    if report.unknown_helpers:
        items = sorted(report.unknown_helpers.items(), key=lambda kv: -kv[1])
        print(f"  unknown helpers ({len(items)}):")
        for name, count in items[:20]:
            print(f"    {name}: {count}")
    if report.duplicate_pairs:
        print(f"  duplicate (deviceId, commandCode) pairs: {len(report.duplicate_pairs)}")
        if verbose:
            for dev, code, a, b in report.duplicate_pairs[:20]:
                print(f"    dev={dev} code={code}: '{a}' vs '{b}'")
    if verbose and report.unresolved_command_refs:
        print(f"  unresolved command refs ({len(report.unresolved_command_refs)}):")
        for line in report.unresolved_command_refs[:20]:
            print(f"    {line}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="build_library",
        description="Build a HangarControl aircraft library JSON from DCS Lua source files.",
    )
    p.add_argument("module_dir", type=Path,
                   help="Path to a DCS aircraft module folder (containing Cockpit/Scripts and Input/).")
    p.add_argument("--aircraft-id", help="aircraftId for the output (defaults to module folder name).")
    p.add_argument("--display-name", help="displayName for the output.")
    p.add_argument("--alias", action="append", default=[],
                   help="Alternative aircraft type string (repeatable).")
    p.add_argument("--out-dir", type=Path, default=Path("out"),
                   help="Output directory (default: ./out)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show detailed validation output.")
    args = p.parse_args(argv)

    if not args.module_dir.is_dir():
        print(f"Module directory not found: {args.module_dir}", file=sys.stderr)
        return 2

    print(f"Building library for {args.module_dir.name}…")
    lib, report = build_from_directory(
        args.module_dir,
        aircraft_id=args.aircraft_id,
        display_name=args.display_name,
        aliases=args.alias,
    )
    out_dict = library_to_dict(lib)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{lib.aircraftId}.json"
    out_path.write_text(json.dumps(out_dict, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"Wrote {out_path}")
    _print_report(report, out_dict, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
