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
import re

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
from mercurial import archival
from mercurial.node import short as hg_hex
from mercurial import commands as hg_commands
from mercurial import cmdutil as hg_cmdutil
import mercurial.patch
import mercurial.util
import mercurial.ui
HG_UI = mercurial.ui.ui()

MANIFEST_FILE = "BUNDLE_MANIFEST.xml"
ASIDE_REPOS = '.hgbundler'
INCLUDES = '.hgbundler_incl'
BUNDLE_RELEASE_BRANCH_PREFIX='hgbundler-release-'

try:
    HG_VERSION_STR = mercurial.util.version()
except AttributeError:
    from mercurial.version import get_version
    HG_VERSION_STR = get_version()

split = HG_VERSION_STR.split('+', 1)
HG_VERSION_COMPLEMENT = len(split) == 2 and split[1] or None
HG_VERSION = tuple(int(x) for x in split[0].split('.'))
BM_MERGE_RE = re.compile(r'^merging changes from \w+://')

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
NOT_HEAD = 'not a head'
WRONG_BRANCH = 'wrong branch'
SEVERAL_PARENTS = 'several parents'

class RepoReleaseError(Exception):
    pass


class RepoNotFoundError(Exception):
    pass


class NodeNotFoundError(Exception):
    pass


def _findrepo(p):
    """Find with of path p is an hg repo.

    Copy-pasted from mercurial.dispatch (GPLv2), since the underscore clearly
    marks this as purely internal and subject to change"""

    while not os.path.isdir(os.path.join(p, ".hg")):
        oldp, p = p, os.path.dirname(p)
        if p == oldp:
            return None
    return p

def _currentNodeRev(repo):
    """Return current node and rev for repo."""
    ctx = repo[None]
    node = ctx.node()
    if node is None:
        parents = ctx.parents()
        if len(parents) != 1:
           raise NodeNotFoundError(SEVERAL_PARENTS)
        ctx = parents[0]
        node = ctx.node()
    return node, ctx.rev()


