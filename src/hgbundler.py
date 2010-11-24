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
from optparse import OptionParser

import logging
logger = logging.getLogger('hgbundler')
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s'))
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

from bundle import Bundle
from common import _findrepo

def release_multiple_bundles(args, base_path='', options=None, opt_parser=None):
    """Release several bundles at once.

    This is useful to provide several distributions from the same tag.
    opt_parser can be given for error feedback integration.
    """
    if len(args) < 2:
        if opt_parser is not None:
            opt_parser.error("Please provide at least one bundle directory "
                             "and the release name")
        else:
            raise ValueError("Not enough arguments.")
        return 1

    release_name = args[-1]
    bundle_dirs = args[:-1]
    if base_path:
        bundle_dirs = [os.path.join(base_path, d) for d in bundle_dirs]

    # Checking the paths
    repo_path = None
    for d in bundle_dirs:
        path = _findrepo(d)
        if path is None:
            raise ValueError("Directory '%s' not in a repository." % d)
        if repo_path is not None and repo_path != path:
            raise ValueError(("Directory '%s' not in same repository as the "
                             "previous ones in the list") % d)
        repo_path = path

    bundles = [Bundle(d) for d in bundle_dirs]

    # First bundle does the common repo preparations
    # all hg operations on this repo can be delegated to the last acting bundle
    # (for commit or abort)
    branch_name = bundles[0].release_repo_check(release_name, options=options)
    for i, bundle in enumerate(bundles):
        status, released = bundle.release(release_name, options=options,
                                          check=False, commit=False,
                                          list_released=True)

        # Transmitting released repos changesets to remaining bundles
        # Note: currently, in Mercurial two identical changesets get the
        # same identifier (as shown by test_hgbundler),
        # so that this operation is actually not necessary

        for rem in bundles[i+1:]:
            rem.pull_clones(from_bundle=bundle, targets=released, update=True)

        if status:
            bundle.release_abort() # any bundle can do it
            return status

    bundle.release_commit(branch_name, release_name, options=options)

def main():
    global_commands = {'release-multiple': release_multiple_bundles}
    bundle_commands = {'make-clones': 'make_clones',
                       'update-clones': 'update_clones',
                       'clones-refresh-url': 'clones_refresh_url',
                       'clones-out': 'clones_out',
                       'release-clone': 'release_clone',
                       'release-bundle': 'release',
                       'archive': 'archive',
                       'bundle-changelog': 'changelog'}
    usage = "usage: %prog [options] " + '|'.join(
        global_commands.keys() + bundle_commands.keys())
    usage += """ [command args] \n

    command arguments:

    command             arguments                     comments
    -----------------------------------------------------------
    release-clone       <clone relative path>         mandatory
    release-bundle      <release name>                mandatory
    archive             <bundle tag> <output dir>     mandatory
    release-multiple    <bdl dir> [<bdl dir>]  <name> at least one bundle dir
    bundle-changelog    <bundle tag1> <bundle tag2>   both mandatory
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
    parser.add_option('-o', '--output', dest='output', metavar='FILE',
                      help="Output file for analysis command (e.g bundle-changelog)")

    options, arguments = parser.parse_args()
    if not arguments:
        parser.error("Need a command")
        sys.exit(1)

    if options.verbose:
        logger.setLevel(logging.DEBUG)

    command = arguments[0]
    meth = global_commands.get(command)
    if meth is not None:
        status = meth(arguments[1:], options=options)
        sys.exit(status)

    bundle = Bundle(options.bundle_dir)
    meth = bundle_commands.get(command)
    if meth is None:
        parser.error("Unknown command: " + command)

    status = getattr(bundle, meth)(*arguments[1:], **dict(options=options))
    sys.exit(status)

if __name__ == '__main__':
    main()
