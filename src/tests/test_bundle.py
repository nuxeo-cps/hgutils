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
import unittest
from tests import TEST_DATA_PATH
from tests import rmr, hg_init, un_hg

from bundle import Server, Bundle
from bundle import MANIFEST_FILE

class ServerTestCase(unittest.TestCase):

    def test_dummy(self):
        server = Server(dict(name="truc", url='http'))
        self.assertEquals(server.name, 'truc')

class BundleTestCase(unittest.TestCase):

    def sampleRepos(self):
        basepath = os.path.join(TEST_DATA_PATH, 'sample_repos')
        return tuple(
            r for r in (os.path.join(basepath, d)
                        for d in os.listdir(basepath))
            if os.path.isdir(r))

    def setUp(self):
        self.tmpdir = os.path.join(TEST_DATA_PATH, 'tmp_bundle')
        os.mkdir(self.tmpdir)

        for r in self.sampleRepos():
            hg_init(r)

    def prepareBundle(self, bdl_rpath, manifest_rpath):
        bundle_path = os.path.join(self.tmpdir, bdl_rpath)
        os.mkdir(bundle_path)

        f = open(os.path.join(TEST_DATA_PATH, manifest_rpath))
        s = f.read()
        f.close()

        s = s.replace('$TEST_DATA_PATH', TEST_DATA_PATH)

        f = open(os.path.join(bundle_path, MANIFEST_FILE), 'w')
        f.write(s)
        f.close()

        return Bundle(bundle_path)

    def test_make_clones(self):
        bundle = self.prepareBundle('bundle', 'bundle1.xml')
        bundle.make_clones()

    def tearDown(self):
        rmr(self.tmpdir)
        # stop versionning the test repos separately : give it back
        # to hgutils
        for r in self.sampleRepos():
            un_hg(r)


def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(ServerTestCase))
    suite.addTest(unittest.makeSuite(BundleTestCase))
    return suite
