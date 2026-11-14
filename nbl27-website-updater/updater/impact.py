#!/usr/bin/env python3
"""Player impact: the Impact tab (data/impact.json). Every NBL player-season from 2015-16 (NBL16) to NBL27, rated on
offence and defence in points per 100 possessions on the floor, against an average NBL player.

    impact = box-score part + on-court part            (for offence and for defence; overall = offence + defence)

Data: every regular-season game since 2015-16 whose play-by-play reconciles with the official box score (the fixed
history in impact_hist.json.gz, built by impact_hist.py), plus every NBL27 game whose NBL play-by-play reproduces the
official box score exactly (nbl_lineups.check), read each morning by rebuild.py. A stint is a stretch with the same ten
players on the floor; each gives two observations, one per side:

    y = 100 x (that side's points in the stint) / possessions - the season's league average
      = home edge + sum of the five attackers' offence - sum of the five defenders' defence + noise

weighted by the stint's possessions (FGA - OREB + TOV + 0.44 FTA, averaged over both sides). Points are luck-adjusted:
every three and free throw counts at the shooter's expected percentage, not whether it went in (a made three over a
miss worth W3 = 2.6 points, a free throw WFT = 0.95, the shooters' estimates as ladder.py makes them: NBL seasons, plus
the Player cards' other leagues for NBL27 players).

A player's offence in a season = box-score offence + his on-court offence (one number for his whole NBL career);
his defence = box-score defence + his on-court defence (career) + a small this-season part. The box-score parts are
weights x (his per-100 rates that season - that season's average, each rate pulled toward the league rate by K_SHR
possessions):
    offence: points (threes and free throws at his expected %), shot attempts, 3-point attempts, free-throw attempts,
             assists, turnovers, offensive rebounds, steals, fouls, minutes a game
    defence: offensive rebounds, defensive rebounds, steals, blocks, fouls, minutes a game
The weights and the on-court parts are fitted together (penalised least squares). To fit the weights on how a box
score predicts results rather than on stats that simply mirror them (a defensive rebound exists because the opponent
missed), each observation's box-score features leave out that game's own box score. The penalties (LAM: how far the
on-court parts are pulled toward zero) were chosen by 5-fold cross-validation over games on 2015-16 to NBL27 data,
14 Nov 2026; season-to-season changes in on-court offence, and the box-score stats left out of each side, didn't
predict held-out games and are left out.

Checked when it was built:
  - cross-validated prediction of held-out games' stints: the combined model beat the box score alone and the
    on-court (plus-minus) part alone;
  - forward (impactcheck.py, which repeats it): in each of eight seasons (2018-19 to 2025-26) fitted on every game
    before mid-season, then used to predict the rest of that season (541 games) from who was on the floor - right
    winner 63.6% (box score alone 61.6%, plus-minus alone 63.0%, team ratings alone 59.1%, the home team 53.8%),
    average miss on the margin 13.9 points (box score alone 14.2, plus-minus alone 14.1, team ratings 14.2).

    python3 impact.py <repo_root>        (standalone: reads the official data itself; rebuild.py calls run())
"""
import collections, datetime, gzip, json, math, os, re, sys, unicodedata
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                 # noqa: E402
import scipy.sparse as sp          # noqa: E402
import scipy.linalg as la          # noqa: E402

