#!/usr/bin/env python
"""Takes the output of hg log in stdin and creates tags for pre-svn's make_pkg.

This is a very simple script. Usage :
hg log | tags_make_pkg.py

You should carefully review the outcome
"""

import sys
import os
import re

lines = sys.stdin.readlines()
tags = {}

for i, li in enumerate(lines):
    matchobj = re.match('summary:\s*make_pkg.*?-(.*)$', li)
    if matchobj is None:
        continue
    tag = matchobj.group(1)
    if tag in tags:
        # already found in newer version
        continue

    # now find the changeset
    j = i
    matchobj = None
    while matchobj is None and j > 0:
        j -= 1
        matchobj = re.match('changeset:.*?:(.*)$', lines[j])
    changeset = matchobj.group(1)
    tags[tag] = changeset
    print "Found tag %s for changeset %s" % (tag, changeset)
    os.system('hg tag -r %s %s' % (changeset, tag))
