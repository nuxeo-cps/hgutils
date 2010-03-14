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

from datetime import datetime
from optparse import OptionParser

import logging
logger = logging.getLogger('hgbundler')
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s'))
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

from bundleman.utils import parseZopeVersionFile, parseNuxeoVersionFile
from bundleman.utils import parseVersionString, parseNuxeoChanges

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
import mercurial.patch
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

LOCAL_CHANGES = 'local changes'
MULTIPLE_HEADS = 'multiple heads'
WRONG_BRANCH = 'wrong branch'
SEVERAL_PARENTS = 'several parents'

def _findrepo(p):
    """Find with of path p is an hg repo.

    Copy-pasted from mercurial.dispatch (GPLv2), since the underscore clearly
    marks this as purely internal and subject to change"""

    while not os.path.isdir(os.path.join(p, ".hg")):
        oldp, p = p, os.path.dirname(p)
        if p == oldp:
            return None
    return p

class RepoReleaseError(Exception):
    pass

class RepoNotFoundError(Exception):
    pass

class NodeNotFoundError(Exception):
    pass

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

        # TODO keep only xml attrs that are not redundant with this object
        # attributes to avoid confusion
        self.xml_attrs = dict(attrs)
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

    def release(self):
        """Perform release of the given clone.

        Release means update to VERSION, CHANGES, etc + mercurial tag etc."""
        raise NotImplementedError

    def getRepo(self):
        """Return mercurial repo object.
        Raise an error if repo can't be found"""
        if self.repo is None:
            self.repo = hg.repository(HG_UI, self.local_path)
        return self.repo

    def update(self):
        """Update to named branch/tag if any, or to the default one."""

        node = self.tip()
        logger.info("Updating %s to node %s (%s)", self.local_path_rel,
                    hg_hex(node), self.getName())
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

    def release(self, **kw):
        logger.warn("No need to perform release on tag %s for target %s",
                    self.name, self.target)

    def getName(self):
        return self.name

    def tip(self):
        """Cheating with terminology: in that case that's just the tag node."""
        tags = self.getRepo().tags()
        name = self.name

        try:
            return tags[name]
        except KeyError:
            raise ValueError("Tag '%s' not found in repo %s", name,
                             self.local_path_rel)

    def xml(self):
        t = etree.Element('tag')
        t.attrib.update(self.xml_attrs)
        t.attrib['name'] = self.name
        return t

