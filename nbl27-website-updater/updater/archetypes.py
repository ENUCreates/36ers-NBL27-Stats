#!/usr/bin/env python3
"""Player archetypes: the Archetypes tab (data/archetypes.json). Ten styles of NBL player, fitted once on every
player-season from NBL19 to NBL26 (archetypes_build.py -> archetypes_model.json, with every past player-season's
group); each morning every NBL27 player with 100+ minutes is placed in the nearest one from his numbers this
season, measured exactly as the model's were (archetypes_build.features): the box-score rates per 100 possessions
behind the Impact tab (data/impact.json) and, from the NBL's own play-by-play (this run's official data, as
impact.py reads it), his shots by zone (nbl_shots.py's rules), dunks and lobs, hooks, and how many of his made twos
and threes were assisted. Each is a z-score against this season's players averaging 15+ minutes.

Each placed player-season: [group, next-nearest group, fit, his 18 numbers]. fit = 1 - (distance to his group) /
(distance to the next one): near 0 he is between the two.

    python3 archetypes.py <repo_root>        (standalone: reads the official data itself; rebuild.py calls run())
"""
import collections, datetime, json, os, sys, tempfile
from zoneinfo import ZoneInfo
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archetypes_build as AB  # noqa: E402
import nbl_shots as NS  # noqa: E402

MODEL = os.path.join(HERE, "archetypes_model.json")
CUR = "2026-27"


def counts(root, off, pairs):
    """{player key: shot counts} for NBL27 from the NBL's play-by-play, players keyed as impact.py keys them"""
    players = json.load(open(os.path.join(root, "data", "players.json")))
    espn_of = {v["pid"]: k for k, v in pairs.items() if v.get("pid")}
    key_of = {e: AB.nkey(v.get("name")) for e, v in pairs.items() if v.get("name")}
    for d in players:
        for L in d["teams"].values():
            for x in L:
                key_of.setdefault(x["id"], AB.nkey(x["name"]))
    C = collections.defaultdict(collections.Counter)
    games = 0
    for m in (off.get("pbp") or {}).values():
        plays = m.get("plays") or []
        if not plays:
            continue
        games += 1
        nm = {p["pid"]: p["name"] for p in m.get("players") or []}
        kk = lambda pid: key_of.get(espn_of.get(pid)) or AB.nkey(nm.get(pid, str(pid)))
        E = NS.ends(m)
        keep, _ = NS.clean(m)
        akey = {(a.get("team"), NS.per_no(a), a.get("clock")) for a in plays if (a.get("type") or "").lower() == "assist"}
        for i in keep:
            a = plays[i]
            three = (a.get("type") or "").lower() == "3pt"
            c = C[kk(a.get("pid"))]
            s = AB.shot_type(a.get("sub"))
            c["fga"] += 1
            c["fin"] += s in AB.FIN
            if not three:
                c["pa2"] += 1
                c["hook"] += s == "hookshot"
            if a.get("ok"):
                ast = (a.get("team"), NS.per_no(a), a.get("clock")) in akey
                c["m3" if three else "m2"] += 1
                c["a3" if three else "a2"] += ast
            X, Y = NS.frame(a.get("x"), a.get("y"), E.get((a.get("team"), NS.half(a)), "lo"))
            z = NS.zone(three, X, Y)
            if z != "unk":
                c["zfga"] += 1
                c[z] += 1
                c["z3"] += z in ("c3", "ab3")
    return C, games


def run(root, off, pairs):
    M = json.load(open(MODEL))
    imp = json.load(open(os.path.join(root, "data", "impact.json")))
    if CUR not in imp.get("seasons", {}):
        raise RuntimeError("no NBL27 players in data/impact.json")
    C, games = counts(root, off, pairs)
    rows = AB.season_rows(imp, CUR, C)
    raw, norm = AB.features(rows)
    cents = np.array([g["centroid"] for g in M["groups"]])
    cur = {}
    for r in rows:
        if r["mn"] >= M["minMinutes"] and r["zfga"] > 0:
            g1, g2, fit = AB.assign(AB.vec(raw[r["key"]], norm), cents)
            cur[r["key"]] = [g1, g2, fit, [round(raw[r["key"]][f], 3) for f in AB.FEATS]]
    seasons = {s: v for s, v in M["hist"].items()}
    seasons[CUR] = cur
    now = datetime.datetime.now(ZoneInfo("Australia/Sydney"))
    doc = {"status": {"computedAt": now.isoformat(timespec="seconds"), "through": imp["status"].get("through"),
                      "games": games, "placed": len(cur), "built": M["built"], "trained": M["trained"],
                      "first": AB.FIRST, "k": M["k"], "stability": M["stability"], "minMpg": M["minMpg"],
                      "minMinutes": M["minMinutes"], "source": M["source"]},
            "feats": M["feats"],
            "groups": [{k: g[k] for k in ("id", "key", "name", "kind", "desc", "n", "profile", "sig", "sigDir")}
                       for g in M["groups"]],
            "seasons": seasons}
    fp = os.path.join(root, "data", "archetypes.json")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(fp), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, fp)
    by = collections.Counter(M["groups"][v[0]]["name"] for v in cur.values())
    yield (f"archetypes: {len(cur)} NBL27 players placed from {games} games' play-by-play; "
           + ", ".join(f"{n} {c}" for n, c in by.most_common()))


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    import official as O
    cache = os.environ.get("NBL_OFFICIAL_CACHE") or tempfile.mkdtemp()
    off = O.load(cache)
    D = lambda n: json.load(open(os.path.join(root, "data", n + ".json")))
    cmp = O.compare(D("games"), D("players"), off)
    for line in run(root, off, cmp["pairs"]):
        print(line)


if __name__ == "__main__":
    main()
