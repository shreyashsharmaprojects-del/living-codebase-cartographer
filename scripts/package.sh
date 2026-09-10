#!/bin/sh
# Package living-codebase-cartographer for distribution.
# This script is the ONLY path that produces a release archive.
# Stdlib-only, POSIX sh.
set -eu
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-/tmp/living-codebase-cartographer.tar.gz}"
cd "$SKILL_DIR"
# RELEASE.json is the release marker: only archives produced by this script
# carry it (built via a witness file so it lands inside the tarball but
# never remains in the source tree). It records the tool version + build
# time for traceability, plus the source tree hash the release was built
# from — so a re-tar of an unpacked release is detectable: its content
# hash won't match the hash the marker commits to. (A marker that merely
# says "I am a release" proves nothing once it ships inside the release
# as an ordinary file.)
STAMP="$(python3 -c "import sys; sys.path.insert(0, '.'); \
from analyzers.graph import VERSION; print(VERSION)" 2>/dev/null || echo ?)"
# Hash BEFORE the witness exists: tree_hash commits to exactly the file
# set the archive ships (minus the marker itself), so anyone can
# recompute it over an unpacked release (excluding RELEASE.json) and
# detect a re-tar or tampering. The walk mirrors tar --exclude-vcs
# (.git and friends are in the hash walk's prune set too — otherwise
# the hash would cover files the archive never ships).
TREE_HASH="$(python3 -c "
import hashlib, os
h = hashlib.sha256()
names = []
for dp, dn, fn in os.walk('.'):
    dn[:] = sorted(d for d in dn
                   if d not in ('__pycache__', '.git', '.codebase-map'))
    for f in sorted(fn):
        if f.endswith(('.pyc', '.pyo')) or f.startswith('.git'):
            continue
        p = os.path.join(dp, f)
        names.append(p)
for p in sorted(names):
    h.update(p.encode())
    with open(p, 'rb') as fh:
        h.update(fh.read())
print(h.hexdigest()[:16])
")"
python3 -c "
import json, datetime
json.dump({'built_by': 'scripts/package.sh',
           'graph_version': '$STAMP',
           'tree_hash': '$TREE_HASH',
           'built_at': datetime.datetime.now(
               datetime.timezone.utc).isoformat(timespec='seconds')},
          open('RELEASE-WITNESS-REMOVE-ME', 'w'))"
tar --exclude-vcs \
    --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='*.pyo' \
    --transform='s/RELEASE-WITNESS-REMOVE-ME/RELEASE.json/' \
    -czf "$OUT" .
rm -f RELEASE-WITNESS-REMOVE-ME
echo "packaged -> $OUT"
tar -tzf "$OUT" | grep -E '__pycache__|\.pyc' \
    && { echo "FAIL: bytecode leaked into archive"; exit 1; } \
    || echo "verified: no bytecode in archive"
# A shipped release must pass its own test suite. Unpack to a pristine
# staging dir (never the source tree) and run both suites there: this
# catches missing files (tests import from the tree) as well as real
# regressions, and keeps bytecode out of the working tree.
# CARTO_SELF_TEST=0 skips this (used by the packaging regression test,
# which would otherwise recurse: test -> package.sh -> suite -> test).
if [ "${CARTO_SELF_TEST:-1}" = "1" ]; then
STAGE="$(mktemp -d)"
tar -xzf "$OUT" -C "$STAGE"
(cd "$STAGE" && python3 tests/run_tests.py) || \
    { echo "FAIL: release self-test (run_tests) failed"; exit 1; }
(cd "$STAGE" && python3 tests/run_viz_tests.py) || \
    { echo "FAIL: release self-test (viz) failed"; exit 1; }
rm -rf "$STAGE"
echo "verified: release passes its own suite"
fi