class Releaser(object):
    """Encapsulates all non-mercurial gathered from local copy to release it.

    Most of this class is adapted from bundleman.productman
    """

    tpl_changes = """Requires
~~~~~~~~
-
New features
~~~~~~~~~~~~
-
Bug fixes
~~~~~~~~~
-
New internal features
~~~~~~~~~~~~~~~~~~~~~
- %s
"""
    tpl_version = """#BUNDLEMAN PRODUCT CONFIGURATION FILE
# do not edit this file
PKG_NAME=%s
PKG_VERSION=%s
PKG_RELEASE=%s
"""


    def __init__(self, desc, release_again=False, increment_major=False):
        self.release_again = release_again
        self.increment_major = increment_major
        self.desc = desc
        self.repo = desc.getRepo()
        self.branch = desc.getName()
        self.parseChanges()
        self.parseVersion()

    def parseChanges(self):
        """Return the change type."""
        desc = self.desc
        changes_path = os.path.join(desc.local_path, 'CHANGES')
        content = ''
        try:
            content = open(changes_path).read()
        except IOError:
            logger.error('no CHANGES file in branch %s of %s', self.branch,
                         desc.local_path_rel)
            raise RepoReleaseError()
        self.changes = parseNuxeoChanges(content)

    def updateVersionFiles(self):
        fpath = self.desc.local_path

        changes = open(os.path.join(fpath, 'CHANGES')).read()
        history = open(os.path.join(fpath, 'HISTORY')).read()
        f = open(os.path.join(fpath, 'HISTORY'), 'w+')

        prod_name = self.product_name
        prod_version = self.version_new[0]

        header = """===========================================================
Package: %s %s
===========================================================
First release built by: %s at: %s
""" % (prod_name, prod_version, os.getenv('USER'),
       datetime.now().isoformat()[:19])
        f.write(header)
        f.write(changes)
        f.write('\n')
        f.write(history)
        f.close()

        # set up VERSION
        f = open(os.path.join(fpath, 'VERSION'), 'w+')
        args = self.version_new
        args.insert(0, prod_name)
        f.write(self.tpl_version % tuple(args))
        f.close()

    def initChangesFile(self):
        f = open(os.path.join(self.desc.local_path, 'CHANGES'), 'w+')
        f.write(self.tpl_changes % '')
        f.close()

    def parseVersion(self):
        """Extract name, version, release from VERSION or version.txt file.

        Adapted from bundleman.productman
        """
        ret = [None, None, None]
        content = None
        desc = self.desc

        for file_name in ('version.txt', 'VERSION.txt', 'VERSION'):
            version_path = os.path.join(desc.local_path, file_name)
            try:
                content = open(version_path).read()
                break
            except IOError:
                continue

        if not content:
            logger.error("No version file found in the branch '%s', "
                         "of %s. Can't release", self.branch,
                         desc.local_path_rel)
            raise RepoReleaseError

        try:
            if file_name == 'VERSION':
                ret = parseNuxeoVersionFile(content)
            else:
                ret = parseZopeVersionFile(content)
        except ValueError:
            logger.error("Invalid version file %s  contents in branch %s "
                         "of %s", file_name, self.branch,
                         desc.local_path_rel)
        if not ret[0]:
            ret[0] = os.path.split(desc.target)[-1]

        self.product_name = ret[0]
        self.version_str = ret[1]
        self.release_nr = ret[2] and int(ret[2]) or None

    def changedSinceTag(self, node, tag_name=None):
        tag_ctx = self.repo[node]
        children = tag_ctx.children()
        base_error_msg = ("Previous tag %s (from VERSION) " +
                         "not done by hgbundler. ")
        if len(children) != 1:
            logger.error(base_error_msg + "The tagged changeset would "
                         "otherwise have exactly."
                         "one child (commit of tag).", tag_name)
            self.dumpLogSince(tag_ctx.node())
            raise RepoReleaseError
        children = children[0].children()
        if len(children) != 1:
            logger.error(base_error_msg + "The tag commit "
                         "would otherwise have exactly one child (reinit of "
                         "CHANGES). ", tag_name)
            self.dumpLogSince(tag_ctx.node())
            raise RepoReleaseError

        node1 = children[0].node()
        logger.debug("Checking diff since changeset %s", hg_hex(node1))
        it = mercurial.patch.diff(self.repo, node1=node1)
        try:
            it.next()
        except StopIteration:
            return False
        logger.warn("Diff since last release (node %s) not empty.",
                    hg_hex(node))
        self.dumpLogSince(node1)
        return True

    def dumpLogSince(self, node):
        current_ctx = self.repo[None]
        current_node = current_ctx.node()
        if current_node is None:
            current_node = current_ctx.parents()[0].node()

        noderange = hg_hex(node), hg_hex(current_node)
        logger.info("Running hg log since %s on branch '%s'.",
                    hg_hex(node), self.branch)
        hg_commands.log(self.repo.ui, self.repo,
                        rev=[':'.join(noderange)],
                        only_branch=[self.branch],
                        date=None, user=None)

    def newVersion(self):
        """Computes the new version of the product.

        Return True if a new tag must be made,
               False if an existing tag must be used instead of the branch,
               None if no action is to be taken
        """
        changes = self.changes
        name = self.product_name
        release = self.release_nr

        version_str = self.version_str
        version = parseVersionString(version_str)
        if not filter(None, changes) or not version:
            tag_node = self.repo.tags().get(self.version_str)
            if tag_node is None:
                if self.version_str:
                    # need to create the tag
                    return self.version_str, self.release_nr
                else:
                    # not a versioned product
                    return None

            if self.changedSinceTag(tag_node, tag_name=self.version_str):
                if not self.release_again:
                    logger.error("Changes since tag %s for %s (branch '%s') "
                                 "but empty CHANGES file",
                                 self.version_str, self.desc.local_path_rel,
                                 self.desc.getName())
                    raise RepoReleaseError()
            else:
                logger.info("Already released: %s (branch '%s') as tag %s. "
                            "Nothing to do",
                            self.desc.local_path_rel, self.desc.getName(),\
                            self.version_str)
                return False

        if changes[0] or changes[1] or changes[3]:
            # any requires/features/int. features
            release = 1
            if self.increment_major:
                # major++
                version[0] += 1
                version[1] = 0
                version[2] = 0
            else:
                # minor++
                version[1] = version[1] + 1
                version[2] = 0
        elif changes[2]:
            # bug fixes
            release = 1
            version[2] = version[2] + 1
        else:
            # release again
            release += 1
        str_version = '.'.join(map(str, version)[:-1])
        if self.branch != 'default':
            # setup branch flag
            str_version += '-' + self.branch
        self.version_new = [str_version, str(release)]
        return True

    def tag(self):
        tag = self.version_new[1]
        msg = "hgbundler made release tag"
        hg_commands.tag(self.repo.ui, self.repo, tag, message=msg)
        return tag

