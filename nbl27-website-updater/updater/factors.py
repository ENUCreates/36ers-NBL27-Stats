#!/usr/bin/env python3
"""The RAPM split (the Impact tab's "RAPM split" view, data/factors.json): every player-season since 2015-16 (NBL16)
rated from the floor alone - who was on it and what happened, no box score - and split into six factors, the way
databallr splits NBA RAPM: how much his team's shooting, ball security and offensive rebounding gain with him on
(oTS, oTOV, oREB), and how much the opponents' lose (dTS, dTOV, dREB).

Data: the same stints as impact.py (the fixed history in impact_hist.json.gz, plus every NBL27 game whose NBL
play-by-play reproduces the official box score, impact.nbl27). A stint is a stretch with the same ten players on the
floor; each gives two observations, one per side on offence:
    TS   = luck-adjusted points / (2 x (FGA + 0.44 FTA))         weight: FGA + 0.44 FTA
    TOV  = turnovers / possessions                              weight: possessions (FGA - OREB + TOV + 0.44 FTA)
    OREB = offensive rebounds / rebound chances                  weight: missed shots + 0.4 x missed free throws
    PTS  = 100 x luck-adjusted points / possessions             weight: possessions
(luck-adjusted as in impact.py: every three and free throw counts at the shooter's expected percentage). Each is
centred on its season's league average and the home side's edge taken out, then fitted by ridge regression:
    y = sum of the five attackers' offence + sum of the five defenders' defence + noise
every player's offence and defence pulled toward zero (average) by LAM possessions' worth of data, scaled to each
outcome's volume (rebound chances are about half the possessions). A season's numbers use that season's stints and
every earlier season's, each earlier season's weight halving every HALF seasons; NBL27's are as of this morning.

LAM and HALF were chosen on 26 Nov 2026 by fitting on every season before each of NBL20-NBL26 and predicting that
season's stints: one shared LAM of 2,500 possessions was the best or within 0.02 points of the best for all four
outcomes (pulling the factors equally keeps them adding up); a longer memory predicted a little better (points:
0.256% of the stint variance at a 1.5-season half-life, 0.286% at 3, 0.312% with no fading) and HALF = 2 keeps each
season's numbers about that season.

Points: each factor's effect is converted to points per 100 possessions with that season's league averages, from
    offensive rating = 200 x TS x (1 - TOV) / (1 - OREB x misses per shot attempt)
(its slope for each factor: about +2.0 points per point of TS%, -1.3 per point of TOV%, +0.7 per point of OREB%),
times one multiplier per factor fitted across every player-season with 800+ possessions, so that the six add up to
the points regression as closely as possible (a better shooting team also has fewer misses to rebound, so the slope
alone overstates shooting).
    RAPM = ORAPM + DRAPM (the points regression)
    RESID = RAPM - the six factors;  PVAL (possession value) = oTOV + oREB + dTOV + dREB
"""
import collections, datetime, json, os, sys, tempfile
from zoneinfo import ZoneInfo
import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import cg, LinearOperator

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import impact as IM  # noqa: E402

LAM = 2500.0
HALF = 2.0
MIN_CAL = 800.0          # possessions a player-season needs to calibrate the multipliers
OUT = ("pts", "ts", "tov", "oreb")
COLS = ["rapm", "orapm", "drapm", "oTS", "oTOV", "oREB", "dTS", "dTOV", "dREB", "resid", "pval", "poss"]


def _solve(A, rhs, x0):
    """conjugate gradient to 1e-9 (SciPy before 1.12 calls the tolerance tol)"""
    try:
        return cg(A, rhs, x0=x0, rtol=1e-9, maxiter=5000)[0]
    except TypeError:
        return cg(A, rhs, x0=x0, tol=1e-9, maxiter=5000)[0]


def outcomes(c):
    pts = IM.adj_pts(c)
    fga, fta, oreb, tov = c.get("fga", 0), c.get("fta", 0), c.get("oreb", 0), c.get("tov", 0)
    tsa = fga + 0.44 * fta
    poss = fga - oreb + tov + 0.44 * fta
    fgm = (c.get("pts", 0) - c.get("ftm", 0) - c.get("tpm", 0)) / 2
    ch = max(0.0, fga - fgm) + 0.4 * max(0, fta - c.get("ftm", 0))
    ok = poss > 0.5
    return {"ts": (pts / (2 * tsa) if tsa > 0 else 0.0, tsa),
            "tov": (tov / poss if ok else 0.0, poss if ok else 0.0),
            "oreb": (oreb / ch if ch > 0 else 0.0, ch),
            "pts": (100 * pts / poss if ok else 0.0, poss if ok else 0.0)}


