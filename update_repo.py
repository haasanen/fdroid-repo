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
    "net.haasanen.calendar": (
        "haasanen/aosp-calendar",
        re.compile(r"^Calendar-[0-9a-f]{7}-android\.apk$"),
    ),
    "org.armorpaint": (
        "haasanen/armorpaint",
        re.compile(r"^ArmorPaint-[0-9a-f]{8}-android-arm64\.apk$"),
    ),
    "ch.protonmail.android": (
        "haasanen/protonmail-android-mail",
        re.compile(r"^ProtonMail-[0-9.]+-[0-9a-f]{7}-notif-fix\.apk$"),
    ),
    "org.mozilla.fennec_fdroid": (
        "haasanen/fennecbuild",
        re.compile(r"^Fennec-[0-9]+-[0-9a-f]{7}\.apk$"),
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
    # Pre-scan: the highest versionCode among ALL candidates. A build is only
    # "stale" if a HIGHER versionCode release exists. This still skips old
    # vc=1 placeholder builds shadowed by a real vc>1 release, but allows a
    # brand-new app whose first (and only) release legitimately has vc=1.
    max_vc = -1
    for pub, tag_name, asset in candidates:
        dest = os.path.join(REPO_DIR, "download-%s.apk" % package)
        if not os.path.exists(dest):
            log("downloading %s (%s)" % (asset["name"], tag_name))
            urllib.request.urlretrieve(asset["browser_download_url"], dest)
        _pkg, vc, _vn = manifest_info(dest)
        max_vc = max(max_vc, vc)
    for pub, tag_name, asset in candidates:
        dest = os.path.join(REPO_DIR, "download-%s.apk" % package)
        pkg, vc, vn = manifest_info(dest)
        if pkg != package:
            raise SystemExit(
                "package mismatch: expected %s, got %s" % (package, pkg)
            )
        if vc < max_vc:
            # Stale build shadowed by a higher-versionCode release.
            log("skipping %s (%s): versionCode %d < max %d (stale)"
                % (asset["name"], tag_name, vc, max_vc))
            continue
        return dest, vc, vn
    raise SystemExit(
        "no usable release APK for %s" % package
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


def current_indexed_apk(package):
    """apkName of `package` in the last built index, if any (None otherwise).

    Lets main() keep serving an app whose source repo temporarily has no
    usable release, instead of dropping it (or, before the Fennec
    onboarding, aborting the whole index for every app).
    """
    idx_path = os.path.join(REPO_DIR, "index-v1.json")
    if not os.path.exists(idx_path):
        return None
    try:
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
        versions = idx.get("packages", {}).get(package, [])
        if versions:
            return versions[0].get("apkName")
    except (ValueError, KeyError, OSError):
        pass
    return None


def main():
    os.makedirs(REPO_DIR, exist_ok=True)
    # 1. fetch + rename each latest APK to <package>_<versionCode>.apk
    # A source repo without a usable release must NOT take the other apps
    # down: keep the app's already-indexed APK if there is one, else skip it.
    latest = {}
    for package, (github_repo, pattern) in APPS.items():
        try:
            path, vc, vn = latest_apk(package, github_repo, pattern)
        except SystemExit as e:
            kept = current_indexed_apk(package)
            if kept and os.path.exists(os.path.join(REPO_DIR, kept)):
                log("WARNING: %s: %s — keeping existing indexed APK %s"
                    % (package, e, kept))
                latest[package] = kept
                continue
            log("WARNING: %s: %s — not in index, skipping" % (package, e))
            continue
        dest = os.path.join(REPO_DIR, "%s_%s.apk" % (package, vc))
        if os.path.abspath(path) != os.path.abspath(dest):
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(path, dest)
        latest[package] = os.path.basename(dest)
        log("%s -> %s (vc %s, %s)" % (package, os.path.basename(dest), vc, vn))

    # 1b. delete every APK that is not the current latest for its package.
    # fdroidserver indexes ALL apks in repo/, so without this the index
    # would keep accumulating old versions (and disk usage on Pages grows
    # without bound — with a 100 MB Proton APK that matters).
    for fn in os.listdir(REPO_DIR):
        if not fn.endswith(".apk"):
            continue
        pkg, _, _ = fn.rpartition("_")
        if latest.get(pkg) != fn:
            os.remove(os.path.join(REPO_DIR, fn))
            log("removed stale apk: %s" % fn)

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
