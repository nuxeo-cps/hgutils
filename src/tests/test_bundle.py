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

console_handler = logging.StreamHandler()
console_handler.setFormatter(
logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s'))
logger = logging.getLogger('hgbundler')
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

class ServerTestCase(unittest.TestCase):

    def test_dummy(self):
        server = Server(dict(name="truc", url='http'))
        self.assertEquals(server.name, 'truc')

class BundleTestCase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = os.path.join(TEST_DATA_PATH, 'tmp_bundle')
        os.mkdir(self.tmpdir)

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

    def test_release(self):
        bundle = self.prepareBundle('bundle', 'bundle1.xml')
        hg_init(bundle.bundle_dir)

        bundle.make_clones()

        # This one's supposed to e tagged at 1.0.0 already
        already = hg.repository(HG_UI, os.path.join(bundle.bundle_dir,
                                                    'AlreadyReleased'))
        hg_commands.tag(already.ui, already, '1.0.0', message='Previous tag')

        bundle.release('TEST', options=tests.Options())

    def test_repo_release_very_first(self):
        bundle = self.prepareBundle('bundle', 'bundle1.xml')
        hg_init(bundle.bundle_dir)

        bundle.make_clones()
        for desc in bundle.getRepoDescriptors():
            if desc.target == 'NeverReleased':
                break

        desc.release()
        l = os.listdir(desc.local_path)
        self.assertTrue('CHANGES' in l)
        self.assertTrue('VERSION' in l)
        self.assertTrue('HISTORY' in l)

    def test_out_with_sub(self):
        bundle = self.prepareBundle('bundle', 'with_sub.xml')
        bundle.make_clones()
        l = set(f for f in os.listdir(bundle.bundle_dir)
                if not f.startswith('.'))

        self.assertEquals(l, set(['AlreadyReleased', 'NeverReleased',
                                  'ToRelease', 'SubBundle',
                                  'BUNDLE_MANIFEST.xml']))
        bundle.clones_out()

    def tearDown(self):
        rmr(self.tmpdir)


def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(ServerTestCase))
    suite.addTest(unittest.makeSuite(BundleTestCase))
    return suite
