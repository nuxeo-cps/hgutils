#!/usr/bin/env python
# (C) Copyright 2010 Georges Racinet <georges@racinet.fr>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA
# 02111-1307, USA.
#
# $Id$

import os
import mercurial.util

try:
    HG_VERSION_STR = mercurial.util.version()
except AttributeError:
    from mercurial.version import get_version
    HG_VERSION_STR = get_version()

try:
    from lxml import etree
except ImportError:
    try:
        from elementtree import ElementTree as etree
    except ImportError:
        logger.fatal("Sorry, need either elementtree or lxml")
        sys.exit(1)

split = HG_VERSION_STR.split('+', 1)
HG_VERSION_COMPLEMENT = len(split) == 2 and split[1] or None
HG_VERSION = tuple(int(x) for x in split[0].split('.'))

class RepoNotFoundError(Exception):
    pass


class NodeNotFoundError(Exception):
    pass


class BranchNotFoundError(KeyError):
    pass


def _findrepo(p):
    """Find with of path p is an hg repo.

    Copy-pasted from mercurial.dispatch (GPLv2), since the underscore clearly
    marks this as purely internal and subject to change"""

    while not os.path.isdir(os.path.join(p, ".hg")):
        oldp, p = p, os.path.dirname(p)
        if p == oldp:
            return None
    return p

def _currentNodeRev(repo):
    """Return current node and rev for repo."""
    ctx = repo.changectx(None)
    node = ctx.node()
    if node is None:
        parents = ctx.parents()
        if len(parents) != 1:
           raise NodeNotFoundError(SEVERAL_PARENTS)
        ctx = parents[0]
        node = ctx.node()
    return node, ctx.rev()


