# -*- coding: utf-8 -*-
"""Pure-Python core of Type Constructor (no Revit imports).

Everything here runs in IronPython 2.7 (pyRevit) and CPython 3, so the naming,
parsing, filtering and validation logic can be unit-tested outside Revit.

Concepts
--------
TypeSchema   One naming standard (e.g. "Doors"), loaded from a JSON file.
Field        One segment of the name / one parameter (e.g. "panel" -> WD).
TypeRecord   An existing type in the project, with a value per field.
staged       A dict {field_key: code} of what the user has typed so far.
"""
from __future__ import print_function

import copy
import io
import json
import os
import re

NONE = u"(none)"          # explicit "this optional field is empty" choice

_UNIT_PER_FOOT = {"in": 12.0, "ft": 1.0, "mm": 304.8, "cm": 30.48, "m": 0.3048}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _text(v):
    if v is None:
        return u""
    try:
        return unicode(v).strip()   # noqa: F821  (IronPython / Py2)
    except NameError:
        return str(v).strip()


def format_number(value):
    """3.0 -> '3', 35.5 -> '35.5', 35.4999999 -> '35.5'."""
    v = round(float(value), 3)
    if abs(v - round(v)) < 1e-6:
        return u"%d" % int(round(v))
    return (u"%.3f" % v).rstrip(u"0").rstrip(u".")


def feet_to_unit(feet, unit):
    return float(feet) * _UNIT_PER_FOOT[unit]


def unit_to_feet(value, unit):
    return float(value) / _UNIT_PER_FOOT[unit]


def is_number(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Field
# --------------------------------------------------------------------------
class ValueOption(object):
    def __init__(self, d):
        if not isinstance(d, dict):
            d = {"code": d}
        self.code = _text(d["code"])
        self.desc = _text(d.get("desc", u""))
        # what is written to / read from the Revit parameter (defaults to code)
        self.param_value = _text(d.get("param_value", self.code))
        # firm = in the JSON standard, project = added in this model,
        # found = read off an existing type's parameter
        self.origin = d.get("origin", "firm")
        self.firm_desc = _text(d.get("firm_desc", u""))   # set when a project overrides it


class Field(object):
    def __init__(self, d):
        self.key = d["key"]
        self.label = d.get("label", self.key)
        p = d.get("param")                             # name or list of candidate names
        self.params = [p] if isinstance(p, (type(u""), type(""))) else list(p or [])
        self.builtins = d.get("builtins", [])          # BuiltInParameter fallbacks
        self.extra = d.get("extra", False)             # project filter field, not in name
        self.kind = d.get("kind", "text")              # text | length | number
        self.unit = d.get("unit", "in")                # for kind == length
        self.required = d.get("required", True)
        self.strict = d.get("strict", False)           # only listed codes allowed
        self.write = d.get("write", True)              # set param on creation
        self.regex = d.get("regex")                    # override name-parse regex
        self.hint = d.get("hint", u"")
        self.values = [ValueOption(v) for v in d.get("values", [])]

    # -- code <-> param value ------------------------------------------------
    def find_option(self, code):
        c = _text(code).lower()
        for o in self.values:
            if o.code.lower() == c:
                return o
        return None

    def normalize(self, code):
        """Canonical form of a code, used for every comparison."""
        c = _text(code)
        if not c or c == NONE:
            return u""
        if self.kind in ("length", "number"):
            return format_number(c) if is_number(c) else c
        opt = self.find_option(c)
        return opt.code if opt else c

    def code_from_param(self, raw):
        """Map a raw (already unit-converted) param string back to a code."""
        r = _text(raw)
        if not r:
            return u""
        if self.kind in ("length", "number"):
            return self.normalize(r)
        for o in self.values:
            if o.param_value.lower() == r.lower() or o.code.lower() == r.lower():
                return o.code
        return r

    def param_value_for(self, code):
        opt = self.find_option(code)
        return opt.param_value if opt else self.normalize(code)

    def describe(self, code):
        opt = self.find_option(code)
        return opt.desc if opt else u""

    def validate(self, code):
        """Return an error string, or None if the staged code is acceptable."""
        c = _text(code)
        if not c or c == NONE:
            if self.required:
                return u"%s is required" % self.label
            return None
        if self.kind in ("length", "number"):
            if not is_number(c) or float(c) <= 0:
                return u"%s must be a positive number" % self.label
            return None
        if self.strict and self.values and not self.find_option(c):
            return (u"%s '%s' isn't a known value. Click ✎ next to %s to add it "
                    u"with a description." % (self.label, c, self.label))
        return None

    @property
    def param(self):
        return self.params[0] if self.params else None

    def name_regex(self):
        if self.regex:
            return self.regex
        if self.kind in ("length", "number"):
            return r"\d+(?:\.\d+)?"
        if self.strict and self.values:
            codes = sorted((o.code for o in self.values), key=len, reverse=True)
            return u"|".join(re.escape(c) for c in codes)
        return r".+?"


# --------------------------------------------------------------------------
# name template:  "{a}-{b}x{c}[-{d}]"   ([...] = optional section)
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"(\{[a-zA-Z0-9_]+\}|\[|\])")


