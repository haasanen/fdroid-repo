# haasanen F-Droid repo

A custom [F-Droid](https://f-droid.org) repository serving **Stealth**
(`com.cosmos.unreddit`) and **ArmorPaint** (`org.armorpaint`) with automatic,
signature-verified updates.

## Repository URL (add in the F-Droid app)

```
https://haasanen.github.io/fdroid-repo/repo
```

F-Droid → menu → *Repositories* → *Add repository* → paste the URL.
On first add, the app shows the signing-certificate fingerprint below —
confirm it matches before trusting the repo:

```
7A:69:EF:F8:58:84:B7:9E:61:7D:5E:A5:36:1D:C0:55:38:06:49:5A:0D:B4:59:1F:18:F8:56:E9:EA:F4:3B:97
```

After that, updates are applied automatically: F-Droid downloads the signed
index (`index-v1.jar`) and installs newer versionCodes.

## How it works

- **No backend.** The F-Droid client pulls static files over HTTPS; GitHub
  Pages serves them. The change history of the served files is the commit
  history of this repo (each run = one commit).
- **APKs** are the release artifacts of the source repos' own CI pipelines
  (Stable release keys, so F-Droid's per-app signer check passes across
  updates):
  - Stealth: `haasanen/cosmosapps_stealth` (versionCode increments per release)
  - ArmorPaint: `haasanen/armorpaint` (versionCode is date-based)
- **Index signing** uses the repo keystore (`repo.jks`, secret, never
  committed). `fdroid update` (fdroidserver) generates `index-v1.jar`,
  `index.jar`, `entry.json`, `index-v2.json` + the web listing. JDK-only
  (jarsigner) — no Android SDK needed.

## Automation (push + poll)

`.github/workflows/update.yml` rebuilds the index when:
1. **push** — a source repo's release CI dispatches it via the GitHub API
   right after publishing (see the `Notify F-Droid repo` step in those
   workflows), or
2. **poll** — weekly on Monday 04:30 UTC (safety net).

Each run: download the latest release APK per app → `fdroid update`
(signed) → verify the client contract → deploy to GitHub Pages.

## Adding Proton Mail fork (later)

Blocked until its CI injects a monotonic `versionCode` at build time
(upstream's is permanently `1`, which F-Droid cannot use for update
detection). Then: add `ch.protonmail.android` to `update_repo.py` APPS,
add `metadata/ch.protonmail.android.yml`, done.

## Secrets (GitHub)

| Secret | Meaning |
| --- | --- |
| `REPO_KEYSTORE_BASE64` | base64 of `repo.jks` (repo signing keystore) |
| `REPO_STORE_PASS` | keystore password |
| `REPO_KEY_PASS` | key password |

Backups live outside this repo (private). Losing the keystore means the
F-Droid app would treat the next index as a *different* repository and
refuse it — keep the backup safe.
