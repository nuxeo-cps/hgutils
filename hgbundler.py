#!/usr/bin/env python

import os
import sys

from optparse import OptionParser
from lxml import etree

import logging
logger = logging.getLogger('hgbundler')
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s'))
logger.addHandler(console_handler)

from mercurial import hg
from mercurial import commands as hg_commands
import mercurial.ui
HG_UI = mercurial.ui.ui()

MANIFEST_FILE = "BUNDLE_MANIFEST.xml"
ASIDE_REPOS = '.hgbundler'
INCLUDES = '.hgbundler_incl'

def make_clone(url, target_path):
    base_dir, target = os.path.split(target_path)
    if not os.path.isdir(base_dir):
        os.mkdir(base_dir)
    logger.debug("Cloning %s to %s", url, os.path.join(base_dir, target))
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

    def __init__(self, remote_url, bundle_dir, target, name, attrs):
        # name is an additional name to qualify used by subclasses
        self.remote_url = remote_url
        self.target = target
        self.bundle_dir = bundle_dir
        self.name = name

        subpath = attrs.get('subpath')
        if subpath is None:
            self.is_sub = False
            self.local_path_rel = target
        else:
            self.is_sub = True
            self.subpath = subpath
            self.clone_target = self.remote_url.rsplit('/')[-1]
            self.local_path_rel = os.path.join(ASIDE_REPOS, self.clone_target)

        self.local_path = os.path.join(self.bundle_dir, self.local_path_rel)

        self.repo = None # Mercurial repo (could not exist yet on fs)

    def getAsideRepoPath(self):
        """Find the path to repo if it's aside (subpath situation)"""
        return os.path.join(ASIDE_REPOS, self.clone_target)

    def make_clone(self):
        """Make the clone if needed and return True if done."""

        if os.path.exists(self.local_path):
            logger.debug("Ignoring the existing clone for %s", self.target)
            return False

        target_path = os.path.join(self.bundle_dir, self.target)
        if os.path.exists(target_path):
            logger.debug("Ignoring the existing target path %s", self.target)
            return False

        logger.info("Creating clone %s", self.local_path_rel)
        make_clone(self.remote_url, self.local_path)

        if self.is_sub:
            logger.info("Extracting to %s", self.target)
            deepness = len(self.target.split(os.path.sep)) - 1

            if deepness:
                local_to_base = os.path.join(*((os.path.pardir,)*deepness))
            else:
                local_to_base = ''

            src = os.path.join(local_to_base, ASIDE_REPOS,
                                    self.clone_target, self.subpath)
            logger.debug("Making symlink %s -> %s", target_path, src)
            os.symlink(src, target_path)

        return True

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
            logger.info("Updating %s to branch %s", self.local_path_rel, name)

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
        self.known_targets = set()

    def makeRepo(self, server, r):
        klass = self.element2class.get(r.tag)
        if klass is None:
            raise ValueError("Unknown repo mode: %s", r.tag)

        attrib = r.attrib
        path = attrib.get('path')
        if path is None:
            raise ValueError(
                "element with no path: %s" % etree.tosring(r))

        target = attrib.get('target')
        if target is None:
            target = path.rsplit('/', 1)[-1]

        if target in self.known_targets:
                raise ValueError("Target name conflict: %s" % target)
        self.known_targets.add(target)

        name = attrib.get('name')

        return klass(server.getRepoUrl(path), self.bundle_dir,
                     target, name, attrib)

    def includeBundles(self, elt, position):
        server = Server(elt.attrib['server-url'])
        for r in elt:
            repo = self.makeRepo(server, r)
            repo.make_clone()
            repo.update()

            manifest = os.path.join(self.bundle_dir, repo.target,
                                    MANIFEST_FILE)
            bdl = etree.parse(manifest)
            for j, subelt in enumerate(bdl.getroot()):
                self.root.insert(position+j, subelt)
        elt.tag = 'already-included-bundles'
        elt.text = ("\n include-bundles element kept for reference after " +
                    "performing the inclusion\n")

    def getRepoDescriptors(self):
        if self.descriptors is not None:
            return self.descriptors

        res = []

        for i, elt in enumerate(self.root):
            if elt.tag == 'include-bundles':
                self.includeBundles(elt, i)

        # need to iterate again, because includes may have changed the children
        for s in self.root:
            if s.tag != 'server':
                continue
            server = Server(s.attrib['url'])
            for r in s:
                res.append(self.makeRepo(server, r))

        self.descriptors = res
        return res

    def make_clones(self):
        for desc in self.getRepoDescriptors():
            if desc.make_clone():
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
    if command == 'make-clones':
        bundle.make_clones()
    elif command == 'update-clones':
        bundle.update_clones()
