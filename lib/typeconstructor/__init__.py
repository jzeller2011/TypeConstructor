# -*- coding: utf-8 -*-
"""Type Constructor - stage, de-duplicate and create standard-named Revit types."""
import os

EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG_DIR = os.path.join(EXTENSION_DIR, "config")


def config_dir(override=None):
    """Use the override (e.g. a network share) when it exists, else the bundled config."""
    if override and os.path.isdir(override):
        return override
    return DEFAULT_CONFIG_DIR
