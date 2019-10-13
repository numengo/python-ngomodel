# *- coding: utf-8 -*-
"""
Base class for loading objects from files

author: Cédric ROMAN (roman@numengo.com)
licence: GPL3
"""
from __future__ import absolute_import
from __future__ import unicode_literals

import os
import logging
import subprocess
import tempfile
from abc import abstractmethod

import six
from future.utils import with_metaclass
from ngoschema.jinja2 import TemplatedString
from ngoschema.decorators import SCH_PATH_DIR_EXISTS, assert_arg

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

import json
from ruamel import yaml
from ruamel.yaml import YAML

from .query import Query
from .protocol_base import ProtocolBase
from .document import Document
from .schema_metaclass import SchemaMetaclass
from .utils import Registry, GenericClassRegistry, filter_collection, is_mapping, is_sequence
from .jinja2 import default_jinja2_env, _jinja2_globals
from .keyed_object import KeyedObject, NamedObject
from .decorators import memoized_property

logger = logging.getLogger(__name__)

handler_registry = GenericClassRegistry()

class ObjectHandler(with_metaclass(SchemaMetaclass, ProtocolBase)):
    """
    Class to store read/write operations of objects
    """
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/ObjectHandler"

    def __init__(self, **kwargs):
        ProtocolBase.__init__(self, **kwargs)
        self._registry = Registry()
        self._class = self.objectClass._imported if self.objectClass is not None else None
        self._keys = None
        if self.keys is not None:
            self._keys = self.keys.for_json()
        elif issubclass(self._class, KeyedObject):
            self._keys = tuple(self._class.primaryKeys)
        self._session = None

    @property
    def session(self):
        return self._session

    def _identity_key(self, instance):
        if not isinstance(instance, self._class):
            raise Exception("%r is not an instance of %r" % (instance, self._class))
        if self._keys:
            if len(self._keys)>1:
                return tuple([instance._get_prop_value(k) for k in self._keys])
            else:
                return instance._get_prop_value(self._keys[0])
        return id(instance)

    def register(self, instance):
        self._registry.register(self._identity_key(instance), instance)
        instance._handler = self

    def unregister(self, instance):
        self._registry.unregister(self._identity_key(instance))
        instance._handler = None

    def get_instance(self, key):
        return self._registry[key]

    def resolve_cname_path(self, cname):
        # use generators because of 'null' which might lead to different paths
        def _resolve_cname_path(cn, cur, cur_cn, cur_path):
            cn = [e.replace('<anonymous>', 'null') for e in cn]
            # empty path, yield current path and doc
            if not cn:
                yield cur, cn, cur_path
            if is_mapping(cur):
                cn2 = cur_cn + [cur.get('name', 'null')]
                if cn2 == cn[0:len(cn2)]:
                    if cn2 == cn:
                        yield cur, cn, cur_path
                    for k, v in cur.items():
                        if is_mapping(cur) or is_sequence(cur):
                            for _ in _resolve_cname_path(cn, v, cn2, cur_path + [k]):
                                yield _
            if is_sequence(cur):
                for i, v in enumerate(cur):
                    for _ in _resolve_cname_path(cn, v, cur_cn, cur_path + [i]):
                        yield _

        cn_path = cname.split('.')
        for i in self.instances:
            if not isinstance(i, NamedObject) and cname.startswith(str(i.canonicalName)):
                continue
            # found ancestor
            cur_cn = []
            # first search without last element, as last one might not be a named object
            # but the name of an attribute
            for d, c, p in _resolve_cname_path(cn_path[:-1], i, cur_cn, []):
                if cn_path[-1] in d or d.get('name', '<anonymous>') == cn_path[-1]:
                    p.append(cn_path[-1])
                    return i, p
                # we can continue the search from last point. we remove the last element of the
                # canonical name which is going to be read again
                for d2, c2, p2 in _resolve_cname_path(cn_path, d, c[:-1], p):
                    return i, p2
        raise Exception("Unresolvable canonical name '{0}' in '{1}'", cname, i)

    def resolve_cname(self, cname):
        cur, path = self.resolve_cname_path(cname)
        for p in path:
            cur = cur[p]
        return cur

    @property
    def instances(self):
        return list(self._registry.values())

    def query(self, *attrs, order_by=False, **attrs_value):
        """
        Make a `Query` on registered documents
        """
        __doc__ = Query.filter.__doc__
        return Query(self.instances).filter(
            *attrs, order_by=order_by, **attrs_value)


    def pre_commit(self):
        return [o.for_json() for o in self._registry.values()]

    @abstractmethod
    def commit(self):
        data = self.pre_commit()
        pass

    @abstractmethod
    def pre_load(self):
        return {}

    def load(self):
        data = self.pre_load()
        if self.many:
            objs = [self._class(**d) for d in data]
            for obj in objs:
                self.register(obj)
            return objs
        else:
            obj = self._class(**data)
            self.register(obj)
            return obj


class FilterObjectHandlerMixin(object):
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/FilterObjectHandlerMixin"

    def filter_data(self, data):
        only = self.only.for_json() if self.only else ()
        but = self.but.for_json() if self.but else ()
        rec = bool(self.recursive)
        return filter_collection(data, only, but, rec)