def parse_template(template):
    root = []
    stack = [root]
    for tok in _TOKEN.split(template):
        if not tok:
            continue
        if tok == u"[":
            group = []
            stack[-1].append(("opt", group))
            stack.append(group)
        elif tok == u"]":
            if len(stack) == 1:
                raise ValueError("Unbalanced ']' in template: %s" % template)
            stack.pop()
        elif tok.startswith(u"{"):
            stack[-1].append(("field", tok[1:-1]))
        else:
            stack[-1].append(("lit", tok))
    if len(stack) != 1:
        raise ValueError("Unbalanced '[' in template: %s" % template)
    return root


def _fields_in(parts):
    out = []
    for kind, val in parts:
        if kind == "field":
            out.append(val)
        elif kind == "opt":
            out.extend(_fields_in(val))
    return out


# --------------------------------------------------------------------------
# TypeRecord
# --------------------------------------------------------------------------
class TypeRecord(object):
    def __init__(self, name, family, elem_id=None):
        self.name = name
        self.family = family
        self.id = elem_id
        self.values = {}        # key -> code (resolved)
        self.from_name = {}     # key -> code parsed from the name
        self.from_param = {}    # key -> code read from the parameter
        self.conforming = False
        self.mismatches = []    # field labels where name != parameter
        self.duplicate_of = []  # other names with the same configuration

    def flags(self):
        f = []
        if not self.conforming:
            f.append(u"non-conforming name")
        if self.mismatches:
            f.append(u"name≠param: " + u", ".join(self.mismatches))
        if self.duplicate_of:
            f.append(u"same config as: " + u", ".join(self.duplicate_of))
        return u"; ".join(f)


