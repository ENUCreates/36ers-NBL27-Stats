#!/usr/bin/env python3
"""The fixed half of the Archetypes tab: the archetypes themselves, fitted once on NBL19 to NBL26, in
updater/archetypes_model.json. One-off, like impact_hist.py: the daily update (archetypes.py) places every NBL27
player in one of them each morning. Run it again only to refit (for example after a season ends).

    python3 archetypes_build.py <repo_root>     (NBL_PBP_PKL may name a pickle of the play-by-play table, as for
                                                 impact_hist.py; NBL_SHOTS_RDS a download of shots/shots.rds)

Data: the official NBL files republished by nblR. Every regular-season game from 2018-19 (NBL19): the play-by-play
(pbp/pbp.rds, read with coaches/rdsread.py) gives each field-goal attempt's shooter, shot type and whether it was
assisted (an assist logged by the same team at the same period and clock as a made shot); shot positions come from
shots/shots.rds up to 2024-25 and from the play-by-play itself from 2025-26 (zones by nbl_shots.py's rules, the ones
the site's shot charts use). The NBL's scorers have logged detailed shot types (pull-ups, drives, hooks ...) only
since NBL19, so the archetypes start there. Box-score rates per 100 possessions come from data/impact.json.

Style only, never results: 18 numbers per player-season, each as a z-score against that season's players averaging
15+ minutes, weighted so each idea counts about once (creating vs finishing, where he shoots, how he scores inside,
size, activity). Shares are pulled toward the season's league share by a few attempts (SHRINK), so a small sample
looks typical. k-means, k = 10 (from 6-13, the most groups whose split still made basketball sense and held up when
the player-seasons were resampled), 100 starts, fixed seed. Each player-season then belongs to its nearest group.
"""
import collections, datetime, gzip, json, os, pickle, re, sys, unicodedata
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "coaches"))
import nbl_shots as NS  # noqa: E402

OUT = os.path.join(HERE, "archetypes_model.json")
FIRST = "2018-19"
K = 10
MIN_MPG, MIN_MIN = 15.0, 100.0          # the z-score population; who gets an archetype
FIN = {"dunk", "alleyoop", "alleyoopdunk", "tipindunk"}
FEATS = ["usage", "ast", "tov", "tpar", "ftr", "oreb", "dreb", "blk", "stl", "pf",
         "ra", "paint", "mid", "c3of3", "fin", "hook2", "ast2", "ast3"]
W = {"usage": 1, "ast": 1, "tov": .5, "ast2": .8, "ast3": .8,          # creating vs finishing
     "tpar": .8, "ra": .6, "paint": .6, "mid": 1, "c3of3": .7,         # where he shoots
     "ftr": .8, "fin": .7, "hook2": .7,                                # how he scores inside
     "oreb": .8, "dreb": .8, "blk": .8,                                # size: boards and rim protection
     "stl": .6, "pf": .4}
SHRINK = {"ra": 25, "paint": 25, "mid": 25, "c3of3": 15, "fin": 25, "hook2": 20, "ast2": 15, "ast3": 10}
# share features: (numerator count, denominator count)
SHARES = {"ra": ("ra", "zfga"), "paint": ("paint", "zfga"), "mid": ("mid", "zfga"), "c3of3": ("c3", "z3"),
          "fin": ("fin", "fga"), "hook2": ("hook", "pa2"), "ast2": ("a2", "m2"), "ast3": ("a3", "m3")}
COUNTS = ["fga", "zfga", "ra", "paint", "mid", "c3", "ab3", "z3", "fin", "hook", "pa2", "m2", "a2", "m3", "a3"]


def nkey(first, family=""):
    s = unicodedata.normalize("NFKD", f"{first or ''} {family or ''}").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def shot_type(s):
    s = re.sub(r"[^a-z]", "", str(s or "").lower())
    return "" if s in ("nan", "none") else s


