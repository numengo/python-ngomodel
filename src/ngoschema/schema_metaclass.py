# *- coding: utf-8 -*-
"""
Utilities and classes to deal with schemas

author: Cédric ROMAN (roman@numengo.com)
licence: GPL3
created on 02/01/2018
"""
from __future__ import absolute_import
from __future__ import unicode_literals

import copy
import logging

import python_jsonschema_objects.util as pjo_util
from jsonschema.compat import iteritems

from . import decorators
from . import utils
from .classbuilder import get_builder
from .inspect_objects import FunctionInspector
from .resolver import get_resolver
#from .schemas_loader import load_module_schemas
from .schemas_loader import load_schema
from .schemas_loader import load_schema_file
from .validators import DefaultValidator

logger = logging.getLogger(__name__)


class SchemaMetaclass(type):
    """
    Metaclass used for classes with schema
    """

    def __new__(cls, clsname, bases, attrs):
        """
        Process schema given by schemaPath if found and merges the schemas of bases

        When processing the schema dictionary, any harcoded initial value for
        schema is used.
        If a dictionary is found it will initialize the object define in schema
        """

        # base schema, should be overwritten
        schema = {}
        schemaUri = None
        # default resolver and builder
        # default resolver is the one with expanding facility => it expand the schema
        # according to the classes it extends, it resolves partial URI non relative to
        # the document, etc...
        builder = get_builder(get_resolver())
        resolver = builder.resolver
        if attrs.get("schema"):
            attrs["schema"].setdefault('$id', clsname)
            schemaUri, schema = load_schema(attrs["schema"])
            #expand the schema
            schema = resolver._expand(schemaUri, schema, schemaUri)
        elif attrs.get("schemaPath"):
            schemaUri, schema = load_schema_file(attrs["schemaPath"])
            #expand the schema
            schema = resolver._expand(schemaUri, schema, schemaUri)
        elif attrs.get("schemaUri"):
            # the schema is automatically expanded when resolved by the resolver
            schemaUri, schema = resolver.resolve(attrs["schemaUri"])
        if schema:
            # make a copy as building class will modify the dict and mess a lot things
            # expand already makes a copy
            #schema = copy.deepcopy(schema)
            # validate schema with its meta-schema
            metaschema = DefaultValidator.META_SCHEMA
            if schema.get("$schema"):
                ms_uri, metaschema = resolver.resolve(schema["$schema"])
            meta_validator = DefaultValidator(metaschema, resolver=resolver)
            # with hacked validator, can set a mode to set default values during
            # validation => schema will have its default values set
            def_bak = getattr(DefaultValidator, "_setDefaults", False)
            DefaultValidator._setDefaults = True
            meta_validator.validate(schema)
            DefaultValidator._setDefaults = def_bak

            logger.debug("creating <%s> with schema", clsname)

            # reset resolver and builder to use the schemaUri as base
            resolver = get_resolver(schemaUri)
            builder = get_builder(resolver)
            # building inner definitions
            for nm, defn in iteritems(schema.get("definitions", {})):
                uri = pjo_util.resolve_ref_uri(schemaUri,
                                               "#/definitions/" + nm)
                from .classbuilder import ProtocolBase
                builder.construct(uri, defn, (ProtocolBase, ), attrs.get(nm, {}))
        else:
            schema["type"] = "object"

        # add some magic on methods defined in class
        # exception handling, argument conversion/validation, etc...
        for k, fn in attrs.items():
            if not (utils.is_method(fn) or utils.is_function(fn)):
                continue
            __add_logging__ = attrs.get("__add_logging__", False)
            __assert_args__ = attrs.get("__assert_args__", True)

            if __add_logging__ and k == "__init__":
                logger.debug("decorate <%s>.__init__ with init logger",
                             clsname)
                fn = decorators.log_init(fn)

            # add argument checking
            if __assert_args__ and fn.__doc__:
                fi = FunctionInspector(fn)
                for pos, p in enumerate(fi.parameters):
                    if p.type:
                        logger.debug(
                            "decorate <%s>.%s with argument %i validity check.",
                            clsname, k, pos)
                        fn = decorators.assert_arg(pos, p.type)(fn)

            # add exception logging
            if __add_logging__ and not k.startswith("__"):
                logger.debug("decorate <%s>.%s with exception logger", clsname,
                             k)
                fn = decorators.log_exceptions(fn)

            attrs[k] = fn

        if schemaUri is not None:
            clsname = schemaUri
        cls = builder.construct(
            clsname, schema, parent=bases, class_attrs=dict(attrs))
        return cls
