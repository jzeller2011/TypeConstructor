# -*- coding: utf-8 -*-
"""Revit API side: read existing types, discover parameters, create new types."""
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, Element,
                               FilteredElementCollector, StorageType)

from typeconstructor import project
from typeconstructor.schema import (NONE, TypeSchema, feet_to_unit,
                                    format_number, unit_to_feet)


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------
def type_name(t):
    p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
    if p is not None and p.AsString():
        return p.AsString()
    return Element.Name.GetValue(t)


def family_name(t):
    try:
        return t.FamilyName or u""
    except Exception:
        p = t.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
        return p.AsString() if p is not None else u""


def category_types(doc, schema):
    bic = getattr(BuiltInCategory, schema.category)
    out = []
    for t in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsElementType():
        if schema.includes_family(family_name(t)):
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------
def find_param(elem, field):
    for name in field.params:
        p = elem.LookupParameter(name)
        if p is not None:
            return p
    if field.params:                                   # same name, different case
        wanted = set(n.lower() for n in field.params)
        for p in elem.Parameters:
            if p.Definition is not None and p.Definition.Name.lower() in wanted:
                return p
    for b in field.builtins:
        bip = getattr(BuiltInParameter, b, None)
        if bip is not None:
            p = elem.get_Parameter(bip)
            if p is not None:
                return p
    return None


def is_length(p):
    try:                                               # Revit 2022+
        from Autodesk.Revit.DB import SpecTypeId
        return p.Definition.GetDataType() == SpecTypeId.Length
    except Exception:
        pass
    try:                                               # older
        from Autodesk.Revit.DB import ParameterType
        return p.Definition.ParameterType == ParameterType.Length
    except Exception:
        return False


def read_param(doc, p, field):
    """Raw string value for comparison; lengths converted to the field's unit."""
    if p is None:
        return None
    st = p.StorageType
    if st == StorageType.Double:
        if not p.HasValue:
            return u""
        v = p.AsDouble()
        if field.kind == "length":
            v = feet_to_unit(v, field.unit)
        return format_number(v)
    if st == StorageType.Integer:
        return u"%d" % p.AsInteger()
    if st == StorageType.String:
        return p.AsString() or u""
    if st == StorageType.ElementId:
        el = doc.GetElement(p.AsElementId())
        return Element.Name.GetValue(el) if el is not None else u""
    return p.AsValueString() or u""


def display_value(p):
    """Human-readable value for the Parameters dialog samples."""
    try:
        if p.StorageType == StorageType.String:
            return p.AsString() or u""
        return p.AsValueString() or u""
    except Exception:
        return u""


def write_param(doc, p, field, code):
    """Set a parameter from a staged code. Returns a warning string or None."""
    if p.IsReadOnly:
        return u"%s is read-only on this family (left as duplicated)" % field.label
    raw = u"" if code in (None, NONE) else field.param_value_for(code)
    st = p.StorageType
    if st == StorageType.String:
        p.Set(raw)
        return None
    if not raw:
        return None  # nothing to clear on numeric params
    if st == StorageType.Double:
        v = float(raw)
        if field.kind == "length":
            v = unit_to_feet(v, field.unit)
        p.Set(v)
        return None
    if st == StorageType.Integer:
        p.Set(int(float(raw)))
        return None
    if st == StorageType.ElementId:
        return u"%s is an element (e.g. material) parameter - set it by hand" % field.label
    return u"%s: unsupported parameter type" % field.label


# ---------------------------------------------------------------------------
# load: firm standard + project store + what's actually in the model
# ---------------------------------------------------------------------------
class LoadResult(object):
    def __init__(self, schema, records, param_hits, type_count):
        self.schema = schema
        self.records = records
        self.param_hits = param_hits    # field key -> number of types that have its param
        self.type_count = type_count

    def unmapped(self):
        """Fields that expect a parameter but none of the types have it."""
        return [f for f in self.schema.fields
                if (f.params or f.builtins) and self.param_hits.get(f.key, 0) == 0]