# --------------------------------------------------------------------------
# TypeSchema
# --------------------------------------------------------------------------
class TypeSchema(object):
    def __init__(self, d, path=None):
        self.path = path
        self.raw = copy.deepcopy(d)                   # for building project variants
        self.name = d["name"]
        self.category = d["category"]                 # BuiltInCategory name
        self.family_filter = d.get("family_filter") or u""
        self.template = d["template"]
        self.loose = d.get("loose_separators", True)
        self.source_match = d.get("source_match", [])
        self.fields = [Field(f) for f in d["fields"]]
        self.by_key = dict((f.key, f) for f in self.fields)
        self.parts = parse_template(self.template)
        missing = [k for k in _fields_in(self.parts) if k not in self.by_key]
        if missing:
            raise ValueError("%s: template uses undefined fields %s" % (self.name, missing))
        # fields that make up the name decide "is this the same type?";
        # extra filter-only fields don't.
        self.identity_keys = _fields_in(self.parts)
        self._rx = re.compile(u"^\\s*" + self._regex(self.parts) + u"\\s*$",
                              re.IGNORECASE if self.loose else 0)
        self._fam_rx = re.compile(self.family_filter) if self.family_filter else None

    # -- loading ---------------------------------------------------------------
    @classmethod
    def load(cls, path):
        with io.open(path, "r", encoding="utf-8") as fp:
            return cls(json.load(fp), path)

    @classmethod
    def load_dir(cls, folder):
        out, errors = [], []
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith(".json"):
                continue
            p = os.path.join(folder, fn)
            try:
                with io.open(p, "r", encoding="utf-8") as fp:
                    d = json.load(fp)
                if d.get("enabled", True):
                    out.append(cls(d, p))
            except Exception as ex:   # bad config shouldn't kill the tool
                errors.append(u"%s: %s" % (fn, ex))
        return out, errors

    def includes_family(self, family_name):
        return True if not self._fam_rx else bool(self._fam_rx.search(family_name or u""))

    # -- name <-> values -------------------------------------------------------
    def _regex(self, parts):
        out = []
        for kind, val in parts:
            if kind == "lit":
                lit = re.escape(val)
                if self.loose:
                    lit = re.sub(r"(\\?-)", r"\\s*-\\s*", lit)
                out.append(lit)
            elif kind == "field":
                out.append(u"(?P<%s>%s)" % (val, self.by_key[val].name_regex()))
            else:
                out.append(u"(?:%s)?" % self._regex(val))
        return u"".join(out)

    def parse_name(self, name):
        """Return {key: code} or None if the name doesn't follow the template."""
        m = self._rx.match(name or u"")
        if not m:
            return None
        return dict((k, self.by_key[k].normalize(v)) for k, v in m.groupdict().items())

    def compose(self, staged, parts=None):
        parts = self.parts if parts is None else parts
        out = []
        for kind, val in parts:
            if kind == "lit":
                out.append(val)
            elif kind == "field":
                out.append(self.by_key[val].normalize(staged.get(val, u"")))
            else:
                if all(self.by_key[k].normalize(staged.get(k, u"")) for k in _fields_in(val)):
                    out.append(self.compose(staged, val))
        return u"".join(out)

    # -- records ---------------------------------------------------------------
    def build_record(self, name, family, elem_id, param_raw):
        """param_raw: {key: raw string or None if the type has no such param}."""
        rec = TypeRecord(name, family, elem_id)
        parsed = self.parse_name(name)
        rec.conforming = parsed is not None
        rec.from_name = parsed or {}
        for f in self.fields:
            pv = f.code_from_param(param_raw.get(f.key))
            nv = rec.from_name.get(f.key, u"")
            rec.from_param[f.key] = pv
            rec.values[f.key] = pv or nv
            if pv and nv and pv.lower() != nv.lower():
                rec.mismatches.append(f.label)
        return rec

    def config_key(self, values):
        return tuple(self.by_key[k].normalize(values.get(k, u"")).lower()
                     for k in self.identity_keys)

    def mark_duplicates(self, records):
        groups = {}
        for r in records:
            if r.conforming:
                groups.setdefault(self.config_key(r.values), []).append(r)
        for grp in groups.values():
            if len(grp) > 1:
                for r in grp:
                    r.duplicate_of = [o.name for o in grp if o is not r]

    # -- filtering -------------------------------------------------------------
    def _field_matches(self, f, rec_value, staged_value):
        s = _text(staged_value)
        if not s:
            return True                                    # wildcard
        if s == NONE:
            return not _text(rec_value)
        rv, sv = f.normalize(rec_value), f.normalize(s)
        if f.kind in ("length", "number") and is_number(rv) and is_number(sv):
            return abs(float(rv) - float(sv)) < 1e-6
        return rv.lower() == sv.lower()

    def filter(self, records, staged, skip_key=None):
        out = []
        for r in records:
            ok = True
            for f in self.fields:
                if f.key == skip_key:
                    continue
                if not self._field_matches(f, r.values.get(f.key), staged.get(f.key)):
                    ok = False
                    break
            if ok:
                out.append(r)
        return out

    def options_for(self, key, records, staged):
        """Faceted choices for one field: [(code, count, desc)].

        Counts come from types that match everything *else* staged, so each
        dropdown only advertises values that still lead somewhere.
        """
        f = self.by_key[key]
        pool = self.filter(records, staged, skip_key=key)
        counts = {}
        for r in pool:
            v = f.normalize(r.values.get(key))
            if v:
                counts[v] = counts.get(v, 0) + 1
        codes = [o.code for o in f.values]
        extra = [c for c in counts if c not in codes]
        if f.kind in ("length", "number"):
            extra.sort(key=lambda c: float(c) if is_number(c) else 1e12)
        else:
            extra.sort(key=lambda c: c.lower())
        out = [(c, counts.get(c, 0), f.describe(c)) for c in codes + extra]
        if not f.required:
            out.insert(0, (NONE, sum(1 for r in pool if not _text(r.values.get(key))), u"leave empty"))
        return out

    def choose_source(self, records, staged):
        """Best existing type to duplicate from (same family-driving values first)."""
        cands = [r for r in records if r.id is not None]
        for k in self.source_match:
            want = self.by_key[k].normalize(staged.get(k))
            if want:
                cands = [r for r in cands
                         if self.by_key[k].normalize(r.values.get(k)).lower() == want.lower()]
        if not cands:
            return None

        def score(r):
            # blank == blank counts too, so a non-rated type prefers a non-rated source
            s = sum(1 for k in self.identity_keys
                    if self.by_key[k].normalize(staged.get(k)).lower() ==
                    self.by_key[k].normalize(r.values.get(k)).lower())
            return (-int(r.conforming), -s, r.name.lower())
        return sorted(cands, key=score)[0]

    # -- the main question: "can I make this, and is it already here?" ----------
    def evaluate(self, records, staged):
        res = {"errors": [], "warnings": [], "matches": [], "exact": [],
               "name": u"", "name_taken": None, "complete": False, "ready": False}
        res["matches"] = self.filter(records, staged)
        for f in self.fields:
            err = f.validate(staged.get(f.key))
            if err:
                res["errors"].append(err)
            elif f.values and _text(staged.get(f.key)) not in (u"", NONE) \
                    and not f.find_option(staged.get(f.key)):
                res["warnings"].append(u"%s '%s' is not a standard value" % (f.label, staged.get(f.key)))
        res["complete"] = not res["errors"]
        res["name"] = self.compose(staged)
        if res["complete"]:
            want = self.config_key(staged)
            res["exact"] = [r for r in records if self.config_key(r.values) == want]
            for r in records:
                if r.name.strip().lower() == res["name"].strip().lower():
                    res["name_taken"] = r
                    break
            res["ready"] = not res["exact"] and res["name_taken"] is None
        return res


