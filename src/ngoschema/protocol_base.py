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
import six
import collections

from python_jsonschema_objects import \
    classbuilder as pjo_classbuilder, \
    util as pjo_util, \
    wrapper_types as pjo_wrapper_types, \
    literals as pjo_literals, \
    validators as pjo_validators

from . import utils, jinja2
from .canonical_name import resolve_cname, CN_KEY
from .decorators import classproperty
from .mixins import HasCache, HasParent, HandleRelativeCname
from .logger import HasLogger
from .uri_identifier import resolve_uri
from .validators import DefaultValidator
from .config import ConfigLoader


# loader to register module with a transforms folder where to look for model transformations
models_module_loader = utils.GenericModuleFileLoader('models')

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
            self.logger.debug("lazy loading of '%s'" % propname)
            setattr(self, propname, self._lazy_data.pop(propname))

        prop = self._properties.get(propname)

        if fget and (not prop or prop.is_dirty()):
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
                #self._add_outputs(**{propname: prop})
                prop.do_validate()
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
            if not (propname in self.__has_default__ and self._properties.get(propname) is None):
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
                            val = typ(**self._obj_conf,
                                      **pjo_util.coerce_for_expansion(val))
                        else:
                            val = typ(val)
                    except Exception as e:
                        errors.append(
                            "Failed to coerce to '%s': %s" % (typ, e))
                    else:
                        if isinstance(val, HasParent):
                            val.set_parent(self)
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
                val.set_parent(self)
                val.do_validate()

        elif getattr(infotype, "isLiteralClass", False):
            if hasattr(infotype, 'foreignClass') and str(val).startswith('#'):
                val = self._clean_cname(val)

            if not isinstance(val, infotype):
                validator = infotype(val)
                # handle case of patterns
                if utils.is_pattern(val):
                    vars = jinja2.get_variables(val)
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
                    val = infotype(**self._obj_conf,
                                   **pjo_util.coerce_for_expansion(val))
                else:
                    val = infotype(val)
            if isinstance(val, HasParent):
                val.set_parent(self)
            val.do_validate()

        elif isinstance(infotype, pjo_classbuilder.TypeProxy):
            val = infotype(val)

        elif isinstance(infotype, pjo_classbuilder.TypeRef):
            if not isinstance(val, infotype.ref_class):
                if not utils.is_string(val):
                    val = infotype(**self._obj_conf,
                                   **pjo_util.coerce_for_expansion(val))
                else:
                    val = infotype(val)
            if isinstance(val, HasParent):
                val.set_parent(self)
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