HIST = os.path.join(HERE, "impact_hist.json.gz")
CUR = "2026-27"
FEATS = ["pts_x", "fga", "tpa", "fta", "ast", "tov", "oreb", "dreb", "stl", "blk", "pf", "mpg"]
OFF = ["pts_x", "fga", "tpa", "fta", "ast", "tov", "oreb", "stl", "pf", "mpg"]
DEF = ["oreb", "dreb", "stl", "blk", "pf", "mpg"]
BOXF = ["pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]
K_SHR = 200.0                        # possessions of league-average rates mixed into each player's rates
W3, WFT = 2.6, 0.95                  # luck: points per make above expectation (as ladder.py)
LAM = {"b": 3000.0, "uO": 2000.0, "uD": 3000.0, "vD": 64000.0}
FORWARD = {"games": 541, "seasons": "2018-19 to 2025-26", "winner": 63.6, "teamOnly": 59.1, "boxOnly": 61.6,
           "pmOnly": 63.0, "homeOnly": 53.8, "rmse": 13.9, "rmseTeam": 14.2}       # impactcheck.py, 14 Nov 2026


def nkey(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def label(season):
    return "NBL" + str(int(season[:4]) + 1)[2:]


def poss(s):
    return s.get("fga", 0) - s.get("oreb", 0) + s.get("tov", 0) + 0.44 * s.get("fta", 0)


def adj_pts(s):
    return s.get("pts", 0) - W3 * (s.get("tpm", 0) - s.get("x3", 0)) - WFT * (s.get("ftm", 0) - s.get("xft", 0))


# ------------------------------------------------------------------ NBL27 from this site's data and the NBL's feed
def nbl27(root, off, pairs, index, hist):
    """-> (stint games, box rows, ESPN id by key, names by key, games played) for NBL27. Stints only from games whose
    NBL play-by-play reproduces the official box score exactly (both teams) and whose ten-man walk keeps five a side."""
    import nbl_lineups as NL
    import nbl_shots as NS
    import ladder as LD
    D = lambda n: json.load(open(os.path.join(root, "data", n + ".json")))
    games, players = D("games"), D("players")
    bios = {b["id"]: b for b in D("bios")}
    # shooters' true 3P% / FT%: this season plus the Player cards' past seasons, as ladder.py estimates them
    s3m, s3a, sfm, sfa = Counter(), Counter(), Counter(), Counter()
    for d in players:
        for L in d["teams"].values():
            for x in L:
                s3m[x["id"]] += x["tpm"]; s3a[x["id"]] += x["tpa"]; sfm[x["id"]] += x["ftm"]; sfa[x["id"]] += x["fta"]
    lg3 = sum(s3m.values()) / max(1, sum(s3a.values())); lgft = sum(sfm.values()) / max(1, sum(sfa.values()))
    p3e, pfte = {}, {}
    for pid in s3a:
        c = LD.career(bios.get(pid) or {})
        f3 = min(1.0, LD.CAP3 / c["tpa"]) if c["tpa"] else 0
        fft = min(1.0, LD.CAPFT / c["fta"]) if c["fta"] else 0
        p3e[pid] = (f3 * c["tpm"] + s3m[pid] + LD.K3 * lg3) / (f3 * c["tpa"] + s3a[pid] + LD.K3)
        pfte[pid] = (fft * c["ftm"] + sfm[pid] + LD.KFT * lgft) / (fft * c["fta"] + sfa[pid] + LD.KFT)
    espn_of = {v["pid"]: k for k, v in pairs.items() if v.get("pid")}
    # a player's key: his official name (the NBL's spelling, which the history uses too)
    key_of = {e: nkey(v.get("name")) for e, v in pairs.items() if v.get("name")}
    names = collections.defaultdict(Counter)
    for d in players:
        for L in d["teams"].values():
            for x in L:
                key_of.setdefault(x["id"], nkey(x["name"]))
                names[key_of[x["id"]]][x["name"]] += 1
    espn = {k: e for e, k in key_of.items()}
    talent = {}
    for e, k in key_of.items():
        if e in p3e:
            talent[k] = (p3e[e], pfte[e])

    def joint(m):
        home, away = m["home"], m["away"]
        opp = {home: away, away: home}
        plays = [a for a in m["plays"] if a.get("clock")]
        on = NL.starters(m, plays)
        keep = set(NS.clean({**m, "plays": plays})[0])
        nm = {p["pid"]: p["name"] for p in m["players"]}
        kk = lambda pid: key_of.get(espn_of.get(pid)) or nkey(nm.get(pid, str(pid)))
        tal = lambda pid: talent.get(kk(pid)) or tuple(hist["talent"].get(kk(pid), (lg3, lgft)))
        out = collections.defaultdict(lambda: [0.0, Counter(), Counter()])
        bad = []
        for per in sorted({NL.per_no(a) for a in plays} | {1, 2, 3, 4}):
            L, last = NL.plen(per), 0.0

            def cur():
                for t in opp:
                    if len(on[t]) != 5:
                        bad.append(t)
                return out[(frozenset(on[home]), frozenset(on[away]))]
            for i, a in ((i, a) for i, a in enumerate(plays) if NL.per_no(a) == per):
                tnow = L - NL.csec(a["clock"])
                if tnow > last:
                    cur()[0] += tnow - last
                    last = tnow
                ty, stp, team = (a.get("type") or "").lower(), (a.get("sub") or "").lower(), a.get("team")
                if ty == "substitution":
                    if team in on and a.get("pid") and stp in ("in", "out"):
                        (on[team].add if stp == "in" else on[team].discard)(a["pid"])
                    continue
                if team not in opp or (ty in ("2pt", "3pt") and i not in keep):
                    continue
                u = cur(); d = u[1] if team == home else u[2]
                if ty in ("2pt", "3pt"):
                    d["fga"] += 1
                    if ty == "3pt":
                        d["x3"] += tal(a.get("pid"))[0]
                        if a.get("ok"):
                            d["tpm"] += 1
                    if a.get("ok"):
                        d["pts"] += 3 if ty == "3pt" else 2
                elif ty == "freethrow":
                    d["fta"] += 1; d["xft"] += tal(a.get("pid"))[1]
                    if a.get("ok"):
                        d["ftm"] += 1; d["pts"] += 1
                elif ty == "rebound" and stp == "offensive":
                    d["oreb"] += 1
                elif ty == "turnover":
                    d["tov"] += 1
            if L > last:
                cur()[0] += L - last
        return out, bad, kk
    sgames, nbad = [], Counter()
    for g in games:
        teams = (g["home"]["team"], g["away"]["team"])
        m = (off.get("pbp") or {}).get(index.get((g["date"], frozenset(teams))))
        if not m or not m.get("plays"):
            nbad["no NBL play-by-play"] += 1; continue
        S, probs, nper = NL.stints(m)
        if any(NL.check(m, S, probs, nper, {teams[0]: g["home"], teams[1]: g["away"]}).values()):
            nbad["doesn't reproduce the box score"] += 1; continue
        jt, bad, kk = joint(m)
        H, A = Counter(), Counter()
        for v in jt.values():
            H.update(v[1]); A.update(v[2])
        if bad or H["pts"] != g["home"]["pts"] or A["pts"] != g["away"]["pts"]:
            nbad["ten-man walk doesn't add up"] += 1; continue
        sgames.append({"id": g["id"], "season": CUR, "date": g["date"], "home": teams[0], "away": teams[1],
                       "stints": [(tuple(sorted(kk(p) for p in a)), tuple(sorted(kk(p) for p in b)), v[0], dict(v[1]), dict(v[2]))
                                  for (a, b), v in jt.items()]})
    # box-score lines, with each player's share of the game's possessions
    G = {g["id"].replace("espn-", ""): g for g in games}
    rows = []
    for d in players:
        g = G.get(d["espnId"])
        if not g:
            continue
        gp = 0.5 * (poss(g["home"]) + poss(g["away"]))
        gm = (g["home"].get("min") or 200) / 5
        for t, L in d["teams"].items():
            for x in L:
                mn = x.get("min") or 0
                if mn > 0:
                    rows.append([key_of[x["id"]], CUR, g["id"], t, 1 if x.get("starter") else 0, mn, gp * mn / gm,
                                 [x.get(f) or 0 for f in BOXF]])
    return sgames, rows, espn, names, talent, (lg3, lgft), len(games), dict(nbad)


# ------------------------------------------------------------------ the model
def tally(rows):
    """box rows -> per player-season totals PS and per player-game counts PG"""
    PS, PG = {}, {}
    for k, s, gid, team, st, mn, ps, vals in rows:
        P = PS.setdefault((k, s), {"n": Counter(), "d": Counter(), "min": 0.0, "poss": 0.0, "g": 0, "gs": 0, "teams": Counter()})
        Q = PG.setdefault((k, s, gid), {"n": Counter(), "d": Counter(), "min": 0.0, "poss": 0.0})
        P["min"] += mn; P["poss"] += ps; P["g"] += 1; P["gs"] += st; P["teams"][team] += mn
        Q["min"] += mn; Q["poss"] += ps
        for f, v in zip(BOXF, vals):
            if v is not None:
                P["n"][f] += v; P["d"][f] += ps; Q["n"][f] += v; Q["d"][f] += ps
    return PS, PG


def with_pts_x(n, d, q3, qf):
    n = dict(n); d = dict(d)
    n["pts_x"] = n.get("pts", 0) - 3 * (n.get("tpm", 0) - n.get("tpa", 0) * q3) - (n.get("ftm", 0) - n.get("fta", 0) * qf)
    d["pts_x"] = d.get("pts", 0)
    return n, d


def rates(n, d, mn, g, mu):
    r = np.zeros(len(FEATS))
    for j, f in enumerate(FEATS[:-1]):
        r[j] = 100 * (n.get(f, 0) + K_SHR * mu[f]) / (d.get(f, 0) + K_SHR)
    r[-1] = mn / g if g > 0 else np.nan
    return r


def fit(sgames, PS, PG, talent_of, lam=None):
    """The model on these stint games and box totals. talent_of(key, season) -> (3P%, FT%) for his expected points.
    -> dict with the fitted numbers and every player-season's rating (all of PS, not only those in stints)"""
    pss = sorted({(k, g["season"]) for g in sgames for st in g["stints"] for k in st[0] + st[1]})
    for k in pss:                                   # a player in a stint with no box line (shouldn't happen): zero counts
        PS.setdefault(k, {"n": Counter(), "d": Counter(), "min": 0.0, "poss": 0.0, "g": 0, "gs": 0, "teams": Counter()})
    psi = {k: i for i, k in enumerate(pss)}
    pl = sorted({k for k, _ in pss}); pli = {k: i for i, k in enumerate(pl)}
    cnt = {k: with_pts_x(PS[k]["n"], PS[k]["d"], *talent_of(*k)) for k in PS}
    tn, td = Counter(), Counter()
    for k in pss:
        for f in FEATS[:-1]:
            tn[f] += cnt[k][0].get(f, 0); td[f] += cnt[k][1].get(f, 0)
    mu = {f: tn[f] / td[f] for f in FEATS[:-1]}
    Rall = {k: rates(cnt[k][0], cnt[k][1], PS[k]["min"], PS[k]["g"], mu) for k in PS}
    centre = {}
    for s in sorted({k[1] for k in pss}):
        ks = [k for k in pss if k[1] == s]
        w = np.array([PS[k]["poss"] for k in ks])
        centre[s] = (np.array([Rall[k] for k in ks]) * w[:, None]).sum(0) / w.sum()
    allw = np.array([PS[k]["poss"] for k in pss])
    C = np.array([Rall[k] - centre[k[1]] for k in pss])
    scale = np.sqrt((C ** 2 * allw[:, None]).sum(0) / allw.sum())
    iO = [FEATS.index(f) for f in OFF]; iD = [FEATS.index(f) for f in DEF]
    cache = {}

    def xvec(k, s, gid):
        """his features for one game: the season's box score without that game's own"""
        ck = (k, s, gid)
        if ck in cache:
            return cache[ck]
        q = PG.get(ck)
        n0, d0 = cnt[(k, s)]
        p = PS[(k, s)]
        if q is None:
            r = Rall[(k, s)].copy(); g = p["g"]
        else:
            qn, qd = with_pts_x(q["n"], q["d"], *talent_of(k, s))
            r = rates({f: n0.get(f, 0) - qn.get(f, 0) for f in FEATS[:-1]}, {f: d0.get(f, 0) - qd.get(f, 0) for f in FEATS[:-1]},
                      p["min"] - q["min"], p["g"] - 1, mu)
        if np.isnan(r[-1]):
            r[-1] = centre[s][-1]
        v = (r - centre[s]) / scale
        cache[ck] = v
        return v
    pO, pD, nP, nS = len(OFF), len(DEF), len(pl), len(pss)
    cbO, cbD = 1, 1 + pO
    cuO = 1 + pO + pD; cuD = cuO + nP; cvD = cuD + nP; ncol = cvD + nS
    spts, sw = Counter(), Counter()
    for g in sgames:
        for fh, fa, sec, h, a in g["stints"]:
            P = max(0.0, 0.5 * (poss(h) + poss(a)))
            spts[g["season"]] += adj_pts(h) + adj_pts(a); sw[g["season"]] += 2 * P
    smean = {s: 100 * spts[s] / sw[s] for s in spts}
    rows, cols, vals, y, w = [], [], [], [], []
    r = 0
    for g in sgames:
        s = g["season"]
        for fh, fa, sec, h, a in g["stints"]:
            P = 0.5 * (poss(h) + poss(a))
            if P < 0.5:
                continue
            for side in (0, 1):
                O, Dd, so = (fh, fa, h) if side == 0 else (fa, fh, a)
                y.append(100 * adj_pts(so) / P - smean[s]); w.append(P)
                rows.append(r); cols.append(0); vals.append(1.0 if side == 0 else -1.0)
                xo = sum(xvec(k, s, g["id"]) for k in O); xd = sum(xvec(k, s, g["id"]) for k in Dd)
                for j, f in enumerate(iO):
                    rows.append(r); cols.append(cbO + j); vals.append(xo[f])
                for j, f in enumerate(iD):
                    rows.append(r); cols.append(cbD + j); vals.append(-xd[f])
                for k in O:
                    rows.append(r); cols.append(cuO + pli[k]); vals.append(1.0)
                for k in Dd:
                    rows += [r, r]; cols += [cuD + pli[k], cvD + psi[(k, s)]]; vals += [-1.0, -1.0]
                r += 1
    X = sp.csr_matrix((vals, (rows, cols)), shape=(r, ncol))
    y, w = np.array(y), np.array(w)
    Xw = X.multiply(w[:, None]).tocsr()
    A = (X.T @ Xw).toarray(); b = Xw.T @ y
    lam = lam or LAM
    L = np.zeros(ncol); L[0] = 1e-6
    L[cbO:cbO + pO + pD] = lam["b"]; L[cuO:cuO + nP] = lam["uO"]; L[cuD:cuD + nP] = lam["uD"]; L[cvD:cvD + nS] = lam["vD"]
    A[np.diag_indices_from(A)] += L
    cf = la.cho_factor(A)
    th = la.cho_solve(cf, b)
    res = y - X @ th
    sig2 = float((w * res ** 2).sum() / w.sum())
    Ainv = la.cho_solve(cf, np.eye(ncol))
    bO, bD = th[cbO:cbO + pO], th[cbO + pO:cbO + pO + pD]
    out = {}
    for (k, s), p in PS.items():
        if s not in centre:
            continue
        rr = Rall[(k, s)].copy()
        if np.isnan(rr[-1]):
            rr[-1] = centre[s][-1]
        x = (rr - centre[s]) / scale
        idx, coef = [], []
        oB = float(x[iO] @ bO); dB = float(x[iD] @ bD)
        oC = dC = 0.0
        idx += list(range(cbO, cbO + pO)); coef += list(x[iO])
        idx += list(range(cbO + pO, cbO + pO + pD)); coef += list(x[iD])
        if k in pli:
            oC += th[cuO + pli[k]]; dC += th[cuD + pli[k]]
            idx += [cuO + pli[k], cuD + pli[k]]; coef += [1.0, 1.0]
        if (k, s) in psi:
            dC += th[cvD + psi[(k, s)]]
            idx.append(cvD + psi[(k, s)]); coef.append(1.0)
        cv = np.array(coef)
        sd = math.sqrt(max(0.0, sig2 * float(cv @ Ainv[np.ix_(idx, idx)] @ cv)))
        out[(k, s)] = {"rates": rr, "oB": oB, "oC": float(oC), "dB": dB, "dC": float(dC), "sd": sd}
    aO = {f: float(bO[j] / scale[FEATS.index(f)]) for j, f in enumerate(OFF)}
    aD = {f: float(bD[j] / scale[FEATS.index(f)]) for j, f in enumerate(DEF)}
    return {"out": out, "centre": centre, "aO": aO, "aD": aD, "home": float(th[0]), "sig": math.sqrt(sig2), "stints": sum(len(g["stints"]) for g in sgames),
            "players": nP, "theta": th, "pss": pss, "pli": pli, "psi": psi, "scale": scale, "mu": mu, "Rall": Rall, "bO": bO, "bD": bD, "smean": smean,
            "cols": {"uO": cuO, "uD": cuD, "vD": cvD}}


def load_hist():
    h = json.load(gzip.open(HIST, "rt"))
    keys, side = h["keys"], h["side"]
    games = []
    for gid, season, date, home, away, stints in h["games"]:
        games.append({"id": gid, "season": season, "date": date, "home": home, "away": away,
                      "stints": [(tuple(keys[i] for i in a), tuple(keys[i] for i in b), sec, dict(zip(side, H)), dict(zip(side, A)))
                                 for a, b, sec, H, A in stints]})
    rows = [[keys[r[0]]] + r[1:] for r in h["boxrows"]]
    return h, games, rows


def best_name(variants):
    """the spelling to show: the one with the most capitals (McVeigh over Mcveigh), then the most used"""
    n = max(variants, key=lambda v: (sum(c.isupper() for c in v), variants[v]))
    n = re.sub(r"\b(Iii|Ii|Iv)$", lambda m: m.group(1).upper(), n)
    return re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), n)     # Mckay -> McKay (some official files)