def model(sgames):
    """stint games -> every season's six factors for every player who played in it"""
    seasons = sorted({g["season"] for g in sgames})
    si = {s: i for i, s in enumerate(seasons)}
    obs = []
    for g in sgames:
        for fh, fa, sec, H, A in g["stints"]:
            for o, d, c, home in ((fh, fa, H, 1.0), (fa, fh, A, -1.0)):
                obs.append((o, d, home, si[g["season"]], outcomes(c)))
    keys = sorted({k for o in obs for k in o[0] + o[1]})
    ki = {k: i for i, k in enumerate(keys)}
    P, n = len(keys), len(obs)
    r, c = [], []
    for i, o in enumerate(obs):
        for k in o[0]:
            r.append(i); c.append(ki[k])
        for k in o[1]:
            r.append(i); c.append(P + ki[k])
    X = sp.csr_matrix((np.ones(len(r)), (r, c)), shape=(n, 2 * P))
    sea = np.array([o[3] for o in obs]); home = np.array([o[2] for o in obs])
    Y = {f: np.array([o[4][f][0] for o in obs]) for f in OUT}
    E = {f: np.array([o[4][f][1] for o in obs]) for f in OUT}
    vol = {f: E[f].sum() / E["pts"].sum() for f in OUT}
    # each season's league averages, and the stints' values centred on them
    lg = {f: np.zeros(len(seasons)) for f in OUT}
    Yc = {}
    for f in OUT:
        yc = Y[f].copy()
        for s in range(len(seasons)):
            m = sea == s
            lg[f][s] = (Y[f][m] * E[f][m]).sum() / E[f][m].sum()
            yc[m] -= lg[f][s]
        Yc[f] = yc
    mrate = np.array([E["oreb"][sea == s].sum() / E["ts"][sea == s].sum() for s in range(len(seasons))])
    # possessions each player was on the floor each season (either end)
    possp = np.zeros((len(seasons), P))
    for i, o in enumerate(obs):
        for k in o[0] + o[1]:
            possp[o[3], ki[k]] += 0.5 * E["pts"][i]
    raw, warm = {}, {}
    for s in range(len(seasons)):
        w0 = np.where(sea <= s, 0.5 ** ((s - sea) / HALF), 0.0)
        beta = {}
        for f in OUT:
            w = E[f] * w0
            hb = (Yc[f] * w * home).sum() / w.sum()          # the home side's edge
            y = Yc[f] - hb * home
            Xw = X.multiply(w[:, None]).tocsr()
            lam = LAM * vol[f]
            A = LinearOperator((2 * P, 2 * P), matvec=lambda v, Xw=Xw, lam=lam: X.T @ (Xw @ v) + lam * v)
            rhs = X.T @ (w * y)
            b = _solve(A, rhs, warm.get(f))
            if np.linalg.norm(A @ b - rhs) > 1e-6 * np.linalg.norm(rhs):
                raise RuntimeError(f"the {f} regression for {seasons[s]} didn't converge")
            warm[f] = b
            beta[f] = b
        TS, t, o, m = lg["ts"][s], lg["tov"][s], lg["oreb"][s], mrate[s]
        den = 1 - o * m
        slope = {"ts": 200 * (1 - t) / den, "tov": -200 * TS / den, "oreb": 200 * TS * (1 - t) * m / den ** 2}
        raw[seasons[s]] = {"beta": beta, "slope": slope, "lg": {"ts": TS, "tov": t, "oreb": o, "ortg": 200 * TS * (1 - t) / den}}
    # one multiplier per factor so the six add up to the points regression (every player-season with MIN_CAL+ possessions)
    rowsX, rowsY = [], []
    for s, sn in enumerate(seasons):
        R = raw[sn]; bo = {f: R["beta"][f][:P] for f in OUT}; bd = {f: R["beta"][f][P:] for f in OUT}; sl = R["slope"]
        parts = np.column_stack([sl["ts"] * bo["ts"], sl["tov"] * bo["tov"], sl["oreb"] * bo["oreb"],
                                 -sl["ts"] * bd["ts"], -sl["tov"] * bd["tov"], -sl["oreb"] * bd["oreb"]])
        R["parts"] = parts
        R["orapm"], R["drapm"] = bo["pts"], -bd["pts"]
        q = possp[s] >= MIN_CAL
        rowsX.append(parts[q]); rowsY.append((bo["pts"] - bd["pts"])[q])
    XX, YY = np.vstack(rowsX), np.concatenate(rowsY)
    mult = np.linalg.lstsq(XX, YY, rcond=None)[0]
    fit = XX @ mult
    r2 = 1 - ((YY - fit) ** 2).sum() / ((YY - YY.mean()) ** 2).sum()
    # a split that stops adding up (fitted 26 Nov 2026: R2 0.991, multipliers 0.79-1.01) means something broke upstream:
    # stop here so the previous data/factors.json stays
    if not (np.isfinite(XX).all() and np.isfinite(mult).all() and r2 >= 0.9 and ((mult > 0.4) & (mult < 1.6)).all()):
        raise RuntimeError(f"the six factors no longer add up to RAPM (R2 {r2:.3f}, multipliers {np.round(mult, 2).tolist()})")
    out = {}
    for s, sn in enumerate(seasons):
        R = raw[sn]
        six = R["parts"] * mult
        rapm = R["orapm"] + R["drapm"]
        resid = rapm - six.sum(1)
        pval = six[:, 1] + six[:, 2] + six[:, 4] + six[:, 5]
        out[sn] = {keys[j]: [round(float(v), 2) for v in (rapm[j], R["orapm"][j], R["drapm"][j], *six[j], resid[j], pval[j])]
                   + [round(float(possp[s][j]))]
                   for j in np.where(possp[s] > 0)[0]}
    conv = {sn: {"slope": {f: round(raw[sn]["slope"][f] / 100, 3) for f in ("ts", "tov", "oreb")},
                 "league": {k: round(v, 4) for k, v in raw[sn]["lg"].items()}} for sn in seasons}
    return out, {"mult": dict(zip(["oTS", "oTOV", "oREB", "dTS", "dTOV", "dREB"], [round(float(v), 3) for v in mult])),
                 "r2": round(float(r2), 3), "resid_sd": round(float(np.std(YY - fit)), 2), "calibrated_on": int(len(YY)),
                 "conv": conv}


