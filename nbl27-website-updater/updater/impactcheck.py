#!/usr/bin/env python3
"""Checks for the player impact ratings (the Impact tab). Not part of the daily update: run it after changing
impact.py (it reads only the fixed history, impact_hist.json.gz).

    python3 impactcheck.py

Forward test. In each of the last eight seasons of the history, the model is fitted on every game before the season's
midpoint (the median date of its games; box scores only up to there too) and then predicts every later game of that
season from who was on the floor: in each stint, each side scores home edge + its five attackers' offence - the five
defenders' defence per 100 possessions, times the stint's possessions; the game's predicted margin is the sum. A player
with no games yet that season counts his career on-court part only (an average box score). The same is done with
  - the box score alone (no on-court part),
  - plus-minus alone (no box-score part: on-court offence and defence only),
  - team ratings alone (each team's offence and defence that season, from the same stints, which can't see who played),
  - home court only (the home team always tipped),
and reported: how often the predicted winner won, and the average miss on the final margin (root mean square). The
shooters' expected percentages (the luck adjustment) use their whole NBL careers, as the ratings do.
"""
import math, os, sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np            # noqa: E402
import scipy.sparse as sp     # noqa: E402
import impact as IM           # noqa: E402

BIG = 1e12
MODELS = {"impact (both parts)": dict(IM.LAM),
          "box score alone": {"b": IM.LAM["b"], "uO": BIG, "uD": BIG, "vD": BIG},
          "plus-minus alone": {"b": BIG, "uO": 2000.0, "uD": 2000.0, "vD": 16000.0}}


def team_ratings(train, smean, lam=400.0):
    """each team-season's offence and defence per 100 possessions from the same stints (ridge, home edge included)"""
    keys = sorted({(g["season"], t) for g in train for t in (g["home"], g["away"])}); ix = {k: i for i, k in enumerate(keys)}
    n = len(keys); rows, cols, vals, y, w = [], [], [], [], []
    r = 0
    for g in train:
        for fh, fa, sec, h, a in g["stints"]:
            P = 0.5 * (IM.poss(h) + IM.poss(a))
            if P < 0.5:
                continue
            for side in (0, 1):
                ot, dt, so = (g["home"], g["away"], h) if side == 0 else (g["away"], g["home"], a)
                y.append(100 * IM.adj_pts(so) / P - smean[g["season"]]); w.append(P)
                rows += [r, r, r]; cols += [0, 1 + ix[(g["season"], ot)], 1 + n + ix[(g["season"], dt)]]
                vals += [1.0 if side == 0 else -1.0, 1.0, -1.0]
                r += 1
    X = sp.csr_matrix((vals, (rows, cols)), shape=(r, 1 + 2 * n)); y, w = np.array(y), np.array(w)
    Xw = X.multiply(w[:, None]).tocsr(); A = (X.T @ Xw).toarray(); b = Xw.T @ y
    L = np.full(A.shape[0], lam); L[0] = 1e-6
    th = np.linalg.solve(A + np.diag(L), b)
    return lambda s, t: (th[1 + ix[(s, t)]], th[1 + n + ix[(s, t)]]) if (s, t) in ix else (0.0, 0.0), th[0]


def main():
    h, games, rows = IM.load_hist()
    dates, ht, L = h["dates"], h["talent"], h["league"]
    talent_of = lambda k, s: tuple(ht.get(k, (L["lg3"], L["lgft"])))
    seasons = sorted({g["season"] for g in games})[-8:]
    tot = {m: Counter() for m in list(MODELS) + ["team ratings alone", "home court only"]}
    sq = Counter()
    for season in seasons:
        ds = sorted(g["date"] for g in games if g["season"] == season)
        cut = ds[len(ds) // 2]
        train = [g for g in games if g["date"] < cut]
        test = [g for g in games if g["season"] == season and g["date"] >= cut]
        trows = [r for r in rows if (dates.get(r[2]) or "9999") < cut]
        preds = {}
        for name, lam in MODELS.items():
            PS, PG = IM.tally(trows)
            M = IM.fit(train, PS, PG, talent_of, lam)
            th, c, out = M["theta"], M["cols"], M["out"]

            def val(k, side, M=M, th=th, c=c, out=out):
                v = out.get((k, season))
                if v:
                    return v["oB"] + v["oC"] if side == "O" else v["dB"] + v["dC"]
                i = M["pli"].get(k)
                return 0.0 if i is None else th[c["uO"] + i] if side == "O" else th[c["uD"] + i]
            preds[name] = (lambda fh, fa, val=val, home=M["home"]:
                           (home + sum(val(k, "O") for k in fh) - sum(val(k, "D") for k in fa),
                            -home + sum(val(k, "O") for k in fa) - sum(val(k, "D") for k in fh)))
            smean = M["smean"]
        TR, home = team_ratings(train, smean)
        preds["team ratings alone"] = None
        preds["home court only"] = None
        for g in test:
            actual = sum(hh.get("pts", 0) - aa.get("pts", 0) for _, _, _, hh, aa in g["stints"])
            for name, f in preds.items():
                m = 0.0
                for fh, fa, sec, hh, aa in g["stints"]:
                    P = max(0.0, 0.5 * (IM.poss(hh) + IM.poss(aa)))
                    if name == "team ratings alone":
                        (oh, dh), (oa, da) = TR(season, g["home"]), TR(season, g["away"])
                        eh, ea = home + oh - da, -home + oa - dh
                    elif name == "home court only":
                        eh, ea = home, -home
                    else:
                        eh, ea = f(fh, fa)
                    m += P * (eh - ea) / 100
                t = tot[name]
                t["n"] += 1; t["right"] += (m > 0) == (actual > 0); sq[name] += (actual - m) ** 2
        print(f"{season}: fitted on {len(train)} games before {cut}, predicted {len(test)}", flush=True)
    for name, t in tot.items():
        print(f"  {name:<22} winners {t['right']}/{t['n']} ({100 * t['right'] / t['n']:.1f}%), margin miss {math.sqrt(sq[name] / t['n']):.2f}")


if __name__ == "__main__":
    main()
