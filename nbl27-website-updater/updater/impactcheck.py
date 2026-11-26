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
  - the RAPM split's RAPM (factors.py's points regression, on-court only: the season being predicted counts fully,
    earlier ones fade with its half-life),
and reported: how often the predicted winner won, and the average miss on the final margin (root mean square). The
shooters' expected percentages (the luck adjustment) use their whole NBL careers, as the ratings do. Last, the ratings
mixed with the RAPM split (a game's prediction is linear in the players' numbers, so mixing predictions = mixing
ratings): the average miss for each share of the ratings, and the share chosen on seven seasons and tested on the eighth.
"""
import math, os, sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np            # noqa: E402
import scipy.sparse as sp     # noqa: E402
import impact as IM           # noqa: E402
import factors as FA          # noqa: E402

BIG = 1e12
RAPM = "RAPM split alone"
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


def rapm_split(train, season):
    """factors.py's points regression on the training games: the season being predicted counts fully, earlier seasons
    fade with FA.HALF; returns the stint predictor (home side, away side) in points per 100 possessions"""
    from scipy.sparse.linalg import LinearOperator
    seasons = sorted({g["season"] for g in train} | {season}); si = {s: i for i, s in enumerate(seasons)}
    obs = []
    for g in train:
        for fh, fa, sec, H, A in g["stints"]:
            for o, d, c, home in ((fh, fa, H, 1.0), (fa, fh, A, -1.0)):
                y, e = FA.outcomes(c)["pts"]
                if e > 0:
                    obs.append((o, d, home, si[g["season"]], y, e))
    keys = sorted({k for o in obs for k in o[0] + o[1]}); ki = {k: i for i, k in enumerate(keys)}; P = len(keys)
    rows, cols = [], []
    for i, o in enumerate(obs):
        for k in o[0]:
            rows.append(i); cols.append(ki[k])
        for k in o[1]:
            rows.append(i); cols.append(P + ki[k])
    X = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(obs), 2 * P))
    sea = np.array([o[3] for o in obs]); home = np.array([o[2] for o in obs])
    Y = np.array([o[4] for o in obs]); E = np.array([o[5] for o in obs])
    for s in set(sea.tolist()):
        m = sea == s; Y[m] -= (Y[m] * E[m]).sum() / E[m].sum()
    w = E * 0.5 ** ((si[season] - sea) / FA.HALF)
    hb = (Y * w * home).sum() / w.sum()
    Xw = X.multiply(w[:, None]).tocsr()
    A = LinearOperator((2 * P, 2 * P), matvec=lambda v: X.T @ (Xw @ v) + FA.LAM * v)
    b = FA._solve(A, X.T @ (w * (Y - hb * home)), None)
    O = {k: b[ki[k]] for k in keys}; D = {k: -b[P + ki[k]] for k in keys}
    return lambda fh, fa: (hb + sum(O.get(k, 0.0) for k in fh) - sum(D.get(k, 0.0) for k in fa),
                           -hb + sum(O.get(k, 0.0) for k in fa) - sum(D.get(k, 0.0) for k in fh))


def mix(games_rows):
    """the ratings mixed with the RAPM split: the average miss for each share of the ratings, and the share chosen on
    seven seasons and tested on the eighth"""
    a = np.array([r[1] for r in games_rows]); R = np.array([r[2] for r in games_rows]); Q = np.array([r[3] for r in games_rows])
    S = np.array([r[0] for r in games_rows]); W = np.round(np.arange(0, 1.0001, 0.05), 2)
    miss = lambda p, m: math.sqrt(((a[m] - p[m]) ** 2).mean())
    allm = np.ones(len(a), bool)
    print("  the ratings mixed with the RAPM split, average miss by the ratings' share: "
          + ", ".join(f"{int(100 * w)}% {miss(w * R + (1 - w) * Q, allm):.2f}" for w in W[::4]))
    out, picks = np.zeros(len(a)), []
    for s in sorted(set(S)):
        best = min(W, key=lambda w: miss(w * R + (1 - w) * Q, S != s)); picks.append(int(100 * best))
        out[S == s] = (best * R + (1 - best) * Q)[S == s]
    print(f"  share chosen on seven seasons, tested on the eighth: {picks} -> average miss {miss(out, allm):.2f}"
          f" (the ratings alone {miss(R, allm):.2f})")


def main():
    h, games, rows = IM.load_hist()
    dates, ht, L = h["dates"], h["talent"], h["league"]
    talent_of = lambda k, s: tuple(ht.get(k, (L["lg3"], L["lgft"])))
    seasons = sorted({g["season"] for g in games})[-8:]
    tot = {m: Counter() for m in list(MODELS) + ["team ratings alone", "home court only", RAPM]}
    sq = Counter()
    games_rows = []
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
        preds[RAPM] = rapm_split(train, season)
        for g in test:
            actual = sum(hh.get("pts", 0) - aa.get("pts", 0) for _, _, _, hh, aa in g["stints"])
            margin = {}
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
                margin[name] = m
            games_rows.append((season, actual, margin["impact (both parts)"], margin[RAPM]))
        print(f"{season}: fitted on {len(train)} games before {cut}, predicted {len(test)}", flush=True)
    for name, t in tot.items():
        print(f"  {name:<22} winners {t['right']}/{t['n']} ({100 * t['right'] / t['n']:.1f}%), margin miss {math.sqrt(sq[name] / t['n']):.2f}")
    mix(games_rows)


if __name__ == "__main__":
    main()
