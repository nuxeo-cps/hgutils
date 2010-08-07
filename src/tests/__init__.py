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

import logging
import os
TEST_DATA_PATH = os.path.join(os.path.split(__file__)[0], 'data')

def rmr(dirpath):
    """python equivalent of 'rm -r'.
    """

    for root, dirs, files in os.walk(dirpath, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            path = os.path.join(root, name)
            if os.path.islink(path):
                os.remove(path)
            else:
                os.rmdir(os.path.join(root, name))
    try:
        os.rmdir(dirpath)
    except OSError:
        logging.exception("Error while removing directory '%s'", dirpath)


def hg_init(dirpath):
    """TODO: platform independence."""
    os.system('hg init %s; cd %s; hg add; hg ci -m "init"' % (dirpath, dirpath))

def un_hg(dirpath):
    hgpath = os.path.join(dirpath, '.hg')
    if os.path.isdir(hgpath):
        rmr(hgpath)

class Options(object):
    """Placeholder for expected command-line options."""

    verbose = False
    multiple_heads = False
    release_again = False
    increment_major = False
