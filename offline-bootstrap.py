##############################################################################
#
# Copyright (c) 2012 CPS-Community (http://cps-cms.org) 
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""Offline variant of buildout's bootstrap

Assumes that  eggs/ already has zc.buildout and its dependencies

Simply run this script in a directory containing a buildout.cfg.
The script accepts buildout command-line options, so you can
use the -c option to specify an alternate configuration file.
"""

import os, shutil, sys, tempfile, urllib2

eggs = os.path.join(os.path.split(__file__)[0], 'eggs')
import pkg_resources
ws = pkg_resources.working_set

ws.add_entry(eggs)
ws.require('zc.buildout')
import zc.buildout.buildout

args = sys.argv[1:] + ['bootstrap']
zc.buildout.buildout.main(args)
