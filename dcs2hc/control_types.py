from dataclasses import dataclass


@dataclass(frozen=True)
class HelperSpec:
    """How a clickabledata helper function maps to HC output.

    control_type is the HC controlType string.
    n_commands is how many command codes the helper consumes from its arguments
    (1 = single entry, 2 = split into UP/DOWN entries).
    is_axis = True means the helper produces an entry in the HC `axes` array.
    """
    control_type: str
    n_commands: int = 1
    is_axis: bool = False


# Suffixes used when splitting a 2-cmd helper into UP/DOWN HC entries.
SPLIT_SUFFIXES = ("UP", "DOWN")


# Each entry maps a clickabledata helper function name to how the tool should
# treat it. New helpers can be added here as we encounter them.
HELPERS: dict[str, HelperSpec] = {
    # momentary (single command)
    "default_button":            HelperSpec("momentary"),
    "default_button2":           HelperSpec("momentary"),
    "default_CB_button":         HelperSpec("momentary"),
    "default_red_cover":         HelperSpec("momentary"),
    "default_trimmer_button":    HelperSpec("momentary"),
    "default_button_stick":      HelperSpec("momentary"),
    "default_button_axis":       HelperSpec("momentary"),
    "push_button_tumb":          HelperSpec("momentary"),
    "default_2_position_button": HelperSpec("momentary"),

    # action_button is special: handled in the parser to emit releaseCommandCode.
    # Listed here so the parser still recognizes it; n_commands=2 marks it as
    # consuming two command args.
    "action_button":             HelperSpec("momentary", n_commands=2),

    # toggle (single command, two positions)
    "default_2_position_tumb":         HelperSpec("toggle"),
    "default_2_position_tumb2":        HelperSpec("toggle"),
    "default_2_position_small_tumb":   HelperSpec("toggle"),
    "default_2_position_cover":        HelperSpec("toggle"),
    "default_2_position_cover_plastic":HelperSpec("toggle"),
    "default_1_position_tumb":         HelperSpec("toggle"),

    # multiposition (single command, value selects position)
    "default_3_position_tumb":         HelperSpec("multiposition"),
    "default_3_position_tumb2":        HelperSpec("multiposition"),
    "default_3_position_small_tumb":   HelperSpec("multiposition"),
    "default_3_pos_thumb":             HelperSpec("multiposition"),
    "default_2_pos_thumb":             HelperSpec("toggle"),
    "multiposition_switch":            HelperSpec("multiposition"),
    "multiposition_switch_":           HelperSpec("multiposition"),
    "multiposition_switch_intercom":   HelperSpec("multiposition"),
    "multiposition_switch_limited":    HelperSpec("multiposition"),
    "multiposition_switch_limited_":   HelperSpec("multiposition"),
    "multiposition_switch_tumb":       HelperSpec("multiposition"),
    "multiposition_switch_axis":       HelperSpec("multiposition"),
    "multiposition_tumb_limited":      HelperSpec("multiposition"),
    "default_air_inlet":               HelperSpec("multiposition"),

    # split UP / DOWN (two commands -> two HC entries)
    "default_button_tumb":             HelperSpec("momentary", n_commands=2),
    "default_tumb_button":             HelperSpec("momentary", n_commands=2),
    "default_small_tumb_button":       HelperSpec("momentary", n_commands=2),
    "springloaded_thumb":              HelperSpec("momentary", n_commands=2),
    "springloaded_2_pos_tumb":         HelperSpec("momentary", n_commands=2),
    "springloaded_3_pos_tumb":         HelperSpec("momentary", n_commands=2),
    "springloaded_3_pos_tumb2":        HelperSpec("momentary", n_commands=2),
    "default_springloaded_switch":     HelperSpec("momentary", n_commands=2),
    "default_springloaded_switch2":    HelperSpec("momentary", n_commands=2),
    "default_springloaded_3pos_switch":HelperSpec("momentary", n_commands=2),
    "multiposition_spring_switch":     HelperSpec("momentary", n_commands=2),

    # axes (continuous controls)
    "default_axis":          HelperSpec("axis", is_axis=True),
    "default_axis_limited":  HelperSpec("axis", is_axis=True),
    "default_animated_lever":HelperSpec("axis", is_axis=True),
    "default_rheostat":      HelperSpec("axis", is_axis=True),
    "default_mirror_2axis":  HelperSpec("axis", is_axis=True),
    "default_mirror_touch":  HelperSpec("axis", is_axis=True),
    "axis_limited_1_side":   HelperSpec("axis", is_axis=True),
    "radio_wheel":           HelperSpec("axis", is_axis=True),

    # additional non-axis variants observed in the wild
    "AFCS_button":                    HelperSpec("momentary"),
    "nav_button":                     HelperSpec("momentary"),
    "electrically_held_switch":       HelperSpec("toggle"),
    "intercom_rotate_tumb":           HelperSpec("multiposition"),
    "multiposition_switch_1_side":    HelperSpec("multiposition"),
    "multiposition_switch_2_cl":      HelperSpec("multiposition"),
    "multiposition_switch3":          HelperSpec("multiposition"),
    "unique_switch_tumb":             HelperSpec("multiposition"),
    "IFF_Master_multiposition_switch":   HelperSpec("multiposition"),
    "IFF_multiposition_spring_switch":   HelperSpec("momentary", n_commands=2),
    "IFF_Code4_multiposition_spring_switch": HelperSpec("momentary", n_commands=2),

    # Synthetic helpers used for inline-table elements (`elements["X"] = {...}`).
    # The parser converts each unique action command into its own synthetic
    # ClickableEntry, so n_commands is always 1 here.
    "__inline_BTN":      HelperSpec("momentary"),
    "__inline_BTN_FIX":  HelperSpec("momentary"),
    "__inline_TUMB":     HelperSpec("multiposition"),
    "__inline_LEV":      HelperSpec("momentary"),
}