def load(doc, base_dict, store):
    sec = project.section(store, base_dict["name"])
    first = TypeSchema(project.effective_dict(base_dict, sec))
    types = category_types(doc, first)

    raws, hits = [], dict((f.key, 0) for f in first.fields)
    for t in types:
        raw = {}
        for f in first.fields:
            p = find_param(t, f)
            if p is not None:
                hits[f.key] += 1
            raw[f.key] = read_param(doc, p, f)
        raws.append((t, raw))

    # values already used on types become known values (so they parse and validate)
    discovered = {}
    for f in first.fields:
        if f.kind == "text":
            codes = set()
            for _, raw in raws:
                c = f.code_from_param(raw.get(f.key))
                if c:
                    codes.add(c)
            discovered[f.key] = codes

    schema = TypeSchema(project.effective_dict(base_dict, sec, discovered))
    records = [schema.build_record(type_name(t), family_name(t), t.Id, raw) for t, raw in raws]
    schema.mark_duplicates(records)
    records.sort(key=lambda r: r.name.lower())
    return LoadResult(schema, records, hits, len(types))


def collect_types(doc, schema):
    """Firm-standard-only collection (kept for simple callers)."""
    return load(doc, schema.raw, project.empty_store()).records


# ---------------------------------------------------------------------------
# parameter discovery for the Parameters dialog
# ---------------------------------------------------------------------------
class ParamInfo(object):
    def __init__(self, name, storage):
        self.name = name
        self.storage = storage          # "text" | "length" | "number" | "integer" | "element"
        self.on_types = 0
        self.filled = 0
        self.samples = []
        self.shared = False

    def summary(self, total):
        s = u"%d/%d types filled · %s" % (self.filled, total, self.storage)
        if self.samples:
            s += u" · e.g. " + u", ".join(self.samples[:5])
        return s

    def field_kind(self):
        return {"length": "length", "number": "number", "integer": "number"}.get(self.storage, "text")


def discover_parameters(doc, base_dict):
    """Every type parameter on this standard's types, plus which one the firm
    standard's own mapping resolves to for each field."""
    base = TypeSchema(base_dict)
    types = category_types(doc, base)
    infos = {}
    resolved = {}
    for t in types:
        for p in t.Parameters:
            d = p.Definition
            if d is None:
                continue
            info = infos.get(d.Name)
            if info is None:
                st = p.StorageType
                kind = ("length" if is_length(p) else "number") if st == StorageType.Double else \
                       {StorageType.String: "text", StorageType.Integer: "integer",
                        StorageType.ElementId: "element"}.get(st, "text")
                info = infos[d.Name] = ParamInfo(d.Name, kind)
                info.shared = bool(getattr(p, "IsShared", False))
            info.on_types += 1
            v = display_value(p)
            if v:
                info.filled += 1
                if v not in info.samples and len(info.samples) < 8:
                    info.samples.append(v)
        for f in base.fields:
            if f.key not in resolved:
                p = find_param(t, f)
                if p is not None and p.Definition is not None:
                    resolved[f.key] = p.Definition.Name
    params = sorted(infos.values(), key=lambda i: i.name.lower())
    return params, resolved, len(types)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
def create_type(doc, schema, staged, source_record):
    """Duplicate source_record and apply staged values. Call inside a Transaction.

    Returns (new_type, [warnings]).
    """
    src = doc.GetElement(source_record.id)
    new_name = schema.compose(staged)
    new_type = src.Duplicate(new_name)
    warnings = []
    for f in schema.fields:
        if not f.write:
            continue
        p = find_param(new_type, f)
        if p is None:
            if staged.get(f.key) not in (None, u"", NONE) and (f.params or f.builtins):
                warnings.append(u"%s: no parameter '%s' on family %s - value only in the name"
                                % (f.label, f.param or u"/".join(f.builtins), source_record.family))
            continue
        w = write_param(doc, p, f, staged.get(f.key))
        if w:
            warnings.append(w)
    return new_type, warnings
