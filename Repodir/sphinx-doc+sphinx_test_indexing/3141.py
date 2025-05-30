"""
    sphinx.registry
    ~~~~~~~~~~~~~~~

    Sphinx component registry.

    :copyright: Copyright 2007-2021 by the Sphinx team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import traceback
from importlib import import_module
from types import MethodType
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Tuple, Type, Union

from docutils import nodes
from docutils.io import Input
from docutils.nodes import Element, Node, TextElement
from docutils.parsers import Parser
from docutils.parsers.rst import Directive
from docutils.transforms import Transform
from pkg_resources import iter_entry_points

from sphinx.builders import Builder
from sphinx.config import Config
from sphinx.domains import Domain, Index, ObjType
from sphinx.domains.std import GenericObject, Target
from sphinx.environment import BuildEnvironment
from sphinx.errors import ExtensionError, SphinxError, VersionRequirementError
from sphinx.extension import Extension
from sphinx.locale import __
from sphinx.parsers import Parser as SphinxParser
from sphinx.roles import XRefRole
from sphinx.util import logging
from sphinx.util.logging import prefixed_warnings
from sphinx.util.typing import RoleFunction, TitleGetter

if TYPE_CHECKING:
    from sphinx.application import Sphinx
    from sphinx.ext.autodoc import Documenter

logger = logging.getLogger(__name__)

# list of deprecated extensions. Keys are extension name.
# Values are Sphinx version that merge the extension.
EXTENSION_BLACKLIST = {
    "sphinxjp.themecore": "1.2"
}


class SphinxComponentRegistry:
    def __init__(self) -> None:
        #: special attrgetter for autodoc; class object -> attrgetter
        self.autodoc_attrgettrs = {}    # type: Dict[Type, Callable[[Any, str, Any], Any]]

        #: builders; a dict of builder name -> bulider class
        self.builders = {}              # type: Dict[str, Type[Builder]]

        #: autodoc documenters; a dict of documenter name -> documenter class
        self.documenters = {}           # type: Dict[str, Type[Documenter]]

        #: css_files; a list of tuple of filename and attributes
        self.css_files = []             # type: List[Tuple[str, Dict[str, str]]]

        #: domains; a dict of domain name -> domain class
        self.domains = {}               # type: Dict[str, Type[Domain]]

        #: additional directives for domains
        #: a dict of domain name -> dict of directive name -> directive
        self.domain_directives = {}     # type: Dict[str, Dict[str, Any]]

        #: additional indices for domains
        #: a dict of domain name -> list of index class
        self.domain_indices = {}        # type: Dict[str, List[Type[Index]]]

        #: additional object types for domains
        #: a dict of domain name -> dict of objtype name -> objtype
        self.domain_object_types = {}   # type: Dict[str, Dict[str, ObjType]]

        #: additional roles for domains
        #: a dict of domain name -> dict of role name -> role impl.
        self.domain_roles = {}          # type: Dict[str, Dict[str, Union[RoleFunction, XRefRole]]]  # NOQA

        #: additional enumerable nodes
        #: a dict of node class -> tuple of figtype and title_getter function
        self.enumerable_nodes = {}      # type: Dict[Type[Node], Tuple[str, TitleGetter]]

        #: HTML inline and block math renderers
        #: a dict of name -> tuple of visit function and depart function
        self.html_inline_math_renderers = {}    # type: Dict[str, Tuple[Callable, Callable]]
        self.html_block_math_renderers = {}     # type: Dict[str, Tuple[Callable, Callable]]

        #: js_files; list of JS paths or URLs
        self.js_files = []              # type: List[Tuple[str, Dict[str, str]]]

        #: LaTeX packages; list of package names and its options
        self.latex_packages = []        # type: List[Tuple[str, str]]

        self.latex_packages_after_hyperref = []     # type: List[Tuple[str, str]]

        #: post transforms; list of transforms
        self.post_transforms = []       # type: List[Type[Transform]]

        #: source paresrs; file type -> parser class
        self.source_parsers = {}        # type: Dict[str, Type[Parser]]

        #: source inputs; file type -> input class
        self.source_inputs = {}         # type: Dict[str, Type[Input]]

        #: source suffix: suffix -> file type
        self.source_suffix = {}         # type: Dict[str, str]

        #: custom translators; builder name -> translator class
        self.translators = {}           # type: Dict[str, Type[nodes.NodeVisitor]]

        #: custom handlers for translators
        #: a dict of builder name -> dict of node name -> visitor and departure functions
        self.translation_handlers = {}  # type: Dict[str, Dict[str, Tuple[Callable, Callable]]]

        #: additional transforms; list of transforms
        self.transforms = []            # type: List[Type[Transform]]

    def add_builder(self, builder: "Type[Builder]", override: bool = False) -> None:
        logger.debug('[app] adding builder: %r', builder)
        if not hasattr(builder, 'name'):
            raise ExtensionError(__('Builder class %s has no "name" attribute') % builder)
        if builder.name in self.builders and not override:
            raise ExtensionError(__('Builder %r already exists (in module %s)') %
                                 (builder.name, self.builders[builder.name].__module__))
        self.builders[builder.name] = builder

    def preload_builder(self, app: "Sphinx", name: str) -> None:
        if name is None:
            return

        if name not in self.builders:
            entry_points = iter_entry_points('sphinx.builders', name)
            try:
                entry_point = next(entry_points)
            except StopIteration as exc:
                raise SphinxError(__('Builder name %s not registered or available'
                                     ' through entry point') % name) from exc

            self.load_extension(app, entry_point.module_name)

    def create_builder(self, app: "Sphinx", name: str) -> Builder:
        if name not in self.builders:
            raise SphinxError(__('Builder name %s not registered') % name)

        return self.builders[name](app)

    def add_domain(self, domain: "Type[Domain]", override: bool = False) -> None:
        logger.debug('[app] adding domain: %r', domain)
        if domain.name in self.domains and not override:
            raise ExtensionError(__('domain %s already registered') % domain.name)
        self.domains[domain.name] = domain

    def has_domain(self, domain: str) -> bool:
        return domain in self.domains

    def create_domains(self, env: BuildEnvironment) -> Iterator[Domain]:
        for DomainClass in self.domains.values():
            domain = DomainClass(env)

            # transplant components added by extensions
            domain.directives.update(self.domain_directives.get(domain.name, {}))
            domain.roles.update(self.domain_roles.get(domain.name, {}))
            domain.indices.extend(self.domain_indices.get(domain.name, []))
            for name, objtype in self.domain_object_types.get(domain.name, {}).items():
                domain.add_object_type(name, objtype)

            yield domain

    def add_directive_to_domain(self, domain: str, name: str,
                                cls: "Type[Directive]", override: bool = False) -> None:
        logger.debug('[app] adding directive to domain: %r', (domain, name, cls))
        if domain not in self.domains:
            raise ExtensionError(__('domain %s not yet registered') % domain)

        directives = self.domain_directives.setdefault(domain, {})
        if name in directives and not override:
            raise ExtensionError(__('The %r directive is already registered to domain %s') %
                                 (name, domain))
        directives[name] = cls

    def add_role_to_domain(self, domain: str, name: str,
                           role: Union[RoleFunction, XRefRole], override: bool = False
                           ) -> None:
        logger.debug('[app] adding role to domain: %r', (domain, name, role))
        if domain not in self.domains:
            raise ExtensionError(__('domain %s not yet registered') % domain)
        roles = self.domain_roles.setdefault(domain, {})
        if name in roles and not override:
            raise ExtensionError(__('The %r role is already registered to domain %s') %
                                 (name, domain))
        roles[name] = role

    def add_index_to_domain(self, domain: str, index: "Type[Index]",
                            override: bool = False) -> None:
        logger.debug('[app] adding index to domain: %r', (domain, index))
        if domain not in self.domains:
            raise ExtensionError(__('domain %s not yet registered') % domain)
        indices = self.domain_indices.setdefault(domain, [])
        if index in indices and not override:
            raise ExtensionError(__('The %r index is already registered to domain %s') %
                                 (index.name, domain))
        indices.append(index)

    def add_object_type(self, directivename: str, rolename: str, indextemplate: str = '',
                        parse_node: Callable = None, ref_nodeclass: "Type[TextElement]" = None,
                        objname: str = '', doc_field_types: List = [], override: bool = False
                        ) -> None:
        logger.debug('[app] adding object type: %r',
                     (directivename, rolename, indextemplate, parse_node,
                      ref_nodeclass, objname, doc_field_types))

        # create a subclass of GenericObject as the new directive
        directive = type(directivename,
                         (GenericObject, object),
                         {'indextemplate': indextemplate,
                          'parse_node': staticmethod(parse_node),
                          'doc_field_types': doc_field_types})

        self.add_directive_to_domain('std', directivename, directive)
        self.add_role_to_domain('std', rolename, XRefRole(innernodeclass=ref_nodeclass))

        object_types = self.domain_object_types.setdefault('std', {})
        if directivename in object_types and not override:
            raise ExtensionError(__('The %r object_type is already registered') %
                                 directivename)
        object_types[directivename] = ObjType(objname or directivename, rolename)

    def add_crossref_type(self, directivename: str, rolename: str, indextemplate: str = '',
                          ref_nodeclass: "Type[TextElement]" = None, objname: str = '',
                          override: bool = False) -> None:
        logger.debug('[app] adding crossref type: %r',
                     (directivename, rolename, indextemplate, ref_nodeclass, objname))

        # create a subclass of Target as the new directive
        directive = type(directivename,
                         (Target, object),
                         {'indextemplate': indextemplate})

        self.add_directive_to_domain('std', directivename, directive)
        self.add_role_to_domain('std', rolename, XRefRole(innernodeclass=ref_nodeclass))

        object_types = self.domain_object_types.setdefault('std', {})
        if directivename in object_types and not override:
            raise ExtensionError(__('The %r crossref_type is already registered') %
                                 directivename)
        object_types[directivename] = ObjType(objname or directivename, rolename)

    def add_source_suffix(self, suffix: str, filetype: str, override: bool = False) -> None:
        logger.debug('[app] adding source_suffix: %r, %r', suffix, filetype)
        if suffix in self.source_suffix and not override:
            raise ExtensionError(__('source_suffix %r is already registered') % suffix)
        else:
            self.source_suffix[suffix] = filetype

    def add_source_parser(self, parser: "Type[Parser]", override: bool = False) -> None:
        logger.debug('[app] adding search source_parser: %r', parser)

        # create a map from filetype to parser
        for filetype in parser.supported:
            if filetype in self.source_parsers and not override:
                raise ExtensionError(__('source_parser for %r is already registered') %
                                     filetype)
            else:
                self.source_parsers[filetype] = parser

    def get_source_parser(self, filetype: str) -> "Type[Parser]":
        try:
            return self.source_parsers[filetype]
        except KeyError as exc:
            raise SphinxError(__('Source parser for %s not registered') % filetype) from exc

    def get_source_parsers(self) -> Dict[str, "Type[Parser]"]:
        return self.source_parsers

    def create_source_parser(self, app: "Sphinx", filename: str) -> Parser:
        parser_class = self.get_source_parser(filename)
        parser = parser_class()
        if isinstance(parser, SphinxParser):
            parser.set_application(app)
        return parser

    def get_source_input(self, filetype: str) -> "Type[Input]":
        try:
            return self.source_inputs[filetype]
        except KeyError:
            try:
                # use special source_input for unknown filetype
                return self.source_inputs['*']
            except KeyError:
                return None

    def add_translator(self, name: str, translator: "Type[nodes.NodeVisitor]",
                       override: bool = False) -> None:
        logger.debug('[app] Change of translator for the %s builder.', name)
        if name in self.translators and not override:
            raise ExtensionError(__('Translator for %r already exists') % name)
        self.translators[name] = translator

    def add_translation_handlers(self, node: "Type[Element]",
                                 **kwargs: Tuple[Callable, Callable]) -> None:
        logger.debug('[app] adding translation_handlers: %r, %r', node, kwargs)
        for builder_name, handlers in kwargs.items():
            translation_handlers = self.translation_handlers.setdefault(builder_name, {})
            try:
                visit, depart = handlers  # unpack once for assertion
                translation_handlers[node.__name__] = (visit, depart)
            except ValueError as exc:
                raise ExtensionError(
                    __('kwargs for add_node() must be a (visit, depart) '
                       'function tuple: %r=%r') % (builder_name, handlers)
                ) from exc

    def get_translator_class(self, builder: Builder) -> "Type[nodes.NodeVisitor]":
        return self.translators.get(builder.name,
                                    builder.default_translator_class)

    def create_translator(self, builder: Builder, *args: Any) -> nodes.NodeVisitor:
        translator_class = self.get_translator_class(builder)
        assert translator_class, "translator not found for %s" % builder.name
        translator = translator_class(*args)

        # transplant handlers for custom nodes to translator instance
        handlers = self.translation_handlers.get(builder.name, None)
        if handlers is None:
            # retry with builder.format
            handlers = self.translation_handlers.get(builder.format, {})

        for name, (visit, depart) in handlers.items():
            setattr(translator, 'visit_' + name, MethodType(visit, translator))
            if depart:
                setattr(translator, 'depart_' + name, MethodType(depart, translator))

        return translator

    def add_transform(self, transform: "Type[Transform]") -> None:
        logger.debug('[app] adding transform: %r', transform)
        self.transforms.append(transform)

    def get_transforms(self) -> List["Type[Transform]"]:
        return self.transforms

    def add_post_transform(self, transform: "Type[Transform]") -> None:
        logger.debug('[app] adding post transform: %r', transform)
        self.post_transforms.append(transform)

    def get_post_transforms(self) -> List["Type[Transform]"]:
        return self.post_transforms

    def add_documenter(self, objtype: str, documenter: "Type[Documenter]") -> None:
        self.documenters[objtype] = documenter

    def add_autodoc_attrgetter(self, typ: "Type",
                               attrgetter: Callable[[Any, str, Any], Any]) -> None:
        self.autodoc_attrgettrs[typ] = attrgetter

    def add_css_files(self, filename: str, **attributes: str) -> None:
        self.css_files.append((filename, attributes))

    def add_js_file(self, filename: str, **attributes: str) -> None:
        logger.debug('[app] adding js_file: %r, %r', filename, attributes)
        self.js_files.append((filename, attributes))

    def has_latex_package(self, name: str) -> bool:
        packages = self.latex_packages + self.latex_packages_after_hyperref
        return bool([x for x in packages if x[0] == name])

    def add_latex_package(self, name: str, options: str, after_hyperref: bool = False) -> None:
        if self.has_latex_package(name):
            logger.warn("latex package '%s' already included" % name)

        logger.debug('[app] adding latex package: %r', name)
        if after_hyperref:
            self.latex_packages_after_hyperref.append((name, options))
        else:
            self.latex_packages.append((name, options))

    def add_enumerable_node(self, node: "Type[Node]", figtype: str,
                            title_getter: TitleGetter = None, override: bool = False) -> None:
        logger.debug('[app] adding enumerable node: (%r, %r, %r)', node, figtype, title_getter)
        if node in self.enumerable_nodes and not override:
            raise ExtensionError(__('enumerable_node %r already registered') % node)
        self.enumerable_nodes[node] = (figtype, title_getter)

    def add_html_math_renderer(self, name: str,
                               inline_renderers: Tuple[Callable, Callable],
                               block_renderers: Tuple[Callable, Callable]) -> None:
        logger.debug('[app] adding html_math_renderer: %s, %r, %r',
                     name, inline_renderers, block_renderers)
        if name in self.html_inline_math_renderers:
            raise ExtensionError(__('math renderer %s is already registred') % name)

        self.html_inline_math_renderers[name] = inline_renderers
        self.html_block_math_renderers[name] = block_renderers

    def load_extension(self, app: "Sphinx", extname: str) -> None:
        """Load a Sphinx extension."""
        if extname in app.extensions:  # already loaded
            return
        if extname in EXTENSION_BLACKLIST:
            logger.warning(__('the extension %r was already merged with Sphinx since '
                              'version %s; this extension is ignored.'),
                           extname, EXTENSION_BLACKLIST[extname])
            return

        # update loading context
        prefix = __('while setting up extension %s:') % extname
        with prefixed_warnings(prefix):
            try:
                mod = import_module(extname)
            except ImportError as err:
                logger.verbose(__('Original exception:\n') + traceback.format_exc())
                raise ExtensionError(__('Could not import extension %s') % extname,
                                     err) from err

            setup = getattr(mod, 'setup', None)
            if setup is None:
                logger.warning(__('extension %r has no setup() function; is it really '
                                  'a Sphinx extension module?'), extname)
                metadata = {}  # type: Dict[str, Any]
            else:
                try:
                    metadata = setup(app)
                except VersionRequirementError as err:
                    # add the extension name to the version required
                    raise VersionRequirementError(
                        __('The %s extension used by this project needs at least '
                           'Sphinx v%s; it therefore cannot be built with this '
                           'version.') % (extname, err)
                    ) from err

            if metadata is None:
                metadata = {}
            elif not isinstance(metadata, dict):
                logger.warning(__('extension %r returned an unsupported object from '
                                  'its setup() function; it should return None or a '
                                  'metadata dictionary'), extname)
                metadata = {}

            app.extensions[extname] = Extension(extname, mod, **metadata)

    def get_envversion(self, app: "Sphinx") -> Dict[str, str]:
        from sphinx.environment import ENV_VERSION
        envversion = {ext.name: ext.metadata['env_version'] for ext in app.extensions.values()
                      if ext.metadata.get('env_version')}
        envversion['sphinx'] = ENV_VERSION
        return envversion


def merge_source_suffix(app: "Sphinx", config: Config) -> None:
    """Merge source_suffix which specified by user and added by extensions."""
    for suffix, filetype in app.registry.source_suffix.items():
        if suffix not in app.config.source_suffix:
            app.config.source_suffix[suffix] = filetype
        elif app.config.source_suffix[suffix] is None:
            # filetype is not specified (default filetype).
            # So it overrides default filetype by extensions setting.
            app.config.source_suffix[suffix] = filetype

    # copy config.source_suffix to registry
    app.registry.source_suffix = app.config.source_suffix


def setup(app: "Sphinx") -> Dict[str, Any]:
    app.connect('config-inited', merge_source_suffix, priority=800)

    return {
        'version': 'builtin',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
