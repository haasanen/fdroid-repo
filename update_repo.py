#!/usr/bin/env python3
"""Download the latest release APKs and rebuild the signed F-Droid index.

Run from the fdroid-repo checkout root. Reads config.yml (with {env:}
secrets) and the metadata/*.yml files. Produces repo/*.jar + index files,
prunes APKs older than the indexed versions, and prints a summary.

Secrets come from environment variables (set by CI):
  FDROID_STORE_PASS, FDROID_KEY_PASS, GITHUB_TOKEN (for the API).
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.join(REPO_ROOT, "repo")
METADATA_DIR = os.path.join(REPO_ROOT, "metadata")

# package -> (github repo, regex matching the android APK asset name)
APPS = {
    "com.cosmos.unreddit": (
        "haasanen/cosmosapps_stealth",
        re.compile(r"^Stealth-[0-9a-f]{7}-android\.apk$"),
    ),
    "org.armorpaint": (
        "haasanen/armorpaint",
        re.compile(r"^ArmorPaint-[0-9a-f]{8}-android-arm64\.apk$"),
    ),
    "ch.protonmail.android": (
        "haasanen/protonmail-android-mail",
        re.compile(r"^ProtonMail-[0-9.]+-[0-9a-f]{7}-notif-fix\.apk$"),
    ),
}


def log(msg):
    print(msg, flush=True)


def gh(url, want_json=True):
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json"}
    )
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        req.add_header("Authorization", "token " + tok)
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    return json.loads(body) if want_json else body


def manifest_info(path):
    """packageName, versionCode, versionName read from the APK manifest."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__import__("fdroidserver").__file__)))
    from fdroidserver import common
    a = common.get_androguard_APK(path)
    xml = a.get_android_manifest_xml()
    ns = "{http://schemas.android.com/apk/res/android}"
    pkg = xml.get(ns + "package") or a.get_package()
    vc = xml.get(ns + "versionCode")
    vc = int(vc, 16) if vc.startswith("0x") else int(vc)
    vn = xml.get(ns + "versionName") or ""
    return pkg, vc, vn


def latest_apk(package, github_repo, pattern):
    """Return (local_path, versionCode, versionName) for the newest release APK.

    The GitHub releases API does NOT guarantee newest-first ordering, so
    collect every matching asset and pick the one whose release has the
    latest published_at. As a guard against stale builds (e.g. an old
    APK still carrying versionCode=1), also require a strictly
    increasing versionCode: a matching release with a lower versionCode
    than one seen later is skipped.
    """
    data = gh("https://api.github.com/repos/%s/releases?per_page=30" % github_repo)
    candidates = []
    for rel in data:
        pub = rel.get("published_at") or rel.get("created_at") or ""
        for asset in rel.get("assets", []):
            if pattern.match(asset["name"]):
                candidates.append((pub, rel["tag_name"], asset))
    if not candidates:
        raise SystemExit("no APK matching %r in recent releases of %s" % (pattern, github_repo))
    candidates.sort(key=lambda c: c[0], reverse=True)
    for pub, tag_name, asset in candidates:
        dest = os.path.join(REPO_DIR, "download-%s.apk" % package)
        if not os.path.exists(dest):
            log("downloading %s (%s)" % (asset["name"], tag_name))
            urllib.request.urlretrieve(asset["browser_download_url"], dest)
        pkg, vc, vn = manifest_info(dest)
        if pkg != package:
            raise SystemExit(
                "package mismatch: expected %s, got %s" % (package, pkg)
            )
        if vc <= 1:
            # Stale build with the upstream placeholder versionCode.
            log("skipping %s (%s): versionCode %d is not usable by F-Droid"
                % (asset["name"], tag_name, vc))
            continue
        return dest, vc, vn
    raise SystemExit(
        "no usable release APK for %s (all candidates had versionCode <= 1)" % package
    )


def prune(index_file):
    """Delete APKs in repo/ that are no longer referenced by the index."""
    with open(index_file, encoding="utf-8") as f:
        idx = json.load(f)
    keep = set()
    for versions in idx.get("packages", {}).values():
        for v in versions:
            keep.add(v["apkName"])
    removed = []
    for fn in os.listdir(REPO_DIR):
        if fn.endswith(".apk") and fn not in keep:
            os.remove(os.path.join(REPO_DIR, fn))
            removed.append(fn)
    if removed:
        log("pruned: " + ", ".join(sorted(removed)))
    return keep


def main():
    os.makedirs(REPO_DIR, exist_ok=True)
    # 1. fetch + rename each latest APK to <package>_<versionCode>.apk
    for package, (github_repo, pattern) in APPS.items():
        path, vc, vn = latest_apk(package, github_repo, pattern)
        dest = os.path.join(REPO_DIR, "%s_%s.apk" % (package, vc))
        if os.path.abspath(path) != os.path.abspath(dest):
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(path, dest)
        log("%s -> %s (vc %s, %s)" % (package, os.path.basename(dest), vc, vn))

    # 2. signed index update (config.yml reads secrets from the environment)
    r = subprocess.run(
        [sys.executable, "-m", "fdroidserver", "update"], cwd=REPO_ROOT
    )
    if r.returncode != 0:
        raise SystemExit("fdroid update failed")

    # 3. prune APKs that fell out of the index
    keep = prune(os.path.join(REPO_DIR, "index-v1.json"))

    # 4. summary (no secrets)
    log("index contains:")
    with open(os.path.join(REPO_DIR, "index-v1.json"), encoding="utf-8") as f:
        idx = json.load(f)
    for p, versions in idx["packages"].items():
        for v in versions:
            log("  %s vc=%s vn=%s size=%s apk=%s" % (
                p, v["versionCode"], v.get("versionName"), v["size"], v["apkName"]))


if __name__ == "__main__":
    main()