def run(root, off, pairs, index):
    h, hgames, _ = IM.load_hist()
    sg27 = IM.nbl27(root, off, pairs, index, h)[0]
    sgames = hgames + sg27
    out, info = model(sgames)
    used = collections.Counter(g["season"] for g in sgames)
    now = datetime.datetime.now(ZoneInfo("Australia/Sydney"))
    doc = {"status": {"computedAt": now.isoformat(timespec="seconds"), "lam": LAM, "half": HALF,
                      "through": max(g["date"] for g in sg27) if sg27 else None,
                      "games": {s: used[s] for s in sorted(used)}, **info},
           "cols": COLS, "seasons": out}
    fp = os.path.join(root, "data", "factors.json")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(fp), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, fp)
    cur = out.get(IM.CUR, {})
    top = sorted(cur.items(), key=lambda kv: -kv[1][0])[:3]
    yield (f"factors: six-factor RAPM for {len(out)} seasons ({sum(len(v) for v in out.values())} player-seasons; "
           f"NBL27 {len(cur)} players from {used[IM.CUR]} games); the six add up to RAPM with R2 {info['r2']} "
           f"(residual sd {info['resid_sd']}); NBL27 top: " + ", ".join(f"{k} {v[0]:+.1f}" for k, v in top))


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    import official as O
    cache = os.environ.get("NBL_OFFICIAL_CACHE") or tempfile.mkdtemp()
    off = O.load(cache)
    D = lambda n: json.load(open(os.path.join(root, "data", n + ".json")))
    cmp = O.compare(D("games"), D("players"), off)
    for line in run(root, off, cmp["pairs"], O.nbl27_index(off)):
        print(line)


if __name__ == "__main__":
    main()
