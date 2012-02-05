import os
import time
import urlparse

from buildbot.changes.filter import ChangeFilter
from bundle import Bundle
from repodescriptor import Tag

class BundleChangeFilter(ChangeFilter):

    basedir = '' # Master base directory, filled in from master.cfg

    change_basedir = '' # the base directory to remove from change dir

    update_interval = 30

    def __init__(self, repourl, path_in_repo='', branch='default'):
        # a bit dirty, but chances of collision are so low
        self.clone_path = os.path.join(self.basedir, 'hgbundler',
                                       repourl.replace('/', '_') + '-' + branch)
        if path_in_repo:
            self.bundle_dir = os.path.join(self.clone_path, path_in_repo)
        else:
            self.bundle_dir = self.clone_path
        self.bundle_branch = branch
        self.bundle_url = repourl
        self.bundle_subpath = path_in_repo
        self.latest_update = 0
        self.update()

    def __repr__(self):
        return 'BundleChangeFilter(%r, branch=%r, path_in_repo=%r)' % (
            self.bundle_url, self.bundle_branch, self.bundle_subpath)

    def update(self):
        """Update or create the bundle repository."""
        now = time.time()
        if now - self.latest_update < self.update_interval:
            return

        cwd = os.getcwd()
        if not os.path.isdir(self.bundle_dir):
            os.system('hg clone %s %s' % (self.bundle_url, self.clone_path))
        else:
            os.chdir(self.bundle_dir)
            os.system('hg pull %s' % self.bundle_url)

        os.chdir(self.bundle_dir)
        os.system('hg up %s' % self.bundle_branch)
        os.chdir(cwd)

        self.extract_descriptors()
        self.latest_update = now

    def extract_descriptors(self):
        bundle = Bundle(self.bundle_dir)
        descriptors = bundle.getRepoDescriptors()
        for b in bundle.getSubBundles():
            descriptors.extend(b['descriptors'])
        self.descriptors = [d for d in descriptors if not isinstance(d, Tag)]

    def filter_change(self, change):
        self.update()
        for desc in self.descriptors:
            if self.match_change_clone(change, desc):
                print "%s triggered %r" % (change, self)
                return True

    def match_change_clone(self, change, clone):
        """True if given change matches the given clone descriptor."""
        change_path = change.repository
        if not change_path.startswith(self.change_basedir):
            return False
        change_path = change_path[len(self.change_basedir)+1:]

        parsed = urlparse.urlparse(clone.remote_url)
        url_path = parsed.path
        if url_path.startswith('/'): # always true in practice
            url_path = url_path[1:]
        expected_path = os.path.join(parsed.hostname, url_path)

        if expected_path != change_path:
            return False

        clone_branch = clone.name or 'default'
        return change.branch == clone_branch
