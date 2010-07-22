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
import logging
import unittest
import tests
from tests import TEST_DATA_PATH
from tests import rmr, hg_init, un_hg

from mercurial import hg
from mercurial import commands as hg_commands
from bundle import Server, Bundle
from bundle import MANIFEST_FILE
from repodescriptor import HG_UI
from hgbundler import release_multiple_bundles

class HgBundlerTestCase(unittest.TestCase):
    """For entry point commands"""

    def setUp(self):
        self.tmpdir = os.path.join(TEST_DATA_PATH, 'tmp_hgbundler')
        os.mkdir(self.tmpdir)

    def prepareMultiBundle(self, bdl_rpath, manifest_rpaths):
        base_path = os.path.join(self.tmpdir, bdl_rpath)
        os.mkdir(base_path)

        for rpath in manifest_rpaths:
            f = open(os.path.join(TEST_DATA_PATH, rpath))
            s = f.read()
            f.close()
            bundle_path = os.path.join(base_path,
                                       os.path.split(rpath)[-1].split('.')[0])
            os.mkdir(bundle_path)
            f = open(os.path.join(bundle_path, MANIFEST_FILE), 'w')
            f.write(s)
            f.close()

        hg_init(base_path)
        return base_path

    def test_multi_release(self):
        base_path = self.prepareMultiBundle('bundles',
                                            ('bundle1.xml', 'bundle2.xml'))
        for b in ('bundle1', 'bundle2'):
            bdl = Bundle(os.path.join(base_path, b))
            bdl.make_clones()

        release_multiple_bundles(('bundle1', 'bundle2','TEST-MULTI'),
                                 base_path=base_path, options=tests.Options())

    def tearDown(self):
        rmr(self.tmpdir)


def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(HgBundlerTestCase))
    return suite
