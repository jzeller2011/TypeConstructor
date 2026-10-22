# Type Constructor (pyRevit extension)

Stage a new type against the firm naming standard, see what already exists while you do it, and create it with a conforming name.

## What it does

**Type Constructor** (main button)

1. Pick a standard (Doors, Walls, ...). Every type of that category in the project is read and broken into fields, from its parameters where they exist, otherwise by parsing its name.
2. Fill in fields on the left in any order. Blank means "any".
   - The list on the right narrows to existing types matching everything staged.
   - Each dropdown shows the standard values with a count of how many existing types would still match. Grey (0) means "nothing like that exists yet".
   - Optional fields (fire rating) have a `(none)` choice for "explicitly non-rated".
3. When every required field is filled, the status line tells you one of:
   - **Already exists**: the matching type is highlighted. Use it.
   - **Name already used by a type with different parameters**: someone mislabelled a type. Run Audit.
   - **Ready**: no type has this configuration. Shows which type it will duplicate from.
4. **Create type** duplicates the closest existing type (same Operation, so the same family), renames it, and writes the staged parameters. Anything it couldn't write (read-only, missing parameter) is reported.

Double-click any existing type to load its values as a starting point, then change only what differs.

### Project-specific parameters and values

Firm standards rarely match every project exactly, so the JSON is only the starting point. Everything below is saved **inside the Revit model** (Extensible Storage on a hidden DataStorage element). It persists between runs, reaches the team when they sync, and doesn't touch any family.

- **Parameters…** reads every type parameter on the project's types and shows fill counts and sample values. For each name segment, pick the parameter that holds it here, or choose "read from type name only". You can also tick extra parameters (e.g. Hardware Set) to show as list columns and filter fields; these never affect whether a type counts as a duplicate. A warning appears in the top bar when a segment's parameter can't be found in the project.
- **Values found on existing types are known values automatically.** A project using `WWD3` in PANEL TYPE gets WWD3 in the dropdown, and names containing it parse correctly. Dropdowns tag each value as [firm], [project] or [found] (toggle with `SHOW_VALUE_ORIGIN`).
- **✎ next to each text field** adds a new value (e.g. `WWD10`, "3-panel Wood Door") or edits the description of any value. Project descriptions override firm ones for that project. Reverting to the firm text removes the override. New values are rejected if they contain a separator from the template (e.g. `-`), since that would make names unreadable.

In a workshared model, saving needs the Type Constructor data element to be editable. If someone else has it borrowed, sync and retry.

**Audit Types** lists, per standard: names that don't follow the template, names that disagree with their parameters (e.g. name says 36 wide, Width = 34"), types with identical configurations, and values found on types that have no description yet.

## Install

1. Copy `TypeConstructor.extension` somewhere permanent (local or network).
2. pyRevit tab > Settings > Custom Extension Directories > add the folder *containing* `TypeConstructor.extension`.
3. Reload pyRevit. A **TypeConstructor** tab appears.

Run the tests outside Revit with `python tests/test_schema.py` (plain Python 3, no Revit needed).

## Defining a standard

Each `config/*.json` file is one standard. Files with `"enabled": false` are skipped. To maintain one firm-wide copy, set `CONFIG_DIR` at the top of both button scripts to a network folder.

```json
"template": "{operation}-{panel}-{width}x{height}-{frame_style}-{frame_material}[-{rating}]"
```

- `{key}` is a field; text outside braces is literal.
- `[ ... ]` is an optional section, dropped from the name when its fields are empty.
- `loose_separators: true` tolerates stray spaces around hyphens and any letter case when *reading* existing names (`CAP -HM` and `cap-hm` both parse). New names are always written cleanly.
- `source_match`: fields that must match for an existing type to be a duplication source. For doors, `operation` picks the family.
- `family_filter`: optional regex on family name, e.g. `"^Basic Wall$"` to skip curtain and stacked walls.

Field options:

| key | meaning |
|---|---|
| `param` | Parameter name, or a list of candidate names tried in order (case-insensitive). A project mapping overrides this |
| `builtins` | BuiltInParameter fallbacks, e.g. `["DOOR_WIDTH"]` |
| `kind` | `text`, `length` (converted using `unit`: in/ft/mm/cm/m), or `number` |
| `required` | `false` makes it optional and adds the `(none)` choice |
| `strict` | Only listed `values` are accepted (also tightens name parsing) |
| `values` | `{"code": "WD", "desc": "Wood", "param_value": "Wood"}`. `param_value` is what's stored in the parameter if it differs from the code in the name |
| `write` | `false` = read-only, not set on creation (e.g. wall width driven by layers) |
| `regex` | Override how this field is recognised inside a name |

## Things to confirm against your template

These are guesses in `config/doors.json`. Fix them before relying on it:

- **Parameter names**: `Door Operation`, `PANEL TYPE`/`Door Panel Material`, `Frame Type`, `Frame Material`. Fix them here for the firm default, or per project with Parameters…. If operation isn't a parameter at all, it's read from the name, which is fine.
- **Code lists**: panel, frame style/material, and operation values are placeholders beyond the `WD`, `CAP`, `HM`, `90` in your example.
- **The space in `CAP -HM`**: your example had it. The template writes `CAP-HM`; if the space is intentional, change the template to `{frame_style} -{frame_material}`.
- **Fire rating storage**: assumed the Fire Rating parameter holds `90`. If it holds `90 MIN`, add `"param_value": "90 MIN"` to each value.
- **Non-rated doors**: assumed the suffix is simply dropped. If you write `NR`, add it as a value and make the field required.

## Known limits / roadmap

- **System family construction.** Walls, floors, roofs and ceilings duplicate and rename correctly, but their real definition is the compound structure (layers). v1 doesn't build layers. Natural next step: a `layers` field kind that maps codes to layer recipes and edits `CompoundStructure`.
- **Element parameters** (materials) are reported for manual setting rather than written.
- **Modal window.** Revit is locked while it's open. Making it modeless needs an `ExternalEvent` for the create transaction, the same approach as your AVRO Family Browser fork.
- **Missing families.** If no type of the needed operation exists, it asks you to load the family. A later version could map operation → family file path and load it.
- **Imperial nominal sizes** like `3070` would need a custom field formatter.
- **Promoting project values to the firm standard** is manual for now (copy into the JSON). An export of each project's added values and descriptions would make that a review step.
- Possible extras: export the standard as a type catalogue for onboarding, place an instance of the new type immediately, and an Audit "fix" mode that renames mislabelled types.

## Files

```
TypeConstructor.extension/
  config/doors.json                  door standard (edit me)
  config/_example_basic_walls.json   disabled placeholder for a system family
  lib/typeconstructor/schema.py      naming, parsing, filtering, validation (no Revit)
  lib/typeconstructor/project.py     project layer: mappings, new values, descriptions (no Revit)
  lib/typeconstructor/revit_io.py    reading types, discovering parameters, creating types
  lib/typeconstructor/storage.py     saving project data in the model (Extensible Storage)
  lib/typeconstructor/dialogs.py     value and parameter dialogs (+ xaml/)
  TypeConstructor.tab/Types.panel/
    Type Constructor.pushbutton/     WPF staging window
    Audit Types.pushbutton/          report to pyRevit output
  tests/test_schema.py               unit tests for schema.py
```
