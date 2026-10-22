# -*- coding: utf-8 -*-
"""Report types that break the naming standard: non-conforming names,
names that disagree with their parameters, duplicate configurations, and
project values that have no description yet."""
# ============================================================================
# USER SETTINGS
# ============================================================================
CONFIG_DIR = None            # same meaning as in Type Constructor
INCLUDE_NONCONFORMING = True
INCLUDE_MISMATCHES = True
INCLUDE_DUPLICATES = True
INCLUDE_UNDESCRIBED = True   # values found on types that nobody has described
# ============================================================================

from pyrevit import forms, revit, script

from typeconstructor import config_dir, project, revit_io, storage
from typeconstructor.schema import TypeSchema

__title__ = "Audit\nTypes"

doc = revit.doc
output = script.get_output()

schemas, errors = TypeSchema.load_dir(config_dir(CONFIG_DIR))
for e in errors:
    output.print_md(u"**Config error:** %s" % e)
if not schemas:
    forms.alert("No enabled standards found.", exitscript=True)

try:
    store = storage.load(doc)
except Exception as ex:
    output.print_md(u"**Couldn't read project data, using firm standard only:** %s" % ex)
    store = project.empty_store()

for base in schemas:
    res = revit_io.load(doc, base.raw, store)
    schema, recs = res.schema, res.records
    output.print_md(u"# %s  \nTemplate: `%s`  \n%d types checked" % (
        schema.name, schema.template, len(recs)))

    unmapped = res.unmapped()
    if unmapped:
        output.print_md(u"**No parameter found in this project for:** %s. "
                        u"Values are read from type names only. Map them with "
                        u"Type Constructor > Parameters." % u", ".join(f.label for f in unmapped))

    if INCLUDE_NONCONFORMING:
        bad = [r for r in recs if not r.conforming]
        if bad:
            output.print_table(
                [[output.linkify(r.id), r.name, r.family] for r in bad],
                columns=["Id", "Type name", "Family"],
                title=u"Names that don't follow the template (%d)" % len(bad))

    if INCLUDE_MISMATCHES:
        mm = [r for r in recs if r.conforming and r.mismatches]
        if mm:
            rows = []
            for r in mm:
                for f in schema.fields:
                    if f.label in r.mismatches:
                        rows.append([output.linkify(r.id), r.name, f.label,
                                     r.from_name.get(f.key, u""), r.from_param.get(f.key, u"")])
            output.print_table(rows, columns=["Id", "Type name", "Field", "In name", "In parameter"],
                               title=u"Names that disagree with their parameters (%d types)" % len(mm))

    if INCLUDE_DUPLICATES:
        seen, rows = set(), []
        for r in recs:
            if r.duplicate_of and r.name not in seen:
                group = [r.name] + r.duplicate_of
                seen.update(group)
                rows.append([u", ".join(group)])
        if rows:
            output.print_table(rows, columns=["Types with identical configuration"],
                               title=u"Duplicates (%d groups)" % len(rows))

    if INCLUDE_UNDESCRIBED:
        rows = []
        for f in schema.fields:
            for o in f.values:
                if o.origin == "found" and not o.desc:
                    n = sum(1 for r in recs if f.normalize(r.values.get(f.key)) == o.code)
                    rows.append([f.label, o.code, n])
        if rows:
            output.print_table(rows, columns=["Field", "Value", "Types using it"],
                               title=u"Values with no description (%d)" % len(rows))

    if not any(r for r in recs if not r.conforming or r.mismatches or r.duplicate_of):
        output.print_md(u"No naming issues found.")
