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
import sys
import popen2
import re

from datetime import datetime
from optparse import OptionParser

import logging
logger = logging.getLogger('hgbundler')
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s'))
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

from bundle import Bundle

def main():
    commands = {'make-clones': 'make_clones',
                'update-clones': 'update_clones',
                'clones-refresh-url': 'clones_refresh_url',
                'release-clone': 'release_clone',
                'release-bundle': 'release',
                'archive': 'archive'}
    usage = "usage: %prog [options] " + '|'.join(commands.keys())
    usage += """ [command args] \n

    command arguments:

    command             arguments                comments
    -----------------------------------------------------
    release-clone       <clone relative path>     mandatory
    release-bundle      <release name>            mandatory
    archive             <bundle tag> <output dir> mandatory
"""
    parser = OptionParser(usage=usage)

    parser.add_option('-d', '--bundle-directory', dest='bundle_dir',
                      default=os.getcwd(),
                      help="Specify the bundle directory (defaults to current"
                      " working directory)")
    parser.add_option('--allow-multiple-heads',
                      dest='multiple_heads',
                      action="store_true",
                      help="While releasing, allow multiple heads situations."
                      "The tip of the branch will then be used")
    parser.add_option('--release-again',
                      dest='release_again',
                      action="store_true",
                      help="Allow releasing again clones")
    parser.add_option('--increment-major',
                      action='store_true',
                      help="Increment major version numbers in case of "
                      "changes that aren't bugfixes only")
    parser.add_option('-v', '--verbose', action='store_true', dest='verbose',
                      help="Sets the logging level to DEBUG")

    options, arguments = parser.parse_args()
    if not arguments:
        parser.error("Need a command")
        sys.exit(1)

    if options.verbose:
        logger.setLevel(logging.DEBUG)

    bundle = Bundle(options.bundle_dir)

    command = arguments[0]
    meth = commands.get(command)
    if meth is None:
        parser.error("Unknown command: " + command)

    status = getattr(bundle, meth)(*arguments[1:], **dict(options=options))
    sys.exit(status)

if __name__ == '__main__':
    main()