def features(rows):
    """rows: one season's player dicts with 'mn', 'g', the box rates per 100 possessions ('r': fga, tpa, fta, ast,
    tov, oreb, dreb, stl, blk, pf) and the shot counts (COUNTS). -> {key: raw feature dict}, {feature: (mean, sd)}
    over the season's players averaging MIN_MPG+ minutes (the z-score population)."""
    pop = [r for r in rows if r["g"] and r["mn"] / r["g"] >= MIN_MPG]
    lg = {f: sum(r[a] for r in pop) / max(1, sum(r[b] for r in pop)) for f, (a, b) in SHARES.items()}
    raw = {}
    for r in rows:
        b = r["r"]
        x = {"usage": b["fga"] + 0.44 * b["fta"] + b["tov"], "ast": b["ast"], "tov": b["tov"],
             "tpar": b["tpa"] / b["fga"] if b["fga"] else 0.0, "ftr": b["fta"] / b["fga"] if b["fga"] else 0.0,
             "oreb": b["oreb"], "dreb": b["dreb"], "blk": b["blk"], "stl": b["stl"], "pf": b["pf"]}
        for f, (a, d) in SHARES.items():
            x[f] = (r[a] + SHRINK[f] * lg[f]) / (r[d] + SHRINK[f])
        raw[r["key"]] = x
    keys = [r["key"] for r in pop]
    norm = {f: (float(np.mean([raw[k][f] for k in keys])), float(np.std([raw[k][f] for k in keys], ddof=1))) for f in FEATS}
    return raw, norm


def vec(x, norm):
    return np.array([W[f] * (x[f] - norm[f][0]) / (norm[f][1] or 1.0) for f in FEATS])


def assign(v, cents):
    d = np.linalg.norm(cents - v, axis=1)
    o = np.argsort(d)
    return int(o[0]), int(o[1]), round(float(1 - d[o[0]] / d[o[1]]), 3)


def counts_nblr(pbp, shots, reg):
    """-> {season: {key: Counter}} from the nblR play-by-play (every shot) and shot positions"""
    sid = lambda x: str(int(x)) if isinstance(x, float) and x == x else str(x)
    seas = lambda s: str(s)[:5] + str(s)[-2:]
    pbp = pbp.assign(mid=pbp["match_id"].map(sid), at=pbp["action_type"].astype(str), season=pbp["season"].astype(str).map(seas))
    pbp = pbp[pbp["mid"].isin(reg) & (pbp["season"] >= FIRST)]
    for c in ("team_name", "period_type", "gt", "first_name", "family_name"):
        pbp[c] = pbp[c].astype(str)
    a = pbp[pbp["at"] == "assist"]
    akey = set(zip(a["mid"], a["team_name"], a["period_type"], a["period"], a["gt"]))
    fg = pbp[pbp["at"].isin(["2pt", "3pt"])]
    C = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for r in fg[["season", "mid", "team_name", "period_type", "period", "gt", "at", "sub_type", "success",
                 "first_name", "family_name"]].itertuples(index=False):
        c = C[r.season][nkey(r.first_name, r.family_name)]
        three, made, s = r.at == "3pt", r.success == 1, shot_type(r.sub_type)
        c["fga"] += 1
        c["fin"] += s in FIN
        if not three:
            c["pa2"] += 1
            c["hook"] += s == "hookshot"
        if made:
            ast = (r.mid, r.team_name, r.period_type, r.period, r.gt) in akey
            c["m3" if three else "m2"] += 1
            c["a3" if three else "a2"] += ast
    # positions: shots.rds to 2024-25, the play-by-play's own from 2025-26
    pos = shots.assign(mid=shots["match_id"].map(sid), season=shots["season"].astype(str).map(seas))
    pos = pos[pos["mid"].isin(reg) & (pos["season"] >= FIRST) & (pos["season"] < "2025-26")]
    pos = pos.drop_duplicates(["mid", "team_name", "period", "x", "y", "first_name", "family_name", "action_type", "r"])
    p26 = fg[fg["season"] >= "2025-26"]
    for df in (pos, p26):
        df = df.assign(three=df["action_type"].astype(str) == "3pt",
                       h=np.where((df["period_type"].astype(str).str.upper() == "OVERTIME") | (df["period"].astype(float) >= 3), 2, 1))
        lo = df.groupby(["mid", "team_name", "h"])["x"].apply(lambda s: (s < 50).mean() >= 0.5).to_dict()
        for r in df[["season", "mid", "team_name", "h", "x", "y", "three", "first_name", "family_name"]].itertuples(index=False):
            X, Y = NS.frame(r.x, r.y, "lo" if lo.get((r.mid, r.team_name, r.h), True) else "hi")
            z = NS.zone(bool(r.three), X, Y)
            if z == "unk":
                continue
            c = C[r.season][nkey(r.first_name, r.family_name)]
            c["zfga"] += 1; c[z] += 1
            c["z3"] += z in ("c3", "ab3")
    return C


