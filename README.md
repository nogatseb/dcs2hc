# DCS → HangarControl Aircraft Library Builder

A Python tool that reads the DCS World Lua source files for an aircraft module
and emits a HangarControl (HC) aircraft library JSON file.

The build process is described in detail in OverkillSimulation's
`DCS_Aircraft_Library_Build_Guide.docx`, included here for reference. 
This tool automates that process for the parts that can be derived mechanically from the Lua source.

## Requirements

- Python 3.11 or newer (uses standard library only)
- Read access to a DCS aircraft module's `Cockpit/Scripts/` and
  `Input/<MODULE>/joystick/` directories

## Usage

From the project root:

```
python build_library.py <path-to-DCS-aircraft-module>
```

Examples:

```
python build_library.py "C:\DCS World\Mods\aircraft\A-10C_2"
```

By default, the JSON is written to `./out/<aircraftId>.json`. The
`aircraftId` defaults to the module folder name; override with
`--aircraft-id`.

### CLI options

| Flag                    | Description                                                     |
|-------------------------|-----------------------------------------------------------------|
| `--aircraft-id ID`      | Override the `aircraftId` field (default: module folder name).  |
| `--display-name NAME`   | Override the `displayName` field.                               |
| `--alias STR`           | Add an alternate aircraft type string. Repeatable.              |
| `--out-dir DIR`         | Output directory (default: `./out`).                            |
| `-v`, `--verbose`       | Print full validation report (duplicates, unresolved refs).     |

After running, the tool prints a summary like:

```
Building library for A-10C_2…
Wrote out/A-10C_2.json
  commands: 478
  axes:     62
  skipped clickables: 4
  cockpit commands added from default.lua: 15
  duplicate (deviceId, commandCode) pairs: 6
```

Use `-v` to see exactly which entries triggered each warning.

## What the tool produces

A JSON file matching HC's current library schema:

```json
{
  "aircraftId": "A-10C_2",
  "displayName": "A-10C_2",
  "commands": [ ... ],
  "axes":     [ ... ]
}
```

Each `commands` entry has:

```json
{
  "id": "BATTERY_SWITCH",
  "displayName": "Battery Switch, ON/OFF/ORIDE",
  "category": "Electrical Power Panel",
  "deviceId": 1,
  "commandCode": 3004,
  "controlType": "multiposition",
  "pressValue": 1.0,
  "releaseValue": 0.0
}
```

`controlType` is one of `momentary`, `toggle`, `multiposition`, `rotary`.
`action_button`-style controls also get `releaseCommandCode`. Spring-loaded
and tumb-button helpers are split into two entries (UP and DOWN), each with
its own command code.

Each `axes` entry has:

```json
{
  "id": "THROTTLE",
  "displayName": "Throttle",
  "category": "Engine Controls",
  "deviceId": 0,
  "commandCode": 2004,
  "axisType": "sim",
  "inverted": false
}
```

`axisType` is `cockpit` (a normal cockpit-device axis), `sim` (universal DCS
flight controls like pitch/roll/yaw/throttle), or `loset` (other LoSetCommand
axes).

## What gets parsed

Four DCS Lua files per aircraft module:

- `Cockpit/Scripts/devices.lua` — names and assigns numeric IDs to each
  cockpit device.
- `Cockpit/Scripts/command_defs.lua` — defines `Keys` (LoSetCommand iCommand
  codes), the main `device_commands` table, and any per-system command
  tables (e.g. `fuel_commands`, `control_commands`, `cb_start_cmd + N`
  arithmetic, `for k=1,N do device_commands["Button_"..k] = … end` loops).
- `Cockpit/Scripts/clickabledata.lua` — the cockpit's clickable elements.
  Both forms are handled:
  - `elements["X"] = default_button(_("hint"), devices.Y, device_commands.Button_K, arg_id)`
  - `elements["X"] = { class = {class_type.BTN, …}, action = {…}, device = devices.Y, … }`
- `Input/<MODULE>/joystick/default.lua` — joystick bindings. Cockpit entries
  not present in clickabledata are pulled from here. iCommand axes that
  appear in `axisCommands` are emitted with `axisType: sim`/`loset`.

`device_init.lua` is not parsed — it provides context but no extracted data.

## What's deliberately not done

- **Indicators** are not emitted.
- **Universal LoSetCommand keyCommands** (deviceId 0 entries from
  `default.lua`'s `keyCommands` like HOTAS Pinky, Pickle, Master Arm, gear
  up/down, etc.) are not emitted. HC has built-ins for these. Universal
  axes (pitch/roll/throttle/wheel brake/etc.) ARE emitted as `axisType: sim`
  or `loset` because they're present in current HC libraries.

## Known limitations

- **Approximate parser, not a Lua interpreter.** The tool relies on regex
  patterns that match DCS's conventions (`devices["X"] = counter()`,
  `Button_K = start_command + K`, `elements["X"] = helper(...)`). Modules
  that deviate significantly from these conventions may produce empty or
  partial output. When that happens, run with `-v` to see which elements
  were skipped and why.
- **Unknown control helpers.** Each helper used in `clickabledata.lua` is
  classified via `dcs2hc/control_types.py` (HELPERS table). Unrecognized
  helpers fall back to a name-based heuristic; if that also fails, the
  element is skipped and counted under `unknown helpers` in the report.
  When you encounter an unknown helper, add it to `HELPERS` with the
  appropriate `HelperSpec(control_type, n_commands)` and re-run.
- **Categories.** Categories are taken from `default.lua`'s `category =
  _("…")` field when available, otherwise derived from the device name
  (`MFCD_LEFT` → `Left MFCD`). Some manual cleanup is typical before
  shipping a library.
- **Display names.** Hints from `clickabledata.lua` are used verbatim (e.g.
  `"Channel Selector (Ones) / X/Y Mode. Right mouse click to select X/Y.
  Rotate mouse wheel to make channel selection"`). These are often verbose
  and benefit from manual trimming.
- **Duplicate `(deviceId, commandCode)` pairs** are reported but not
  resolved automatically — the first entry wins. Review the `-v` output to
  decide whether to keep either, both, or merge them.
- **`aircraftId` and `displayName`** default to the module folder name. The
  guide describes finding the canonical type string in the module's
  `entry.lua`; pass `--aircraft-id` if the folder name doesn't match.

## Project layout

```
build_library.py        Entry shim
dcs2hc/
  parsers.py            Regex parsers for the four Lua files
  control_types.py      HELPERS table + ICOMMAND_AXES
  builder.py            Merger and HC JSON emitter
  cli.py                argparse CLI
```

## Extending the tool

The most common extension is adding a new clickabledata helper. Open
`dcs2hc/control_types.py` and add an entry to the `HELPERS` dict:

```python
"my_new_helper": HelperSpec("toggle"),                      # single-cmd
"my_split_helper": HelperSpec("momentary", n_commands=2),   # split UP/DOWN
"my_axis_helper":  HelperSpec("axis", is_axis=True),
```

For new iCommand-driven axes you want to emit, add to `ICOMMAND_AXES` in
the same file.
