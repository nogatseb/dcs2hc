"""Regex-based parsers for the four DCS Lua files we need.

These are not full Lua interpreters — they exploit the consistent conventions
DCS uses (devices["NAME"] = counter(), Button_K = start_command + K, etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# Strip Lua comments from a source string: "-- ..." line comments and
# "--[[ ... ]]" block comments. Preserves text otherwise.
_BLOCK_COMMENT_RE = re.compile(r"--\[\[.*?\]\]", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def strip_lua_comments(text: str) -> str:
    text = _BLOCK_COMMENT_RE.sub("", text)
    text = _LINE_COMMENT_RE.sub("", text)
    return text


# ---------------------------------------------------------------------------
# devices.lua
# ---------------------------------------------------------------------------

_DEVICE_LINE_RE = re.compile(r'devices\s*\[\s*"([^"]+)"\s*\]\s*=\s*counter\s*\(\s*\)')


def parse_devices(text: str) -> dict[str, int]:
    """Return {device_name: numeric_id} in source order, IDs starting at 1."""
    text = strip_lua_comments(text)
    out: dict[str, int] = {}
    for i, m in enumerate(_DEVICE_LINE_RE.finditer(text), start=1):
        out[m.group(1)] = i
    return out


# ---------------------------------------------------------------------------
# command_defs.lua
# ---------------------------------------------------------------------------

@dataclass
class CommandDefs:
    keys: dict[str, int] = field(default_factory=dict)        # iCommand-ish names from Keys table
    # Maps both flat names (Button_N, Foo) and namespaced names (table.entry)
    # to a numeric command code. Modules like F-5E split device commands into
    # per-system tables (fuel_commands.FuelShutoff_Left) so we keep both
    # flat (last-write-wins) and namespaced lookups.
    device_commands: dict[str, int] = field(default_factory=dict)
    namespaced_commands: dict[str, int] = field(default_factory=dict)
    start_command: int = 3000


_KEYS_HEADER_RE = re.compile(r"\bKeys\s*=\s*\{", re.DOTALL)
_DC_HEADER_RE = re.compile(r"\bdevice_commands\s*=\s*\{", re.DOTALL)
_START_CMD_RE = re.compile(r"\bstart_command\s*=\s*(\d+)")
_NAMED_INT_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*(-?\d+)\b")
_BUTTON_OFFSET_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*start_command\s*\+\s*(-?\d+)")
_NAMED_COUNTER_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*counter\s*\(\s*\)")
# Common for-loop construction of Button_K commands. Matches either:
#   for k = 1,N do device_commands["Button_"..k] = counter() end           (F-5E)
#   for k = 1,N do device_commands["Button_"..k] = start_command + k end   (UH-1H)
_BUTTON_FOR_LOOP_RE = re.compile(
    r"for\s+\w+\s*=\s*(\d+)\s*,\s*(\d+)\s*do\s*"
    r"device_commands\s*\[\s*\"Button_\"\s*\.\.\s*\w+\s*\]\s*=\s*"
    r"(?:counter\s*\(\s*\)|start_command\s*\+\s*\w+)\s*end",
)
# Each per-system commands table: e.g. `count = start_command\n<table> = { ... }`
_TABLE_HEADER_RE = re.compile(
    r"\bcount\s*=\s*start_command\s*([A-Za-z_]\w*)\s*=\s*\{",
)


def _extract_balanced_block(text: str, open_pos: int) -> str:
    """Given the position of an opening '{', return the substring inside the
    matching '}'. Naive but good enough — Lua syntax in these files doesn't
    embed unbalanced braces inside strings in a way that breaks this."""
    depth = 0
    i = open_pos
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : i]
        i += 1
    return text[open_pos + 1 :]


def parse_command_defs(text: str) -> CommandDefs:
    text = strip_lua_comments(text)
    cd = CommandDefs()

    m = _START_CMD_RE.search(text)
    if m:
        cd.start_command = int(m.group(1))

    # Keys table
    km = _KEYS_HEADER_RE.search(text)
    if km:
        block = _extract_balanced_block(text, km.end() - 1)
        for name, val in _NAMED_INT_RE.findall(block):
            cd.keys[name] = int(val)

    # device_commands table (Button_K = start_command + K, plus any other
    # named entries like SomeKnob_Increase = 3010)
    dm = _DC_HEADER_RE.search(text)
    if dm:
        block = _extract_balanced_block(text, dm.end() - 1)
        for name, off in _BUTTON_OFFSET_RE.findall(block):
            cd.device_commands[name] = cd.start_command + int(off)
        for name, val in _NAMED_INT_RE.findall(block):
            cd.device_commands.setdefault(name, int(val))

    # F-5E-style for-loop construction: for k = 1,N do device_commands["Button_"..k] = counter() end
    for m in _BUTTON_FOR_LOOP_RE.finditer(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        for k in range(lo, hi + 1):
            cd.device_commands.setdefault(f"Button_{k}", cd.start_command + k)

    # Top-level scalar aliases defined outside any table, e.g.:
    #     cb_start_cmd = device_commands.Button_21
    #     MFCD_ADJ_Stop = device_commands.Button_23
    # Pick these up so they resolve later in clickabledata.
    for am in re.finditer(
        r"^\s*(?:local\s+)?([A-Za-z_]\w*)\s*=\s*device_commands\.([A-Za-z_]\w*)\s*$",
        text, re.MULTILINE,
    ):
        alias_name, target = am.group(1), am.group(2)
        if target in cd.device_commands:
            cd.device_commands.setdefault(alias_name, cd.device_commands[target])

    # Per-system command tables found across DCS modules. Two flavours:
    #   1) `reset_counter()  <name> = { X = counter(); ... }`  (A-10C_2 style)
    #   2) `count = start_command\n<name> = { X = counter(); ... }`  (F-5E style)
    # Both reset a counter to start_command and increment with each counter()
    # call inside the table.
    for tab_match in re.finditer(r"reset_counter\(\s*\)\s*([A-Za-z_]\w*)\s*=\s*\{", text):
        _consume_counter_table(text, tab_match, cd)
    for tab_match in _TABLE_HEADER_RE.finditer(text):
        _consume_counter_table(text, tab_match, cd)

    return cd


def _consume_counter_table(text: str, header_match: re.Match, cd: CommandDefs) -> None:
    """Helper: read the `{...}` body following a `<name> = {` header match
    where group(1) is the table name. Each `X = counter()` adds X to both
    the flat device_commands map (last-wins) and to namespaced_commands
    keyed as `<name>.X`."""
    table_name = header_match.group(1)
    # find the '{' immediately following the match end
    brace_pos = text.find("{", header_match.end() - 1)
    if brace_pos < 0:
        return
    block = _extract_balanced_block(text, brace_pos)
    count = cd.start_command
    for name_match in _NAMED_COUNTER_RE.finditer(block):
        count += 1
        entry_name = name_match.group(1)
        cd.namespaced_commands[f"{table_name}.{entry_name}"] = count
        cd.device_commands.setdefault(entry_name, count)
    # Also pick up explicit numeric assignments inside the table.
    for name, val in _NAMED_INT_RE.findall(block):
        cd.namespaced_commands.setdefault(f"{table_name}.{name}", int(val))
        cd.device_commands.setdefault(name, int(val))


# ---------------------------------------------------------------------------
# clickabledata.lua
# ---------------------------------------------------------------------------

@dataclass
class ClickableEntry:
    element_name: str
    helper: str
    display_name: str | None
    device_ref: str | None       # e.g. "ELEC_INTERFACE" or None if literal int
    device_id: int | None        # resolved
    command_refs: list[str]      # e.g. ["Button_4"] or ["MFCD_ADJ_Increase","MFCD_ADJ_Stop"]
    command_codes: list[int]     # resolved (None entries removed)
    arg_id: int | None           # cockpit argument id (last numeric in call)
    raw_args: str                # the full arg string, for debugging
    line: int


# Captures `elements["NAME"] = helper(`. We then read the balanced parens.
_ELEMENT_RE = re.compile(r'elements\s*\[\s*"([^"]+)"\s*\]\s*=\s*([A-Za-z_]\w*)\s*\(')
# Captures `elements["NAME"] = {` (inline table form).
_INLINE_ELEMENT_RE = re.compile(r'elements\s*\[\s*"([^"]+)"\s*\]\s*=\s*\{')

# Top-level local aliases like  MFCD_ADJ_Increase = device_commands.Button_21
# Also handles  Foo = device_commands.Button_3   without `local` prefix.
_ALIAS_RE = re.compile(r'^[ \t]*(?:local\s+)?([A-Za-z_]\w*)\s*=\s*device_commands\.([A-Za-z_]\w*)\s*$', re.MULTILINE)

_DEVICE_REF_RE = re.compile(r"devices\.([A-Za-z_]\w*)")
_DC_REF_RE = re.compile(r"device_commands\.([A-Za-z_]\w*)")
# A general namespaced reference, e.g. fuel_commands.FuelShutoff_Left.
_NAMESPACED_REF_RE = re.compile(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
# Match _("text") OR _('text'); supports backslash escapes inside.
_HINT_RE = re.compile(
    r"""_\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')\s*\)"""
)