def classify_helper(name: str) -> HelperSpec | None:
    """Return a HelperSpec for `name`, falling back to a naming heuristic if
    the name isn't in the explicit table. Returns None if the name is clearly
    not a control helper (e.g. `true`, `false`)."""
    if name in HELPERS:
        return HELPERS[name]
    if name in ("true", "false", "nil"):
        return None
    lower = name.lower()
    if "axis" in lower or lower.endswith("rheostat") or "lever" in lower or lower.endswith("wheel"):
        return HelperSpec("axis", is_axis=True)
    if "spring" in lower or "tumb_button" in lower or "button_tumb" in lower:
        return HelperSpec("momentary", n_commands=2)
    if "multiposition" in lower or "3_position" in lower or "3_pos" in lower:
        return HelperSpec("multiposition")
    if "2_position" in lower or "2_pos" in lower or "1_position" in lower or "cover" in lower:
        return HelperSpec("toggle")
    if "button" in lower:
        return HelperSpec("momentary")
    return None


# Built-in DCS iCommand axes (deviceId 0). Maps the iCommand identifier as it
# appears in default.lua to (commandCode, axisType, displayName, category).
# This list is intentionally small; we emit only iCommand axes the user is
# likely to want bound, and only those that actually appear in the file's
# axisCommands. Codes are stable across DCS modules.
ICOMMAND_AXES: dict[str, tuple[int, str, str, str]] = {
    "iCommandPlanePitch":           (2001, "sim",   "Flight Control - Pitch",      "Flight Controls"),
    "iCommandPlaneRoll":            (2002, "sim",   "Flight Control - Roll",       "Flight Controls"),
    "iCommandPlaneRudder":          (2003, "sim",   "Flight Control - Rudder",     "Flight Controls"),
    "iCommandPlaneThrustCommon":    (2004, "sim",   "Throttle",                    "Engine Controls"),
    "iCommandPlaneThrustLeft":      (2005, "sim",   "Throttle - Left Engine",      "Engine Controls"),
    "iCommandPlaneThrustRight":     (2006, "sim",   "Throttle - Right Engine",     "Engine Controls"),
    "iCommandWheelBrake":           (2007, "sim",   "Wheel Brakes - Both",         "Flight Controls"),
    "iCommandLeftWheelBrake":       (2008, "sim",   "Wheel Brake - Left",          "Flight Controls"),
    "iCommandRightWheelBrake":      (2009, "sim",   "Wheel Brake - Right",         "Flight Controls"),
    "iCommandPlaneSelecterHorizontalAbs": (2030, "loset", "HOTAS Slew Horizontal", "HOTAS"),
    "iCommandPlaneSelecterVerticalAbs":   (2031, "loset", "HOTAS Slew Vertical",   "HOTAS"),
    "iCommandViewVerticalAbs":      (2010, "loset", "Camera Vertical View",        "View"),
    "iCommandViewHorizontalAbs":    (2011, "loset", "Camera Horizontal View",      "View"),
    "iCommandViewZoomAbs":          (2012, "loset", "Zoom View",                   "View"),
    "iCommandHelicopterCollective": (2087, "loset", "Collective",                  "Flight Controls"),
}
