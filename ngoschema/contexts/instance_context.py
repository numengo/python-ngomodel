# *- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals

from .object_protocol_context import ObjectProtocolContext


class InstanceContext(ObjectProtocolContext):
    _parentInstance = None

    def set_context(self, *parents, **local):
        from ..models.instances import Instance
        ObjectProtocolContext.set_context(self, *parents, **local)
        self._parentInstance = next((m for m in self._context.maps
                                      if isinstance(m, Instance) and m is not self), None)
        if '_parentInstance' in self._properties:
            self._set_data_validated('_parentInstance', self._parentInstance)