def _hint_text(m: re.Match) -> str:
    return m.group(1) if m.group(1) is not None else m.group(2)


def _read_balanced_parens(text: str, open_pos: int) -> tuple[str, int]:
    """Read from text[open_pos] (which is '(') and return (inner, end_index)
    where inner excludes the outer parens and end_index points just after ')'."""
    depth = 0
    i = open_pos
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : i], i + 1
        i += 1
    return text[open_pos + 1 :], len(text)


def _split_top_level_commas(s: str) -> list[str]:
    """Split a comma-separated argument list, respecting parens/braces/strings."""
    out: list[str] = []
    depth_p = depth_b = depth_c = 0
    in_str: str | None = None
    last = 0
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\" and i + 1 < len(s):
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'"):
                in_str = c
            elif c == "(":
                depth_p += 1
            elif c == ")":
                depth_p -= 1
            elif c == "{":
                depth_b += 1
            elif c == "}":
                depth_b -= 1
            elif c == "[":
                depth_c += 1
            elif c == "]":
                depth_c -= 1
            elif c == "," and depth_p == depth_b == depth_c == 0:
                out.append(s[last:i].strip())
                last = i + 1
        i += 1
    if last < len(s):
        out.append(s[last:].strip())
    return out


def parse_clickabledata(text: str, command_defs: CommandDefs, devices: dict[str, int]) -> list[ClickableEntry]:
    text = strip_lua_comments(text)

    # Collect top-level aliases:  NAME = device_commands.Button_K
    aliases: dict[str, int] = {}
    for name, target in _ALIAS_RE.findall(text):
        if target in command_defs.device_commands:
            aliases[name] = command_defs.device_commands[target]

    # Some modules use scalar aliases like `cb_start_cmd = device_commands.Button_21`
    # in command_defs.lua, then reference them in clickabledata as
    # `cb_start_cmd + 4`. Also pick up bare aliases from command_defs (we don't
    # track those there yet, so scan the file at parse time).
    # Capture any "name = device_commands.Button_K" or "name = N" at top level
    # of clickabledata, which are also relied on as command tokens.
    cb_aliases: dict[str, int] = dict(aliases)
    for m in re.finditer(r"^\s*(?:local\s+)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*$",
                         text, re.MULTILINE):
        ref = m.group(2)
        rm = _NAMESPACED_REF_RE.fullmatch(ref)
        if not rm:
            continue
        ns, name = rm.group(1), rm.group(2)
        full = f"{ns}.{name}"
        if full in command_defs.namespaced_commands:
            cb_aliases[m.group(1)] = command_defs.namespaced_commands[full]
        elif ns == "device_commands" and name in command_defs.device_commands:
            cb_aliases[m.group(1)] = command_defs.device_commands[name]

    # Resolution helper for a command reference token (e.g. "Button_4",
    # "MFCD_ADJ_Increase", or a bare integer literal).
    def resolve_command_ref(token: str) -> tuple[str | None, int | None]:
        token = token.strip()
        # Handle <name> + <int>  or  <name.entry> + <int>
        plus_match = re.fullmatch(r"([A-Za-z_][\w.]*)\s*\+\s*(-?\d+)", token)
        if plus_match:
            base_token, off = plus_match.group(1), int(plus_match.group(2))
            base_ref, base_code = resolve_command_ref(base_token)
            if base_code is not None:
                return base_ref, base_code + off
        # Namespaced reference like fuel_commands.FuelShutoff_Left.
        m = _NAMESPACED_REF_RE.fullmatch(token)
        if m:
            ns, name = m.group(1), m.group(2)
            full = f"{ns}.{name}"
            if full in command_defs.namespaced_commands:
                return name, command_defs.namespaced_commands[full]
            # device_commands.Button_K legacy path
            if ns == "device_commands":
                return name, command_defs.device_commands.get(name)
            # Last-resort: try the flat map (handles less-careful definitions).
            if name in command_defs.device_commands:
                return name, command_defs.device_commands[name]
            return name, None
        # Bare identifier — could be an alias or a Keys entry
        if re.fullmatch(r"[A-Za-z_]\w*", token):
            if token in cb_aliases:
                return token, cb_aliases[token]
            if token in command_defs.device_commands:
                return token, command_defs.device_commands[token]
            if token in command_defs.keys:
                return token, command_defs.keys[token]
            return token, None
        # Integer literal
        if re.fullmatch(r"-?\d+", token):
            return None, int(token)
        return None, None

    def _parse_lua_list(s: str | None) -> list[str]:
        """Parse a Lua list literal `{a, b, c}` (or a single value) into the
        list of stripped item strings. Returns [] for None/empty."""
        if not s:
            return []
        s = s.strip()
        if s.startswith("{") and s.endswith("}"):
            return [t.strip() for t in _split_top_level_commas(s[1:-1]) if t.strip()]
        return [s]

    def parse_inline_table_element(name: str, body: str, line: int) -> list[ClickableEntry]:
        """Convert an inline-table `elements["X"] = { ... }` body into one or
        more synthetic ClickableEntry objects. Each unique action command
        becomes one entry. Multi-command tables get a directional suffix on
        the display name based on the corresponding arg_value sign."""
        # Re-wrap the body as if it were a leaf block so _kv works.
        block = "{" + body + "}"
        cls_text = _kv(block, "class")
        hint_text = _kv(block, "hint")
        device_text = _kv(block, "device")
        action_text = _kv(block, "action")
        arg_text = _kv(block, "arg")
        arg_value_text = _kv(block, "arg_value")

        if device_text is None or action_text is None:
            return []

        # Resolve device.
        dev_match = _DEVICE_REF_RE.search(device_text)
        dev_ref = dev_match.group(1) if dev_match else None
        dev_id = devices.get(dev_ref) if dev_ref else None

        # Resolve hint.
        display = None
        if hint_text:
            hm = _HINT_RE.search(hint_text)
            if hm:
                display = _hint_text(hm)

        # Determine HC controlType from the first class entry.
        cls_first = re.search(r"class_type\.([A-Za-z_]+)", cls_text or "")
        cls_name = cls_first.group(1) if cls_first else "BTN"
        helper_name = f"__inline_{cls_name}"

        # Parse list-valued fields.
        actions = _parse_lua_list(action_text)
        arg_values = _parse_lua_list(arg_value_text)
        args_list = _parse_lua_list(arg_text)

        # Resolve actions and collect unique commands preserving first-seen order.
        unique: dict[int, tuple[str | None, str]] = {}  # code -> (ref, suffix)
        for i, tok in enumerate(actions):
            ref, code = resolve_command_ref(tok)
            if code is None:
                continue
            if code in unique:
                continue
            v = arg_values[i] if i < len(arg_values) else None
            try:
                vv = float(v) if v is not None else None
            except ValueError:
                vv = None
            if vv is not None and len(actions) > 1:
                if vv > 0:
                    suffix = " Increase"
                elif vv < 0:
                    suffix = " Decrease"
                else:
                    suffix = ""
            else:
                suffix = ""
            unique[code] = (ref, suffix)

        if not unique:
            return []

        # arg_id: the first numeric arg in arg_text.
        arg_id: int | None = None
        for a in args_list:
            try:
                arg_id = int(a)
                break
            except ValueError:
                continue
        if arg_id is None:
            mname = re.search(r"(\d+)\s*$", name)
            if mname:
                arg_id = int(mname.group(1))

        out: list[ClickableEntry] = []
        multi = len(unique) > 1
        for code, (ref, suffix) in unique.items():
            label = (display or name) + (suffix if multi and suffix else "")
            out.append(ClickableEntry(
                element_name=name if not multi else f"{name}{suffix.replace(' ', '_')}",
                helper=helper_name,
                display_name=label,
                device_ref=dev_ref,
                device_id=dev_id,
                command_refs=[ref or ""],
                command_codes=[code],
                arg_id=arg_id,
                raw_args="(inline table)",
                line=line,
            ))
        return out

    entries: list[ClickableEntry] = []
    inline_consumed_starts: set[int] = set()
    for im in _INLINE_ELEMENT_RE.finditer(text):
        elem_name = im.group(1)
        brace_pos = im.end() - 1
        body = _extract_balanced_block(text, brace_pos)
        line = text.count("\n", 0, im.start()) + 1
        for e in parse_inline_table_element(elem_name, body, line):
            entries.append(e)
        inline_consumed_starts.add(im.start())

    for m in _ELEMENT_RE.finditer(text):
        if m.start() in inline_consumed_starts:
            continue
        elem_name = m.group(1)
        helper = m.group(2)
        # find the '(' that ends m
        open_paren = m.end() - 1
        inner, _end = _read_balanced_parens(text, open_paren)
        line = text.count("\n", 0, m.start()) + 1
        args = _split_top_level_commas(inner)

        # display name: first arg, expected as _("...") — fall back to none
        display = None
        if args:
            h = _HINT_RE.search(args[0])
            if h:
                display = _hint_text(h)

        # device ref: any "devices.X" anywhere in the call
        dev_ref = None
        dev_id = None
        dm = _DEVICE_REF_RE.search(inner)
        if dm:
            dev_ref = dm.group(1)
            dev_id = devices.get(dev_ref)

        # find command refs — scan arg list and collect command-like tokens.
        # A command token is either "device_commands.X" or a known alias
        # name or a bare command-defs name. We use position to keep order.
        cmd_refs: list[str] = []
        cmd_codes: list[int] = []
        for arg in args[1:]:  # skip hint
            arg = arg.strip()
            # skip the device argument
            if _DEVICE_REF_RE.fullmatch(arg) or arg.startswith("devices."):
                continue
            ref, code = resolve_command_ref(arg)
            if code is None:
                continue
            # Confirm the token actually looked like a command reference (not
            # some incidental integer or float literal in trailing options).
            looks_like_cmd_ref = bool(
                _NAMESPACED_REF_RE.fullmatch(arg)
                or arg in cb_aliases
                or arg in command_defs.device_commands
                or arg in command_defs.keys
                or re.fullmatch(r"[A-Za-z_][\w.]*\s*\+\s*-?\d+", arg)
            )
            if not looks_like_cmd_ref:
                continue
            cmd_refs.append(ref or arg)
            cmd_codes.append(code)

        # arg id: the last bare integer in the call, or the trailing number in
        # the element name (pnt_404 → 404). Prefer the explicit numeric arg.
        arg_id: int | None = None
        for arg in reversed(args):
            arg = arg.strip()
            if re.fullmatch(r"-?\d+", arg):
                arg_id = int(arg)
                break
        if arg_id is None:
            mname = re.search(r"(\d+)\s*$", elem_name)
            if mname:
                arg_id = int(mname.group(1))

        entries.append(ClickableEntry(
            element_name=elem_name,
            helper=helper,
            display_name=display,
            device_ref=dev_ref,
            device_id=dev_id,
            command_refs=cmd_refs,
            command_codes=cmd_codes,
            arg_id=arg_id,
            raw_args=inner,
            line=line,
        ))
    return entries


