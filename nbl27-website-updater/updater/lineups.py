#!/usr/bin/env python3
"""NBL27 lineup stints from ESPN play-by-play (site.api.espn.com only).
Usage: python3 -I lineups.py <db_dir> <espn_dir> <out_dir> [ids...]
 db_dir/players/<id>.json : players docs (box scores) ; db_dir/shots/<id>.json : event list
 espn_dir/<id>.json        : cached summary (fetched if missing)
Writes out_dir/<id>.json lineup docs and prints validation summary.
Who is on the floor at each play comes from oncourt.py (one optimisation per team-game, guided by the official
box score). Set NBL_ONCOURT_CACHE to reuse solved games (rebuild.py does).
"""
import json, os, sys, glob, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oncourt  # noqa: E402
from oncourt import CLUBS, KEYS, TOV_TYPES, STATS, club, secs, plen, is_tov, sub_kind, order_plays, process  # noqa: E402,F401

API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nbl/summary?event={}"


def fetch(eid, espn_dir):
    fp = os.path.join(espn_dir, f"{eid}.json")
    if not os.path.exists(fp):
        with urllib.request.urlopen(API.format(eid), timeout=60) as r:
            open(fp, "wb").write(r.read())
    return json.load(open(fp))


def check_sub_order(d, pdoc):
    names = {p["id"]: p["name"] for pls in pdoc["teams"].values() for p in pls}
    good = bad = 0
    for pl in d["plays"]:
        if pl["type"]["text"] == "Substitution" and len(pl.get("participants", [])) == 2 and "in" in pl["text"]:
            nm = pl["text"].split(" substitution")[0]
            e, l = [x["athlete"]["id"] for x in pl["participants"]]
            if names.get(e) == nm: good += 1
            elif names.get(l) == nm: bad += 1
    return good, bad


def main():
    db, espn, out = sys.argv[1:4]
    ids = sys.argv[4:] or sorted(os.path.basename(f)[:-5] for f in glob.glob(os.path.join(db, "shots", "*.json")))
    os.makedirs(out, exist_ok=True); os.makedirs(espn, exist_ok=True)
    ids = [e for e in ids if os.path.exists(os.path.join(db, "players", f"{e}.json"))]
    swap = False
    if ids:
        g, b = check_sub_order(fetch(ids[0], espn), json.load(open(os.path.join(db, "players", f"{ids[0]}.json"))))
        swap = b > g
        print(f"SUB ORDER CHECK {ids[0]}: participants[0]=entering matches {g}, reversed matches {b} -> swap={swap}")
    for eid in ids:
        fetch(eid, espn)
    oncourt.presolve([(e, os.path.join(espn, f"{e}.json"), os.path.join(db, "players", f"{e}.json")) for e in ids], swap)
    nwritten = ninv = nskip = 0
    for eid in ids:
        d = fetch(eid, espn)
        pdoc = json.load(open(os.path.join(db, "players", f"{eid}.json")))
        res = process(eid, d, pdoc, swap, allow_solve=False)   # presolve() already solved all it could
        if res is None:
            print(eid, "SKIP no on-court solution this run (kept previous if any)"); nskip += 1; continue
        doc, reasons, issues, tov, ok = res
        if not ok:
            print(eid, "SKIP not NBL27 regular season"); continue
        json.dump(doc, open(os.path.join(out, f"{eid}.json"), "w"))
        nwritten += 1; ninv += bool(doc.get("invalid"))
        print(eid, doc["date"], doc["away"], "@", doc["home"], "OK" if not doc.get("invalid") else "INVALID: " + doc["invalid"], "|", tov, "| issues", issues)
    oncourt.save_cache()
    print(f"written {nwritten}, invalid {ninv}, skipped {nskip}")


if __name__ == "__main__":
    main()
