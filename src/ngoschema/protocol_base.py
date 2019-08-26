# *- coding: utf-8 -*-
"""
Derived classbuilder from python-jsonschema-object for ngoschema specific
requirements

author: Cédric ROMAN (roman@numengo.com)
licence: GPL3
created on 11/06/2018
"""
from __future__ import absolute_import
from __future__ import unicode_literals

import itertools
import weakref

import inflection
import six
import collections
import copy
import json

from python_jsonschema_objects import \
    classbuilder as pjo_classbuilder, \
    util as pjo_util, \
    wrapper_types as pjo_wrapper_types, \
    literals as pjo_literals, \
    validators as pjo_validators

from . import utils
from .canonical_name import resolve_cname, CN_KEY
from . import mixins
from .mixins import HasCache, HasParent, HandleRelativeCname, HasInstanceQuery
from .logger import HasLogger
from .uri_identifier import resolve_uri
from .validators import DefaultValidator
from .config import ConfigLoader
from .decorators import SCH_PATH_DIR_EXISTS, SCH_STR
from .decorators import assert_arg
from .serializers import JsonSerializer

# loader to register module with a transforms folder where to look for model transformations
models_module_loader = utils.GenericModuleFileLoader('schemas')

def load_module_models(module_name):
    return models_module_loader.register(module_name)

# loader of objects default configuration
objects_config_loader = ConfigLoader()

def get_descendant(obj, key_list, load_lazy=False):
    """
    Get descendant in an object/dictionary by providing the path as a list of keys
    :param obj: object to iterate
    :param key_list: list of keys
    :param load_lazy: in case of lazy loaded object, force loading
    """
    k0 = key_list[0]

    lazy_data = getattr(obj, '_lazy_data', {})
    if not load_lazy and key_list[0] in lazy_data:
        try:
            return resolve_cname(key_list, lazy_data)
        except Exception as er:
            #logger.warning(er)
            return None

    try:
        child = obj[k0]
    except Exception as er:
        child = None
    return get_descendant(child, key_list[1:], load_lazy) \
            if child and len(key_list)>1 else child


