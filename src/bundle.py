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

"""Bundle and Server classes."""

import os
import sys
import logging
from subprocess import Popen, PIPE

from mercurial import hg
from mercurial.node import short as hg_hex
from mercurial import commands as hg_commands

from bundleman.utils import rst_title

from common import etree
from common import _findrepo, _currentNodeRev
from common import NodeNotFoundError, RepoNotFoundError

from releaser import RepoReleaseError
from repodescriptor import Branch, Tag
from repodescriptor import HG_UI
from repodescriptor import SEVERAL_PARENTS
from constants import (ASIDE_REPOS,
                      )
from common import HG_VERSION, HG_VERSION_STR

MANIFEST_FILE = "BUNDLE_MANIFEST.xml"
INCLUDES = '.hgbundler_incl'
BUNDLE_RELEASE_BRANCH_PREFIX='hgbundler-release-'

logger = logging.getLogger('hgbundler.bundle')

_default = object()

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


class Bundle(object):

    element2class = {'tag': Tag, 'branch': Branch}

    def __init__(self, bundle_dir):
        self.bundle_dir = bundle_dir
        if MANIFEST_FILE not in os.listdir(bundle_dir):
            raise RuntimeError(
                "Not a bundle directory : %s (no MANIFEST_FILE)" % bundle_dir)

        self.tree = None
        self.root = None
        self.bundle_repo = None
        self.sub_bundles = None
        self.descriptors = None
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
            logger.info("Found mercurial repository at %s", repo_path or '.')
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

    def getRoot(self):
        root = self.root
        if self.root is not None:
            return self.root

        self.tree = etree.parse(self.getManifestPath())
        root = self.root = self.tree.getroot()
        return root

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

    def getSubBundles(self):
        """Extract and return subbundles information."""

        sub_bundles = self.sub_bundles
        if sub_bundles is not None:
            return sub_bundles

        sub_bundles = []
        for pos, elt in enumerate(self.getRoot()):
            if elt.tag != 'include-bundles':
                continue

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

            descs = []
            for r in elt:
                repo = self.makeRepo(server, r)
                if repo is None: # happens, e.g, with XML comments
                    continue
                repo.make_clone()
                repo.update()

                descs.append(repo)

            sub_bundles.append(dict(server=server, position=pos,
                                    excluded=excluded,
                                    element=elt,
                                    descriptors=tuple(descs)))

        self.sub_bundles = sub_bundles
        return sub_bundles

    def includeBundles(self, server=None, position=None, excluded=None,
                       descriptors=None, element=None):
        """Do the actual job of inclusion from the info from getSubBundles."""

        if server is None or position is None or excluded is None:
            raise ValueError

        if excluded is None:
            excluded = ()

        root = self.getRoot()
        for repo in descriptors:
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

                root.insert(position+j, subelt)

        if element is not None:
            element.tag = 'already-included-bundles'
            element.text = (
                "\n include-bundles element kept for reference after " +
                "performing the inclusion\n")

    def getRepoDescriptors(self, store=True):
        if self.descriptors is not None and store:
            return self.descriptors

        repos = {} # target -> repo
        targets = [] # *ordered* list of repo names

        for s in self.getSubBundles():
            self.includeBundles(**s)

        # need to iterate again, because includes may have changed the children
        for s in self.getRoot():
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

        descriptors = tuple(repos[target] for target in targets)
        if store:
            self.descriptors = descriptors
        return descriptors

    def pull_clones(self, from_bundle=None, targets=(), update=False):
        """Perform a pull for targets from the given (local) bundle."""

        logger.info("Pulling %s from %s", targets, from_bundle.bundle_dir)
        for target in targets:
            try:
                our_desc = self.getRepoDescriptorByTarget(target)
            except KeyError:
                logger.debug("Target %s not present in %s", target,
                             self.bundle_dir)
                continue
            our_repo = our_desc.getRepo()

            from_path = from_bundle.getRepoDescriptorByTarget(target).local_path
            logger.debug("Pulling %s from %s", our_desc.local_path_rel,
                         from_path)
            hg_commands.pull(HG_UI, our_repo, source=from_path)
            if update:
                logger.debug("Updating %s", our_desc.local_path_rel)
                our_desc.update()

    def push_clones(self, from_bundle=None, targets=(), update=False):
        """Perform a push for targets to the given (local_ bundle."""

        raise NotImplementedError

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
        for s in self.getSubBundles():
            for desc in s['descriptors']:
                desc.updateUrls()

        for desc in self.getRepoDescriptors():
            desc.updateUrls()

    def clones_list(self, options=None):
        for s in self.getSubBundles():
            for desc in s['descriptors']:
                print desc.local_path_rel

        for desc in self.getRepoDescriptors():
            print desc.local_path_rel

    def clones_out(self, options=None):
        for s in self.getSubBundles():
            for desc in s['descriptors']:
                desc.outgoing()

        for desc in self.getRepoDescriptors():
            logger.debug('Performing outgoing for %s', desc.local_path_rel)
            o, dest = desc.outgoing()
            if o:
                logger.warn("%d changeset(s) not in %s", o, dest)

    def getRepoDescriptorByTarget(self, target, default=_default):
        found = [desc for desc in self.getRepoDescriptors()
                 if desc.target == target]
        if found:
            return found[0]
        if default is _default:
            raise KeyError(target)
        return default

    def release_clone(self, target, options=None):
        """Release one given clone
        """
        try:
            desc = self.getRepoDescriptorByTarget(target)
        except KeyError:
            logger.fatal("No clone with target '%s'" % target)
            return 1

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
            logger.error("Could not release '%s'" % target)
            return 1


    def writeManifest(self):
        """Dumps the XML tree in the manifest file."""

        # We go through tidy since pretty_print not present in all lxml
        # versions)

        tidy = Popen('tidy -xml -asxml -wrap 79 --indent yes '
                     '--indent-attributes yes --indent-spaces 2',
                     shell=True,   # for PATH lookup
                     stdout=PIPE, stdin=PIPE, stderr=PIPE,)

        tidy.stdin.write('<?xml version="1.0"?>')
        self.tree.write(tidy.stdin)
        tidy.stdin.close()
        status = tidy.wait()
        if status:
            logger.critical(
                "Error in tidy (exit status %d). stderr: %s", status,
                tidy.stderr.read())
            sys.exit(status)

        formatted = tidy.stdout.read()
        tidy.stdout.close()

        f = open(self.getManifestPath(), 'w')
        f.write(formatted)
        f.close()

    def release_repo_check(self, release_name, options=None):
        """Does the mercurial checkings for release of the whole bundle.
        """
        try:
            self.initBundleRepo()
        except RepoNotFoundError:
            logger.critical("The current bundle is not part of a mercurial."
                            "Repository. Releasing makes not sense.")
            return
        except NodeNotFoundError, e:
            if str(e) == SEVERAL_PARENTS:
                logger.critical("Current bundle state has several parents. "
                                "uncommited merge?")
                return

        bundle_repo = self.bundle_repo
        branch_name = BUNDLE_RELEASE_BRANCH_PREFIX+release_name
        if branch_name in bundle_repo.branchtags():
            logger.critical("There is already a release '%s' for this bundle",
                            release_name)
            return

        return branch_name

    def release(self, release_name, check=True, commit=True, options=None,
                list_released=False):
        """Release the whole bundle.

        Return status or (status, list of released targets) according to
        the 'list_released' kwarg.
        In this implementation, the list of released targets is the list of
        those that get updated in the manifest file.
        """

        if check:
            branch_name = self.release_repo_check(release_name, options=options)
            if branch_name is None:
                return list_released and (1, ()) or 1

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
                return list_released and (1, new_tags.keys()) or 1

        # update xml tree
        known_targets = set(desc.target for desc in descriptors)
        for s in self.getRoot().getchildren():
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

        self.writeManifest()

        if commit:
            self.release_commit(branch_name, release_name, options=options)

        return list_released and (0, new_tags.keys()) or 0

    def release_abort(self):
        self.initBundleRepo()
        repo = self.bundle_repo
        ctx = repo.changectx(None)
        parents = ctx.parents()
        if len(parents) != 1:
            log.fatal("Could not revert repo at %s. Do it manually.",
                      repo.root)
        parent = parents[0].node()
        # Letting this command guess where we want to revert can be
        # hazardeous, esp. with yound repos as in unit tests
        hg_commands.revert(repo.ui, repo, all=True, rev=parent, date=None)

    def release_commit(self, branch_name, release_name, options=None):
        """Does all the mercurial writes after manifest updates for release.

        Create branch, update manifest, commit, tag, close branch and get back.
        """
        self.initBundleRepo()
        bundle_repo = self.bundle_repo

        hg_commands.branch(bundle_repo.ui, bundle_repo, branch_name)

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

    def changelog(self, tag1, tag2, options=None):
        """Output a changelog between two tags."""

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


        descs = []
        for tag in (tag1, tag2):
            try:
                self.updateToTag(tag)
            except NodeNotFoundError:
                logger.critical("Release (bundle tag) %s not found", tag)
                return 1
            bdl = Bundle(self.bundle_dir)
            descs.append(bdl.getRepoDescriptors(store=False))

        self.updateToInitialNode()

        targets = [ set(desc.target for desc in descs[i]) for i in (0,1)]
        new_targets = targets[1].difference(targets[0])
        removed_targets = targets[0].difference(targets[1])
        check_targets = targets[0].intersection(targets[1])
        changed_targets = []

        requires = []
        features = []
        bug_fixes = []
        int_features = []

        for target in check_targets:
            ds = [None, None]
            for i in (0,1):
                for d in descs[i]:
                    if d.target == target:
                        ds[i] = d
                        break
                else:
                    raise RuntimeError('Target not found: %r' % target)
            if not isinstance(ds[0], Tag) or not isinstance(ds[1], Tag):
                logger.info("target %s not managed by us.", target)
                continue

            ttags = [d.name for d in ds]
            if ttags[0] == ttags[1]:
                logger.debug("changelog: target %s unchanged", target)
                continue

            changed_targets.append((target, ttags[0], ttags[1]))

            # now we ask the repo itself
            req, feat, bug, int_feat = ds[1].changelog(*ttags)

            int_features.extend(['[%s] %s'% (target, line)
                                 for line in int_feat])
            features.extend(['[%s] %s'% (target, line) for line in feat])
            requires.extend(['[%s] %s'% (target, line) for line in req])
            bug_fixes.extend(['[%s] %s'% (target, line) for line in bug])

        output_buffer = []
        output = output_buffer.append
        output(rst_title("CHANGELOG between bundles tags %s and %s" %
                         (tag1, tag2), 0))
        output("New tag: %s\n" % tag2)
        output("Old tag: %s\n" % tag1)
        if new_targets:
            output(rst_title('\nNew products', 2))
            output('\n'.join(new_targets))
        if removed_targets:
            output(rst_title('\nRemoved products', 2))
            output('\n'.join(removed_targets))
        if changed_targets:
            output(rst_title('\nChanged products', 2))
            for prod in changed_targets:
                output('* %s \t %s -> %s\n' % (prod[0], prod[1], prod[2]))

        if requires:
            output(rst_title('\nRequires', 3))
            output('* ' + '\n* '.join(requires))
        if features:
            output(rst_title('\nFeatures', 3))
            output('* ' + '\n* '.join(features))
        if bug_fixes:
            output(rst_title('\nBug fixes', 3))
            output('* ' + '\n* '.join(bug_fixes))
        if int_features:
            output(rst_title('\nInternal features', 3))
            output('* ' + '\n* '.join(int_features))
        output('\n')

        if options.output:
            f = open(options.output, 'w')
            f.writelines(output_buffer)
            f.close()
        else:
            sys.stdout.writelines(output_buffer)
        return 0

