#!/usr/bin/env python3
"""Claude page publisher. The Claude page (artifact) reads the same data files as the GitHub site, published
alongside it (data/*.json, data/photos/*.webp, ...). This works out which of those files changed since they were last
published, so the daily update republishes only those, and checks a published file against the repo afterwards.

  python3 claudepub.py plan <repo_root> <plan.json>     files to publish -> plan.json, and prints the Artifact `files` map
  python3 claudepub.py done <repo_root> <plan.json>     after a successful publish: record those files as published
  python3 claudepub.py verify <repo_root> <published path> <downloaded copy>   same bytes as the repo's file?

The record of what was published is nbl27-website-updater/claude_published.json ({published path: sha256}), kept in
the repo so every run starts from it. Paths in the `files` map are relative to the repo root (publish with
`root` = the repo folder). The page itself is nbl27-website-updater/claude_page.html (published as the artifact's
page, not as a file); plan.json says whether it changed.
"""
import hashlib, json, os, sys

MANIFEST = "nbl27-website-updater/claude_published.json"
PAGE = "nbl27-website-updater/claude_page.html"
DATA = ["games.json", "shots.json", "players.json", "lineups.json", "pshots.json", "events.json", "meta.json",
        "bios.json", "photos.json", "coachlineups.json"]
DIRS = ["photos", "coachphotos"]            # *.webp
MAX_FILES = 200                             # the Artifact tool takes at most 255 files per publish


def sha(fp):
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def wanted(root):
    out = {}
    for f in DATA:
        fp = os.path.join(root, "data", f)
        if os.path.exists(fp):
            out["data/" + f] = sha(fp)
    for d in DIRS:
        dp = os.path.join(root, "data", d)
        if os.path.isdir(dp):
            for f in sorted(os.listdir(dp)):
                if f.endswith(".webp"):
                    out[f"data/{d}/{f}"] = sha(os.path.join(dp, f))
    return out


def load_manifest(root):
    fp = os.path.join(root, MANIFEST)
    return json.load(open(fp)) if os.path.exists(fp) else {}


def plan(root, out):
    have, want = load_manifest(root), wanted(root)
    changed = {p: s for p, s in want.items() if have.get(p) != s}
    gone = sorted(p for p in have if p not in want and p != "page")
    page_sha = sha(os.path.join(root, PAGE))
    page_changed = have.get("page") != page_sha
    paths = sorted(changed)
    # files map(s) for the Artifact tool: {published path: source path relative to the repo root}; null removes a file
    chunks, cur = [], {}
    for p in paths:
        cur[p] = p
        if len(cur) == MAX_FILES:
            chunks.append(cur); cur = {}
    for p in gone:
        cur[p] = None
        if len(cur) == MAX_FILES:
            chunks.append(cur); cur = {}
    if cur or not chunks:
        chunks.append(cur)
    res = {"page": PAGE, "page_changed": page_changed, "page_sha": page_sha, "chunks": chunks,
           "record": {**changed, **{p: None for p in gone}}}
    json.dump(res, open(out, "w"), indent=1)
    n = len(paths) + len(gone)
    print(f"{n} file(s) to publish ({len(paths)} new or changed, {len(gone)} to remove); page changed: {page_changed}")
    for i, c in enumerate(chunks):
        if c:
            print(f"--- files map {i + 1} of {len(chunks)} ({len(c)} files):")
            print(json.dumps(c))


def done(root, planfile):
    pl = json.load(open(planfile))
    have = load_manifest(root)
    for p, s in pl["record"].items():
        if s is None:
            have.pop(p, None)
        else:
            have[p] = s
    have["page"] = pl["page_sha"]
    json.dump(dict(sorted(have.items())), open(os.path.join(root, MANIFEST), "w"), indent=0)
    print(f"recorded {len(pl['record'])} file(s) and the page as published")


def verify(root, path, copy):
    a, b = sha(os.path.join(root, path)), sha(copy)
    print(("OK " if a == b else "MISMATCH ") + path + f" (repo {a[:12]}, published {b[:12]})")
    sys.exit(0 if a == b else 1)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "plan":
        plan(sys.argv[2], sys.argv[3])
    elif cmd == "done":
        done(sys.argv[2], sys.argv[3])
    elif cmd == "verify":
        verify(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
