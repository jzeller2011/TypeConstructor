# -*- coding: utf-8 -*-
"""Save the project store (descriptions, new values, parameter mapping) in the
Revit model itself, using Extensible Storage on a DataStorage element.

It travels with the .rvt and reaches the rest of the team on sync. It is not
visible in the Revit UI and doesn't touch any family.
"""
import json

import clr
# .NET assemblies aren't importable until referenced. System.Data (DataTable)
# isn't loaded by Revit/pyRevit by default; the WPF ones usually are, but
# referencing them explicitly avoids load-order surprises.
clr.AddReference("System.Data")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Guid, String

from Autodesk.Revit.DB import DataStorage, FilteredElementCollector, Transaction
from Autodesk.Revit.DB.ExtensibleStorage import AccessLevel, Entity, Schema, SchemaBuilder

from typeconstructor.project import empty_store

# Never change this GUID: it's how existing projects find their saved data.
SCHEMA_GUID = Guid("6f3c2a91-4b7e-4d1a-9c5e-2b8f0d7a1e43")
SCHEMA_NAME = "TypeConstructorProjectData"
FIELD = "Payload"


def _schema(create=False):
    s = Schema.Lookup(SCHEMA_GUID)
    if s is not None or not create:
        return s
    sb = SchemaBuilder(SCHEMA_GUID)
    sb.SetSchemaName(SCHEMA_NAME)
    sb.SetReadAccessLevel(AccessLevel.Public)
    sb.SetWriteAccessLevel(AccessLevel.Public)
    sb.SetDocumentation("Type Constructor pyRevit extension: project values and mappings")
    sb.AddSimpleField(FIELD, clr.GetClrType(String))
    return sb.Finish()


def _storage_element(doc, schema):
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        ent = ds.GetEntity(schema)
        if ent is not None and ent.IsValid():
            return ds
    return None


def load(doc):
    s = _schema()
    if s is None:
        return empty_store()
    ds = _storage_element(doc, s)
    if ds is None:
        return empty_store()
    try:
        data = json.loads(ds.GetEntity(s).Get[String](FIELD) or u"{}")
    except Exception:
        return empty_store()
    data.setdefault("schemas", {})
    return data


def update(doc, mutator, description=u"Type Constructor: update project data"):
    """Re-read the latest store, apply mutator(store), save. Returns the new store.

    Re-reading inside the transaction means two people editing different
    values don't overwrite each other (after they sync).
    """
    t = Transaction(doc, description)
    t.Start()
    try:
        s = _schema(create=True)
        store = load(doc)
        mutator(store)
        ds = _storage_element(doc, s)
        if ds is None:
            ds = DataStorage.Create(doc)
        ent = Entity(s)
        ent.Set[String](FIELD, json.dumps(store, sort_keys=True))
        ds.SetEntity(ent)
        t.Commit()
        return store
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
