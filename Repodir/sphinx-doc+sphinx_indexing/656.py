"""
    sphinx.util.nodes
    ~~~~~~~~~~~~~~~~~

    Docutils node-related utility functions for Sphinx.

    :copyright: Copyright 2007-2020 by the Sphinx team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import re
import warnings
from typing import Any, Callable, Iterable, List, Set, Tuple
from typing import cast

from docutils import nodes
from docutils.nodes import Element, Node
from docutils.parsers.rst import Directive
from docutils.parsers.rst.states import Inliner
from docutils.statemachine import StringList

from sphinx import addnodes
from sphinx.deprecation import RemovedInSphinx40Warning
from sphinx.locale import __
from sphinx.util import logging

if False:
    # For type annotation
    from typing import Type  # for python3.5.1
    from sphinx.builders import Builder
    from sphinx.environment import BuildEnvironment
    from sphinx.utils.tags import Tags

logger = logging.getLogger(__name__)


# \x00 means the "<" was backslash-escaped
explicit_title_re = re.compile(r'^(.+?)\s*(?<!\x00)<([^<]*?)>$', re.DOTALL)
caption_ref_re = explicit_title_re  # b/w compat alias


class NodeMatcher:
    """A helper class for Node.traverse().

    It checks that given node is an instance of specified node-classes and it has
    specified node-attributes.

    For example, following example searches ``reference`` node having ``refdomain``
    and ``reftype`` attributes::

        matcher = NodeMatcher(nodes.reference, refdomain='std', reftype='citation')
        doctree.traverse(matcher)
        # => [<reference ...>, <reference ...>, ...]

    A special value ``typing.Any`` matches any kind of node-attributes.  For example,
    following example searches ``reference`` node having ``refdomain`` attributes::

        from typing import Any
        matcher = NodeMatcher(nodes.reference, refdomain=Any)
        doctree.traverse(matcher)
        # => [<reference ...>, <reference ...>, ...]
    """

    def __init__(self, *classes: "Type[Node]", **attrs: Any) -> None:
        self.classes = classes
        self.attrs = attrs

    def match(self, node: Node) -> bool:
        try:
            if self.classes and not isinstance(node, self.classes):
                return False

            if self.attrs:
                if not isinstance(node, nodes.Element):
                    return False

                for key, value in self.attrs.items():
                    if key not in node:
                        return False
                    elif value is Any:
                        continue
                    elif node.get(key) != value:
                        return False

            return True
        except Exception:
            # for non-Element nodes
            return False

    def __call__(self, node: Node) -> bool:
        return self.match(node)


def get_full_module_name(node: Node) -> str:
    """
    return full module dotted path like: 'docutils.nodes.paragraph'

    :param nodes.Node node: target node
    :return: full module dotted path
    """
    return '{}.{}'.format(node.__module__, node.__class__.__name__)


def repr_domxml(node: Node, length: int = 80) -> str:
    """
    return DOM XML representation of the specified node like:
    '<paragraph translatable="False"><inline classes="versionmodified">New in version...'

    :param nodes.Node node: target node
    :param int length:
       length of return value to be striped. if false-value is specified, repr_domxml
       returns full of DOM XML representation.
    :return: DOM XML representation
    """
    try:
        text = node.asdom().toxml()
    except Exception:
        text = str(node)
    if length and len(text) > length:
        text = text[:length] + '...'
    return text


def apply_source_workaround(node: Element) -> None:
    # workaround: nodes.term have wrong rawsource if classifier is specified.
    # The behavior of docutils-0.11, 0.12 is:
    # * when ``term text : classifier1 : classifier2`` is specified,
    # * rawsource of term node will have: ``term text : classifier1 : classifier2``
    # * rawsource of classifier node will be None
    if isinstance(node, nodes.classifier) and not node.rawsource:
        logger.debug('[i18n] PATCH: %r to have source, line and rawsource: %s',
                     get_full_module_name(node), repr_domxml(node))
        definition_list_item = node.parent
        node.source = definition_list_item.source
        node.line = definition_list_item.line - 1
        node.rawsource = node.astext()  # set 'classifier1' (or 'classifier2')
    elif isinstance(node, nodes.classifier) and not node.source:
        # docutils-0.15 fills in rawsource attribute, but not in source.
        node.source = node.parent.source
    if isinstance(node, nodes.image) and node.source is None:
        logger.debug('[i18n] PATCH: %r to have source, line: %s',
                     get_full_module_name(node), repr_domxml(node))
        node.source, node.line = node.parent.source, node.parent.line
    if isinstance(node, nodes.title) and node.source is None:
        logger.debug('[i18n] PATCH: %r to have source: %s',
                     get_full_module_name(node), repr_domxml(node))
        node.source, node.line = node.parent.source, node.parent.line
    if isinstance(node, nodes.term):
        logger.debug('[i18n] PATCH: %r to have rawsource: %s',
                     get_full_module_name(node), repr_domxml(node))
        # strip classifier from rawsource of term
        for classifier in reversed(list(node.parent.traverse(nodes.classifier))):
            node.rawsource = re.sub(r'\s*:\s*%s' % re.escape(classifier.astext()),
                                    '', node.rawsource)

    # workaround: literal_block under bullet list (#4913)
    if isinstance(node, nodes.literal_block) and node.source is None:
        node.source = get_node_source(node)

    # workaround: recommonmark-0.2.0 doesn't set rawsource attribute
    if not node.rawsource:
        node.rawsource = node.astext()

    if node.source and node.rawsource:
        return

    # workaround: some docutils nodes doesn't have source, line.
    if (isinstance(node, (
            nodes.rubric,  # #1305 rubric directive
            nodes.line,  # #1477 line node
            nodes.image,  # #3093 image directive in substitution
            nodes.field_name,  # #3335 field list syntax
    ))):
        logger.debug('[i18n] PATCH: %r to have source and line: %s',
                     get_full_module_name(node), repr_domxml(node))
        node.source = get_node_source(node)
        node.line = 0  # need fix docutils to get `node.line`
        return


IGNORED_NODES = (
    nodes.Invisible,
    nodes.literal_block,
    nodes.doctest_block,
    addnodes.versionmodified,
    # XXX there are probably more
)


def is_pending_meta(node: Node) -> bool:
    if (isinstance(node, nodes.pending) and
       isinstance(node.details.get('nodes', [None])[0], addnodes.meta)):
        return True
    else:
        return False


def is_translatable(node: Node) -> bool:
    if isinstance(node, addnodes.translatable):
        return True

    if isinstance(node, nodes.Inline) and 'translatable' not in node:  # type: ignore
        # inline node must not be translated if 'translatable' is not set
        return False

    if isinstance(node, nodes.TextElement):
        if not node.source:
            logger.debug('[i18n] SKIP %r because no node.source: %s',
                         get_full_module_name(node), repr_domxml(node))
            return False  # built-in message
        if isinstance(node, IGNORED_NODES) and 'translatable' not in node:
            logger.debug("[i18n] SKIP %r because node is in IGNORED_NODES "
                         "and no node['translatable']: %s",
                         get_full_module_name(node), repr_domxml(node))
            return False
        if not node.get('translatable', True):
            # not(node['translatable'] == True or node['translatable'] is None)
            logger.debug("[i18n] SKIP %r because not node['translatable']: %s",
                         get_full_module_name(node), repr_domxml(node))
            return False
        # <field_name>orphan</field_name>
        # XXX ignore all metadata (== docinfo)
        if isinstance(node, nodes.field_name) and node.children[0] == 'orphan':
            logger.debug('[i18n] SKIP %r because orphan node: %s',
                         get_full_module_name(node), repr_domxml(node))
            return False
        return True

    if isinstance(node, nodes.image) and node.get('translatable'):
        return True

    if isinstance(node, addnodes.meta):
        return True
    if is_pending_meta(node):
        return True

    return False


LITERAL_TYPE_NODES = (
    nodes.literal_block,
    nodes.doctest_block,
    nodes.raw,
)
IMAGE_TYPE_NODES = (
    nodes.image,
)
META_TYPE_NODES = (
    addnodes.meta,
)


def extract_messages(doctree: Element) -> Iterable[Tuple[Element, str]]:
    """Extract translatable messages from a document tree."""
    for node in doctree.traverse(is_translatable):  # type: nodes.Element
        if isinstance(node, addnodes.translatable):
            for msg in node.extract_original_messages():
                yield node, msg
            continue
        if isinstance(node, LITERAL_TYPE_NODES):
            msg = node.rawsource
            if not msg:
                msg = node.astext()
        elif isinstance(node, IMAGE_TYPE_NODES):
            msg = '.. image:: %s' % node['uri']
            if node.get('alt'):
                msg += '\n   :alt: %s' % node['alt']
        elif isinstance(node, META_TYPE_NODES):
            msg = node.rawcontent
        elif isinstance(node, nodes.pending) and is_pending_meta(node):
            msg = node.details['nodes'][0].rawcontent
        else:
            msg = node.rawsource.replace('\n', ' ').strip()

        # XXX nodes rendering empty are likely a bug in sphinx.addnodes
        if msg:
            yield node, msg


def find_source_node(node: Element) -> str:
    warnings.warn('find_source_node() is deprecated.',
                  RemovedInSphinx40Warning)
    return get_node_source(node)


def get_node_source(node: Element) -> str:
    for pnode in traverse_parent(node):
        if pnode.source:
            return pnode.source
    return None


def get_node_line(node: Element) -> int:
    for pnode in traverse_parent(node):
        if pnode.line:
            return pnode.line
    return None


def traverse_parent(node: Element, cls: Any = None) -> Iterable[Element]:
    while node:
        if cls is None or isinstance(node, cls):
            yield node
        node = node.parent


def get_prev_node(node: Node) -> Node:
    pos = node.parent.index(node)
    if pos > 0:
        return node.parent[pos - 1]
    else:
        return None


def traverse_translatable_index(doctree: Element) -> Iterable[Tuple[Element, List[str]]]:
    """Traverse translatable index node from a document tree."""
    for node in doctree.traverse(NodeMatcher(addnodes.index, inline=False)):  # type: addnodes.index  # NOQA
        if 'raw_entries' in node:
            entries = node['raw_entries']
        else:
            entries = node['entries']
        yield node, entries


def nested_parse_with_titles(state: Any, content: StringList, node: Node) -> str:
    """Version of state.nested_parse() that allows titles and does not require
    titles to have the same decoration as the calling document.

    This is useful when the parsed content comes from a completely different
    context, such as docstrings.
    """
    # hack around title style bookkeeping
    surrounding_title_styles = state.memo.title_styles
    surrounding_section_level = state.memo.section_level
    state.memo.title_styles = []
    state.memo.section_level = 0
    try:
        return state.nested_parse(content, 0, node, match_titles=1)
    finally:
        state.memo.title_styles = surrounding_title_styles
        state.memo.section_level = surrounding_section_level


def clean_astext(node: Element) -> str:
    """Like node.astext(), but ignore images."""
    node = node.deepcopy()
    for img in node.traverse(nodes.image):
        img['alt'] = ''
    for raw in node.traverse(nodes.raw):
        raw.parent.remove(raw)
    return node.astext()


def split_explicit_title(text: str) -> Tuple[bool, str, str]:
    """Split role content into title and target, if given."""
    match = explicit_title_re.match(text)
    if match:
        return True, match.group(1), match.group(2)
    return False, text, text


indextypes = [
    'single', 'pair', 'double', 'triple', 'see', 'seealso',
]


def process_index_entry(entry: str, targetid: str) -> List[Tuple[str, str, str, str, str]]:
    from sphinx.domains.python import pairindextypes

    indexentries = []  # type: List[Tuple[str, str, str, str, str]]
    entry = entry.strip()
    oentry = entry
    main = ''
    if entry.startswith('!'):
        main = 'main'
        entry = entry[1:].lstrip()
    for type in pairindextypes:
        if entry.startswith(type + ':'):
            value = entry[len(type) + 1:].strip()
            value = pairindextypes[type] + '; ' + value
            indexentries.append(('pair', value, targetid, main, None))
            break
    else:
        for type in indextypes:
            if entry.startswith(type + ':'):
                value = entry[len(type) + 1:].strip()
                if type == 'double':
                    type = 'pair'
                indexentries.append((type, value, targetid, main, None))
                break
        # shorthand notation for single entries
        else:
            for value in oentry.split(','):
                value = value.strip()
                main = ''
                if value.startswith('!'):
                    main = 'main'
                    value = value[1:].lstrip()
                if not value:
                    continue
                indexentries.append(('single', value, targetid, main, None))
    return indexentries


def inline_all_toctrees(builder: "Builder", docnameset: Set[str], docname: str,
                        tree: nodes.document, colorfunc: Callable, traversed: List[str]
                        ) -> nodes.document:
    """Inline all toctrees in the *tree*.

    Record all docnames in *docnameset*, and output docnames with *colorfunc*.
    """
    tree = cast(nodes.document, tree.deepcopy())
    for toctreenode in tree.traverse(addnodes.toctree):
        newnodes = []
        includefiles = map(str, toctreenode['includefiles'])
        for includefile in includefiles:
            if includefile not in traversed:
                try:
                    traversed.append(includefile)
                    logger.info(colorfunc(includefile) + " ", nonl=True)
                    subtree = inline_all_toctrees(builder, docnameset, includefile,
                                                  builder.env.get_doctree(includefile),
                                                  colorfunc, traversed)
                    docnameset.add(includefile)
                except Exception:
                    logger.warning(__('toctree contains ref to nonexisting file %r'),
                                   includefile, location=docname)
                else:
                    sof = addnodes.start_of_file(docname=includefile)
                    sof.children = subtree.children
                    for sectionnode in sof.traverse(nodes.section):
                        if 'docname' not in sectionnode:
                            sectionnode['docname'] = includefile
                    newnodes.append(sof)
        toctreenode.parent.replace(toctreenode, newnodes)
    return tree


def make_id(env: "BuildEnvironment", document: nodes.document,
            prefix: str = '', term: str = None) -> str:
    """Generate an appropriate node_id for given *prefix* and *term*."""
    node_id = None
    if prefix:
        idformat = prefix + "-%s"
    else:
        idformat = document.settings.id_prefix + "%s"

    # try to generate node_id by *term*
    if prefix and term:
        node_id = nodes.make_id(idformat % term)
        if node_id == prefix:
            # *term* is not good to generate a node_id.
            node_id = None

    while node_id is None or node_id in document.ids:
        node_id = idformat % env.new_serialno(prefix)

    return node_id


def make_refnode(builder: "Builder", fromdocname: str, todocname: str, targetid: str,
                 child: Node, title: str = None) -> nodes.reference:
    """Shortcut to create a reference node."""
    node = nodes.reference('', '', internal=True)
    if fromdocname == todocname and targetid:
        node['refid'] = targetid
    else:
        if targetid:
            node['refuri'] = (builder.get_relative_uri(fromdocname, todocname) +
                              '#' + targetid)
        else:
            node['refuri'] = builder.get_relative_uri(fromdocname, todocname)
    if title:
        node['reftitle'] = title
    node.append(child)
    return node


def set_source_info(directive: Directive, node: Node) -> None:
    node.source, node.line = \
        directive.state_machine.get_source_and_line(directive.lineno)


def set_role_source_info(inliner: Inliner, lineno: int, node: Node) -> None:
    node.source, node.line = inliner.reporter.get_source_and_line(lineno)  # type: ignore


def copy_source_info(src: Element, dst: Element) -> None:
    dst.source = get_node_source(src)
    dst.line = get_node_line(src)


NON_SMARTQUOTABLE_PARENT_NODES = (
    nodes.FixedTextElement,
    nodes.literal,
    nodes.math,
    nodes.image,
    nodes.raw,
    nodes.problematic,
    addnodes.not_smartquotable,
)


def is_smartquotable(node: Node) -> bool:
    """Check the node is smart-quotable or not."""
    if isinstance(node.parent, NON_SMARTQUOTABLE_PARENT_NODES):
        return False
    elif node.parent.get('support_smartquotes', None) is False:
        return False
    elif getattr(node, 'support_smartquotes', None) is False:
        return False
    else:
        return True


def process_only_nodes(document: Node, tags: "Tags") -> None:
    """Filter ``only`` nodes which does not match *tags*."""
    for node in document.traverse(addnodes.only):
        try:
            ret = tags.eval_condition(node['expr'])
        except Exception as err:
            logger.warning(__('exception while evaluating only directive expression: %s'), err,
                           location=node)
            node.replace_self(node.children or nodes.comment())
        else:
            if ret:
                node.replace_self(node.children or nodes.comment())
            else:
                # A comment on the comment() nodes being inserted: replacing by [] would
                # result in a "Losing ids" exception if there is a target node before
                # the only node, so we make sure docutils can transfer the id to
                # something, even if it's just a comment and will lose the id anyway...
                node.replace_self(nodes.comment())


# monkey-patch Element.copy to copy the rawsource and line
# for docutils-0.14 or older versions.

def _new_copy(self: Element) -> Element:
    newnode = self.__class__(self.rawsource, **self.attributes)
    if isinstance(self, nodes.Element):
        newnode.source = self.source
        newnode.line = self.line
    return newnode


nodes.Element.copy = _new_copy  # type: ignore
