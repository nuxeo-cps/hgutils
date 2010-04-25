# looks for revisions whose parent is -1 and strip them
# This will typically be the Producs reorg from svn 
for x in `ls`; do (cd $x; hg heads | grep -B1 "\-1" | grep changeset | sed "s/^changeset[:].*[:]//g"|xargs hg strip) done