class ProtocolBase(HandleRelativeCname, HasParent, HasCache, HasLogger, pjo_classbuilder.ProtocolBase):
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
    _instances = weakref.WeakValueDictionary()
    __prop_names__ = dict()
    __prop_translated__ = dict()

    def __new__(cls,
                *args,
                lazy_loading=None,
                validate_lazy=None,
                attr_by_name=None,
                **props):
        """
        function creating the class with a special treatment to resolve subclassing
        """
        from .resolver import get_resolver
        from .classbuilder import get_builder

        # specific treatment in case schemaUri redefines the class to create
        if len(args)==1 and utils.is_string(args[0]):
            props['$ref'] = args[0]
        if '$ref' in props:
            props.update(resolve_uri(props.pop('$ref')))

        schemaUri = props.get('schemaUri', None)
        if schemaUri is not None and schemaUri != cls.__schema__.get('$id'):
            cls = get_builder().resolve_or_build(schemaUri)

        cls.init_class_logger()
        cls._lazy_loading = lazy_loading or cls.__lazy_loading__
        cls._validate_lazy = validate_lazy or cls.__validate_lazy__
        cls._attr_by_name = attr_by_name or cls.__attr_by_name__

        # option to validate arguments at init even if lazy loading
        if cls._lazy_loading and cls._validate_lazy and cls._validator is None:
            cls._validator = DefaultValidator(
                cls.__schema__, resolver=get_resolver())
            cls._validator._setDefaults = True

        new = super(ProtocolBase, cls).__new__
        if new is object.__new__:
            return new(cls)
        return new(cls, **props)
        # return new(cls, *args, **props) super ProtocolBase does not support args

    def __init__(self,
                 *args,
                 lazy_loading=None,
                 validate_lazy=None,
                 attr_by_name=None,
                 **props):
        """
        main initialization method, dealing with lazy loading
        """
        self.logger.debug(pjo_util.lazy_format("INIT {0}", self.short_repr()))

        cls = self.__class__

        self._key2attr = dict()

        HasCache.__init__(self)

        # register instance
        for c in self.pbase_mro(ngo_base=True):
            c._instances[id(self)] = self

        self._lazy_data = dict()
        self._extended_properties = dict()
        self._properties = dict()
        for c in self.pbase_mro(ngo_base=True):
            self._properties.update(dict(
                zip(c.__prop_names__.values(),
                    [None
                     for x in six.moves.xrange(len(c.__prop_names__))])))

        # To support defaults, we have to actually execute the constructors
        # but only for the ones that have defaults set.
        for c in self.pbase_mro():
            for name in c.__has_default__:
                if name not in props:
                    default_value = c.__propinfo__[name]['default']
                    setattr(self, name, default_value)

        # reference to property extern to document to be resolved later
        if len(args)==1 and utils.is_string(args[0]):
            props['$ref'] = args[0]
        if '$ref' in props:
            props.update(resolve_uri(props.pop('$ref')))

        # remove initial values of readonly members
        for k in self.__read_only__.intersection(props.keys()):
            props.pop(k)
            self.logger.warning('property %s is read-only. Initial value provided not used.', k)

        # force lazy loading to meta
        self._lazy_loading = False
        if 'parent' in props:
            self.parent = props['parent']
        if 'name' in props:
            self.name = props['name']
        if 'canonicalName' in props:
            self.canonicalName = props['canonicalName']

        self._lazy_loading = lazy_loading or cls._lazy_loading
        self._validate_lazy = validate_lazy or cls._validate_lazy
        self._attr_by_name = attr_by_name or cls._attr_by_name
        # _obj_conf will be used to initialize children objects and propagate settings
        self._obj_conf = {
            'lazy_loading': self._lazy_loading,
            'validate_lazy': self._validate_lazy,
            'attr_by_name': self._attr_by_name
        }

        if self._lazy_loading:
            self._lazy_data.update(props)
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

    @property
    def _id(self):
        return id(self)

    def __hash__(self):
        """hash function to store objects references"""
        return id(self)

    def short_repr(self):
        return "<%s id=%s>" % (
            self.fullname(),
            self._id
        )

    def __str__(self):
        props = sorted(["%s=%s" % (self.__prop_translated_flatten__.get(k, k), str(v))
                        for k, v in itertools.chain(six.iteritems(self._properties),
                                    six.iteritems(self._extended_properties))
                        if v is not None])
        props = props[:20] + (['...'] if len(props)>=20 else [])
        return "<%s id=%s %s>" % (
            self.fullname(),
            self._id,
            " ".join(props)
        )

    def __repr__(self):
        props = sorted(["%s=%s" % (self.__prop_translated_flatten__.get(k, k), repr(v))
                        for k, v in itertools.chain(six.iteritems(self._properties),
                                    six.iteritems(self._extended_properties))])
        return "<%s id=%s %s>" % (
            self.fullname(),
            self._id,
            " ".join(props)
        )

    def __eq__(self, other):
        if not HasCache.__eq__(self, other):
            return False
        if not isinstance(other, ProtocolBase):
            return False
        for k in set(self.keys()).intersection(other.keys()):
            if self[k] != other[k]:
                return False
        return True

    def for_json(self, no_defaults=True):
        """
        serialization method, removing all members flagged as NotSerialized
        """
        out = collections.OrderedDict()
        for prop in self:
            # remove items flagged as not_serilalized
            if prop in self.__not_serialized__:
                continue
            propval = self[prop]
            if no_defaults and not propval:
                continue
            # evaluate default value and drop it from json
            if no_defaults and prop in self.__has_default__:
                default_value = self.__propinfo__[prop]['default']
                if isinstance(propval, (ProtocolBase, pjo_wrapper_types.ArrayWrapper)):
                    if len(propval) != len(default_value):
                        pass
                    elif propval == default_value:
                        continue
                elif isinstance(propval, pjo_literals.LiteralValue):
                    if propval == default_value:
                        continue
                    if utils.is_pattern(default_value):
                        default_value = jinja2.TemplatedString(default_value)(self)
                        if propval == default_value:
                            continue
            if isinstance(propval, (ProtocolBase, pjo_wrapper_types.ArrayWrapper)):
                if not len(propval) and no_defaults:
                    continue
            if hasattr(propval, 'for_json'):
                value = propval.for_json()
                if utils.is_pattern(value):
                    value = jinja2.TemplatedString(value)(self)
            elif isinstance(propval, list):
                value = [x.for_json() for x in propval]
            elif propval is not None:
                value = propval
            if not value and not isinstance(value, bool):
                continue
            prop = self.__prop_translated__.get(prop, prop)
            out[prop] = value
        return out

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
        base = ProtocolBase if ngo_base else pjo_classbuilder.ProtocolBase
        return (c for c in cls.__mro__
                if pjo_util.safe_issubclass(c, base))

    @classproperty
    def __prop_names_flatten__(cls):
        """list of all available inherited properties"""
        return itertools.chain(*[getattr(c, '__prop_names__', ())
            for  c in cls.pbase_mro()])


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

        propname = self.__prop_translated_flatten__.get(name, name)

        # check it s not a schema defined property, we should not reach there
        for c in self.pbase_mro():
            if propname in c.__propinfo__:
                raise KeyError(name)
            if hasattr(c, name):
                return object.__getattribute__(self, name)
        # check it s an extended property
        if name in self._extended_properties:
            return self._extended_properties[name]

        raise AttributeError("{0} is not a valid property of {1}".format(
                             name, self.__class__.__name__))


    def __getitem__(self, key):
        """access property as in a dict"""
        try:
            return getattr(self, key)
        except AttributeError as er:
            raise KeyError(key)
        except Exception as er:
            raise er

    def __setattr__(self, name, val):
        """allow setting of protected attributes"""
        if name.startswith("_"):
            return collections.MutableMapping.__setattr__(self, name, val)

        propname = self.__prop_translated_flatten__.get(name, name)

        for c in self.pbase_mro():
            if name in c.__object_attr_list__:
                return object.__setattr__(self, name, val)
            elif name in c.__propinfo__:
                # If its in __propinfo__, then it actually has a property defined.
                # The property does special validation, so we actually need to
                # run its setter. We get it from the class definition and call
                # it directly. XXX Heinous.
                prop = getattr(c, c.__prop_names__[propname])
                prop.fset(self, val)
                return

        prop, index = self._key2attr.get(name, (None, None))
        if prop:
            if index is None:
                return ProtocolBase.__setattr__(self, prop, val)
            else:
                attr = getattr(self, prop)
                attr[index] = val
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
        if self._lazy_loading and name in self._lazy_data:
            setattr(self, name, self._lazy_data.pop(name))
        if name in self.__prop_names_flatten__:
            return self._properties.get(name)
        return self._extended_properties.get(name)

    def _get_prop_value(self, name):
        if self._lazy_loading and name in self._lazy_data:
            return self._lazy_data[name]
        prop = self._get_prop(name)
        return prop.for_json() if prop else None

    def _set_prop_value(self, name, value):
        """
        Set a property shorcutting the setter. To be used in setters
        """
        if self._lazy_loading:
            self._lazy_data[name] = value
        else:
            prop = self._get_prop(name)
            if prop:
                prop.__init__()
            propinfo = self.propinfo(name)
        prop = self._get_prop(name)
        return prop.for_json() if prop else None

    def _set_prop_value(self, prop, value):
        """
        Set a property shorcutting the setter. To be used in setters
        """
        # if the component is lazy loaded, dont force its loading now
        # add the value to the data to be loaded later and return
        if self._lazy_loading and isinstance(self._lazy_loading, dict):
            self._lazy_loading[prop] = value
            return
        try:
            propval = self._properties.get(prop)
            propinfo = self.propinfo(prop)
            if hasattr(propval, 'validate'):
                propval.__init__(value)
                propval.validate()
                # should be enough... set it back anyway ?
                self._properties[prop] = value
            # a validator is available
            elif propinfo.get('_type') and issubclass(propinfo['_type'], pjo_literals.LiteralValue):
                val = propinfo['_type'](value)
                val.validate()
                self._properties[prop] = val
            else:
                prop_ = getattr(self.__class__, self.__prop_translated_flatten__.get(prop, prop))
                prop_.fset(self, value)
        except Exception as er:
            self.logger.error(er)
            raise er

    def _get_prop_value(self, prop, default=None):
        """
        Get a property shorcutting the setter. To be used in setters
        """
        try:
            validator = self._properties.get(prop)
            if self._lazy_loading and prop in self._lazy_data:
                return self._lazy_data.get(prop, default)
            if validator is not None:
                validator.do_validate(force=True)
                return validator
            return default
        except Exception as er:
            raise er


    @classmethod
    def one(cls, *attrs, load_lazy=False, **attrs_value):
        """retrieves exactly one instance corresponding to query

        Query can used all usual operators"""
        from .query import Query
        ret = list(
            Query(cls._instances)._filter_or_exclude(
                *attrs, load_lazy=load_lazy, **attrs_value))
        if len(ret) == 0:
            raise ValueError('Entry %s does not exist' % attrs_value)
        elif len(ret) > 1:
            import logging
            cls.logger.error(ret)
            raise ValueError('Multiple objects returned')
        return ret[0]

    @classmethod
    def one_or_none(cls, *attrs, load_lazy=False, **attrs_value):
        """retrieves exactly one instance corresponding to query

        Query can used all usual operators"""
        from .query import Query
        ret = list(
            Query(cls._instances)._filter_or_exclude(
                *attrs, load_lazy=load_lazy, **attrs_value))
        if len(ret) == 0:
            return None
        elif len(ret) > 1:
            import logging
            cls.logger.error(ret)
            raise ValueError('Multiple objects returned')
        return ret[0]

    @classmethod
    def first(cls, *attrs, load_lazy=False, **attrs_value):
        """retrieves exactly one instance corresponding to query

        Query can used all usual operators"""
        from .query import Query
        return next(
            Query(cls._instances).filter(
                *attrs, load_lazy=load_lazy, **attrs_value))

    @classmethod
    def filter(cls, *attrs, load_lazy=False, **attrs_value):
        """retrieves a list of instances corresponding to query

        Query can used all usual operators"""
        from .query import Query
        return list(
            Query(cls._instances).filter(
                *attrs, load_lazy=load_lazy, **attrs_value))

    @classproperty
    def instances(cls):
        return (v() for v in cls._instances.valuerefs() if v())