def run(root, off, pairs, index):
    """Rebuild data/impact.json. -> report lines (raises on failure; the caller keeps the previous file)"""
    h, hgames, hrows = load_hist()
    sg27, rows27, espn, names27, talent27, lg27, ngames27, bad27 = nbl27(root, off, pairs, index, h)
    PS, PG = tally(hrows + rows27)
    ht = h["talent"]; L = h["league"]

    def talent_of(k, s):
        if s == CUR:
            return talent27.get(k) or tuple(ht.get(k, lg27))
        return tuple(ht.get(k, (L["lg3"], L["lgft"])))
    M = fit(hgames + sg27, PS, PG, talent_of)
    out = M["out"]
    names = collections.defaultdict(Counter)
    for k, v in h["names"].items():
        names[k].update(v)
    seasons = {}
    used = Counter(g["season"] for g in hgames + sg27)
    totals = {s: v["games"] for s, v in h["seasons"].items()}; totals[CUR] = ngames27
    for (k, s), v in out.items():
        p = PS[(k, s)]
        nm = best_name(names27[k]) if names27.get(k) else best_name(names[k]) if names.get(k) else k
        teams = [t for t, _ in p["teams"].most_common()]
        O = v["oB"] + v["oC"]; Dv = v["dB"] + v["dC"]
        r2 = lambda x: round(float(x), 2)
        rec = {"k": k, "n": nm, "t": teams[0], "g": p["g"], "gs": p["gs"], "mn": round(p["min"], 1), "ps": round(p["poss"], 1),
               "r": [r2(x) for x in v["rates"]], "o": r2(O), "d": r2(Dv), "ob": r2(v["oB"]), "oc": r2(v["oC"]), "db": r2(v["dB"]),
               "dc": r2(v["dC"]), "sd": r2(v["sd"]), "pg": r2((O + Dv) * p["poss"] / p["g"] / 100)}
        if len(teams) > 1:
            rec["ts"] = teams
        if espn.get(k):
            rec["e"] = espn[k]
        seasons.setdefault(s, []).append(rec)
    doc = {"status": {
        "computedAt": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=11))).isoformat(timespec="seconds"),
        "through": max((g["date"] for g in sg27), default=None),
        "stints": M["stints"], "players": M["players"], "feats": FEATS, "off": OFF, "def": DEF,
        "weights": {"O": {f: round(a, 4) for f, a in M["aO"].items()}, "D": {f: round(a, 4) for f, a in M["aD"].items()}},
        "homeEdge": round(M["home"], 2), "sigma": round(M["sig"] / 100, 3), "kShr": K_SHR, "lam": LAM, "forward": FORWARD,
        "nbl27Left": bad27},
        "seasons": {s: {"label": label(s), "games": totals.get(s), "used": used.get(s, 0),
                        "avg": [round(float(x), 2) for x in M["centre"][s]],
                        "players": sorted(v, key=lambda r: -(r["o"] + r["d"]))} for s, v in sorted(seasons.items())}}
    fp = os.path.join(root, "data", "impact.json")
    with open(fp + ".tmp", "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(fp + ".tmp", fp)
    top = doc["seasons"][CUR]["players"][:5] if CUR in doc["seasons"] else []
    return [f"impact: {sum(len(v['players']) for v in doc['seasons'].values())} player-seasons, {M['stints']} stints; NBL27 "
            f"{used.get(CUR, 0)} of {ngames27} games in the on-court part" + (f" (left out: {bad27})" if bad27 else "")
            + "; top: " + ", ".join(f"{r['n']} {r['o'] + r['d']:+.1f}" for r in top)]


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    import official as O
    import tempfile
    cache = os.environ.get("NBL_OFFICIAL_CACHE") or tempfile.mkdtemp()
    off = O.load(cache)
    D = lambda n: json.load(open(os.path.join(root, "data", n + ".json")))
    cmp = O.compare(D("games"), D("players"), off)
    for line in run(root, off, cmp["pairs"], O.nbl27_index(off)):
        print(line)


if __name__ == "__main__":
    main()