class Branch(RepoDescriptor):

    def getName(self):
        """Return name, after infering it if necessary."""

        name = self.name
        if name is not None:
            return name

        # implicit specification (unique branch, or 'default')
        # mercurial has a cache for this (costly) dict (branch name) -> tip
        branches = self.getRepo().branchtags()
        logger.debug("Found branches: %s", branches.keys())

        if len(branches) > 1:
            logger.debug(
                "No specified branch, found several, trying 'default'")
            name = 'default'
        else:
            name = branches.keys()[0]
            logger.debug("Using the unique branch '%s" % name)

        # final check
        if name not in branches:
            raise ValueError((
                "Wrong specified or guessed branch '%s' for %s."
                "Please specify an existing one") % (name, self.local_path_rel))

        self.name = name
        return name

    def checkLocalRepo(self):
        """Ensure that there are no local changes.
        TODO: if there are several branch and we're not on tip, this shows
        a difference...
        """
        repo = self.getRepo()
        ctx = repo[None]
        current_branch = ctx.branch()
        if current_branch != self.getName():
            logger.error("Repository %s is on named branch '%s' instead of the"
                         "'%s' that's specified in %s (or guessed)",
                         self.local_path_rel, current_branch, self.name,
                         MANIFEST_FILE)
            raise RepoReleaseError(WRONG_BRANCH)

        st = repo.status()
        for x in st:
            if x:
                logger.error("Uncommited changes in %s. Aborting.",
                             self.local_path_rel)
                raise RepoReleaseError(LOCAL_CHANGES)

    def isHgBundlerManaged(self):
        """True if the releases for this repo are managed through Hg Bundler"""
        l = os.listdir(self.local_path_rel)
        if not 'CHANGES' in l:
            return False

        return True # NOCOMMIT

    def checkHeads(self, allow_multiple=False):
        heads = self.heads()
        if not heads:
            # Can happen after a rollback
            logger.error("No head in branch %s of %s. Aborting.",
                         name, self.local_path_rel)
            raise RepoReleaseError()

        if len(heads) > 1:
            name = self.getName()
            if allow_multiple:
                logger.info("Several heads for branch %s of %s. Allowed, "
                            "but you should check and fix that",
                            name, self.local_path_rel)
            else:
                logger.error("Several heads for branch %s of %s. Aborting.",
                             name, self.local_path_rel)
                raise RepoReleaseError(MULTIPLE_HEADS)

    def release(self, multiple_heads=False, release_again=False,
                increment_major=False):
        if not self.isHgBundlerManaged():
            logger.info("Does not look to be managed by hgbundler : %s",
                        self.local_path_rel)
            return

        name = self.getName()
        self.checkLocalRepo()
        self.checkHeads(allow_multiple=multiple_heads)
        logger.info("Performing release of branch %s for %s",
                    name, self.local_path_rel)

        releaser = Releaser(self, release_again=release_again,
                            increment_major=increment_major)

        to_tag = releaser.newVersion()
        if to_tag is None:
            return
        elif to_tag:
            releaser.updateVersionFiles()
            self.getRepo().commit(
                text="hgbundler prepared version files for release")
            tag_str = releaser.tag()
            releaser.initChangesFile()
            self.getRepo().commit(text="hgbundler init new CHANGES file")
        else:
            tag_str = releaser.version_str

        return Tag(self.remote_url, self.bundle_dir, self.target, tag_str,
                       self.xml_attrs)

    def tip(self):
        """Return the tip of this branch."""
        return self.getRepo().branchtags()[self.getName()]

    def heads(self):
        """Return the heads for this branch."""
        return self.getRepo().branchheads(self.getName())

