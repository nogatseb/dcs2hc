"""Combine the parsed Lua data into the HC aircraft library JSON shape."""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .control_types import HELPERS, ICOMMAND_AXES, SPLIT_SUFFIXES, classify_helper
from .parsers import (
    ClickableEntry, CommandDefs, DefaultData, DefaultKeyEntry,
    parse_clickabledata, parse_command_defs, parse_default, parse_devices,
    read_text,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Strip everything that isn't [A-Z0-9_] and uppercase. Used to derive ids.
_ID_STRIP_RE = re.compile(r"[^A-Z0-9_]+")


def _slugify_id(text: str) -> str:
    """Make an HC-style identifier: uppercase, alphanumerics + underscores."""
    s = text.upper().replace(" ", "_").replace("-", "_").replace("/", "_")
    s = _ID_STRIP_RE.sub("", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "UNKNOWN"


def _humanize_device(device_ref: str | None) -> str:
    """Turn 'MFCD_LEFT' into 'Left MFCD'-ish by simple rules. Falls back to
    title-cased original if nothing matches."""
    if not device_ref:
        return "Misc"
    name = device_ref.replace("_", " ")
    # "MFCD LEFT" -> "Left MFCD"
    parts = name.split()
    if len(parts) == 2 and parts[1] in ("LEFT", "RIGHT", "L", "R"):
        return f"{parts[1].title()} {parts[0]}"
    # Title-case but preserve all-caps acronyms ≤4 chars.
    titled = []
    for p in parts:
        if len(p) <= 4 and p.isalpha() and p.isupper():
            titled.append(p)
        else:
            titled.append(p.title())
    return " ".join(titled)


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

@dataclass
class Library:
    aircraftId: str
    displayName: str
    commands: list[dict] = field(default_factory=list)
    axes: list[dict] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass
class BuildReport:
    unknown_helpers: dict[str, int] = field(default_factory=dict)
    unresolved_command_refs: list[str] = field(default_factory=list)
    duplicate_pairs: list[tuple[int, int, str, str]] = field(default_factory=list)
    skipped_clickables: int = 0
    cockpit_only_in_default: int = 0


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_library(
    devices: dict[str, int],
    command_defs: CommandDefs,
    clickables: list[ClickableEntry],
    default_data: DefaultData,
    aircraft_id: str,
    display_name: str,
    aliases: list[str] | None = None,
) -> tuple[Library, BuildReport]:
    report = BuildReport()
    lib = Library(aircraftId=aircraft_id, displayName=display_name, aliases=list(aliases or []))

    # commands keyed by (deviceId, commandCode) so we can dedupe and merge
    # default.lua names/categories into clickabledata-derived entries.
    cmd_index: "OrderedDict[tuple[int, int], dict]" = OrderedDict()

    # Pre-compute a lookup from (deviceId, commandCode) → default entry list,
    # used to enrich category/displayName when clickabledata is sparse.
    default_lookup: dict[tuple[int, int], list[DefaultKeyEntry]] = {}
    for de in default_data.key_entries:
        if de.cockpit_device_id is None:
            continue
        for code in (de.down_code, de.up_code):
            if code is not None:
                default_lookup.setdefault((de.cockpit_device_id, code), []).append(de)

    used_ids: set[str] = set()

    def unique_id(base: str) -> str:
        candidate = base
        i = 2
        while candidate in used_ids:
            candidate = f"{base}_{i}"
            i += 1
        used_ids.add(candidate)
        return candidate

    def insert_command(entry: dict) -> None:
        key = (entry["deviceId"], entry["commandCode"])
        if key in cmd_index:
            existing = cmd_index[key]
            # If the new entry has a release code and existing doesn't, keep
            # the new one (action_button case). Otherwise prefer the existing.
            if entry.get("releaseCommandCode") and not existing.get("releaseCommandCode"):
                cmd_index[key] = entry
            else:
                report.duplicate_pairs.append(
                    (entry["deviceId"], entry["commandCode"],
                     existing.get("displayName", ""), entry.get("displayName", ""))
                )
            return
        cmd_index[key] = entry

    # ----------------- pass 1: clickabledata -----------------
    for ce in clickables:
        spec = classify_helper(ce.helper)
        if spec is None:
            report.unknown_helpers[ce.helper] = report.unknown_helpers.get(ce.helper, 0) + 1
            report.skipped_clickables += 1
            continue
        if ce.device_id is None:
            report.unresolved_command_refs.append(f"{ce.element_name}: device {ce.device_ref}")
            report.skipped_clickables += 1
            continue
        if not ce.command_codes:
            report.unresolved_command_refs.append(f"{ce.element_name}: no command refs ({ce.command_refs})")
            report.skipped_clickables += 1
            continue

        # axis goes to the axes list, not commands
        if spec.is_axis:
            cmd_code = ce.command_codes[0]
            cmd_ref = ce.command_refs[0] if ce.command_refs else None
            base_id = _slugify_id(f"AX_{ce.device_ref or ''}_{cmd_ref or cmd_code}")
            lib.axes.append({
                "id": unique_id(base_id),
                "displayName": ce.display_name or (cmd_ref or "Axis"),
                "category": _humanize_device(ce.device_ref),
                "deviceId": ce.device_id,
                "commandCode": cmd_code,
                "axisType": "cockpit",
                "inverted": False,
            })
            continue

        # action_button: helper signature is (hint, device, cmd_press, cmd_stop, arg, ...)
        # so command_codes[0] is press, command_codes[1] is stop/release.
        if ce.helper == "action_button" and len(ce.command_codes) >= 2:
            press = ce.command_codes[0]
            release = ce.command_codes[1]
            entry = {
                "id": unique_id(_slugify_id(ce.display_name or ce.element_name)),
                "displayName": ce.display_name or ce.element_name,
                "category": _humanize_device(ce.device_ref),
                "deviceId": ce.device_id,
                "commandCode": press,
                "controlType": "momentary",
                "pressValue": 1.0,
                "releaseValue": 0.0,
                "releaseCommandCode": release,
            }
            insert_command(entry)
            continue

        # n_commands == 2 split helpers (button_tumb, springloaded, etc.)
        if spec.n_commands == 2 and len(ce.command_codes) >= 2:
            base_name = ce.display_name or ce.element_name
            for i, cmd_code in enumerate(ce.command_codes[:2]):
                suffix = SPLIT_SUFFIXES[i]
                disp = f"{base_name} {suffix}"
                entry = {
                    "id": unique_id(_slugify_id(f"{base_name}_{suffix}")),
                    "displayName": disp,
                    "category": _humanize_device(ce.device_ref),
                    "deviceId": ce.device_id,
                    "commandCode": cmd_code,
                    "controlType": spec.control_type,
                    "pressValue": 1.0,
                    "releaseValue": 0.0,
                }
                insert_command(entry)
            continue

        # default single-command case (momentary / toggle / multiposition)
        cmd_code = ce.command_codes[0]
        entry = {
            "id": unique_id(_slugify_id(ce.display_name or ce.element_name)),
            "displayName": ce.display_name or ce.element_name,
            "category": _humanize_device(ce.device_ref),
            "deviceId": ce.device_id,
            "commandCode": cmd_code,
            "controlType": spec.control_type,
            "pressValue": 1.0,
            "releaseValue": 0.0,
        }
        insert_command(entry)

    # ----------------- pass 2: default.lua extras -----------------
    # Add cockpit-device commands from default.lua that aren't in clickabledata.
    # We treat each entry as a momentary by default. Skip iCommand entries
    # entirely (HC has built-ins for those).
    for de in default_data.key_entries:
        if de.cockpit_device_id is None:
            continue
        # we only emit one entry per (device, command_code) pair, and prefer
        # the "down" code; if there's an "up" with a different code we'd emit
        # both, but in practice DCS uses the same code for both directions.
        codes = [c for c in (de.down_code, de.up_code) if c is not None]
        seen: set[int] = set()
        for code in codes:
            if code in seen:
                continue
            seen.add(code)
            key = (de.cockpit_device_id, code)
            if key in cmd_index:
                # enrich existing entry with category/displayName if better
                existing = cmd_index[key]
                if existing["category"] == _humanize_device(None) or not existing.get("category"):
                    if de.category:
                        existing["category"] = de.category
                continue
            # New command from default.lua only
            report.cockpit_only_in_default += 1
            display = de.name or f"Command {code}"
            cat = de.category or _humanize_device(_device_ref_for_id(de.cockpit_device_id, devices))
            entry = {
                "id": unique_id(_slugify_id(display)),
                "displayName": display,
                "category": cat,
                "deviceId": de.cockpit_device_id,
                "commandCode": code,
                "controlType": "momentary",
                "pressValue": de.value_down if de.value_down is not None else 1.0,
                "releaseValue": de.value_up if de.value_up is not None else 0.0,
            }
            insert_command(entry)

    # Re-enrich category from default.lua where clickabledata's humanized
    # device name was used but default.lua has a real cockpit panel name.
    for (dev_id, code), entry in cmd_index.items():
        d_entries = default_lookup.get((dev_id, code))
        if not d_entries:
            continue
        # Pick a non-empty category preferring the first.
        for de in d_entries:
            if de.category:
                # only overwrite the heuristic device-name category, not a
                # real one already coming from default.lua merge above.
                if entry["category"] == _humanize_device(_device_ref_for_id(dev_id, devices)):
                    entry["category"] = de.category
                break

    # ----------------- pass 3: axes from default.lua -----------------
    # cockpit axes: device_commands.Button_K with cockpit_device_id
    seen_axes: set[tuple[int, int]] = {(a["deviceId"], a["commandCode"]) for a in lib.axes}
    for ae in default_data.axis_entries:
        if ae.cockpit_device_id is not None and ae.action_code is not None:
            key = (ae.cockpit_device_id, ae.action_code)
            if key in seen_axes:
                continue
            seen_axes.add(key)
            display = ae.name or f"Axis {ae.action_code}"
            cat = ae.category or _humanize_device(ae.cockpit_device_ref)
            base_id = _slugify_id(f"AX_{display}")
            lib.axes.append({
                "id": unique_id(base_id),
                "displayName": display,
                "category": cat,
                "deviceId": ae.cockpit_device_id,
                "commandCode": ae.action_code,
                "axisType": "cockpit",
                "inverted": False,
            })
        elif ae.is_icommand_axis and ae.action_ref in ICOMMAND_AXES:
            code, axis_type, default_display, default_cat = ICOMMAND_AXES[ae.action_ref]
            key = (0, code)
            if key in seen_axes:
                continue
            seen_axes.add(key)
            display = ae.name or default_display
            cat = ae.category or default_cat
            lib.axes.append({
                "id": unique_id(_slugify_id(f"LOSET_{display}")),
                "displayName": display,
                "category": cat,
                "deviceId": 0,
                "commandCode": code,
                "axisType": axis_type,
                "inverted": False,
            })

    lib.commands = list(cmd_index.values())
    return lib, report


def _device_ref_for_id(device_id: int, devices: dict[str, int]) -> str | None:
    for name, did in devices.items():
        if did == device_id:
            return name
    return None


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def find_input_default_lua(module_root: Path) -> Path | None:
    """Find Input/<MODULE>/joystick/default.lua under module_root, accepting
    odd casing and stray spaces in the inner module folder."""
    input_dir = module_root / "Input"
    if not input_dir.is_dir():
        return None
    candidates = list(input_dir.iterdir())
    candidates.sort(key=lambda p: (not p.name.strip().lower().startswith(module_root.name.lower()),
                                   len(p.name)))
    for c in candidates:
        f = c / "joystick" / "default.lua"
        if f.is_file():
            return f
    return None


def build_from_directory(
    module_root: Path,
    aircraft_id: str | None = None,
    display_name: str | None = None,
    aliases: list[str] | None = None,
) -> tuple[Library, BuildReport]:
    cockpit = module_root / "Cockpit" / "Scripts"
    devices = parse_devices(read_text(cockpit / "devices.lua"))
    cd = parse_command_defs(read_text(cockpit / "command_defs.lua"))
    clk = parse_clickabledata(read_text(cockpit / "clickabledata.lua"), cd, devices)
    default_path = find_input_default_lua(module_root)
    dflt = parse_default(read_text(default_path), cd, devices) if default_path else DefaultData()

    aid = aircraft_id or module_root.name
    name = display_name or aid
    return build_library(devices, cd, clk, dflt, aid, name, aliases)


# ---------------------------------------------------------------------------
# JSON emission
# ---------------------------------------------------------------------------

def library_to_dict(lib: Library) -> dict:
    out: dict = {
        "aircraftId": lib.aircraftId,
        "displayName": lib.displayName,
    }
    if lib.aliases:
        out["aliases"] = lib.aliases
    out["commands"] = lib.commands
    out["axes"] = lib.axes
    return out


# Re-export for the CLI
__all__ = [
    "Library", "BuildReport", "build_library",
    "build_from_directory", "library_to_dict",
]


# Mark unused imports as used (keeps imports clear in a single module).
_ = HELPERS  # surface for callers that want to inspect/extend the table