def season_rows(imp, season, C):
    F = imp["status"]["feats"]
    out = []
    for p in imp["seasons"][season]["players"]:
        r = dict(zip(F, p["r"]))
        c = C.get(p["k"], collections.Counter())
        out.append({"key": p["k"], "name": p["n"], "mn": p["mn"], "g": p["g"], "T": p["o"] + p["d"], "r": r,
                    **{n: c.get(n, 0) for n in COUNTS}})
    return out


NAMES = {   # name, kind, one line on how they play (from the group's profile)
    "lead": ("Primary Ball Handler", "perimeter", "Runs the offence: the most assists and the most shots of his own, many off the dribble and from mid-range."),
    "scorer": ("Scoring Guard", "perimeter", "A high-usage scorer who also creates for others: pull-ups, threes and drives, fewer assists than a primary handler."),
    "pass": ("Pass-First Playmaker", "perimeter", "Sets others up at a lower usage, guard or point-forward: lots of assists and steals, a balanced shot diet."),
    "shooter": ("Volume Shooter", "perimeter", "Lives behind the arc: the highest share of threes, mostly set up by others."),
    "midwing": ("Mid-Range Wing", "perimeter", "A wing who scores from the mid-range and in the paint as much as from three."),
    "spot": ("Spot-Up Wing", "perimeter", "A low-usage wing who spaces the floor: threes, many from the corners, nearly all assisted."),
    "rim": ("Rim-Running Big", "big", "Finishes at the rim and protects it: dunks and lobs, the most blocks and rebounds, almost no threes."),
    "post": ("Post Big", "big", "Scores inside with his back to the basket: hooks and paint shots, offensive boards, trips to the line."),
    "faceup": ("Face-Up Forward", "big", "A forward who scores from everywhere at a high usage: inside, mid-range and some threes."),
    "stretch": ("Stretch Big", "big", "A rebounding big who spaces the floor: threes (often from the corners) and finishes at the rim."),
}


