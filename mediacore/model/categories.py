# This file is a part of MediaCore, Copyright 2009 Simple Station Inc.
#
# MediaCore is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MediaCore is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from datetime import datetime
from sqlalchemy import Table, ForeignKey, Column, sql
from sqlalchemy.types import Unicode, UnicodeText, Integer, DateTime, Boolean, Float
from sqlalchemy.orm import mapper, relation, backref, synonym, interfaces, validates, Query
from sqlalchemy.orm.attributes import set_committed_value

from mediacore.lib.compat import defaultdict
from mediacore.model import SLUG_LENGTH, slugify
from mediacore.model.meta import DBSession, metadata
from mediacore.plugin import events


categories = Table('categories', metadata,
    Column('id', Integer, autoincrement=True, primary_key=True),
    Column('name', Unicode(50), nullable=False, index=True),
    Column('slug', Unicode(SLUG_LENGTH), nullable=False, unique=True),
    Column('parent_id', Integer, ForeignKey('categories.id', onupdate='CASCADE', ondelete='CASCADE')),
    mysql_engine='InnoDB',
    mysql_charset='utf8'
)

class CategoryNestingException(Exception):
    pass

def traverse(cats, depth=0, ancestors=None):
    """Iterate through a depth-first traversal of the given categories.

    Yields a 2-tuple of the :class:`Category` instance and it's
    relative depth in the tree.

    :param cats: A list of :class:`Category` instances.
    :param depth: Distance from the root
    :param ancestors: Visited ancestors, tracked to prevent infinite
        loops on circular nesting.
    :type ancestors: dict

    """
    if ancestors is None:
        ancestors = {}
    for cat in cats:
        if cat.id in ancestors:
            raise CategoryNestingException, 'Category tree contains ' \
                'invalid nesting: %s is a parent to one of its ' \
                'ancestors %s.' % (cat, ancestors)
        child_anc = ancestors.copy()
        child_anc[cat.id] = True

        yield cat, depth
        for subcat, subdepth in traverse(cat.children, depth + 1, child_anc):
            yield subcat, subdepth

def populated_tree(cats):
    """Return the root categories with children populated to any depth.

    Adjacency lists are notoriously inefficient for fetching deeply
    nested trees, and since our dataset will always be reasonably
    small, this method should greatly improve efficiency. Only one
    query is necessary to fetch a tree of any depth. This isn't
    always the solution, but for some situations, it is worthwhile.

    For example, printing the entire tree can be done with one query::

        query = Category.query.options(undefer('media_count'))
        for cat, depth in query.populated_tree().traverse():
            print "    " * depth, cat.name, '(%d)' % cat.media_count

    Without this method, especially with the media_count undeferred,
    this would require a lot of extra queries for nested categories.

    NOTE: If the tree contains circular nesting, the circular portion
          of the tree will be silently omitted from the results.

    """
    children = defaultdict(CategoryList)
    for cat in cats:
        children[cat.parent_id].append(cat)
    for cat in cats:
        set_committed_value(cat, 'children', children[cat.id])
    return children[None]

class CategoryQuery(Query):
    traverse = traverse
    """Iterate over all categories and nested children in depth-first order."""

    def all(self):
       return CategoryList(self)

    def roots(self):
        """Filter for just root, parentless categories."""
        return self.filter(Category.parent_id == None)

    def populated_tree(self):
        return populated_tree(self.all())


class CategoryList(list):
    traverse = traverse
    """Iterate over all categories and nested children in depth-first order."""

    def __unicode__(self):
        return ', '.join(cat.name for cat in self.itervalues())

    def populated_tree(self):
        return populated_tree(self)

class Category(object):
    """
    Category Mapped Class
    """
    query = DBSession.query_property(CategoryQuery)

    def __init__(self, name=None, slug=None):
        self.name = name or None
        self.slug = slug or name or None

    def __repr__(self):
        return '<Category: %r>' % self.name

    def __unicode__(self):
        return self.name

    @validates('slug')
    def validate_slug(self, key, slug):
        return slugify(slug)

    def traverse(self):
        """Iterate over all nested categories in depth-first order."""
        return traverse(self.children)

    def descendants(self):
        """Return a list of descendants in depth-first order."""
        return [desc for desc, depth in self.traverse()]

    def ancestors(self):
        """Return a list of ancestors, starting with the root node.

        This method is optimized for when all categories have already
        been fetched in the current DBSession::

            >>> Category.query.all()    # run one query
            >>> row = Category.query.get(50)   # doesn't use a query
            >>> row.parent    # the DBSession recognized the primary key
            <Category: parent>
            >>> print row.ancestors()
            [...,
             <Category: great-grand-parent>,
             <Category: grand-parent>,
             <Category: parent>]

        """
        ancestors = CategoryList()
        anc = self.parent
        while anc:
            if anc is self:
                raise CategoryNestingException, 'Category %s is defined as a ' \
                    'parent of one of its ancestors.' % anc
            ancestors.insert(0, anc)
            anc = anc.parent
        return ancestors

    def depth(self):
        """Return this category's distance from the root of the tree."""
        return len(self.ancestors())


mapper(Category, categories, order_by=categories.c.name, extension=events.MapperObserver(events.Category), properties={
    'children': relation(Category,
        backref=backref('parent', remote_side=[categories.c.id]),
        order_by=categories.c.name.asc(),
        collection_class=CategoryList,
        join_depth=2),
})
