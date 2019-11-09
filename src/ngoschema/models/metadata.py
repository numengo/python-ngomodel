# *- coding: utf-8 -*-
"""
Base class for metadata (inherited by all components normally)

author: Cédric ROMAN (roman@numengo.com)
licence: GPL3
"""
from __future__ import absolute_import
from __future__ import unicode_literals

from future.utils import with_metaclass
from ngoschema.models.keyed_object import NamedObject

from ngoschema.schema_metaclass import SchemaMetaclass


class Metadata(with_metaclass(SchemaMetaclass, NamedObject)):
    """
    Class to deal with metadata and parents/children relationships
    """
    __schema__ = "http://numengo.org/ngoschema/draft-05#/definitions/Metadata"

    def __init__(self, *args, **kwargs):
        NamedObject.__init__(self, *args, **kwargs)