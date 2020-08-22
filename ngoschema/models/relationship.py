# *- coding: utf-8 -*-
"""
Foreign key component

author: Cédric ROMAN (roman@numengo.com)
licence: GPL3
created on 28/01/2019
"""
from __future__ import absolute_import
from __future__ import unicode_literals

from ..decorators import classproperty
from ..types import with_metaclass, ObjectMetaclass, ObjectProtocol


class ForeignKey(with_metaclass(ObjectMetaclass)):
    """
    Class to deal with relationships
    """
    _schema_id = 'https://numengo.org/ngoschema#/$defs/ForeignKey'

    def __new__(cls, *args, **kwargs):
        new = super(ObjectProtocol, cls).__new__
        if new is object.__new__:
            return new(cls)
        return new(cls, *args, **kwargs)


class Relationship(with_metaclass(ObjectMetaclass)):
    """
    Class to deal with relationships
    """
    _schema_id = 'https://numengo.org/ngoschema#/$defs/Relationship'

    def __new__(cls, *args, **kwargs):
        new = super(ObjectProtocol, cls).__new__
        if new is object.__new__:
            return new(cls)
        return new(cls, *args, **kwargs)

    @classproperty
    def foreignSchema(cls):
        return cls._schema.get('$schema')

    _foreign_class = None
    @classproperty
    def foreignClass(cls):
        from ..types.type_builder import TypeBuilder
        from ngoschema.models.entity import Entity
        if not cls._foreign_class and cls.foreignSchema:
            try:
                cls._foreign_class = TypeBuilder.load(cls.foreignSchema)
            except Exception as er:
                cls.logger.error("error resolving foreign schema %s", cls.foreignSchema, exc_info=True)
                raise
            if not issubclass(cls._foreign_class, Entity):
                raise ValueError('target class (%r) must implement (%r) interface.' \
                                % (cls._foreign_class, Entity))
        return cls._foreign_class

    def resolve(self, keys):
        return self._foreign_class.resolve_by_keys(keys)

    def __get__(self):
        return self.resolve(self.fkeys)
