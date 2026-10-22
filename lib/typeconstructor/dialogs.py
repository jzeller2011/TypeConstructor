# -*- coding: utf-8 -*-
"""WPF dialogs: add/describe a value, and map project parameters."""
import os

import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import forms

from System.Windows import FontWeights, Thickness, TextWrapping
from System.Windows.Controls import (CheckBox, ComboBox, ComboBoxItem, Grid,
                                     ColumnDefinition, StackPanel, TextBlock,
                                     TextChangedEventHandler)
from System.Windows.Controls.Primitives import TextBoxBase
from System.Windows import GridLength
from System.Windows.Media import Brushes

from typeconstructor import project
from typeconstructor.schema import NONE, TypeSchema, suggest_param, validate_new_code

XAML_DIR = os.path.join(os.path.dirname(__file__), "xaml")

ORIGIN_TEXT = {
    "firm": u"Firm standard value.",
    "project": u"Added in this project.",
    "found": u"Found on existing types in this project.",
}


# ---------------------------------------------------------------------------
class ValueDialog(forms.WPFWindow):
    """Add a new value to a field, or edit the description of an existing one.

    After ShowDialog(), self.result is (code, description) or None.
    """

    def __init__(self, schema, field, records, initial_code=u"", owner=None):
        forms.WPFWindow.__init__(self, os.path.join(XAML_DIR, "value.xaml"))
        if owner is not None:
            self.Owner = owner
        self.schema, self.field, self.records = schema, field, records
        self.result = None
        self._loaded_from = None

        self.field_tb.Text = u"%s  —  %s" % (field.label, schema.name)
        for o in field.values:
            self.code_cb.Items.Add(o.code)
        self.code_cb.AddHandler(TextBoxBase.TextChangedEvent,
                                TextChangedEventHandler(self._on_code_changed))
        self.save_btn.Click += self._on_save
        self.cancel_btn.Click += lambda s, e: self.Close()

        self.code_cb.Text = u"" if initial_code in (None, NONE) else initial_code
        self._on_code_changed(None, None)

    def _usage(self, code):
        c = self.field.normalize(code).lower()
        return sum(1 for r in self.records
                   if c and self.field.normalize(r.values.get(self.field.key)).lower() == c)

    def _on_code_changed(self, sender, args):
        code = (self.code_cb.Text or u"").strip()
        opt = self.field.find_option(code)
        n = self._usage(code)
        if opt is not None:
            if opt is not self._loaded_from:
                self.desc_tb.Text = opt.desc
                self._loaded_from = opt
            info = ORIGIN_TEXT.get(opt.origin, u"")
            if opt.firm_desc:
                info += u" Firm description: \"%s\" (this project overrides it)." % opt.firm_desc
            err = None
        else:
            # typed past an existing value (WWD1 -> WWD10): drop its description
            if self._loaded_from is not None and self.desc_tb.Text == self._loaded_from.desc:
                self.desc_tb.Text = u""
            self._loaded_from = None
            info = u"New value." if code else u""
            err = validate_new_code(self.schema, self.field.key, code) if code else None
        if code:
            info += u"  Used by %d existing type(s)." % n
        self.info_tb.Text = info
        self.error_tb.Text = err or u""
        self.save_btn.IsEnabled = bool(code) and err is None

    def _on_save(self, sender, args):
        code = (self.code_cb.Text or u"").strip()
        opt = self.field.find_option(code)
        if opt is not None:
            code = opt.code                       # keep canonical case
        elif validate_new_code(self.schema, self.field.key, code):
            return
        self.result = (code, (self.desc_tb.Text or u"").strip())
        self.Close()