# ---------------------------------------------------------------------------
# default.lua (joystick input bindings)
# ---------------------------------------------------------------------------

@dataclass
class DefaultKeyEntry:
    name: str | None
    category: str | None
    cockpit_device_ref: str | None
    cockpit_device_id: int | None
    down_ref: str | None
    down_code: int | None
    up_ref: str | None
    up_code: int | None
    value_down: float | None
    value_up: float | None


@dataclass
class DefaultAxisEntry:
    name: str | None
    category: str | None
    cockpit_device_ref: str | None
    cockpit_device_id: int | None
    action_ref: str | None
    action_code: int | None
    is_icommand_axis: bool


@dataclass
class DefaultData:
    key_entries: list[DefaultKeyEntry] = field(default_factory=list)
    axis_entries: list[DefaultAxisEntry] = field(default_factory=list)


def _kv(block: str, key: str) -> str | None:
    """Find `key = <value>` in the table-entry block. Returns the value text,
    or None if absent. Handles simple values; not nested tables."""
    m = re.search(rf"\b{re.escape(key)}\s*=\s*", block)
    if not m:
        return None
    rest = block[m.end():]
    # value runs until the next top-level comma or end of block
    depth_p = depth_b = 0
    in_str: str | None = None
    i = 0
    while i < len(rest):
        c = rest[i]
        if in_str:
            if c == "\\" and i + 1 < len(rest):
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'"):
                in_str = c
            elif c == "(":
                depth_p += 1
            elif c == ")":
                depth_p -= 1
            elif c == "{":
                depth_b += 1
            elif c == "}":
                depth_b -= 1
                if depth_b < 0:
                    break
            elif c == "," and depth_p == 0 and depth_b == 0:
                break
        i += 1
    return rest[:i].strip()