# --------------------------------------------------------------------------
# helpers for adding values / mapping parameters
# --------------------------------------------------------------------------
def validate_new_code(schema, key, code):
    """Can `code` be added to field `key` without breaking name parsing?

    Builds a sample name with the code in it and checks it reads back the same.
    Returns an error string or None.
    """
    c = _text(code)
    f = schema.by_key[key]
    if not c:
        return u"Enter a value."
    if c == NONE:
        return u"'%s' is reserved." % NONE
    if f.kind in ("length", "number"):
        return None if is_number(c) else u"%s must be a number." % f.label
    if key not in schema.identity_keys:
        return None                      # not in the name, anything goes
    seps = set(ch for kind, lit in _literals(schema.parts) for ch in lit
               if not ch.isalnum() and not ch.isspace())
    bad = sorted(ch for ch in seps if ch in c)
    if bad:
        return (u"'%s' contains '%s', which separates parts of the type name (%s)."
                % (c, u"".join(bad), schema.template))
    d = copy.deepcopy(schema.raw)
    for fd in d["fields"]:
        if fd["key"] == key:
            vals = fd.setdefault("values", [])
            if not any(_text(v["code"] if isinstance(v, dict) else v).lower() == c.lower()
                       for v in vals):
                vals.append({"code": c})
    test = TypeSchema(d)
    sample = {}
    for k in test.identity_keys:
        tf = test.by_key[k]
        if k == key:
            sample[k] = c
        elif tf.kind in ("length", "number"):
            sample[k] = u"36"
        elif tf.values:
            sample[k] = tf.values[0].code
        else:
            sample[k] = u"X"
    parsed = test.parse_name(test.compose(sample))
    if not parsed or parsed.get(key, u"").lower() != c.lower():
        return (u"'%s' would make type names ambiguous (it probably contains a "
                u"separator used by the naming template: %s)." % (c, schema.template))
    return None


def _literals(parts):
    for kind, val in parts:
        if kind == "lit":
            yield kind, val
        elif kind == "opt":
            for item in _literals(val):
                yield item


def _squash(s):
    return re.sub(r"[^a-z0-9]", u"", _text(s).lower())


def suggest_param(field, param_names):
    """Best guess at which project parameter holds this field, or None."""
    by_lower = dict((n.lower(), n) for n in param_names)
    for cand in field.params:                                  # configured name, any case
        if cand.lower() in by_lower:
            return by_lower[cand.lower()]
    squashed = dict((_squash(n), n) for n in param_names)
    label = re.sub(r"\(.*?\)", u"", field.label)
    for cand in field.params + [label, field.key]:             # ignoring spaces/case
        if _squash(cand) in squashed:
            return squashed[_squash(cand)]
    starts = [n for n in param_names if _squash(n).startswith(_squash(field.key))]
    return starts[0] if len(starts) == 1 else None
