# -*- coding: utf-8 -*-
"""Stage a new family/system type against your naming standard.

Shows the types already in the project, narrows the list as you fill in
values, blocks duplicates, and creates the new type with a conforming name.
Project-specific values, descriptions and parameter mappings are saved in
the model.
"""
# ============================================================================
# USER SETTINGS
# ============================================================================
# Folder holding the standards (*.json). None = the extension's own config
# folder. Point this at a network share to maintain one firm-wide standard,
# e.g. r"\\fileserver\BIM\Standards\TypeConstructor"
CONFIG_DIR = None

# Standard selected when the window opens (matches "name" in the JSON).
DEFAULT_SCHEMA = "Doors"

# Show types whose names don't follow the template in the list by default.
SHOW_NONCONFORMING = False

# Close the window after a type is created (False = keep staging more).
CLOSE_AFTER_CREATE = False

# Ask for confirmation before creating a type.
CONFIRM_BEFORE_CREATE = True

# How many existing matches to name in the status line before "and N more".
MAX_NAMES_IN_STATUS = 3

# Show the origin of each dropdown value: [firm] standard, [project] added
# here, [found] read off existing types.
SHOW_VALUE_ORIGIN = True
# ============================================================================

import os

import clr
clr.AddReference("System.Data")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore

import clr
# .NET assemblies aren't importable until referenced. System.Data (DataTable)
# isn't loaded by Revit/pyRevit by default; the WPF ones usually are, but
# referencing them explicitly avoids load-order surprises.
clr.AddReference("System.Data")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import forms, revit, script

from System.Data import DataTable
from System import String
from System.Windows import (FontStyles, FontWeights, Thickness, TextWrapping,
                            VerticalAlignment)
from System.Windows.Controls import (Button, ComboBox, ComboBoxItem, Dock, DockPanel,
                                     GridViewColumn, StackPanel, TextBlock,
                                     TextChangedEventHandler, TextSearch)
from System.Windows.Controls.Primitives import TextBoxBase
from System.Windows.Data import Binding
from System.Windows.Media import Brushes

from typeconstructor import config_dir, project, revit_io, storage
from typeconstructor.dialogs import ParamsDialog, ValueDialog
from typeconstructor.schema import NONE, TypeSchema

__title__ = "Type\nConstructor"
__author__ = "Jim"

doc = revit.doc
logger = script.get_logger()
XAML = os.path.join(os.path.dirname(__file__), "ui.xaml")
ORIGIN_TAG = {"firm": u"", "project": u"  [project]", "found": u"  [found]"}