def _parse_string(val: str | None) -> str | None:
    if val is None:
        return None
    matches = list(_HINT_RE.finditer(val))
    if matches:
        # If it's a category table like {_('A'), _('B')}, the most specific
        # label is the last one.
        return _hint_text(matches[-1])
    return None


def _parse_command_token(val: str | None, command_defs: CommandDefs) -> tuple[str | None, int | None]:
    if val is None:
        return None, None
    val = val.strip()
    m = _DC_REF_RE.search(val)
    if m:
        name = m.group(1)
        return name, command_defs.device_commands.get(name)
    if re.fullmatch(r"-?\d+", val):
        return None, int(val)
    if re.fullmatch(r"[A-Za-z_]\w*", val):
        # could be Keys entry or a defined name
        if val in command_defs.keys:
            return val, command_defs.keys[val]
        if val in command_defs.device_commands:
            return val, command_defs.device_commands[val]
        return val, None
    return None, None


def _parse_device_token(val: str | None, devices: dict[str, int]) -> tuple[str | None, int | None]:
    if val is None:
        return None, None
    val = val.strip()
    m = _DEVICE_REF_RE.search(val)
    if m:
        name = m.group(1)
        return name, devices.get(name)
    if re.fullmatch(r"-?\d+", val):
        return None, int(val)
    return None, None


