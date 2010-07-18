#!/usr/bin/env python
import sys
import os

def dump(lines, fpath):
    lines = [line + '\n' for line in lines]
    fd = open(fpath, 'w')
    fd.writelines(lines)
    fd.close()

def accumulate(path, inert, repos):
    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        if os.path.isdir(entry_path):
            if os.path.isdir(os.path.join(entry_path, '.hg')):
                repos.append(entry_path)
                continue
            inert.append(entry_path)
            accumulate(entry_path, inert, repos)

def analyze(path):
    inert = []
    repos = []
    accumulate(path, inert, repos)
    return inert, repos

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print "Usage: %s <inert_file> <repos_file>" % sys.argv[0]
        sys.exit(1)

    inert_file = sys.argv[1]
    repos_file = sys.argv[2]

    inert, repos = analyze(os.getcwd())

    dump(inert, inert_file)
    dump(repos, repos_file)
