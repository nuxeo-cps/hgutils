#!/usr/bin/env python

import os
import sys

from optparse import OptionParser
from lxml import etree

import logging
logger = logging.getLogger('hgbundler')
logger.setLevel(logging.DEBUG)

from mercurial import hg
from mercurial import commands as hg_commands
import mercurial.ui
HG_UI = mercurial.ui.ui()

MANIFEST_FILE = "BUNDLE_MANIFEST.xml"

def make_clone(url, base_dir, target):
    cmd = 'cd %s && hg clone %s %s' % (base_dir, url, target)
    logger.debug(cmd)
    os.system(cmd)

def hg_up(path, name):
    cmd = "cd %s && hg update %s" % (path, name)
    logger.debug(cmd)
    os.system(cmd)


class Server(object):

    def __init__(self, url):
        if url.endswith('/'):
            url = url[:-1]
        self.url = url

    def getRepoUrl(self, path):
        if not path.startswith('/'):
            path = '/' + path
        return self.url + path


class RepoDescriptor(object):

    def __init__(self, remote_url, bundle_dir, target, name):
        # name is an additional name to qualify used by subclasses
        self.remote_url = remote_url
        self.target = target
        self.bundle_dir = bundle_dir
        self.local_path = os.path.join(bundle_dir, target)
        self.name = name
        self.repo = None # Mercurial repo

    def make_clone(self):
        logging.info("Cloning %s to %s", self.remote_url, self.target)
        make_clone(self.remote_url, self.bundle_dir, self.target)

    def getRepo(self):
        """Return mercurial repo object.
        Raise an error if repo can't be found"""
        if self.repo is None:
            self.repo = hg.repository(HG_UI, self.local_path)
        return self.repo

class Tag(RepoDescriptor):

    def update(self):
        hg_commands.update(HG_UI, self.getRepo(), self.name)

class Branch(RepoDescriptor):

    def update(self):
        """updates to named branch if any.
        Caution: if user has created a new branch, this doesn't get back
        to the default branch."""

        name = self.name
        if name is None:
            logger.info("Updating %s", self.target)
            hg_commands.update(HG_UI, self.getRepo())
        else:
            logger.info("Updating %s to branch %s", self.target, name)
            hg_commands.update(HG_UI, self.getRepo(), name)


class Bundle(object):

    element2class = {'tag': Tag, 'branch': Branch}

    def __init__(self, bundle_dir):
        self.bundle_dir = bundle_dir
        if MANIFEST_FILE not in os.listdir(bundle_dir):
            raise RuntimeError(
                "Not a bundle directory : %s (no MANIFEST_FILE)" % bundle_dir)
        self.tree = etree.parse(os.path.join(bundle_dir, MANIFEST_FILE))
        self.root = self.tree.getroot()
        self.descriptors = None

    def getRepoDescriptors(self):
        if self.descriptors is not None:
            return self.descriptors

        res = []
        targets = set()
        for s in self.root:
            if s.tag != 'server':
                continue
            server = Server(s.attrib['url'])
            for r in s:
                klass = self.element2class.get(r.tag)
                if klass is None:
                    continue

                attrib = r.attrib
                path = attrib.get('path')
                if path is None:
                    raise ValueError(
                        "element with no path: %s" % etree.tosring(r))

                target = attrib.get('target')
                if target is None:
                    target = path.rsplit('/', 1)[-1]

                if target in targets:
                        raise ValueError("Target name conflict: %s" % target)
                targets.add(target)

                name = attrib.get('name')

                res.append(klass(server.getRepoUrl(path),
                                 self.bundle_dir,
                                 target, name))

        self.descriptors = res
        return res

    def make_clones(self):
        for desc in self.getRepoDescriptors():
            desc.make_clone()
            desc.update()

    def update_clones(self):
        for desc in self.getRepoDescriptors():
            desc.update()

if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option('-d', '--bundle-directory', dest='bundle_dir',
                      default=os.getcwd(),
                      help="Specify the bundle directory (defaults to current"
                      " working directory)")

    options, arguments = parser.parse_args()
    if not arguments:
        parser.error("Need a command")
        sys.exit(1)

    bundle = Bundle(options.bundle_dir)

    command = arguments[0]
    if command == 'make-clones':
        bundle.make_clones()
    elif command == 'update-clones':
        bundle.update_clones()