def _parse_float(val: str | None) -> float | None:
    if val is None:
        return None
    val = val.strip()
    try:
        return float(val)
    except ValueError:
        return None


def _all_balanced_brace_pairs(text: str) -> list[tuple[int, int]]:
    """Return every (start, end) brace pair, where end is exclusive (the index
    just past the closing '}'). Includes nested pairs."""
    starts: list[int] = []
    pairs: list[tuple[int, int]] = []
    in_str: str | None = None
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\" and i + 1 < len(text):
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'"):
                in_str = c
            elif c == "{":
                starts.append(i)
            elif c == "}" and starts:
                start = starts.pop()
                pairs.append((start, i + 1))
        i += 1
    return pairs


def _is_leaf_entry(block: str) -> tuple[bool, bool]:
    """Return (is_axis_entry, is_key_entry) for a candidate leaf-entry block.

    The block string includes the surrounding braces. A leaf "axis" entry has
    `action = ...` at top level and no `down`/`pressed`. A leaf "key" entry
    has `down = ...` or `pressed = ...` at top level and no `action`. Other
    blocks are either wrappers (keyCommands = {...}, etc.) or unrelated.
    """
    has_action = re.search(r"\baction\s*=", block) is not None
    has_down = re.search(r"\bdown\s*=", block) is not None
    has_pressed = re.search(r"\bpressed\s*=", block) is not None
    is_axis = has_action and not has_down and not has_pressed
    is_key = (has_down or has_pressed) and not has_action
    return is_axis, is_key


