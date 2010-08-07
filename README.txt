===========================
Specification for hgbundler
===========================

Purpose
=======

Replace svn externals and bundleman

File format and organisation
============================

A bundle is a directory with a manifest file listing all involved
repositories, where to find them, etc. Once cloned, the involved
repositories will be toplevel subdirectories of the bundle.

The bundle can itself be a mercurial repository, or a directory in a
repository (use-case: CPS-3-full, CPS-3-base, CPS-3-legacy can all be
versioned together).

The manifest file is called BUNDLE_MANIFEST.xml. The xml format is as in
the following example::

  <?xml version="1.0"?>
  <bundle name="CPS-example">
    <server name="racinet"
            url="http://hg.cps-cms.org/CPS">
      <tag name="CPS-3.4.0" path="products/CPSSchemas"/>
      <branch path="products/CPSDefault"/> <!-- default branch, aka trunk -->
      <branch name="unicode" path="products/CPSDocument"/>
    </server>
  </bundle>

This declares three components, available as repositories at the given server
url. For instance, the repository for the first would be::

  http://hg.cps-cms.org/CPS/products/CPSSchemas

As you can see, there are two types of components: tags and
branches. The trunk being in Mercurial the default branch, there's no
need to make a special case for it.

There is an optional attribute to specify the default server
to push to. See the section about paths in ```man hgrc```. Example::

  <server name="CPS products at Nuxeo"
          url="http://hgcps.nuxeo.org"
          push-url="https://hgcps.nuxeo.org">

The name attribute is totally optional and isn't leveraged much right
now (for error messages mostly).

Deeper svn externals
--------------------

There were some cases in the CPS subversion server where externals
were used to point *inside* a product or a directory that's been
exported as a repository (at least in ``CPSTramline``, in
``nuxeo.lucene``).

This is not part of mercurial logic to clone or export a part of a
repo. Instead, we provide a workaround. The syntax is as follows
(CPS-3-full bundle excerpt)::

    <branch name="gracinet-fix-range"
            target="CPSTramline/tramlinepath" path="tramline"
            subpath="src/tramline/path"/>

This results in a ``src/tramline/path`` subdirectory of a
``tramline`` clone being symlinked as ``CPSTramline/tramlinepath``.
The clone itself is stored in a hidden directory of the bundle
(currently in ``.hgbundler``). Update operations will log the path
to the clone.

The <include-bundles> directive
-------------------------------

To avoid repeating over and over a big list of references, you may
specify bundles to include. Example for the CPS-3-full bundle::

   <include-bundles
       server-url="http://hg.cps-cms.org/CPS">
     <branch target="CPS-3-full" path="bundles" subpath="CPS-3-full"/>
   </include-bundles>