def label(prof):
    """cluster profiles (raw means) -> {cluster: name id}, by the groups' own numbers"""
    P = dict(prof)
    left = set(P)
    pick = {}
    def take(name, score, pool=None):
        c = max(pool if pool is not None else left, key=score)
        pick[c] = name
        left.discard(c)
    size = lambda c: P[c]["dreb"] + P[c]["oreb"] + 2 * P[c]["blk"]
    bigs = set(sorted(left, key=size)[-4:])
    take("rim", lambda c: P[c]["blk"] + 10 * P[c]["fin"], bigs & left)
    take("post", lambda c: P[c]["hook2"] * 10 + P[c]["paint"] - P[c]["tpar"], bigs & left)
    take("stretch", lambda c: P[c]["tpar"] - P[c]["usage"] / 100, bigs & left)
    take("faceup", lambda c: 1, bigs & left)
    take("lead", lambda c: P[c]["ast"] + P[c]["usage"] / 4)
    take("scorer", lambda c: P[c]["usage"])
    take("pass", lambda c: P[c]["ast"])
    take("spot", lambda c: -P[c]["usage"])
    take("shooter", lambda c: P[c]["tpar"])
    take("midwing", lambda c: P[c]["mid"])
    assert not left and len(set(pick.values())) == K, pick
    return pick


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..")
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score
    import pyreadr, rdsread
    reg = set(json.load(gzip.open(os.path.join(HERE, "impact_hist.json.gz")))["dates"])
    pbp = pickle.load(open(os.environ["NBL_PBP_PKL"], "rb")) if os.environ.get("NBL_PBP_PKL") else rdsread.read_df(os.environ["NBL_PBP_RDS"])
    shots = pyreadr.read_r(os.environ["NBL_SHOTS_RDS"])[None]
    C = counts_nblr(pbp, shots, reg)
    imp = json.load(open(os.path.join(root, "data", "impact.json")))
    seasons = sorted(s for s in imp["seasons"] if FIRST <= s < "2026-27")
    rows, raws, norms = {}, {}, {}
    for s in seasons:
        rows[s] = season_rows(imp, s, C[s])
        raws[s], norms[s] = features(rows[s])
    train = [(s, r) for s in seasons for r in rows[s] if r["g"] and r["mn"] / r["g"] >= MIN_MPG]
    missing = [(s, r["name"]) for s, r in train if r["zfga"] == 0]
    assert not missing, missing[:5]
    X = np.array([vec(raws[s][r["key"]], norms[s]) for s, r in train])
    km = KMeans(K, n_init=100, random_state=0).fit(X)
    rng = np.random.default_rng(7)
    ari = [adjusted_rand_score(km.labels_, KMeans(K, n_init=10, random_state=b).fit(X[rng.choice(len(X), len(X))]).predict(X))
           for b in range(20)]
    prof = {c: {f: float(np.mean([raws[s][r["key"]][f] for (s, r), l in zip(train, km.labels_) if l == c])) for f in FEATS}
            for c in range(K)}
    name_of = label(prof)
    order = ["lead", "scorer", "pass", "shooter", "midwing", "spot", "rim", "post", "faceup", "stretch"]
    idx = {name_of[c]: c for c in range(K)}
    groups = []
    for i, nid in enumerate(order):
        c = idx[nid]
        nm, kind, desc = NAMES[nid]
        members = [(s, r) for (s, r), l in zip(train, km.labels_) if l == c]
        z = {f: float(km.cluster_centers_[c][j] / W[f]) for j, f in enumerate(FEATS)}
        groups.append({"id": i, "key": nid, "name": nm, "kind": kind, "desc": desc, "n": len(members),
                       "centroid": [round(float(v), 5) for v in km.cluster_centers_[c]],
                       "profile": {f: round(prof[c][f], 4) for f in FEATS},
                       "z": {f: round(v, 2) for f, v in z.items()}})
    # each group's signature: the numbers that set it apart from the other groups of its family (bigs, perimeter)
    for g in groups:
        fam = [h for h in groups if h["kind"] == g["kind"]]
        dz = {f: g["z"][f] - sum(h["z"][f] for h in fam) / len(fam) for f in FEATS}
        g["sig"] = sorted(FEATS, key=lambda f: -abs(dz[f]))[:3]
        g["sigDir"] = [1 if dz[f] > 0 else -1 for f in g["sig"]]
    cents = np.array([g["centroid"] for g in groups])
    hist = {}
    for s in seasons:
        hist[s] = {}
        for r in rows[s]:
            if r["mn"] >= MIN_MIN and r["zfga"] > 0:
                g1, g2, fit = assign(vec(raws[s][r["key"]], norms[s]), cents)
                hist[s][r["key"]] = [g1, g2, fit, [round(raws[s][r["key"]][f], 3) for f in FEATS]]
    doc = {"built": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "source": "the official NBL play-by-play and shot positions republished by nblR (JaseZiv/nblr_data), NBL19-NBL26",
           "feats": FEATS, "weights": W, "shrink": SHRINK, "minMpg": MIN_MPG, "minMinutes": MIN_MIN, "k": K,
           "trained": len(train), "stability": round(float(np.mean(ari)), 3), "groups": groups, "hist": hist}
    json.dump(doc, open(OUT, "w"), separators=(",", ":"))
    print(f"archetypes: {K} groups from {len(train)} player-seasons ({seasons[0]} to {seasons[-1]}), resampling "
          f"agreement {doc['stability']}; {sum(len(v) for v in hist.values())} player-seasons placed -> {OUT}")
    for g in groups:
        print(f"  {g['name']:22s} {g['kind']:9s} {g['n']:4d}")


if __name__ == "__main__":
    main()