def parse_default(text: str, command_defs: CommandDefs, devices: dict[str, int]) -> DefaultData:
    text = strip_lua_comments(text)
    out = DefaultData()

    # Collect every balanced brace pair. Process from smallest to largest so
    # leaves are claimed before any wrapping outer block. A block is skipped
    # if it strictly contains another already-claimed block (it's a wrapper).
    pairs = _all_balanced_brace_pairs(text)
    pairs.sort(key=lambda se: se[1] - se[0])
    claimed_set: set[tuple[int, int]] = set()

    def contains_claimed(s: int, e: int) -> bool:
        # Does (s, e) strictly contain any (cs, ce) already claimed?
        for cs, ce in claimed_set:
            if s < cs and ce <= e and not (s == cs and e == ce):
                return True
        return False

    for s, e in pairs:
        block = text[s:e]
        is_axis, is_key = _is_leaf_entry(block)
        if not (is_axis or is_key):
            continue
        if contains_claimed(s, e):
            continue
        claimed_set.add((s, e))

        name = _parse_string(_kv(block, "name"))
        category = _parse_string(_kv(block, "category"))

        if is_axis:
            dev_ref, dev_id = _parse_device_token(_kv(block, "cockpit_device_id"), devices)
            act_ref, act_code = _parse_command_token(_kv(block, "action"), command_defs)
            is_icmd = dev_ref is None and dev_id is None and act_ref is not None and act_ref.startswith("iCommand")
            out.axis_entries.append(DefaultAxisEntry(
                name=name, category=category,
                cockpit_device_ref=dev_ref, cockpit_device_id=dev_id,
                action_ref=act_ref, action_code=act_code,
                is_icommand_axis=is_icmd,
            ))
        else:
            dev_ref, dev_id = _parse_device_token(_kv(block, "cockpit_device_id"), devices)
            down_ref, down_code = _parse_command_token(_kv(block, "down"), command_defs)
            up_ref, up_code = _parse_command_token(_kv(block, "up"), command_defs)
            value_down = _parse_float(_kv(block, "value_down"))
            value_up = _parse_float(_kv(block, "value_up"))
            out.key_entries.append(DefaultKeyEntry(
                name=name, category=category,
                cockpit_device_ref=dev_ref, cockpit_device_id=dev_id,
                down_ref=down_ref, down_code=down_code,
                up_ref=up_ref, up_code=up_code,
                value_down=value_down, value_up=value_up,
            ))

    return out


# ---------------------------------------------------------------------------
# convenience
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str:
    # DCS Lua files are mostly UTF-8 but a few have stray bytes.
    return path.read_text(encoding="utf-8", errors="replace")