class Bundle(object):

    element2class = {'tag': Tag, 'branch': Branch}

    def __init__(self, bundle_dir):
        self.bundle_dir = bundle_dir
        if MANIFEST_FILE not in os.listdir(bundle_dir):
            raise RuntimeError(
                "Not a bundle directory : %s (no MANIFEST_FILE)" % bundle_dir)
        self.tree = etree.parse(self.getManifestPath())
        self.root = self.tree.getroot()
        self.descriptors = None
        self.known_targets = set()
        self.bundle_repo = None
        self.initial_node = None

    def getManifestPath(self):
        return os.path.join(self.bundle_dir, MANIFEST_FILE)

    def initBundleRepo(self):
        """Store repo and initial node info for the bundle itself if needed.
        Raise an error if repo or initial node can't be found.
        """

        if self.bundle_repo is None:
            repo_path = _findrepo(self.bundle_dir)
            if repo_path is None:
                raise RepoNotFoundError()
            logger.info("Found mercurial repository at %s", repo_path)
            repo = self.bundle_repo = hg.repository(HG_UI, repo_path)

        if self.initial_node is None:
            ctx = repo[None]
            current_node = ctx.node()
            if current_node is None:
                parents = ctx.parents()
                if len(parents) != 1:
                   raise NodeNotFoundError(SEVERAL_PARENTS)
                ctx = parents[0]
            current_node = ctx.node()
            current_rev = ctx.rev()
            logger.debug("Currently at rev %s (%s)", parents[0].rev(),
                         hg_hex(current_node))
            self.initial_node = current_node
            self.initial_rev = current_rev

    def updateToInitialNode(self):
        self.initBundleRepo()
        logger.info("Getting back to rev %s (%s)", self.initial_rev,
                     hg_hex(self.initial_node))
        hg.update(self.bundle_repo, self.initial_node)

    def makeRepo(self, server, r, new=True):
        """Make a repo descriptor from server instance and xml element.

        If new, this will assert that this repo's target is unknown
        Otherwise, will ensure on the contrary that it is known."""

        etag = r.tag
        if callable(etag):
            # This is probably just an XML comment (lxml only)
            return None
        klass = self.element2class.get(etag)
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

        known = target in self.known_targets
        if new and known:
                raise ValueError("Target name conflict: %s" % target)
        if not new and not known:
                raise ValueError("Target name unknown: %s" % target)
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

    #
    # Command-line operations
    #

    def make_clones(self, options=None):
        for desc in self.getRepoDescriptors():
            if desc.make_clone():
                desc.update()

    def update_clones(self, options=None):
        for desc in self.getRepoDescriptors():
            desc.update()

    def clones_refresh_url(self, options=None):
        for desc in self.getRepoDescriptors():
            desc.updateUrls()

    def release_clone(self, target, options=None):
        """Release one given clone
        """
        for desc in self.getRepoDescriptors():
            if desc.target == target:
                try:
                    desc.release(multiple_heads=options.multiple_heads,
                                 release_again=options.release_again,
                                 increment_major=options.increment_major)
                    msg = "Release of %s (branch '%s') done. "
                    if not getattr(options, 'auto_push', False):
                        msg += "You may want to push "
                        "(default is %s)" % desc.remote_url_push
                    logger.warn(msg, desc.local_path_rel, desc.getName())
                    return 0
                except RepoReleaseError:
                        return 1

        logger.fatal("No clone with target '%s'" % target)
        return 1

    def writeManifest(self):
        """Dumps the XML tree in the manifest file."""

        # We go through tidy since pretty_print not present in all lxml
        # versions)
        # TODO this is a rough way of doing, even with the pipe

        tidy_out, tidy_in, tidy_err = popen2.popen3(
            'tidy --wrap 79 --indent-attributes yes '
            '--indent yes --indent-spaces 2 -asxml -xml ')
        self.tree.write(tidy_in)
        tidy_in.close()
        formatted = tidy_out.read()
        tidy_out.close()

        f = open(self.getManifestPath(), 'w')
        f.write(formatted)
        f.close()

    def release(self, release_name, options=None):
        """Release the whole bundle."""
        try:
            self.initBundleRepo()
        except RepoNotFoundError:
            logger.critical("The current bundle is not part of a mercurial."
                            "Repository. Releasing makes not sense.")
            return 1
        except NodeNotFoundError, e:
            if str(e) == SEVERAL_PARENTS:
                logger.critical("Current bundle state has several parents. "
                                "uncommited merge?")
                return 1

        bundle_repo = self.bundle_repo

        if release_name in bundle_repo.branchtags():
            logger.critical("There is already a release '%s' for this bundle",
                            release_name)
            return 1

        new_tags = {}
        # Release of all repos
        for desc in self.getRepoDescriptors():
            if isinstance(desc, Tag):
                logger.info("Target %s is a tag (%s). Not releasing.",
                            desc.target, desc.name)
                continue
            try:
                new_tags[desc.target] = desc.release(
                    multiple_heads=options.multiple_heads,
                    increment_major=options.increment_major)
            except RepoReleaseError:
                return 1

        # update xml tree
        for s in self.root.getchildren():
            if s.tag != 'server':
                continue
            server = Server(s.attrib)
            for i, r in enumerate(s.getchildren()):
                if r.tag != 'branch':
                    continue

                desc = self.makeRepo(server, r, new=False)
                new_tag = new_tags[desc.target]
                if new_tag is None:
                    # not relased, but not an error
                    continue

                s.remove(r)
                t = new_tag.xml()
                s.insert(i, t)

        # create branch, update manifest, commit, tag and get back
        hg_commands.branch(bundle_repo.ui, bundle_repo, release_name)
        self.writeManifest()
        bundle_repo.commit(text="hgbundler update manifest for release")
        hg_commands.tag(bundle_repo.ui, bundle_repo, release_name,
                        message="hgbundler setting tag")
        #TODO mark branch as inactive ?
        self.updateToInitialNode()

if __name__ == '__main__':
    commands = {'make-clones': 'make_clones',
                'update-clones': 'update_clones',
                'clones-refresh-url': 'clones_refresh_url',
                'release-clone': 'release_clone',
                'release-bundle': 'release',}
    usage = "usage: %prog [options] " + '|'.join(commands.keys())
    usage += """ [command args] \n

    command arguments:

    command             arguments        comments
    ----------------------------------------------
    release-bundle      <release name>   mandatory
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