class FileObjectHandler(with_metaclass(SchemaMetaclass, ObjectHandler, FilterObjectHandlerMixin)):
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/FileObjectHandler"

    @abstractmethod
    def deserialize_data(self):
        pass

    def pre_load(self):
        doc = self.document
        if not doc.loaded:
            doc.load()
        data = self.deserialize_data()
        data = self.filter_data(data)
        return data

    @abstractmethod
    def serialize_data(self, data):
        pass

    def dumps(self):
        data = self.pre_commit()
        return self.serialize_data(data)

    def commit(self):
        doc = self.document
        fpath = doc.filepath
        stream = self.dumps()
        if fpath.exists():
            doc.load()
            if stream == doc.contentRaw:
                self.logger.info("File '%s' already exists with same content. Not overwriting.", fpath)
                return

        self.logger.info("DUMP file %s", fpath)
        self.logger.debug("data:\n%r ", stream)
        doc.write(stream)


@handler_registry.register()
class JsonFileObjectHandler(with_metaclass(SchemaMetaclass, FileObjectHandler)):
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/JsonFileObjectHandler"

    def __init__(self, **kwargs):
        FileObjectHandler.__init__(self, **kwargs)

    def deserialize_data(self):
        return self.document.deserialize_json()

    def serialize_data(self, data):
        return json.dumps(
            data,
            indent=self.get("indent", 2),
            ensure_ascii=self.get("ensure_ascii", False),
            separators=self.get("separators", None),
            default=self.get("default", None),
        )


@handler_registry.register()
class YamlFileObjectHandler(with_metaclass(SchemaMetaclass, FileObjectHandler)):
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/YamlFileObjectHandler"
    _yaml = YAML(typ="safe")

    def deserialize_data(self):
        return self.document.deserialize_yaml()

    def serialize_data(self, data):
        yaml.indent = self.get("indent", 2)
        yaml.allow_unicode = self.get("encoding", "utf-8") == "utf-8"

        output = StringIO()
        self._yaml.safe_dump(data, output, default_flow_style=False)
        return output.getvalue()


@handler_registry.register()
class Jinja2FileObjectHandler(with_metaclass(SchemaMetaclass, FileObjectHandler)):
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/Jinja2FileObjectHandler"

    def __init__(self, template=None, environment=None, context=None, protectedRegions=None, **kwargs):
        """
        Serializer based on a jinja template. Template is loaded from
        environment. If no environment is provided, use the default one
        `default_jinja2_env`
        """
        FileObjectHandler.__init__(self, template=template, **kwargs)
        self._jinja = environment or default_jinja2_env()
        self._jinja.globals.update(_jinja2_globals)
        self._context = context or {}
        self._protected_regions = self._jinja.globals['protected_regions'] = protectedRegions or {}

    def pre_commit(self):
        return self._context

    def deserialize_data(self):
        raise Exception("not implemented")

    def serialize_data(self, data):
        logger.info("DUMP template '%s' file %s", self.template, self.document.filepath)
        logger.debug("data:\n%r ", data)

        stream = self._jinja.get_template(self.template).render(data)
        return six.text_type(stream)


@handler_registry.register()
class Jinja2MacroFileObjectHandler(with_metaclass(SchemaMetaclass, Jinja2FileObjectHandler)):
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/Jinja2MacroFileObjectHandler"

    def serialize_data(self, data):
        macro_args = self.macroArgs.for_json()
        if 'protected_regions' not in macro_args:
            macro_args.append('protected_regions')
        args = [k for k in macro_args if k in data]
        to_render = "{%% from '%s' import %s %%}{{%s(%s)}}" % (
            self.template, self.macroName, self.macroName, ', '.join(args))
        try:
            template = self._jinja.from_string(to_render)
            return template.render(**data)
        except Exception as er:
            logger.info(er)
            raise er


@handler_registry.register()
class Jinja2MacroTemplatedPathFileObjectHandler(with_metaclass(SchemaMetaclass, Jinja2MacroFileObjectHandler)):
    __schema__ = "http://numengo.org/draft-05/ngoschema/object-handlers#/definitions/Jinja2MacroTemplatedPathFileObjectHandler"

    def serialize_data(self, data):
        tpath = TemplatedString(self.templatedPath)(**data)
        fpath = self.outputDir.joinpath(tpath)
        self.document = self.document or Document()
        self.document.filepath = fpath
        if not fpath.parent.exists():
            os.makedirs(str(fpath.parent))
        stream = Jinja2MacroFileObjectHandler.serialize_data(self, data)
        if fpath.suffix in ['.h', '.c', '.cpp']:
            tf = tempfile.NamedTemporaryFile(mode='w+b', suffix=fpath.suffix, dir=fpath.parent, delete=True)
            tf.write(stream.encode('utf-8'))
            stream = subprocess.check_output(
                'clang-format %s' % tf.name, cwd=str(self.outputDir), shell=True)
            tf.close()
            stream = stream.decode('utf-8')
        return stream
