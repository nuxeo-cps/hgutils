#!/usr/bin/env python
"""Clone a whole mercurial hierarchy, using the output of analyze_hg_hierarchy.

Arguments (in that order):

 - remote url: the base url of the hierarchy on the remote peer.
 - remote basedir: the absolute path to the directory enclosing the whole
   hierarchy on the remote peer
 - inert_file: a file listing all *ordinary* directories on the remote peer.
   Produced by running analyze_hg_hierarchy on the remote peer.
   Will be used in conjunction with remote basedir to reproduce that hierarchy
   on the local host in current working directory.
 - repos_file: a file listing all hg repos as absolute paths on the remote peer.
   Produced by running analyze_hg_hierarchy on the remote peer.
   Will be used in conjunction with remote basedir and remote url to issue the
   hg clone commands.
"""

import sys
import os

def read(path):
    f = open(path)
    lines = [line.strip() for line in f.readlines()]
    f.close()
    return lines

def strip_path(path, remote_basedir):
    if not path.startswith(remote_basedir):
        raise ValueError("Path not under base directory")
    path = path[len(remote_basedir):]
    if path.startswith('/'):
        path = path[1:]
    return path

def make_dirs(paths, remote_basedir):
    for path in paths:
        try:
            os.mkdir(strip_path(path, remote_basedir))
        except ValueError:
            pass

def make_clone(path, remote_url, remote_basedir):
    try:
        path = strip_path(path, remote_basedir)
    except ValueError:
        return

    if not remote_url.endswith('/'):
        remote_url = remote_url + '/'
    remote = remote_url + path
    cmd = 'hg clone %s %s' % (remote, path)
    print cmd
    os.system(cmd)

def make_clones(paths, remote_url, remote_basedir):
    for path in paths:
        make_clone(path, remote_url, remote_basedir)

if __name__ == '__main__':
    if len(sys.argv) < 5:
        print ("Usage: %s <remote_url> <remote_basedir> " +
               "<inert_file> <repos_file>") % sys.argv[0]
        sys.exit(1)

    remote_url = sys.argv[1]
    remote_basedir = sys.argv[2]
    inert_file = sys.argv[3]
    repos_file = sys.argv[4]

    inert = read(inert_file)
    repos = read(repos_file)
    make_dirs(inert, remote_basedir)
    make_clones(repos, remote_url, remote_basedir)