One can exclude some targets from an ``include-bundles`` directive
(see trac ticket #2147)::

   <include-bundles
       server-url="http://hg.cps-cms.org/CPS">
     <exclude target="CPSCourrier" />
     <branch target="CPS-3-full" path="bundles" subpath="CPS-3-full"/>
   </include-bundles>

Remarks::

 - inclusion nesting is neither possible nor planned

 - releasing (see below) a bundle with inclusions behaves
   exactly as if one had copy-pasted the included bundle in the
   bundle being released. In particular, this does *not*
   release the included bundle, but releases the components if
   needed. There aren't any more includes in the resulting bundle tag.

 - ``exclude`` directives are read before the inclusion actually
   starts. Therefore it doesn't matter if they are before or after the
   ``branch`` or ``tag`` elements. They are local to the current
   ``include-bundles`` directive,

Planned options::

 - including, with a change of server urls

Precedence rules and overrides
------------------------------
See also trac #2141

It is illegal to specify twice the same target,
except in one case: if one of those comes from
``include-bundles``. In that case, the explicit specification (at
toplevel) wins.

Example: CPS-3.4.6-base, with CPSDefault on a private branch::

   <server name="myserver" url="http://hg.example.com/CPS/products">
      <branch path="CPSDefault" name="my-private-branch"/>
   </server>

   <include-bundles
       server-url="http://hg.cps-cms.org/CPS">
     <tag target="CPS-3.4.6-base" path="bundles" subpath="CPS-3-base"/>
   </include-bundles>

The ordering does not matter, but is preserved: everything happens as
if CPSDefault had been explicitely excluded from ``include-bundle``.

Beware that ordering might be important in case of nested repos (see
the "deeper svn externals" section).


OPERATIONS
==========

hgmap <hg command> <arguments>
------------------------------

This is the simplest: map the given hg command over all hg clones
present in the directory. Completely unaware of the bundle definition.

This avoids making useless hgbundler commands for ``push``, ``pull``, ``update``

Also, this way, destructive operations like push/pull are explicit.

hgbundler make-clones
---------------------

This uses the server urls and the paths to clone all the involved
repositories in the bundle directory.
Automatically followed by ``update-clones``

hgbundler update-clones
-----------------------

Each clone is updated on the tag or branch specified in the
manifest.

Question: should this command also create missing clones ?

hgbundler clones-refresh-url
----------------------------

This refreshes clones default paths (specified
in ``.hg/hgrc`` files ) to base them on the bundle server urls.

Use cases: severe the link from the public server while making a private
bundle from a public one

Question: find a better name ?

hgbundler clones-out
--------------------

Indicates which of the managed clones have changesets not found on
server (logged at WARN level).

The differences with ```hgmap outgoing``` are:

 - output is much more limited (to the point where one must get down
   and perform a ``hg outgoing`` in the clone itself to get details).
 - all clones are taken into account, including deeper ones or those
   of which but a subdirectory is used in the bundle.

In short, if one wants to check that there's nothing to push without
being flooded by console output, this is the right tool

The repo being inspected is logged at DEBUG level, too.

hgbundler release-bundle <tag>
------------------------------
For a bundle that happens to be also its own mercurial repository or
inside one:

 - call release-clone for each of the <branch> clones
 - branch the bundle
 - modify the bundle manifest in the created branch to point to the
 new tags, commit
 - tag the bundle
 - close the release branch (TODO check repo compat with mercurial < 1.2)
 - get back to default branch of the bundle

Pushing the bundle to a reference repo is to be made manually afterwards.


Question: commands semantics in the case where there are several
bundles in a single repo (case for CPS-3-full, CPS-3-base, etc). Two
cases:
  - release each bundle
  - release just one
For now, the command releases just one, meaning that we have a problem
with tag unicity. This problem can be postponed. In the meanwhile, use
different tags to release each bundle, and for the final coordinated
release, make a new branch, merge all the release branches in that one
and set a global tag manually. Alternative solution : implement a
no-bundle tag.

hgbundler release-clone <clone name>
------------------------------------
*This is still experimental*

Uses the same rules as ``bm-product`` to issue a tag for the given
clone. Does a few checkings to avoid accidents:

 - the branch must have only one head (there's an option to force
   though).
 - there repo must be updated to a head a head: accidental
   releasing of a whole bundle from the previous tagged nodes can be a
   pain to fix. If you need to release from an intermediate state,
   make a branch first.
 - the current branch must be the one specified in the bundle
   manifest. TODO option to overcome that if releasing a single clone.
 - no uncommited local changes.
 - if CHANGES is empty, latest tag (read from VERSION) is recognized as made by
   hgbundler. If that's not the case, simply commit something in CHANGES.
 - empty CHANGES is acceptable only if the diff with previous tag is
 empty or the user requested a re-release (increment of release nr,
 not version number, typically because the release itself was faulty
 for some reason).

In the latest two cases, hgbundler will show the relevant ``hg log``
output if the checking fails.

Do hgbundler.py --help for the list of options

What the release process does is to issue three changesets:

 1. dump the contents of CHANGES into HISTORY, update VERSION with the
    new number, computed according to the contents of CHANGES and options
 2. Tag the previous changeset with the new version number
 3. Reinitialize CHANGES

In case the release is done from another branch than "default", the
branch name gets appended to the version number. Example: 3.39.1-unicode

Upon next release, the command expects to find these changesets in
order to check the diff from the latest tag (actually from changeset 3
above) in case CHANGES is empty.

Special note for first runs: hgbundler won't understand tags made by
``bm-product`` and converted from Subversion to Mercurial. In case
no changes have been made since such a tag, the user can simply put a
line in CHANGES stating for instance that this is for the first hgbundler run.

hgbundler archive <tag> <output_dir>
------------------------------------

Prepare an archive for the given bundle tag by calling ``hg archive``
on all repos. This archive is a directory, ready to be tarballed or
zipped.

TODO: provide true archives in .tgz and .zip formats.

Note: subrepos are correctly extracted and put at their right place
through this process. The ``.hg_archival.txt`` file is moved from the
full repo extraction to the subrepo. The directory holding clones for
subrepos is removed from the archive at the end of the process.

hgbundler make-bundle (Prio: 5)
-------------------------------

Create the manifest file in the current directory from the clones that
can be found there (choices tags/branches ?)

hgbundler bundleman-convert (Prio: 5)
-------------------------------------

Convert a bundleman svn bundle into a hgbundler one. Must probably
ship with a CPS-specific correspondence and accept not to convert a
few externals, leaving them to the user

EXAMPLE
=======

I have a project for my client, involving some CPS3 products, and
two specific products: CL1 and CL2.

Project Creation
----------------

 + Creation of the bundle on the dev machine
 + Creation of repos on the reference priv server




