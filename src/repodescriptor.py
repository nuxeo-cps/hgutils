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

"""Repository descriptor: base class and subclasses : Branch and Tag."""


import os
import re
import sys
import logging

from mercurial import hg
from mercurial import archival
from mercurial import patch
from mercurial.node import short as hg_hex
from mercurial.node import nullid
from mercurial import cmdutil as hg_cmdutil
CMDUTIL_REMOTEUI = 'remoteui' in dir(hg_cmdutil)
HG_REMOTEUI = 'remoteui' in dir(hg)
CMDUTIL_SETREMOTE = 'setremoteui' in dir(hg_cmdutil)

import mercurial.patch
import mercurial.util
import mercurial.ui

from common import etree
from common import _currentNodeRev
from common import BranchNotFoundError

from bundleman.utils import parseNuxeoHistory

from releaser import Releaser
from releaser import RepoReleaseError
from releaser import parseNuxeoVersionFile

from constants import (ASIDE_REPOS,
                       )

logger = logging.getLogger('hgbundler.repodescriptor')

class LoggerUi(mercurial.ui.ui):
    """Subclass to indirect mercurial output through a logger."""

    def __init__(self, out_logger, **kw):
        mercurial.ui.ui.__init__(self, **kw)
        self.out_logger = out_logger

    def copy(self):
        return self.__class__(self.out_logger)

    def write(self, *args, **opts):
        if self._buffers:
            self._buffers[-1].extend([str(a) for a in args])
        else:
            logger = self.out_logger
            for a in args:
                logger.info(str(a))

HG_UI = LoggerUi(logger)


LOCAL_CHANGES = 'local changes'
MULTIPLE_HEADS = 'multiple heads'
NOT_HEAD = 'not a head'
WRONG_BRANCH = 'wrong branch'
SEVERAL_PARENTS = 'several parents'

BM_MERGE_RE = re.compile(r'^merging changes from \w+://')

def make_clone(url, target_path):
    base_dir, target = os.path.split(target_path)
    if not os.path.isdir(base_dir):
        os.mkdir(base_dir)
    logger.debug("Cloning %s to %s", url, os.path.join(base_dir, target))
    cmd = 'cd %s && hg clone %s %s' % (base_dir, url, target)
    logger.debug(cmd)
    os.system(cmd)

def repo_add(repo, fnames):
    """Add files to the given hg repository.

    This method to improve hg version independence.
    """
    try:
        meth = repo.add
    except AttributeError:
        meth = repo[None].add

    return meth(fnames)



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

    def tip(self):
        raise NotImplementedError()

    def outgoing(self):
        raise NotImplementedError()

    def getName(self):
        return 'no applicable name'

    def changelog(self, rev1, rev2):
        """Return parsed changelog between rev1 and rev2.

        Usually, rev1 and rev2 would be tag names
        """

        repo = self.getRepo()
        node1, node2 = hg_cmdutil.revpair(repo, (rev1, rev2))
        opts = {}
        m = hg_cmdutil.match(repo, pats=('path:HISTORY',))

        diffopts = patch.diffopts(repo.ui)
        diff = []
        for l in patch.diff(repo, node1, node2, match=m, opts=diffopts):
            diff.extend([line[1:] for line in l.split('\n')
                         if line.startswith('+') and not line.startswith('++')])
        return parseNuxeoHistory('\n'.join(diff))

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
        tag_ctx = self.repo.changectx(node)
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

    def outgoing(self):
        """By definition, a tag has nothing to push."""
        return 0, None

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
        ctx = repo.changectx(None)
        current_branch = ctx.branch()
        if current_branch != self.getName():
            logger.error("Repository %s is on named branch '%s' instead of the"
                         "'%s' that's specified in the bundle manifest file "
                         "(or guessed)",
                         self.local_path_rel, current_branch, self.name)
            raise RepoReleaseError(WRONG_BRANCH)

        st = repo.status()
        for x in st:
            if x:
                logger.error("Uncommited changes in %s. Aborting.",
                             self.local_path_rel)
                raise RepoReleaseError(LOCAL_CHANGES)

    def isHgBundlerManaged(self):
        """True if the releases for this repo are managed through Hg Bundler"""
        l = os.listdir(self.local_path)
        if not '.hgbundler_managed' in l and not 'CHANGES' in l:
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
            if releaser.initial:
                repo_add(self.getRepo(), ('VERSION', 'HISTORY'))
            self.getRepo().commit(
                text="hgbundler prepared version files for release")
            tag_str = releaser.tag()
            releaser.initChangesFile()
            if releaser.initial:
                repo_add(self.getRepo(), ('CHANGES',))
            self.getRepo().commit(text="hgbundler init new CHANGES file")
        else:
            tag_str = releaser.version_str

        return Tag(self.remote_url, self.bundle_dir, self.target, tag_str,
                       self.xml_attrs)

    def tip(self):
        """Return the tip of this branch."""
        try:
            return self.getRepo().branchtags()[self.getName()]
        except KeyError:
            logger.error("Branch %s not found in repository %s ",
                         self.getName(), self.local_path_rel)
            raise BranchNotFoundError(self.getName())

    def heads(self):
        """Return the heads for this branch."""
        return self.getRepo().branchheads(self.getName())

    def outgoing(self, **opts):
        """Tell how many changesets are not found in destination

        Adapted from mercurial.commands to produce no output.
        """
        repo = self.getRepo()
        ui = repo.ui
        old_quiet = ui.quiet
        ui.quiet = True
        limit = hg_cmdutil.loglimit(opts)
        parsed = hg.parseurl(
            ui.expandpath('default-push', 'default'), opts.get('rev'))
        if len(parsed) == 2:
            dest, (revs, checkout) = parsed
        else:
            dest, revs, checkout = parsed

        if revs:
            revs = [repo.lookup(rev) for rev in revs]
        if CMDUTIL_REMOTEUI:
            other = hg.repository(hg_cmdutil.remoteui(repo, opts), dest)
        elif CMDUTIL_SETREMOTE:
            hg_cmdutil.setremoteconfig(ui, opts)
            other = hg.repository(ui, dest)
        elif HG_REMOTEUI:
            other = hg.repository(hg.remoteui(repo, opts), dest)
        else:
            logger.critical("Problem on this Mercurial version")
            sys.exit(1)

        if HG_REMOTEUI:
            from mercurial import discovery
            o = discovery.findoutgoing(repo, other, force=opts.get('force'))
        else:
            o = repo.findoutgoing(other, force=opts.get('force'))
        ui.quiet = old_quiet
        return len(o), dest