class Server(object):

    @classmethod
    def _normTrailingSlash(self, url):
        if url is None:
            return None
        return url.endswith('/') and url[:-1] or url

    def __init__(self, attrib):
        self.name = attrib.get('name')
        self.from_include = attrib.get('from-include',
                                       '').strip().lower() == 'true'
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
                 from_include=False, remote_url_push=None):
        # name is an additional name to qualify used by subclasses
        self.remote_url = remote_url
        self.remote_url_push = remote_url_push
        self.target = target
        self.bundle_dir = bundle_dir
        self.name = name
        self.from_include = from_include

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

    def subSrcDest(self, base_path=None):
        """Return origin, destination path in the bundle and clone path

        Meaningful for sub repos only (if self.is_sub).

        destination and clone paths are absolute.
        if base_path is not specified, origin is relative to dest (suitable
        for a relocatable symlink). Same for clone path. Otherwise it'll be
        absolute.
        """

        clone_from_bdl = os.path.join(ASIDE_REPOS, self.clone_target)
        dest_from_bdl = os.path.join(clone_from_bdl, self.subpath)

        if base_path is not None:
            src = os.path.join(base_path, dest_from_bdl)
        else:
            base_path = self.bundle_dir
            deepness = len(self.target.split(os.path.sep)) - 1

            if deepness:
                local_to_base = os.path.join(*((os.path.pardir,)*deepness))
            else:
                local_to_base = ''

            src = os.path.join(local_to_base, dest_from_bdl)

        dest = os.path.join(base_path, self.target)
        clone = os.path.join(base_path, clone_from_bdl)

        return src, dest, clone

    def make_clone(self):
        """Make the clone if needed and return True if done."""

        if os.path.exists(self.local_path):
            logger.debug("Ignoring the existing clone %s", self.local_path_rel)
        else:
            logger.info("Creating clone %s", self.local_path_rel)
            make_clone(self.remote_url, self.local_path)
            if self.remote_url_push:
                self.updateUrls()

        if self.is_sub:
            target_path = os.path.join(self.bundle_dir, self.target)
            if os.path.exists(target_path):
                logger.debug("Ignoring the existing target path %s",
                             self.target)
                return False

            logger.info("Extracting to %s", self.target)
            src, target_path, _ = self.subSrcDest()
            logger.debug("Making symlink %s -> %s", target_path, src)
            os.symlink(src, target_path)

        return True

    def release(self):
        """Perform release of the given clone.

        Release means update to VERSION, CHANGES, etc + mercurial tag etc."""
        raise NotImplementedError

    def updateVersionFilesInArchive(self, ar_dir):
        """Update version files to be more appropriate in archive.

        this is also meant to ease diffing bundleman produced archives."""

        def in_ar(path):
            return os.path.join(ar_dir, path)

        try:
            os.unlink(in_ar('CHANGES'))
            os.rename(in_ar('HISTORY'), in_ar('CHANGELOG.txt'))
        except OSError:
            logger.debug("Tag not made by hgbundler nor bundleman")
            return

        detailed_v = in_ar('VERSION')
        if os.path.isfile(detailed_v):
            f = open(detailed_v)
            content = f.read()
            _, v, r = parseNuxeoVersionFile(content)
            f.close()

            f = open(in_ar('version.txt'), 'w')
            f.write('%s-%s\n\n' % (v, r))
            f.close()

    def archive(self, output_dir):
        repo = self.getRepo()
        dest = os.path.join(output_dir, self.local_path_rel)
        logger.info("Extracting %s (%s) to %s",
                    self.local_path_rel, self.getName(), dest)
        archival.archive(repo, dest, self.tip(),
                         'files', True, hg_cmdutil.match(repo, []))

        self.updateVersionFilesInArchive(dest)

        if self.is_sub:
            src, dest, clone = self.subSrcDest(base_path=output_dir)

            # TODO platform independency
            cmd = 'cp -rp %s %s' % (src, dest)
            logger.info('Subpath extraction: ' + cmd)
            os.system(cmd)

            cmd = 'cp %s %s' % (os.path.join(clone, '.hg_archival.txt'), dest)
            logger.debug('hg archival file extraction: ' + cmd)
            os.system(cmd)

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

    def releaseCheck(self, **kw):
        ctx = self.getRepo()[None]
        parents = ctx.parents()
        if len(parents) != 1:
            logger.critical("Target %s is supposed to be at tag %s but "
                            "looks like an uncommited merge", self.target,
                            self.name)
            raise RepoReleaseError

        if self.tip() != parents[0].node():
            logger.error("Target %s is not updated to tag %s. Aborting.",
                         self.target, self.name)
            raise RepoReleaseError

    def getName(self):
        return self.name

    def nodeIfBundleman(self, node):
        """If tag has been done by bundleman, return child. See #2143
        """
        tag_ctx = self.repo[node]
        children = tag_ctx.children()
        if not children:
            # not a bundleman tag
            return node

        for ctx in children:
            child_desc = ctx.description()
            if BM_MERGE_RE.match(child_desc) and child_desc.endswith('/'.join(
                (self.target, 'tags', self.name))):
                node = ctx.node()
                logger.debug("Tag %s for %s made by bundleman (using child %s)",
                             self.name, self.local_path_rel, hg_hex(node))
                break

        return node

    def tip(self):
        """Cheating with terminology: in that case that's just the tag node.

        In the special case of bundleman made tags (see #2143)
        the conversion process to mercurial had the effet to set the tag right
        before update of version files, because bundleman changed them later
        in the svn tag itself.
        Therefore, we need to go to the child (merge from the tag).
        """
        tags = self.getRepo().tags()
        name = self.name

        try:
            node = tags[name]
        except KeyError:
            raise ValueError("Tag '%s' not found in repo %s", name,
                             self.local_path_rel)

        return self.nodeIfBundleman(node)

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

        return True 

    def checkHeads(self, allow_multiple=False):
        heads = self.heads()
        name = self.getName()
        if not heads:
            # Can happen after a rollback
            logger.error("No head in branch %s of %s. Aborting.",
                         name, self.local_path_rel)
            raise RepoReleaseError()

        if len(heads) > 1:
            if allow_multiple:
                logger.warn("Several heads for branch %s of %s. Allowed, "
                            "but you should check and fix that",
                            name, self.local_path_rel)
            else:
                logger.error("Several heads for branch %s of %s. Aborting.",
                             name, self.local_path_rel)
                raise RepoReleaseError(MULTIPLE_HEADS)

        node, _ = _currentNodeRev(self.repo)
        if node not in heads:
            logger.error("Current node %s on branch %s of %s "
                         "not a head. Aborting.",
                         hg_hex(node), name, self.local_path_rel)
            raise RepoReleaseError(NOT_HEAD)

    def release(self, multiple_heads=False, release_again=False,
                increment_major=False):
        if not self.isHgBundlerManaged():
            logger.info("Does not look to be managed by hgbundler : %s",
                        self.local_path_rel)
            return

        name = self.getName()
        self.checkLocalRepo()
        self.checkHeads(allow_multiple=multiple_heads)
        releaser = Releaser(self, release_again=release_again,
                            increment_major=increment_major)

        to_tag = releaser.newVersion()
        if to_tag is None:
            return
        elif to_tag:
            logger.info("Performing release of branch %s for %s",
                        name, self.local_path_rel)
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

        self.tree = None
        self.root = None
        self.descriptors = None
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
            node, rev = _currentNodeRev(repo)
            logger.debug("Currently at rev %s (%s)", rev, node)
            self.initial_node = node
            self.initial_rev = rev

    def updateToInitialNode(self):
        self.initBundleRepo()
        logger.info("Getting back to rev %s (%s)", self.initial_rev,
                     hg_hex(self.initial_node))
        hg.update(self.bundle_repo, self.initial_node)

    def updateToTag(self, tag_name):
        try:
            node = self.bundle_repo.tags()[tag_name]
        except KeyError:
            raise NodeNotFoundError(tag_name)
        logger.info("Updating bundle to tag %s (node %s)",
                    tag_name, hg_hex(node))
        hg.update(self.bundle_repo, node)

    @classmethod
    def repoClass(self, elt):
        """Return the class for repo XML elt, or None if not a repo but valid.
        """
        etag = elt.tag
        if callable(etag):
            # This is probably just an XML comment (lxml only)
            return None
        try:
            return self.element2class[etag]
        except KeyError:
            raise ValueError("Unknown repo mode: %s", etag)

    @classmethod
    def isRepoElement(self, elt):
        """To be used if one does not need the class.

        This doesn't raise exceptions.
        """
        try:
            return self.repoClass(elt) is not None
        except ValueError:
            return False

    @classmethod
    def targetAndPath(self, r):
        """Compute the target of the given repo element.

        if r is None, just return None."""

        if r is None:
            return

        attrib = r.attrib
        path = attrib.get('path')
        if path is None:
            raise ValueError(
                "element with no path: %s" % etree.tostring(r))

        target = attrib.get('target')
        if target is None:
            target = path.rsplit('/', 1)[-1]

        return target, path

    @classmethod
    def repoTarget(self, r):
        """Shortcut"""
        return self.targetAndPath(r)[0]

    def makeRepo(self, server, r):
        """Make a repo descriptor from server instance and xml element.

        If new, this will assert that this repo's target is unknown
        Otherwise, will ensure on the contrary that it is known."""

        klass = self.repoClass(r)
        if klass is None:
            return
        target, path = self.targetAndPath(r)

        attrib = r.attrib
        name = attrib.get('name')

        repo = klass(server.getRepoUrl(path), self.bundle_dir,
                     target, name, attrib, from_include=server.from_include,
                     remote_url_push=server.getRepoUrl(path, push=True))
        return repo

    def includeBundles(self, elt, position):
        server = Server(elt.attrib)

        excluded = set()

        todel = []
        for e in elt:
            if e.tag != 'exclude':
                continue
            excluded.add(e.attrib['target'])
            todel.append(e)
        # two passes to avoid pbms with liveness of lxml obj
        for e in todel:
            elt.remove(e)

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
                subelt.attrib['from-include'] = "true"

                subrepos = [t for t in subelt if self.isRepoElement(t)]

                for t in subrepos:
                    target = self.repoTarget(t)
                    if target not in excluded:
                        continue

                    logger.info("Excluding target %s from included bundle %s",
                                target, repo.target)
                    subelt.remove(t)

                self.root.insert(position+j, subelt)

        elt.tag = 'already-included-bundles'
        elt.text = ("\n include-bundles element kept for reference after " +
                    "performing the inclusion\n")

    def getRepoDescriptors(self):
        if self.descriptors is not None:
            return self.descriptors

        self.tree = etree.parse(self.getManifestPath())
        self.root = self.tree.getroot()

        repos = {} # target -> repo
        targets = [] # *ordered* list of repo names

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
                if repo is None:
                    continue

                target = repo.target
                existing = repos.get(target)
                if existing is None:
                    targets.append(target)
                    repos[target] = repo
                else:
                    if repo.from_include:
                        logger.info(("Got target %s at toplevel and later "
                                     "through include-bundles. First wins."),
                                    target)
                        continue
                    if existing.from_include and not repo.from_include:
                        logger.info(("Got target %s first through "
                                     "include-bundles and then at toplevel. "
                                     "Second wins"), target)
                        targets.remove(target)
                        targets.append(target)
                        repos[target] = repo
                    else:
                        raise ValueError("Target name conflict: %s" % target)

        self.descriptors = tuple(repos[target] for target in targets)
        return self.descriptors

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

        branch_name = BUNDLE_RELEASE_BRANCH_PREFIX+release_name
        if branch_name in bundle_repo.branchtags():
            logger.critical("There is already a release '%s' for this bundle",
                            release_name)
            return 1

        new_tags = {}
        # Release of all repos
        descriptors = self.getRepoDescriptors()
        for desc in descriptors:
            if isinstance(desc, Tag):
                logger.info("Target %s is a tag (%s). Just checking.",
                            desc.target, desc.name)
                desc.releaseCheck()
                continue
            try:
                new_tags[desc.target] = desc.release(
                    multiple_heads=options.multiple_heads,
                    increment_major=options.increment_major)
            except RepoReleaseError:
                return 1

        # update xml tree
        known_targets = set(desc.target for desc in descriptors)
        for s in self.root.getchildren():
            if s.tag != 'server':
                continue
            server = Server(s.attrib)
            for i, r in enumerate(s.getchildren()):
                if r.tag != 'branch':
                    continue

                desc = self.makeRepo(server, r)
                target = desc.target
                if target not in known_targets:
                    raise ValueError(
                        "Released target name %s unknown before hand" % target)
                new_tag = new_tags[target]
                if new_tag is None:
                    # not relased, but not an error
                    continue

                s.remove(r)
                t = new_tag.xml()
                s.insert(i, t)

        # create branch, update manifest, commit, tag, close branch and get back
        hg_commands.branch(bundle_repo.ui, bundle_repo, branch_name)

        self.writeManifest()
        bundle_repo.commit(text="hgbundler update manifest for release")
        hg_commands.tag(bundle_repo.ui, bundle_repo, release_name,
                        message="hgbundler setting tag")
        logger.debug("version tuple: %s", HG_VERSION)
        if HG_VERSION < (1, 2):
            logger.warn("Closing branch implemented in Mercurial > 1.2 "
                        " (current is %s)", HG_VERSION_STR)
        else:
            bundle_repo.commit(text="closing release branch",
                               extra=dict(close=1))
        self.updateToInitialNode()

    def archive(self, tag_name, output_dir, options=None):
        """Produces an archive."""
        try:
            self.initBundleRepo()
        except RepoNotFoundError:
            logger.critical("The current bundle is not part of a mercurial."
                            "Repository. No tags, no archives.")
            return 1
        except NodeNotFoundError, e:
            if str(e) == SEVERAL_PARENTS:
                logger.critical("Current bundle state has several parents. "
                                "uncommited merge?")
                return 1
            raise
        try:
           self.updateToTag(tag_name)
        except NodeNotFoundError:
            logger.critical("Release (bundle tag) %s not found", tag_name)
            return 1

        logger.info("Creation of output directory %s", output_dir)
        os.mkdir(output_dir)

        self.createArchiveVersionFiles(tag_name, output_dir)

        has_sub = False
        for desc in self.getRepoDescriptors():
            has_sub = has_sub or desc.is_sub
            desc.archive(output_dir)

        if has_sub:
            aside = os.path.join(output_dir, ASIDE_REPOS)
            logger.info("Removal of extracted temp directory for sub-repos %s",
                        aside)

            # TODO platform independency
            cmd = "rm -r %s" % aside
            os.system(cmd)

        self.updateToInitialNode()

    def createArchiveVersionFiles(self, tag_name, output_dir):
        def in_ar(p):
            return os.path.join(output_dir, p)

        f = open(in_ar('version.txt'), 'w')
        f.write('%s\nArchive produced by hgbundler from bundle tag %s\n' % (
                tag_name, tag_name))
        f.close()

def main():
    commands = {'make-clones': 'make_clones',
                'update-clones': 'update_clones',
                'clones-refresh-url': 'clones_refresh_url',
                'release-clone': 'release_clone',
                'release-bundle': 'release',
                'archive': 'archive'}
    usage = "usage: %prog [options] " + '|'.join(commands.keys())
    usage += """ [command args] \n

    command arguments:

    command             arguments                comments
    -----------------------------------------------------
    release-clone       <clone relative path>     mandatory
    release-bundle      <release name>            mandatory
    archive             <bundle tag> <output dir> mandatory
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

if __name__ == '__main__':
    main()
