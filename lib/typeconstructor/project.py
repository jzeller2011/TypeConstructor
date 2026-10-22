# -*- coding: utf-8 -*-
"""Project-level layer on top of the firm standard (no Revit imports).

The store is a plain dict saved inside the Revit model (see storage.py):

{
  "version": 1,
  "schemas": {
    "Doors": {
      "params":       {"panel": "PANEL TYPE", "operation": ""},   # "" = read from name only
      "extra_fields": [{"key": "x_hardware_set", "label": "Hardware Set",
                        "param": "Hardware Set", "kind": "text"}],
      "values":       {"panel": {"WWD3": {"desc": "WD, project panel style"}}}
    }
  }
}

Precedence when building the effective standard:
  firm JSON  <  project mapping/values/descriptions  <  values found on types
(found values only add codes; they never override a description).
"""
import copy
import re

STORE_VERSION = 1


def empty_store():
    return {"version": STORE_VERSION, "schemas": {}}


def section(store, schema_name, create=False):
    schemas = store.setdefault("schemas", {}) if create else store.get("schemas", {})
    sec = schemas.get(schema_name)
    if sec is None:
        sec = {"params": {}, "extra_fields": [], "values": {}}
        if create:
            schemas[schema_name] = sec
    for k, default in (("params", {}), ("extra_fields", []), ("values", {})):
        sec.setdefault(k, default)
    return sec


def extra_key(param_name):
    return u"x_" + re.sub(r"[^a-z0-9]+", u"_", param_name.lower()).strip(u"_")


# -- mutations (each takes the whole store so it can be re-read before saving) --
def set_value(store, schema_name, field_key, code, desc):
    vals = section(store, schema_name, create=True)["values"].setdefault(field_key, {})
    for existing in list(vals):                  # keep one entry per code, any case
        if existing.lower() == code.lower() and existing != code:
            del vals[existing]
    vals[code] = {"desc": desc or u""}
    return store


def remove_value(store, schema_name, field_key, code):
    vals = section(store, schema_name, create=True)["values"].get(field_key, {})
    for existing in list(vals):
        if existing.lower() == code.lower():
            del vals[existing]
    return store


def set_mapping(store, schema_name, params, extras):
    """params: {field_key: param_name or ""}; extras: [{"label", "param", "kind"}]."""
    sec = section(store, schema_name, create=True)
    sec["params"] = dict(params)
    sec["extra_fields"] = [
        {"key": extra_key(e["param"]), "label": e.get("label") or e["param"],
         "param": e["param"], "kind": e.get("kind", "text"), "unit": e.get("unit", "in")}
        for e in extras]
    return store


# -- building the effective standard -------------------------------------------
def effective_dict(base, store_section=None, discovered=None):
    """Firm standard dict + project section + discovered values -> new dict."""
    d = copy.deepcopy(base)
    sec = store_section or {}
    params = sec.get("params", {})
    proj_values = sec.get("values", {})
    discovered = discovered or {}

    existing_keys = set(f["key"] for f in d["fields"])
    for e in sec.get("extra_fields", []):
        if e["key"] not in existing_keys:
            fd = dict(e)
            fd.update({"required": False, "extra": True, "strict": False})
            d["fields"].append(fd)

    for fd in d["fields"]:
        key = fd["key"]
        if key in params and not fd.get("extra"):
            chosen = params[key]
            fd["param"] = [chosen] if chosen else []
            fd["builtins"] = []            # an explicit project choice wins
        vals = [v if isinstance(v, dict) else {"code": v} for v in fd.get("values", [])]
        fd["values"] = vals

        def find(code):
            for v in vals:
                if v["code"].lower() == code.lower():
                    return v
            return None

        for code, info in proj_values.get(key, {}).items():
            v = find(code)
            desc = (info or {}).get("desc", u"")
            if v is None:
                vals.append({"code": code, "desc": desc, "origin": "project"})
            elif desc:
                v["firm_desc"] = v.get("desc", u"")
                v["desc"] = desc               # project description overrides firm's

        if fd.get("kind", "text") == "text":
            for code in sorted(discovered.get(key, ()), key=lambda c: c.lower()):
                if code and find(code) is None:
                    vals.append({"code": code, "origin": "found"})
    return d