# ---------------------------------------------------------------------------
class ParamsDialog(forms.WPFWindow):
    """Map name segments to this project's parameters and pick extra filters.

    After ShowDialog(), self.result is (params, extras) for project.set_mapping,
    or None.
    """

    def __init__(self, base_dict, store, params, resolved, total, owner=None):
        forms.WPFWindow.__init__(self, os.path.join(XAML_DIR, "params.xaml"))
        if owner is not None:
            self.Owner = owner
        self.base = TypeSchema(base_dict)
        self.sec = project.section(store, self.base.name)
        self.params = params
        self.by_name = dict((p.name, p) for p in params)
        self.resolved = resolved
        self.total = total
        self.result = None
        self.map_combos = {}
        self.extra_checks = []

        self.intro_tb.Text = (
            u"Read from the %d %s types in this model. Pick the parameter that holds each "
            u"part of the type name here. Choose \"read from type name only\" if this project "
            u"doesn't store it in a parameter." % (total, self.base.name.lower()))
        self._build_mapping()
        self._build_extras()
        self.save_btn.Click += self._on_save
        self.cancel_btn.Click += lambda s, e: self.Close()

    def _build_mapping(self):
        names = [p.name for p in self.params]
        overrides = self.sec.get("params", {})
        for f in self.base.fields:
            row = Grid()
            row.Margin = Thickness(0, 0, 0, 8)
            c1, c2 = ColumnDefinition(), ColumnDefinition()
            c1.Width = GridLength(170)
            row.ColumnDefinitions.Add(c1)
            row.ColumnDefinitions.Add(c2)

            lbl = TextBlock()
            lbl.Text = f.label
            lbl.FontWeight = FontWeights.SemiBold
            lbl.Margin = Thickness(0, 3, 8, 0)
            row.Children.Add(lbl)

            right = StackPanel()
            Grid.SetColumn(right, 1)
            cb = ComboBox()
            note = TextBlock()
            note.Foreground = Brushes.Gray
            note.FontSize = 11
            note.TextWrapping = TextWrapping.Wrap
            right.Children.Add(cb)
            right.Children.Add(note)
            row.Children.Add(right)

            self._add_item(cb, u"(read from type name only)", u"")
            for p in self.params:
                self._add_item(cb, u"%s   (%d/%d filled · %s)" % (
                    p.name, p.filled, self.total, p.storage), p.name)

            if f.key in overrides:
                chosen, why = overrides[f.key], u"Saved for this project."
                if chosen and chosen not in self.by_name:
                    self._add_item(cb, u"%s   (not found on any type)" % chosen, chosen)
            elif f.key in self.resolved:
                chosen, why = self.resolved[f.key], u"From the firm standard."
            else:
                chosen = suggest_param(f, names) or u""
                if chosen:
                    why = u"Suggested: the firm standard's parameter isn't in this project. Check it."
                elif f.params or f.builtins:
                    why = (u"The firm standard expects '%s', which isn't in this project."
                           % (f.param or u"/".join(f.builtins)))
                else:
                    why = u""
            self._select(cb, chosen)
            cb.SelectionChanged += self._on_map_changed(cb, note, why)
            self._show_note(cb, note, why)
            self.map_panel.Children.Add(row)
            self.map_combos[f.key] = cb

    def _build_extras(self):
        current = set(e["param"] for e in self.sec.get("extra_fields", []))
        for p in self.params:
            if p.storage == "element" or (p.filled == 0 and p.name not in current):
                continue
            chk = CheckBox()
            chk.Content = u"%s   — %s" % (p.name, p.summary(self.total))
            chk.Tag = p.name
            chk.IsChecked = p.name in current
            chk.Margin = Thickness(0, 0, 0, 4)
            self.extra_panel.Children.Add(chk)
            self.extra_checks.append(chk)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _add_item(cb, text, tag):
        it = ComboBoxItem()
        it.Content = text
        it.Tag = tag
        cb.Items.Add(it)

    @staticmethod
    def _select(cb, tag):
        for i in range(cb.Items.Count):
            if cb.Items[i].Tag == tag:
                cb.SelectedIndex = i
                return
        cb.SelectedIndex = 0

    def _show_note(self, cb, note, why):
        tag = cb.SelectedItem.Tag if cb.SelectedItem is not None else u""
        info = self.by_name.get(tag)
        parts = [why] if why else []
        if info is not None and info.samples:
            parts.append(u"Values: " + u", ".join(info.samples[:8]))
        note.Text = u"  ".join(parts)

    def _on_map_changed(self, cb, note, why):
        def handler(sender, args):
            self._show_note(cb, note, u"")
        return handler

    def _on_save(self, sender, args):
        params = {}
        mapped = set()
        for key, cb in self.map_combos.items():
            tag = cb.SelectedItem.Tag if cb.SelectedItem is not None else u""
            if tag:
                mapped.add(tag)
            if tag != self.resolved.get(key, u""):      # only store real overrides
                params[key] = tag
        extras = []
        for chk in self.extra_checks:
            if chk.IsChecked and chk.Tag not in mapped:
                info = self.by_name.get(chk.Tag)
                extras.append({"param": chk.Tag,
                               "kind": info.field_kind() if info else "text"})
        self.result = (params, extras)
        self.Close()