def make_property(propname, info, fget=None, fset=None, fdel=None, desc=""):
    # flag to know if variable is readOnly check is active
    info['RO_active'] = True

    def getprop(self):
        self.logger.debug(pjo_util.lazy_format("GET {!s}.{!s}", self.short_repr(), propname))

        # load missing component
        if propname in self._lazy_data:
            try:
                self.logger.debug("lazy loading of '%s'", propname)
                setattr(self, propname, self._lazy_data.pop(propname))
            except Exception as er:
                raise AttributeError("Lazy loading of property '%s' failed.\n%s" % (propname, er))

        prop = self._properties.get(propname)

        if fget and (prop is None or prop.is_dirty()):
            try:
                #self._properties[propname] = val
                info['RO_active'] = False
                setprop(self, fget(self))
                prop = self._properties[propname]
            except Exception as er:
                info['RO_active'] = True
                self.logger.error( "GET {!s}.{!s}.\n%s", self, propname, er)
                raise AttributeError(
                    "Error getting property %s.\n%s" % (propname, er))
        try:
            if prop is not None:
                # only forces validation if pattern
                force = hasattr(prop, '_pattern') or isinstance(prop, ProtocolBase) or info["type"] == 'array'
                prop.do_validate(force)
                return prop
        except KeyError as er:
            self.logger.error(er)
            raise AttributeError("No attribute %s" % propname)
        except Exception as er:
            self.logger.error(er)
            raise AttributeError(er)

    def setprop(self, val):
        self.logger.debug(
            pjo_util.lazy_format("SET {!s}.{!s}={!s}", self.short_repr(), propname, utils.any_pprint(val)))
        if val is None and propname not in self.__required__:
            self._properties[propname] = None
            return
        if info['RO_active'] and propname in self.__read_only__:
            # in case default has not been set yet
            if not (propname in self.__has_default_flatten__ and self._properties.get(propname) is None):
                raise AttributeError("'%s' is read only" % propname)

        infotype = info["type"]

        depends_of = self.__dependencies__.get(propname, set())

        old_prop = self._properties.get(propname)
        old_val = old_prop._value if isinstance(old_prop, pjo_literals.LiteralValue) else None

        if self._attr_by_name:
            if utils.is_mapping(val) and CN_KEY in val:
                self._key2attr[str(val[CN_KEY])] = (propname, None)
            if utils.is_sequence(val):
                for i, v2 in enumerate(val):
                    if utils.is_mapping(v2) and CN_KEY in v2:
                        self._key2attr[str(v2[CN_KEY])] = (propname, i)

        if isinstance(infotype, (list, tuple)):
            ok = False
            errors = []
            type_checks = []

            for typ in infotype:
                if not isinstance(typ, dict):
                    type_checks.append(typ)
                    continue
                typ = next(t for n, t in pjo_validators.SCHEMA_TYPE_MAPPING +
                           pjo_validators.USER_TYPE_MAPPING
                           if typ["type"] == n)
                if typ is None:
                    typ = type(None)
                if isinstance(typ, (list, tuple)):
                    type_checks.extend(typ)
                else:
                    type_checks.append(typ)

            for typ in type_checks:
                if isinstance(val, typ):
                    ok = True
                    break
                elif hasattr(typ, "isLiteralClass"):
                    try:
                        validator = typ(val)
                    except Exception as e:
                        errors.append("Failed to coerce to '{0}': {1}".format(
                            typ, e))
                    else:
                        validator.do_validate()
                        ok = True
                        break
                elif pjo_util.safe_issubclass(typ, ProtocolBase):
                    # force conversion- thus the val rather than validator assignment
                    try:
                        if not utils.is_string(val):
                            val = typ(**self._child_conf,
                                      **pjo_util.coerce_for_expansion(val))
                        else:
                            val = typ(val)
                    except Exception as e:
                        errors.append(
                            "Failed to coerce to '%s': %s" % (typ, e))
                    else:
                        if isinstance(val, HasParent):
                            val._parent = self
                        val.do_validate()
                        ok = True
                        break
                elif pjo_util.safe_issubclass(typ,
                                              pjo_wrapper_types.ArrayWrapper):
                    try:
                        val = typ(val)
                    except Exception as e:
                        errors.append(
                            "Failed to coerce to '%s': %s" % (typ, e))
                    else:
                        val.do_validate()
                        ok = True
                        break

            if not ok:
                errstr = "\n".join(errors)
                raise pjo_validators.ValidationError(
                    "Object must be one of %s: \n%s" % (infotype, errstr))

        elif infotype == "array":
            if hasattr(info["validator"].__itemtype__, 'foreignClass'):
                for i, e in enumerate(val):
                    if str(e).startswith('#'):
                        val[i] = self._clean_cname(e)
            val = info["validator"](val)
            # only validate if items are not foreignKey
            if not hasattr(info["validator"].__itemtype__, 'foreignClass'):
                val._parent = self
                val.do_validate()

        elif getattr(infotype, "isLiteralClass", False):
            if hasattr(infotype, 'foreignClass') and str(val).startswith('#'):
                val = self._clean_cname(val)

            if not isinstance(val, infotype):
                validator = infotype(val)
                # handle case of patterns
                if utils.is_pattern(val):
                    from .jinja2 import get_variables
                    vars = get_variables(val)
                    depends_of.update(vars)
                    validator._pattern = val
                    validator.touch()
                # only validate if it s not a pattern or a foreign key
                else:
                    # it s not a pattern, remove
                    #if getattr(validator, "_pattern", False):
                    #    delattr(validator, "_pattern")
                    #if not getattr(validator, 'foreignClass', False):
                    if not hasattr(self, 'do_validate'):
                        raise Exception('WHY????')
                    validator.do_validate()
                val = validator

        elif pjo_util.safe_issubclass(infotype, ProtocolBase):
            if not isinstance(val, infotype):
                if not utils.is_string(val):
                    val = infotype(**self._child_conf,
                                   **pjo_util.coerce_for_expansion(val))
                else:
                    val = infotype(val)
            if isinstance(val, HasParent):
                val._parent = self
            val.do_validate()

        elif isinstance(infotype, pjo_classbuilder.TypeProxy):
            val = infotype(val)

        elif isinstance(infotype, pjo_classbuilder.TypeRef):
            if not isinstance(val, infotype.ref_class):
                if not utils.is_string(val):
                    val = infotype(**self._child_conf,
                                   **pjo_util.coerce_for_expansion(val))
                else:
                    val = infotype(val)
            if isinstance(val, HasParent):
                val._parent = self
            val.do_validate()

        elif infotype is None:
            # This is the null value
            if val is not None:
                raise pjo_validators.ValidationError(
                    "None is only valid value for null")

        else:
            raise TypeError("Unknown object type: '%s'" % infotype)

        # set dependency tree
        val._set_context(self)
        val._add_inputs(*depends_of)

        if old_val != val:
            if fset:
                # call the setter, and get the value stored in _properties
                fset(self, val)
            val.touch(recursive=True)
            val.set_clean()

        self._properties[propname] = val


    def delprop(self):
        self.logger.debug(pjo_util.lazy_format("DEL {!s}.{!s}", self.short_repr(), propname))
        if propname in self.__required__:
            raise AttributeError("'%s' is required" % propname)
        else:
            if fdel:
                fdel(self)
            del self._properties[propname]

    return property(getprop, setprop, delprop, desc)