class ConstructorWindow(forms.WPFWindow):
    def __init__(self, bases):
        forms.WPFWindow.__init__(self, XAML)
        self._busy = False
        self.bases = bases                 # firm-standard dicts from the JSON files
        self.base = None
        self.schema = None                 # effective standard for this project
        self.loaded = None
        self.records = []
        self._shown = []
        self.combos = {}
        self.hints = {}
        self.source = None
        try:
            self.store = storage.load(doc)
        except Exception as ex:
            logger.warning(u"Couldn't read Type Constructor project data: %s" % ex)
            self.store = project.empty_store()

        for b in bases:
            self.schema_cb.Items.Add(b["name"])
        self.nonconf_cb.IsChecked = SHOW_NONCONFORMING

        self.schema_cb.SelectionChanged += self.on_schema_changed
        self.clear_btn.Click += self.on_clear
        self.params_btn.Click += self.on_params
        self.nonconf_cb.Click += lambda s, e: self.refresh()
        self.types_lv.MouseDoubleClick += self.on_row_double_click
        self.create_btn.Click += self.on_create

        names = [b["name"] for b in bases]
        self.schema_cb.SelectedIndex = names.index(DEFAULT_SCHEMA) if DEFAULT_SCHEMA in names else 0

    # ------------------------------------------------------------------ load
    def on_schema_changed(self, sender, args):
        self.base = self.bases[self.schema_cb.SelectedIndex]
        self.combos = {}
        self.reload()

    def reload(self, staged=None):
        """Re-read types + project data and rebuild the form, keeping staged values."""
        if staged is None:
            staged = self.staged()
        self.loaded = revit_io.load(doc, self.base, self.store)
        self.schema = self.loaded.schema
        self.records = self.loaded.records
        self._build_fields()
        self._build_columns()
        self._busy = True
        try:
            for k, v in staged.items():
                if k in self.combos:
                    self.combos[k].Text = v
        finally:
            self._busy = False
        self.refresh()

    def _build_fields(self):
        self.fields_panel.Children.Clear()
        self.combos, self.hints = {}, {}
        for f in self.schema.fields:
            row = StackPanel()
            row.Margin = Thickness(0, 0, 0, 10)

            head = DockPanel()
            if f.kind == "text":
                btn = Button()
                btn.Content = u"\u270e"
                btn.Width = 24
                btn.ToolTip = u"Add a new %s value or edit a description" % f.label
                btn.Click += self._value_handler(f.key)
                DockPanel.SetDock(btn, Dock.Right)
                head.Children.Add(btn)
            lbl = TextBlock()
            lbl.Text = f.label if f.required else f.label + u"  (optional)"
            lbl.FontWeight = FontWeights.SemiBold
            lbl.VerticalAlignment = VerticalAlignment.Center
            if f.extra:
                lbl.ToolTip = u"Project filter parameter (not part of the name)"
                lbl.FontStyle = FontStyles.Italic
            head.Children.Add(lbl)
            row.Children.Add(head)

            cb = ComboBox()
            cb.IsEditable = True
            cb.IsTextSearchEnabled = bool(f.values) and f.kind == "text"
            cb.Margin = Thickness(0, 2, 0, 0)
            cb.AddHandler(TextBoxBase.TextChangedEvent,
                          TextChangedEventHandler(self._changed_handler(f.key)))
            row.Children.Add(cb)

            hint = TextBlock()
            hint.Foreground = Brushes.Gray
            hint.FontSize = 11
            hint.TextWrapping = TextWrapping.Wrap
            hint.Text = f.hint
            row.Children.Add(hint)

            self.fields_panel.Children.Add(row)
            self.combos[f.key] = cb
            self.hints[f.key] = hint

    def _build_columns(self):
        gv = self.types_gv
        gv.Columns.Clear()
        cols = [(u"Type name", "c_name")] + \
               [(f.label, "f_" + f.key) for f in self.schema.fields] + \
               [(u"Family", "c_family"), (u"Flags", "c_flags")]
        for header, path in cols:
            c = GridViewColumn()
            c.Header = header
            c.DisplayMemberBinding = Binding(path)
            gv.Columns.Add(c)

    # ------------------------------------------------------------------ state
    def staged(self):
        return dict((k, (cb.Text or u"").strip()) for k, cb in self.combos.items())

    def _fill_options(self, key, staged):
        f = self.schema.by_key[key]
        cb = self.combos[key]
        text = cb.Text
        cb.Items.Clear()
        for code, count, desc in self.schema.options_for(key, self.records, staged):
            opt = f.find_option(code)
            label = code + (u"  \u2014 " + desc if desc and code != NONE else u"")
            if SHOW_VALUE_ORIGIN and opt is not None:
                label += ORIGIN_TAG.get(opt.origin, u"")
            it = ComboBoxItem()
            it.Content = u"%s   (%d)" % (label, count)
            if count == 0:
                it.Foreground = Brushes.Gray   # no existing type with this value
            TextSearch.SetText(it, code)
            cb.Items.Add(it)
        cb.Text = text

    def _changed_handler(self, key):
        def handler(sender, args):
            self.on_field_changed(key)
        return handler

    def _value_handler(self, key):
        def handler(sender, args):
            self.on_value(key)
        return handler

    def on_field_changed(self, key):
        if self._busy:
            return
        self.refresh(active_key=key)

    def _hint_for(self, f, code):
        opt = f.find_option(code)
        if opt is None:
            return f.hint
        if opt.desc:
            return opt.desc
        if opt.origin == "found":
            return u"Found on existing types; no description yet. Click \u270e to add one."
        return f.hint

    def refresh(self, active_key=None):
        if self.schema is None:
            return
        self._busy = True
        try:
            staged = self.staged()
            for k in self.combos:
                if k != active_key:          # don't rebuild the box being typed in
                    self._fill_options(k, staged)
            for f in self.schema.fields:
                self.hints[f.key].Text = self._hint_for(f, staged.get(f.key))
            res = self.schema.evaluate(self.records, staged)
            self._update_list(res)
            self._update_status(res, staged)
        finally:
            self._busy = False

    def _update_list(self, res):
        show_bad = bool(self.nonconf_cb.IsChecked)
        rows = [r for r in res["matches"] if show_bad or r.conforming]
        self._shown = rows

        table = DataTable()
        for col in ["c_name", "c_family", "c_flags"] + ["f_" + f.key for f in self.schema.fields]:
            table.Columns.Add(col, String)
        for r in rows:
            dr = table.NewRow()
            dr["c_name"] = r.name
            dr["c_family"] = r.family
            dr["c_flags"] = r.flags()
            for f in self.schema.fields:
                dr["f_" + f.key] = r.values.get(f.key, u"")
            table.Rows.Add(dr)
        self.types_lv.ItemsSource = table.DefaultView

        hidden = len(res["matches"]) - len(rows)
        self.list_gb.Header = u"Existing types matching staged values: %d%s" % (
            len(rows), u"  (+%d non-conforming hidden)" % hidden if hidden else u"")

        bad = sum(1 for r in self.records if not r.conforming)
        summary = u"%d %s types, %d non-conforming" % (
            len(self.records), self.schema.name.lower(), bad)
        unmapped = self.loaded.unmapped() if self.loaded else []
        if unmapped:
            summary += u"   \u26a0 No parameter found for: %s. Click Parameters\u2026" % \
                u", ".join(f.label for f in unmapped)
            self.summary_tb.Foreground = Brushes.DarkOrange
        else:
            self.summary_tb.Foreground = Brushes.Gray
        self.summary_tb.Text = summary

        if res["exact"] and res["exact"][0] in rows:
            self.types_lv.SelectedIndex = rows.index(res["exact"][0])
            self.types_lv.ScrollIntoView(self.types_lv.SelectedItem)

    def _names(self, recs):
        names = [u"'%s'" % r.name for r in recs[:MAX_NAMES_IN_STATUS]]
        more = len(recs) - MAX_NAMES_IN_STATUS
        return u", ".join(names) + (u" and %d more" % more if more > 0 else u"")

    def _update_status(self, res, staged):
        self.name_tb.Text = res["name"]
        self.source = None
        warn = (u"  \u26a0 " + u"; ".join(res["warnings"])) if res["warnings"] else u""

        if not res["complete"]:
            missing = [e for e in res["errors"] if e.endswith(u"is required")]
            invalid = [e for e in res["errors"] if e not in missing]
            if invalid:
                self._status(u"; ".join(invalid) + warn, Brushes.Firebrick)
            else:
                self._status(u"%d existing type(s) match so far. Still needed: %s%s" % (
                    len(res["matches"]),
                    u", ".join(e.replace(u" is required", u"") for e in missing), warn),
                    Brushes.DimGray)
            self.create_btn.IsEnabled = False
            return

        if res["exact"]:
            self._status(u"Already exists: %s. Use that type instead." % self._names(res["exact"]),
                         Brushes.Firebrick)
        elif res["name_taken"] is not None:
            self._status(u"The name is already used by a type whose parameters don't match it "
                         u"(%s). Fix that type first - see Audit Types." % res["name_taken"].family,
                         Brushes.Firebrick)
        else:
            self.source = self.schema.choose_source(self.records, staged)
            if self.source is None:
                keys = u", ".join(u"%s = %s" % (self.schema.by_key[k].label, staged.get(k))
                                  for k in self.schema.source_match)
                self._status(u"No existing type with %s to duplicate from. Load that family "
                             u"into the project first." % keys, Brushes.Firebrick)
            else:
                self._status(u"\u2714 No existing type has this configuration. Will duplicate "
                             u"'%s' (%s) and set its parameters.%s" % (
                                 self.source.name, self.source.family, warn),
                             Brushes.DarkOrange if warn else Brushes.ForestGreen)
        self.create_btn.IsEnabled = self.source is not None

    def _status(self, text, brush):
        self.status_tb.Text = text
        self.status_tb.Foreground = brush

    # ------------------------------------------------------- project data
    def _save_store(self, mutator, description):
        try:
            self.store = storage.update(doc, mutator, description)
            return True
        except Exception as ex:
            logger.error(ex)
            forms.alert(u"Couldn't save to the project:\n%s\n\nIn a workshared model, someone "
                        u"else may be editing Type Constructor data. Sync and try again."
                        % ex, exitscript=False)
            return False

    def on_value(self, key):
        f = self.schema.by_key[key]
        dlg = ValueDialog(self.schema, f, self.records, self.combos[key].Text, owner=self)
        dlg.ShowDialog()
        if dlg.result is None:
            return
        code, desc = dlg.result
        opt = f.find_option(code)
        name = self.schema.name
        if opt is not None and opt.origin == "firm" and desc == (opt.firm_desc or opt.desc):
            # same as the firm description: drop any project override
            mutator = lambda st: project.remove_value(st, name, key, code)
        else:
            mutator = lambda st: project.set_value(st, name, key, code, desc)
        if self._save_store(mutator, u"Type Constructor: %s value %s" % (f.label, code)):
            staged = self.staged()
            staged[key] = code
            self.reload(staged)

    def on_params(self, sender, args):
        params, resolved, total = revit_io.discover_parameters(doc, self.base)
        dlg = ParamsDialog(self.base, self.store, params, resolved, total, owner=self)
        dlg.ShowDialog()
        if dlg.result is None:
            return
        mapping, extras = dlg.result
        name = self.base["name"]
        if self._save_store(lambda st: project.set_mapping(st, name, mapping, extras),
                            u"Type Constructor: %s parameters" % name):
            self.reload()

    # ---------------------------------------------------------------- actions
    def on_clear(self, sender, args):
        self._busy = True
        try:
            for cb in self.combos.values():
                cb.Text = u""
        finally:
            self._busy = False
        self.refresh()

    def on_row_double_click(self, sender, args):
        i = self.types_lv.SelectedIndex
        if i < 0 or i >= len(self._shown):
            return
        rec = self._shown[i]
        self._busy = True
        try:
            for f in self.schema.fields:
                v = rec.values.get(f.key, u"")
                self.combos[f.key].Text = v if (v or f.required) else NONE
        finally:
            self._busy = False
        self.refresh()

    def on_create(self, sender, args):
        staged = self.staged()
        res = self.schema.evaluate(self.records, staged)   # re-check, never trust the UI state
        src = self.schema.choose_source(self.records, staged)
        if not res["ready"] or src is None:
            self.refresh()
            return
        if CONFIRM_BEFORE_CREATE and not forms.alert(
                u"Create '%s'?\n\nDuplicated from: %s (%s)" % (res["name"], src.name, src.family),
                yes=True, no=True):
            return
        try:
            with revit.Transaction(u"Type Constructor: create " + res["name"]):
                new_type, warnings = revit_io.create_type(doc, self.schema, staged, src)
        except Exception as ex:
            logger.error(ex)
            forms.alert(u"Could not create the type:\n%s" % ex, exitscript=False)
            return

        self.reload(staged)
        msg = u"Created '%s'." % res["name"]
        if warnings:
            forms.alert(msg + u"\n\nCheck these by hand:\n- " + u"\n- ".join(warnings))
        self._status(msg + (u"  (%d warning(s))" % len(warnings) if warnings else u""),
                     Brushes.ForestGreen)
        if CLOSE_AFTER_CREATE:
            self.Close()


# ============================================================================
def main():
    folder = config_dir(CONFIG_DIR)
    schemas, errors = TypeSchema.load_dir(folder)
    if errors:
        forms.alert(u"Some standards could not be loaded:\n\n" + u"\n".join(errors), warn_icon=True)
    if not schemas:
        forms.alert(u"No enabled standards (*.json) found in:\n%s" % folder, exitscript=True)
    ConstructorWindow([s.raw for s in schemas]).ShowDialog()


main()
