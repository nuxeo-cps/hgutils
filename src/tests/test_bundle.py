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

from subprocess import call
from StringIO import StringIO
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

class Options(object):
    """Simulate optparse options object."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

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

    def test_clones_list(self):
        bundle = self.prepareBundle('bundle', 'bundle1.xml')
        out = StringIO()
        listed = bundle.clones_list(outfile=out)
        self.assertEquals(out.getvalue().split(),
                          ['NeverReleased', 'AlreadyReleased', 'ToRelease'])

        # in case of true optparse Options, we'd have a value, and it'd be None
        out = StringIO()
        options = Options(attributes_filter=None)
        listed = bundle.clones_list(outfile=out, options=options)
        self.assertEquals(out.getvalue().split(),
                          ['NeverReleased', 'AlreadyReleased', 'ToRelease'])

        out = StringIO()
        options = Options(tags_only=True)
        listed = bundle.clones_list(options=options, outfile=out)
        self.assertEquals(out.getvalue().split(), [])

        out = StringIO()
        options = Options(attributes_filter=dict(testing=("continuous",)))
        listed = bundle.clones_list(options=options, outfile=out)
        self.assertEquals(out.getvalue().split(), ['ToRelease'])

        out = StringIO()
        options = Options(attributes_filter=dict(testing=("continuous", "yes")))
        listed = bundle.clones_list(options=options, outfile=out)
        self.assertEquals(out.getvalue().split(),
                          ['AlreadyReleased', 'ToRelease'])

    def test_release(self):
        bundle = self.prepareBundle('bundle', 'bundle1.xml')
        hg_init(bundle.bundle_dir)

        bundle.make_clones()

        # This one's supposed to e tagged at 1.0.0 already
        already = hg.repository(HG_UI, os.path.join(bundle.bundle_dir,
                                                    'AlreadyReleased'))
        hg_commands.tag(already.ui, already, '1.0.0', message='Previous tag')

        bundle.release('TEST', options=tests.Options())

    def test_release_branch_at_tag(self):
        bundle = self.prepareBundle('bundle', 'bundle3.xml')
        hg_init(bundle.bundle_dir)

        bundle.make_clones()
        repo_path = os.path.join(bundle.bundle_dir, 'ToRelease')
        call(['hg', '--cwd', repo_path, 'up', '0.0.1'])
        call(['hg', '--cwd', repo_path, 'branch', 'new-branch'])
        f = open(os.path.join(repo_path, 'file_in_branch'), 'w')
        f.write("A file in new branch" + os.linesep)
        f.close()
        call(['hg', '--cwd', repo_path, 'add', 'file_in_branch'])
        call(['hg', '--cwd', repo_path, 'commit', '-m', 'branch start'])
        call(['hg', '--cwd', repo_path, 'up', '-C', 'default'])
        bundle.release('TEST', options=tests.Options())
        vf = open(os.path.join(repo_path, 'VERSION'))
        lines = vf.readlines()
        self.assertEquals(lines[3].split('=')[1].strip(), '0.0.2')


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

    def test_release_abort(self):
        bundle = self.prepareBundle('bundle', 'bundle1.xml')
        path = bundle.bundle_dir
        hg_init(path)
        # make any change to the bundle
        mf_path = os.path.join(path, MANIFEST_FILE)
        original = open(mf_path, 'r').read()
        open(mf_path, 'w').write("This is a big modification, isn't it ?")

        bundle.release_abort()
        # it got reverted
        self.assertEquals(open(mf_path, 'r').read(), original)

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
