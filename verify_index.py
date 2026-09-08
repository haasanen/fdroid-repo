#!/usr/bin/env python3
"""Verify the signed index the F-Droid client will consume.

Replicates the client's IndexV1Verifier contract:
  - index-v1.jar is a JAR containing index-v1.json
  - a single code signer, single certificate
  - the index-v1.json manifest entry carries a SHA1-Digest or SHA-256-Digest
  - every APK referenced by the index exists in repo/
  - both expected packages are present
Exits non-zero on any failure. No secrets.
"""
import json
import os
import re
import sys
import zipfile

REPO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repo")
JAR = os.path.join(REPO_DIR, "index-v1.jar")
EXPECTED = ["com.cosmos.unreddit", "org.armorpaint", "ch.protonmail.android"]
SUPPORTED_DIGESTS = ("SHA1-Digest", "SHA-256-Digest")


def fail(msg):
    print("VERIFY FAILED:", msg)
    sys.exit(1)


def section_attrs(sf_text, entry):
    """Return the attribute lines for one 'Name: <entry>' block in a .SF file.

    .SF format: 'Name: <file>' at column 0, followed by indented or
    'Key: value' attribute lines, terminated by a blank line.
    """
    lines = sf_text.splitlines()
    attrs = []
    in_entry = False
    for line in lines:
        if not line.strip():
            if in_entry:
                break
            continue
        if line.startswith("Name: "):
            in_entry = line[len("Name: "):].strip() == entry
            continue
        if in_entry and ":" in line:
            attrs.append(line)
    return attrs


def main():
    if not os.path.exists(JAR):
        fail("index-v1.jar missing")
    z = zipfile.ZipFile(JAR)
    names = z.namelist()

    if "index-v1.json" not in names:
        fail("index-v1.json not in jar")

    sf = [n for n in names if re.match(r"^META-INF/.*\.SF$", n)]
    sig = [n for n in names if re.match(r"^META-INF/.*\.(RSA|DSA|EC)$", n)]
    if len(sf) != 1 or len(sig) != 1:
        fail("expected exactly one .SF and one signature block, got %s / %s" % (sf, sig))

    sfdata = z.read(sf[0]).decode("utf-8")
    attrs = section_attrs(sfdata, "index-v1.json")
    keys = {a.split(":", 1)[0] for a in attrs}
    print("index-v1.json manifest attrs:", sorted(keys))
    if not (keys & set(SUPPORTED_DIGESTS)):
        fail("index-v1.json entry has no supported digest (need %s)" % (SUPPORTED_DIGESTS,))

    data = json.loads(z.read("index-v1.json"))
    pkgs = list(data["packages"].keys())
    print("packages in index:", pkgs)
    for p in EXPECTED:
        if p not in pkgs:
            fail("expected package %s not in index" % p)
    for p, versions in data["packages"].items():
        for v in versions:
            apk = os.path.join(REPO_DIR, v["apkName"])
            if not os.path.exists(apk):
                fail("referenced APK missing: %s" % v["apkName"])
            if "signer" not in v:
                fail("package %s has no signer fingerprint" % p)
            print("  %s vc=%s vn=%s size=%s signer OK" % (
                p, v["versionCode"], v.get("versionName"), v["size"]))

    if not os.path.exists(os.path.join(REPO_DIR, "entry.json")):
        fail("entry.json missing")

    print("VERIFY OK")


if __name__ == "__main__":
    main()
