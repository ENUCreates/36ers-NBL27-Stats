"""Make the Claude page's database an exact copy of the verified website data (data/*.json).

Usage:
  python3 dbsync.py <repo_root> <db_export_dir> <out_dir> [versions.json]

<db_export_dir> is where ArtifactData "list ... out_dir" saved the database (<dir>/<collection>/<doc_id>.json,
data objects only). The script compares every document and writes ArtifactData batch files to <out_dir>:
  batch_01.json, batch_02.json, ...  each a JSON list of writes (max 40 per batch) ready for ArtifactData
  action "batch" (pass the list as `writes`). Document bodies are written to <out_dir>/docs/.
Writes to documents that already exist need their current version: the first run lists them in
<out_dir>/needs_versions.json; put {"<collection>/<doc_id>": <version>, ...} in versions.json and run again.
Doc ids: games -> the game's "id"; shots/players/lineups/pshots/events -> "espnId";
meta -> status, shots_status, players_status, lineups_status, pshots_status, events_status.
"""
import json, os, sys, glob

COLS = ["games", "shots", "players", "lineups", "pshots", "events"]
META = ["status", "shots_status", "players_status", "lineups_status", "pshots_status", "events_status"]


def main():
    root, exp, out = sys.argv[1:4]
    versions = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else None
    os.makedirs(os.path.join(out, "docs"), exist_ok=True)
    want = {}
    for c in COLS:
        for d in json.load(open(os.path.join(root, "data", c + ".json"))):
            want[(c, d["id"] if c == "games" else d["espnId"])] = d
    meta = json.load(open(os.path.join(root, "data", "meta.json")))
    for k in META:
        if k in meta:
            want[("meta", k)] = meta[k]
    have = {}
    for c in COLS + ["meta"]:
        for f in glob.glob(os.path.join(exp, c, "*.json")):
            i = os.path.basename(f)[:-5]
            if c == "meta" and i not in META:
                continue
            have[(c, i)] = json.load(open(f))
    writes, need = [], []
    for (c, i), d in sorted(want.items()):
        if (c, i) in have and have[(c, i)] == d:
            continue
        fp = os.path.abspath(os.path.join(out, "docs", f"{c}__{i}.json"))
        json.dump(d, open(fp, "w"), separators=(",", ":"))
        w = {"op": "set", "collection": c, "doc_id": i, "file_path": fp}
        if (c, i) in have:
            need.append(f"{c}/{i}")
            if versions is not None:
                w["if_version"] = versions[f"{c}/{i}"]
        writes.append(w)
    for (c, i) in sorted(have):
        if (c, i) not in want:
            need.append(f"{c}/{i}")
            w = {"op": "delete", "collection": c, "doc_id": i}
            if versions is not None:
                w["if_version"] = versions[f"{c}/{i}"]
            writes.append(w)
    json.dump(need, open(os.path.join(out, "needs_versions.json"), "w"), indent=0)
    for f in glob.glob(os.path.join(out, "batch_*.json")):
        os.remove(f)
    if versions is None and need:
        print(f"{len(writes)} writes; {len(need)} existing docs need versions -> {out}/needs_versions.json")
        return
    n = 0
    for k in range(0, len(writes), 40):
        n += 1
        json.dump(writes[k:k + 40], open(os.path.join(out, f"batch_{n:02d}.json"), "w"), indent=0)
    print(f"{len(writes)} writes in {n} batch file(s) ({sum(w['op'] == 'delete' for w in writes)} deletes)")


if __name__ == "__main__":
    main()
