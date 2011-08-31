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
from lxml import etree

SERVERS_FILE = "BUNDLE_SERVERS.xml"

known_servers = {}

class ServerTemplate(object):
    """A server that can be re-used.

    Typically loaded from an auxiliary, not versioned file."""

    templates = {}

    def __init__(self, attrib):
        try:
            self.id = attrib.pop('id')
        except KeyError:
            logger.error("Missing id in template %s", attrib.get('name'))
            raise

        self.attrib = attrib # simpler to keep them for later use

if os.path.isfile(SERVERS_FILE):
    tree = etree.parse(SERVERS_FILE)
    root = tree.getroot()
    for child in root:
        if child.tag != 'server':
            continue
        s = ServerTemplate(child.attrib)
        known_servers[s.id] = s
