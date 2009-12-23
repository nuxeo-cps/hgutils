#!/usr/bin/env python

import os
from optparse import OptionParser
from lxml import etree

import logging
logger = logging.getLogger('hgbundler')
logger.setLevel(logging.DEBUG)


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


class Repo(object):

    def __init__(self, server, path, target, name):
        # name is an additional name to qualify used by subclasses
        self.server = server
        self.url = server.url + '/' + path
        self.target = target
        self.name = name

    def make_clone(self, base_dir):
        logging.info("Cloning %s to %s", self.url, self.target)
        make_clone(self.url, base_dir, self.target)


class Tag(Repo):

    def update(self, base_dir):
        path = os.path.join(base_dir, self.target)
        hg_up(path, self.name)

class Branch(Repo):

    def update(self, base_dir):
        path = os.path.join(base_dir, self.target)
        name = self.name
        if name is None:
            name = 'default'
        hg_up(path, name)


class Bundle(object):

    element2class = {'tag': Tag, 'branch': Branch}

    def __init__(self, bundle_dir):
        self.bundle_dir = bundle_dir
        if MANIFEST_FILE not in os.listdir(bundle_dir):
            raise RuntimeError(
                "Not a bundle directory : %s (no MANIFEST_FILE)" % bundle_dir)
        self.tree = etree.parse(os.path.join(bundle_dir, MANIFEST_FILE))
        self.root = self.tree.getroot()
        self.repos = None

    def getRepos(self):
        if self.repos is not None:
            return self.repos

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

                path = r.attrib.get('path')
                if path is None:
                    raise ValueError(
                        "element with no path: %s" % etree.tosring(r))

                target = path.rsplit('/', 1)[-1]
                if target in targets:
                    raise ValueError("Target name conflict: %s" % target)
                targets.add(target)

                name = r.attrib.get('name')
                res.append(klass(server, path, target, name))

        self.repos = res
        return res

    def make_clones(self):
        for repo in self.getRepos():
            repo.make_clone(base_dir=self.bundle_dir)

    def update_clones(self):
        for repo in self.getRepos():
            repo.update(base_dir=self.bundle_dir)

if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option('-d', '--bundle-directory', dest='bundle_dir',
                      default=os.getcwd(),
                      help="Specify the bundle directory (defaults to current"
                      " working directory)")

    options, arguments = parser.parse_args()
    if not arguments:
        parser.error("Need a command")

    bundle = Bundle(options.bundle_dir)

    command = arguments[0]
    if command == 'make-clones':
        bundle.make_clones()
        bundle.update_clones()
    elif command == 'update-clones':
        bundle.update_clones()