class ProtocolBase(mixins.HasParent, mixins.HasInstanceQuery, mixins.HasCache, HasLogger, pjo_classbuilder.ProtocolBase):
    __doc__ = pjo_classbuilder.ProtocolBase.__doc__ + """
    
    Protocol shared by all instances created by the class builder. It extends the 
    ProtocolBase object available in python-jsonschema-objects and add some features:
    
    * metamodel has a richer vocabulary, and class definition supports inheritance, and 
    database persistence
    
    * hybrid classes: classes have a json schema defining all its members, but have some 
    business implementation done in python and where default setters/getters can be 
    overriden. 
    
    * string literal value with patterns: a string literal value can be defined as a 
    formatted string which can depend on other properties.
    
    * complex literal types: path/date/datetime are automatically created and can be 
    handled as expected python objects, and will then be properly serialized
    
    * allow lazy loading on member access

    * methods are automatically decorated to add logging possibility, exception handling
    and argument validation/conversion to the proper type (type can be given as a schema
    through a decorator or simply by documenting the docstring with a schema)
        
    * all instances created are registered and can then be queried using Query
    
    * default values can be configured in the config files
    """

    # additional private and protected props
    _validator = None
    __prop_names__ = dict()
    __prop_translated__ = dict()
    __instances__ = weakref.WeakValueDictionary()

    def __new__(cls,
                *args,
                **props):
        """
        function creating the class with a special treatment to resolve subclassing
        """
        from .resolver import get_resolver
        from .classbuilder import get_builder

        if '$ref' in props:
            props.update(resolve_uri(props.pop('$ref')))

        if '$schema' in props:
            if props['$schema'] != cls.__schema__:
                cls = get_builder().resolve_or_build(props['$schema'])

        cls.init_class_logger()

        # option to validate arguments at init even if lazy loading
        if cls.__lazy_loading__ and cls.__validate_lazy__ and cls._validator is None:
            cls._validator = DefaultValidator(
                cls.__schema__, resolver=get_resolver())
            cls._validator._setDefaults = True

        new = super(ProtocolBase, cls).__new__
        if new is object.__new__:
            return new(cls)
        return new(cls, **props)

    def __init__(self,
                 *args,
                 **props):
        """
        main initialization method, dealing with lazy loading
        """
        self.logger.info(pjo_util.lazy_format("INIT {0} with {1}", self.short_repr(), utils.any_pprint(props)))

        cls = self.__class__

        self._key2attr = dict()
        self._lazy_loading = props.pop('lazy_loading', None) or cls.__lazy_loading__
        self._validate_lazy = props.pop('validate_lazy', None) or cls.__validate_lazy__
        self._attr_by_name = props.pop('attr_by_name', None) or cls.__attr_by_name__
        self._propagate = props.pop('propagate', None) or cls.__propagate__
        self._child_conf = {
            'lazy_loading':  self._lazy_loading,
            'validate_lazy': self._validate_lazy,
            'attr_by_name':  self._attr_by_name,
            'propagate': self._propagate
        } if self._propagate else {}

        mixins.HasCache.__init__(self)

        # register instance
        for c in self.pbase_mro(ngo_base=True):
            c.__instances__[id(self)] = self

        self._lazy_data = dict()
        self._extended_properties = dict()
        self._properties = dict(zip(
                    self.__prop_names_flatten__.values(),
                    [None for x in six.moves.xrange(len(self.__prop_names_flatten__))]))

        # To support defaults, we have to actually execute the constructors
        # but only for the ones that have defaults set.
        for k, v in self.__has_default__.items():
            if k not in props:
                setattr(self, k, copy.copy(v))

        # non keyword argument = reference to property extern to document to be resolved later
        if len(args)==1 and utils.is_string(args[0]):
            props['$ref'] = args[0]
        if '$ref' in props:
            props.update(resolve_uri(props.pop('$ref')))

        # remove initial values of readonly members
        for k in self.__read_only__.intersection(props.keys()):
            props.pop(k)
            self.logger.warning('property %s is read-only. Initial value provided not used.', k)

        if 'name' in props:
            if isinstance(self, mixins.HasCanonicalName):
                mixins.HasCanonicalName.set_name(self, props['name'])
            elif isinstance(self, mixins.HasName):
                mixins.HasName.set_name(self, props['name'])

        if self._lazy_loading:
            self._lazy_data.update({self.__prop_names_flatten__.get(k, k): v for k, v in props.items()})
        else:
            try:
                for prop in props:
                    if props[prop] is not None:
                        setattr(self, prop, props[prop])
                if self.__strict__:
                    self.do_validate(force=True)
            except Exception as er:
                self.logger.error('problem initializing %s' % self)
                self.logger.error(er, exc_info=True)
                raise er

    def __hash__(self):
        """hash function to store objects references"""
        return id(self)

    def short_repr(self):
        return "<%s id=%s>" % (
            self.fullname(),
            id(self)
        )

    def __str__(self):
        props = sorted(["%s=%s" % (self.__prop_translated_flatten__.get(k, k), str(v))
                        for k, v in itertools.chain(six.iteritems(self._properties),
                                    six.iteritems(self._extended_properties))
                        if v is not None])
        props = props[:20] + (['...'] if len(props)>=20 else [])
        return "<%s id=%s %s>" % (
            self.fullname(),
            id(self),
            " ".join(props)
        )

    def __repr__(self):
        props = sorted(["%s=%s" % (self.__prop_translated_flatten__.get(k, k), json.dumps(v.for_json()))
                        for k, v in itertools.chain(six.iteritems(self._properties),
                                    six.iteritems(self._extended_properties)) if v])
        return "<%s id=%s %s>" % (
            self.fullname(),
            id(self),
            " ".join(props)
        )

    def __eq__(self, other):
        if not mixins.HasCache.__eq__(self, other):
            return False
        if not isinstance(other, ProtocolBase):
            return False
        for k in set(self.keys()).intersection(other.keys()):
            if self.get(k) != other.get(k):
                return False
        return True

    def for_json(self, no_defaults=True):
        """key@
        serialization method, removing all members flagged as NotSerialized
        """
        out = collections.OrderedDict()
        for pn_id, pn in self.__prop_names_flatten__.items():
            if pn_id in getattr(self, '__not_serialized__', []):
                continue
            prop = self._get_prop(pn)
            if no_defaults:
                if prop is None:
                    continue
                defv = self.__has_default__.get(pn)
                if isinstance(prop, pjo_literals.LiteralValue):
                    if getattr(prop, '_pattern', '') == defv:
                        continue
                    val = self._get_prop_value(pn)
                    if val == defv:
                        continue
                    if val is not None:
                        out[pn_id] = val
                else:
                    if isinstance(prop, pjo_classbuilder.ProtocolBase):
                        if len(prop) == len(prop.__has_default__):
                            if all(prop._get_prop_value(p) == d for p, d in prop.__has_default__):
                                continue
                    if isinstance(prop, pjo_wrapper_types.ArrayWrapper):
                        if len(prop) == len(defv):
                            if not defv or prop == defv:
                                continue
                    val = prop.for_json(no_defaults=no_defaults)
                    if val:
                        out[pn_id] = val
            else:
                out[pn_id] = self._get_prop_value(pn, no_defaults=no_defaults)
        for pn, prop in self._extended_properties.items():
            out[pn_id] = self._get_prop_value(pn, no_defaults=no_defaults)
        return out

    def for_xml(self, no_defaults=True):
        from lxml import etree
        def get_tag(obj):
            return inflection.underscore(obj.__class__.__name__)
        tag = get_tag(self)
        attribs = {k: v for k, v in self._properties.items() if isinstance(v, pjo_literals.LiteralValue)}
        attribs.update({k: ', '.join(list(v)) for k, v in self._properties.items() if isinstance(v, pjo_wrapper_types.ArrayWrapper) and issubclass(v.__itemtype__, pjo_literals.LiteralValue)})
        elt = etree.Element(tag, attrib=attribs)
        sub_elts = {get_tag(v): v for k, v in self._properties.items() if isinstance(v, ProtocolBase)}
        sub_elts.update({k: ', '.join(list(v)) for k, v in self._properties.items() if isinstance(v, pjo_wrapper_types.ArrayWrapper) and issubclass(v.__itemtype__, pjo_literals.ProtocolBase)})

    @classmethod
    def jsonschema(cls):
        from .resolver import get_resolver
        return get_resolver().resolve(cls.__schema__)[1]

    @property
    def _id(self):
        return id(self)

    @assert_arg(1, SCH_PATH_DIR_EXISTS)
    @assert_arg(2, SCH_STR)
    def serialize_json(self, output_dir, filename, no_defaults=True, overwrite=False):
        output_fp = output_dir.joinpath(filename)
        if output_fp.exists() and not overwrite:
            self.logger.warning("File '%s' already exists. Not overwriting.", output_fp)
        else:
            self.logger.info("SERIALIZING '%s'", output_fp)
            JsonSerializer.dump(self.for_json(no_defaults=no_defaults), output_fp, overwrite=overwrite)

    def validate(self):
        if self._lazy_loading:
            if self._lazy_data and self._validate_lazy:
                pass
        else:
            pjo_classbuilder.ProtocolBase.validate(self)

    @classmethod
    def issubclass(cls, klass):
        """subclass specific method"""
        return pjo_util.safe_issubclass(cls, klass)

    @classmethod
    def fullname(cls):
        return utils.fullname(cls)

    @classmethod
    def set_configfiles_defaults(cls, overwrite=False):
        """
        Look for default values in objects_config_loader to initialize properties
        in the object.

        :param overwrite: overwrite values already set
        """
        propnames = cls.__prop_names_flatten__
        defconf = objects_config_loader.get_values(cls.fullname(), propnames)
        for k, v in defconf.items():
            if overwrite:
                try:
                    cls.logger.debug("CONFIG SET %s.%s = %s", cls.fullname(), k, v)
                    cls.__propinfo__[k]['default'] = v
                except Exception as er:
                    cls.logger.error(er, exc_info=True)

    @classmethod
    def pbase_mro(cls, ngo_base=False):
        return cls.__ngo_pbase_mro__ if ngo_base else cls.__pbase_mro__

    @classmethod
    def propinfo(cls, propname):
        propid = cls.__prop_translated_flatten__.get(propname, propname)
        for c in cls.__pbase_mro__:
            if propname in c.__prop_names__:
                return c.__propinfo__[propid]
            elif c is not cls and propid in getattr(c, '__prop_names_flatten__', {}):
                return c.propinfo(propid)
        return {}

    def __getattr__(self, name):
        """
        Allow getting class attributes, protected attributes and protocolBase attributes
        as optimally as possible. attributes can be looked up base on their name, or by
        their canonical name, using a correspondence map done with _set_key2attr
        """
        # private and protected attributes at accessed directly
        if name.startswith("_"):
            return collections.MutableMapping.__getattribute__(self, name)

        # check it s a standard attribute or method
        for c in self.pbase_mro():
            if name in c.__object_attr_list__:
                return object.__getattribute__(self, name)

        prop, index = self._key2attr.get(name, (None, None))
        if prop:
            attr = ProtocolBase.__getattr__(self, prop)
            if index is None:
                return attr
            else:
                return attr[index]

        # check it s not a schema defined property, we should not reach there
        if name in self.__prop_names_flatten__.values():
            if name in self._properties:
                return self._properties.get(name)
            raise KeyError(name)
        # check it s not a translated property
        if name in self.__prop_names_flatten__:
            return getattr(self, self.__prop_names_flatten__[name])
        # check it s an extended property
        if name in self._extended_properties:
            return self._extended_properties[name]

        raise AttributeError("{0} is not a valid property of {1}".format(
                             name, self.__class__.__name__))


    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError as er:
            if default is None:
                return None
            info = self.propinfo(key)
            if utils.is_mapping(default):
                return info['type'](**default)
            else:
                return info['type'](default).to_json()

    def __getitem__(self, key):
        """access property as in a dict and returns json if not composed of objects """
        def json_if_not_of_objects(obj):
            cur = obj
            if isinstance(obj, pjo_wrapper_types.ArrayWrapper):
                while issubclass(getattr(cur, '__itemtype__', None), pjo_wrapper_types.ArrayWrapper):
                    cur = cur.__itemtype__
                return cur
            if isinstance(cur, pjo_literals.LiteralValue):
                return cur.for_json()
            elif isinstance(cur, ProtocolBase):
                return cur

        try:
            # to be able to call
            if '.' in key:
                keys = key.strip('#').split('.')
                cur = ProtocolBase.__getattr__(self, keys[0])
                for _ in keys:
                    cur = ProtocolBase.__getattr__(cur, _)
                return json_if_not_of_objects(cur)
            ret = getattr(self, key)
            if ret is None:
                raise KeyError(key)
            return json_if_not_of_objects(ret)
        except AttributeError as er:
            raise KeyError(key)
        except Exception as er:
            raise er

    def __setattr__(self, name, val):
        """allow setting of protected attributes"""
        if name.startswith("_"):
            return collections.MutableMapping.__setattr__(self, name, val)

        for c in self.pbase_mro():
            if name in c.__object_attr_list__:
                return object.__setattr__(self, name, val)

        name = self.__prop_names_flatten__.get(name, name)

        prop, index = self._key2attr.get(name, (None, None))
        if prop:
            if index is None:
                name = prop
            else:
                attr = getattr(self, prop)
                attr[index] = val
                return

        if name in self.__prop_names_flatten__.values():
            # If its in __propinfo__, then it actually has a property defined.
            # The property does special validation, so we actually need to
            # run its setter. We get it from the class definition and call
            # it directly. XXX Heinous.
            prop = getattr(self.__class__, name)
            prop.fset(self, val)
            return


        # This is an additional property of some kind
        try:
            val = self.__extensible__.instantiate(name, val)
        except Exception as e:
            raise pjo_validators.ValidationError(
                "Attempted to set unknown property '{0}' in {1}: {2} "
                .format(name, self.__class__.__name__, e))
        self._extended_properties[name] = val

    def _get_prop(self, name):
        """
        Accessor to property dealing with lazy_data, standard properties and potential extended properties
        """
        if self._lazy_loading and name in self._lazy_data:
            setattr(self, name, self._lazy_data.pop(name))
        if name in self.__prop_names_flatten__.values():
            return self._properties.get(name)
        return self._extended_properties.get(name)

    def _get_prop_value(self, name, default=None, no_defaults=True):
        """
        Accessor to property value (as for json)
        """
        if self._lazy_loading and name in self._lazy_data:
            val = self._lazy_data[name]
            return val.for_json(no_defaults=no_defaults) if hasattr(val, 'for_json') else val
        prop = self._get_prop(name)
        return prop.for_json() if prop else default

    def _set_prop_value(self, name, value):
        """
        Set a property shortcutting the setter. To be used in setters
        """
        if self._lazy_loading:
            self._lazy_data[name] = value
        else:
            prop = self._get_prop(name)
            if prop:
                prop.__init__(value)
                prop.do_validate()
            elif name in self.__prop_names_flatten__:
                pinfo = self.propinfo(name)
                typ = pinfo.get('_type') if pinfo else None
                if typ and issubclass(typ, pjo_literals.LiteralValue):
                    prop = typ(value)
                    prop.do_validate()
                    self._properties[name] = prop
                else:
                    raise AttributeError("no type specified for property '%s'"% name)
