#!/usr/bin/env python

import os
import sys

from optparse import OptionParser
import logging
logger = logging.getLogger('hgbundler')
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s'))
logger.addHandler(console_handler)

try:
    from lxml import etree
except ImportError:
    try:
        from elementtree import ElementTree as etree
    except ImportError:
        logger.fatal("Sorry, need either elementtree or lxml")
        sys.exit(1)

from mercurial import hg
from mercurial.node import short as hg_hex
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

    @classmethod
    def _normTrailingSlash(self, url):
        if url is None:
            return None
        return url.endswith('/') and url[:-1] or url

    def __init__(self, attrib):
        self.name = attrib.get('name')
        url = attrib.get('url')
        if url is None:
            url = attrib.get('server-url') # for include-bundles
        self.url = url = self._normTrailingSlash(url)

        if url is None:
            raise ValueError('Missing url in serveur with name=%s' % self.name)

        self.push_url = self._normTrailingSlash(attrib.get('push-url'))

    def getRepoUrl(self, path, push=False):
        if not path.startswith('/'):
            path = '/' + path
        if push:
            if self.push_url is None:
                return None
            return self.push_url + path
        return self.url + path


class RepoDescriptor(object):

    def __init__(self, remote_url, bundle_dir, target, name, attrs,
                 remote_url_push=None):
        # name is an additional name to qualify used by subclasses
        self.remote_url = remote_url
        self.remote_url_push = remote_url_push
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
        if self.remote_url_push:
            self.updateUrls()

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

    def update(self):
        """Update to named branch/tag if any, or to the default one."""

        name, node = self.head()
        logger.info("Updating %s to node %s (%s)", self.local_path_rel,
                    hg_hex(node), name)
        hg.update(self.getRepo(), node)

    def writeHgrcPaths(self):
        """Write the paths registered in config object to hgrc."""
        hgrc = os.path.join(self.local_path, '.hg', 'hgrc')
        fd = open(hgrc, 'r')
        rlines = fd.readlines()
        fd.close()

        wlines = []
        in_paths = False
        for line in rlines:
            l = line.strip()
            if in_paths:
                if l.startswith('default'): # skip paths we are replacing
                    continue
                wlines.append(line)
                if l.startswith('['): # end of paths section
                    in_paths = False
            else:
                wlines.append(line)
                if l.startswith('[paths]'):
                    # Entering the paths section: dumping right away our default
                    in_paths = True
                    for p in ('default', 'default-push'):
                        v = self.getRepo().ui.config('paths', p)
                        if v is not None:
                            wlines.append('%s = %s\n' % (p, v))

        fd = open(hgrc, 'w')
        fd.writelines(wlines)
        fd.close()

    def updateUrls(self):
        ui = self.getRepo().ui
        current = ui.config('paths', 'default')
        current_push = ui.config('paths', 'default-push')
        if current == self.remote_url and current_push == self.remote_url_push:
            return

        ui.setconfig('paths', 'default', self.remote_url)
        ui.setconfig('paths', 'default-push', self.remote_url_push)
        self.writeHgrcPaths()

class Tag(RepoDescriptor):

    def head(self):
        """Return name and node for the tag."""
        tags = self.getRepo().tags()
        name = self.name
        try:
            return name, tags[name]
        except KeyError:
            raise ValueError("Tag '%s' not found in repo %s", name,
                             self.local_path_rel)

class Branch(RepoDescriptor):

    def head(self):
        """Return name and node for branch head.

        Finds the default branch if none is specified.
        """
        branches = self.getRepo().branchtags()
        logger.debug("Found branches: %s", branches.keys())
        name = self.name
        if name is None:
            # TODO filter active branches only at this point
            if len(branches) > 1:
                logger.debug(
                    "No specified branch, found several, trying 'default'")
                name = 'default'
            else:
                name = branches.keys()[0]
                logger.debug("Using the unique branch '%s" % name)

        try:
            node = branches[name]
        except KeyError:
            raise ValueError((
                "Wrong specified or guessed branch '%s' for %s."
                "Please specify an existing one") % (name, self.local_path_rel))
        return name, node


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
        etag = r.tag
        if callable(etag):
            # This is probably just an XML comment (lxml only)
            return None
        klass = self.element2class.get(etag)
        if klass is None:
            import pdb; pdb.set_trace()
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
                     target, name, attrib,
                     remote_url_push=server.getRepoUrl(path, push=True))

    def includeBundles(self, elt, position):
        server = Server(elt.attrib)
        for r in elt:
            repo = self.makeRepo(server, r)
            if repo is None: # happens, e.g, with XML comments
                continue
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
            server = Server(s.attrib)
            for r in s:
                repo = self.makeRepo(server, r)
                if repo is not None:
                    res.append(repo)

        self.descriptors = res
        return res

    def make_clones(self):
        for desc in self.getRepoDescriptors():
            if desc.make_clone():
                desc.update()

    def update_clones(self):
        for desc in self.getRepoDescriptors():
            desc.update()

    def clones_refresh_url(self):
        for desc in self.getRepoDescriptors():
            desc.updateUrls()

if __name__ == '__main__':
    commands = {'make-clones': 'make_clones',
                'update-clones': 'update_clones',
                'clones-refresh-url': 'clones_refresh_url'}
    usage = "usage: %prog [options] " + '|'.join(commands.keys())

    parser = OptionParser(usage=usage)

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
    meth = commands.get(command)
    if meth is None:
        parser.error("Unknown command: " + command)

    getattr(bundle, meth)()
