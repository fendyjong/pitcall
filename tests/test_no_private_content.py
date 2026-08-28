"""The repository is public. This gate is the publishable half of what makes
that safe.

It runs first in CI and it landed before any other content, because a private
fact pushed to a public repo is not undone by deleting it in the next commit:
the object stays reachable and clones do not rewind.

WHAT THIS FILE IS NOT
---------------------
It is not the whole defence, and a reader who believes it is will publish
something. It carries only rules that are safe to publish - shapes, never
names. The project-specific vocabulary (the names of the private projects this
plugin was extracted from, their internal domains, their service inventory,
their database schema layout) is deliberately NOT here, because a denylist is
itself a disclosure: a list of private names sitting in a public repository
hands any reader more than the accidental paste it was written to prevent.
That list lives in the PRIVATE repository, as a pre-push check run against
this checkout before anything reaches the remote. Both halves are required and
only one of them can be published; this file is not a substitute for the other.

WHAT IT COVERS
--------------
RFC1918 and link-local IPv4 addresses; hostnames on the DNS suffixes that
resolve only inside a private network; a small set of credential-material
shapes (PEM private-key armour, GitHub tokens, prefixed API keys, AWS
access-key ids); and short decision-handle tokens. The rules themselves are
exact and are in the block below - read them, not this paragraph.

WHAT IT DOES NOT COVER
----------------------
Stated plainly, so nobody mistakes silence here for a clean bill of health:

- Names of any kind. A project, product, person, host or schema name passes
  this gate untouched. That is the private-side check's job, not this one's.
- Number-shaped identifiers. A rule for runs of eight or more digits was
  written and rejected: it fires on Unix timestamps in a vendored skill's
  worked examples and on the deliberately fake phone numbers in this
  repository's own test fixtures. A pre-push check run by a human can afford
  that noise; a required CI gate cannot, and a gate people routinely override
  is worse than one that admits its bound.
- A hostname whose name continues past the private suffix - the shape of
  `settings.local.json`, which is a filename this repository may legitimately
  document and cannot rename. The rule stops at the suffix for that reason.
- A committer deliberately obfuscating content to defeat it. This stops
  carelessness: a pasted path, a copied rule, a half-generalised sentence
  carrying a name it shouldn't. A repository whose contributors are
  adversaries has a problem no test file solves.

Deliberately blunt within that scope. A false positive costs one rename; a
false negative is permanent. Widen the rules when a new generic shape appears;
never narrow one to make a commit pass.

Design notes, each pinned to the bypass it closes:

- Scans the git BLOB for each tracked path (`git cat-file -p :<path>`), never
  `path.read_text()` on the working tree: a tracked symlink's blob content
  IS the literal target string, so a symlink pointed at a private path is
  caught by content, not silently followed to whatever that path holds.
- Content that fails to decode as UTF-8 FAILS the gate, deliberately, with no
  exemption path. "I could not read this" must never collapse into "this is
  clean" for a security check - and this repo is markdown, Python and JSON,
  so a binary is unreviewable by a lexical gate regardless. Rejecting it is
  correct, not incidental; weakening this is a decision for a future PR to
  make explicitly, not a case to special-case here.
- Both file content and the file's own relative path are scanned - a path
  is as public as its content once the object is pushed.
- Everything is lower-cased before matching (fixes a private name typed in a
  different case).
- Substring matching uses an ALLOW-list, not a deny-list of separators. An
  earlier version tried to enumerate "things that can split a token":
  literal whitespace, then Unicode format characters (category Cf) once a
  zero-width-space bypass was found, and Cf still left nonspacing marks
  (category Mn, e.g. COMBINING GRAPHEME JOINER U+034F) open. That is an
  infinite tail - Me, Cc and assorted Zs variants are still out there, and
  so is the next Unicode revision. Instead, `_KEEP` retains only the
  characters that can legitimately appear INSIDE a private identifier
  (`[a-z0-9._:/-]`) and drops everything else - visible or invisible,
  known today or added tomorrow - so nothing outside that set survives to
  separate a token. The same normalisation is applied to each needle, not
  just the haystack, which is what lets a needle contain an ordinary space
  or underscore without spelling a variant for each: it collapses the same
  way the haystack does. The accepted cost: this will occasionally fuse two
  adjacent ordinary words into a string that happens to match. That is the
  same trade already stated above - a false positive costs one rename, a
  false negative is permanent.
- Regex patterns are matched against the lower-cased text WITHOUT the
  allow-list collapse, because `\\b` word boundaries rely on the
  whitespace or punctuation that collapsing would delete: fusing a
  preceding word directly onto a short token erases the boundary the
  pattern depends on, turning a real match into a miss (this is exactly
  the defect an earlier version of this file introduced and then fixed).
  They are matched a second time against the allow-list collapsed copy too
  (an OR, not instead-of) purely for defense in depth against a pattern
  someone tries to split across a separator; that second pass can only add
  matches, never lose the one the first pass already guarantees.
  A CONSEQUENCE worth knowing before adding a rule: a pattern anchored with
  a leading `\b` is effectively raw-pass-only, because the collapse fuses the
  preceding word onto the token and erases that boundary. The address rule
  uses `(?<![0-9.])` instead for exactly this reason - it is the rule whose
  split-token case the second pass has to catch. A pattern containing `_` is
  likewise raw-pass-only, since the collapse drops underscores; that is
  accepted, because a zero-width character spliced INSIDE a pasted credential
  is the deliberate-obfuscation case this gate does not claim to cover.
- `test_enumeration_is_not_empty` guards the gate itself: a suite that
  checks zero files exits 0 and is indistinguishable from a suite that
  checked everything and found nothing.
- Nothing is exempt by filename. `LICENSE`, the vendored skills and this
  file's own path are all scanned. The ONLY exemption anywhere is the FIRST
  `denylist:start`/`denylist:end` marker pair in this file, holding the rule
  definitions themselves (they must contain the shapes they match). Only the
  first pair is stripped - a second pair appended later is left in place and
  scanned like any other content, so it cannot be used to smuggle in a fake
  "exempt" wrapper.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF_PATH = "tests/test_no_private_content.py"

# Generic shapes only - see the module docstring for why no project-specific
# vocabulary appears here, and where that half of the check lives instead.
# Matched against a lower-cased, allow-list-collapsed copy of both file
# content and file path.
# --- denylist:start ---
#: Literal needles. Both carry a space or an underscore, which is precisely
#: what the allow-list normalisation exists for: needle and haystack collapse
#: identically, so neither has to spell a separator variant.
DENY_SUBSTRINGS = [
    # The tail of a PEM private-key armour line, for every key type (RSA, EC,
    # PKCS#8, OPENSSH). The trailing dashes are what keep this off ordinary
    # prose about keys.
    "PRIVATE KEY-----",
    # Present essentially only where real AWS credentials have been pasted;
    # the variable name itself is public documentation.
    "AWS_SECRET_ACCESS_KEY",
]

DENY_PATTERNS = [
    # RFC1918 and link-local IPv4. Loopback is deliberately absent: it is the
    # same address on every machine, names nobody's network, and a vendored
    # skill's dev server binds it.
    #
    # Bounded by lookarounds rather than `\b`, and that is load-bearing: on the
    # collapsed haystack the preceding word fuses onto the address ("host is
    # 10.0.0.7" -> "hostis10.0.0.7"), which destroys a leading `\b` and with it
    # the whole point of the second pass. An address split by a zero-width or
    # combining character is caught only there, so this rule must survive the
    # fusion. `[0-9.]` is what could actually be part of an address, so
    # excluding just that still refuses to match inside a longer number.
    re.compile(r"(?<![0-9.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|169\.254)"
               r"\.\d{1,3}\.\d{1,3}(?![0-9.])"),
    # Hostnames on suffixes that resolve only inside a private network. The
    # lookahead stops at the suffix so `settings.local.json` - a filename, not
    # a host - does not fire; see the docstring for that trade.
    re.compile(r"\b[a-z0-9][a-z0-9-]*\.(?:local|internal|lan)\b(?!\.[a-z0-9])"),
    # Credential shapes. Each requires a body long enough that the prefix
    # alone cannot fire: GitHub tokens, prefixed API keys, AWS access-key ids.
    re.compile(r"\bgh[pousr]_[a-z0-9]{16,}\b"),
    re.compile(r"\bsk-[a-z0-9_-]{16,}\b"),
    re.compile(r"\bakia[a-z0-9]{16}\b"),
    # Decision handles, e.g. D3, D9, D23 (case-folded).
    re.compile(r"\bd\d{1,3}\b"),
]
# --- denylist:end ---

_DENYLIST_BLOCK = re.compile(r"# --- denylist:start ---.*?# --- denylist:end ---", re.S)

# Allow-list for the substring check: keep only what can legitimately appear
# INSIDE a private identifier (a host, a dotted address, a hyphenated slug, a
# decision handle). Everything else - visible or invisible, ASCII or not,
# a known Unicode separator category or one introduced after this was
# written - is dropped, so nothing outside this set can be used to split a
# token apart. See the module docstring for why this replaced an earlier
# deny-list-of-separator-classes approach.
_KEEP = re.compile(r"[^a-z0-9._:/-]+")


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\n") if f]


def blob_bytes(rel):
    """Read the git-tracked blob, not the working-tree file.

    A tracked symlink's blob content IS its target path string. Reading via
    path.read_text() instead would follow the link on disk and scan the
    WRONG file's content, missing the private path that is the actual thing
    that gets pushed to the object store.
    """
    result = subprocess.run(["git", "cat-file", "-p", f":{rel}"], cwd=ROOT,
                            capture_output=True, check=True)
    return result.stdout


def assert_clean(label, text):
    """Check a lower-cased copy against both the denylist substrings and the
    regex patterns.

    Substrings are checked with an ALLOW-list collapse applied to BOTH sides
    of the comparison - the haystack, so no separator (visible, invisible,
    or not yet invented) can be used to split a token apart; and each
    needle, so a needle containing a space or an underscore still matches
    instead of silently matching nothing, since it collapses exactly the way
    the haystack does. Collapsing can only ever ADD a match versus the
    uncollapsed text, never hide one, so this check alone is sufficient for
    substrings.

    Patterns are matched against the lower-cased text WITHOUT the allow-list
    collapse, because `\\b` word boundaries rely on the whitespace or
    punctuation that collapsing would delete: fusing a preceding word
    directly onto a short token erases the boundary the pattern depends on,
    turning a real match into a miss. They are matched a second time against
    the collapsed copy too (an OR, not instead-of) purely for defense in
    depth against a pattern someone tries to split across a separator; that
    second pass can only add matches, never lose the one the first pass
    already guarantees.
    """
    low = text.lower()
    collapsed = _KEEP.sub("", low)

    for needle in DENY_SUBSTRINGS:
        needle_key = _KEEP.sub("", needle.lower())
        assert needle_key not in collapsed, f"{label}: private token {needle!r}"

    for pat in DENY_PATTERNS:
        for haystack in (low, collapsed):
            m = pat.search(haystack)
            assert m is None, f"{label}: private pattern {m.group(0)!r}"


def test_enumeration_is_not_empty():
    """A gate that checks zero files exits 0 and looks identical, from the
    outside, to a gate that checked everything and found nothing."""
    files = tracked_files()
    assert len(files) >= 5, f"only {len(files)} tracked file(s) - the gate did not run"


@pytest.mark.parametrize("rel", tracked_files())
def test_file_carries_no_private_content(rel):
    raw = blob_bytes(rel)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Deliberate, not incidental: this repo is markdown, Python and
        # JSON. A binary file is unreviewable by a lexical gate, so failing
        # closed here is correct. If a real binary asset is ever needed,
        # that decision belongs in the PR that adds it - not in an
        # exemption carved into this gate.
        pytest.fail(
            f"{rel}: content is not valid UTF-8 - cannot verify it carries "
            "no private content, so it cannot be published"
        )

    if rel == SELF_PATH:
        # The only exemption anywhere: the FIRST denylist definitions block,
        # which must contain the shapes it matches. count=1 means a second
        # marker pair appended later is left in the scanned text and cannot
        # be used to smuggle content past this check.
        text = _DENYLIST_BLOCK.sub("", text, count=1)

    assert_clean(rel, text)
    assert_clean(f"{rel} (path)", rel)
