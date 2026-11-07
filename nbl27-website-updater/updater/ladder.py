#!/usr/bin/env python3
"""Projected NBL27 final ladder -> data/ladder.json (the website's Projected ladder tab).

Built only from this site's own data (games, players, Player cards) and the NBL's own fixture (prod.rosetta.nbl.com.au,
the same API official.py reads). Deterministic: a fixed random seed, so the same data gives the same ladder.

  1. Team strength = point margin per game, adjusted for opponent and home court, with shooting luck taken out:
     - each team-game's expected 3-pointers and free throws come from the shooters who took them: every player's
       true 3P% / FT% is estimated from this season plus his past seasons on the Player cards (all leagues, older
       seasons count less, capped so a long career can't outweigh what he is doing now), plus the defence's small
       effect on 3P% allowed and the team's own shot-quality effect, each shrunk by how much they really vary;
     - luck = actual - expected (a made three is worth 2.6 points over a miss, which can be rebounded; a free
       throw 0.95); it is taken out of each game's margin before the ratings are fitted.
  2. Ratings: a Bayesian fit of those adjusted margins (team strength ~ N(0, tau^2), tau weighted by the data and a
     prior from typical NBL seasons), so a team's rating is pulled toward average by how few games it has played.
  3. The home edge for future games comes from the actual margins (shooting at home is part of playing at home).
  4. The rest of the season (every regular-season game in the NBL's fixture that is not yet in data/games.json) is
     played out SIMS times, each with team strengths drawn from their uncertainty and game margins ~ N(expected, sd).
     Ladder order in each run: wins, then wins between the tied teams, then points difference in those games, then
     overall points difference.
  5. Projected record = the average wins, rounded so the ladder still adds up (largest remainder).

Usage: python3 ladder.py <repo_root>        writes data/ladder.json (exit 0), or exits 1 and leaves the old file
"""
import collections, datetime, json, math, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402
import official as O  # noqa: E402

SYD = datetime.timezone(datetime.timedelta(hours=11))   # dates as the site uses them (NBL27 runs in AEDT)
SIMS, SEED = 100000, 27
W3, WFT = 2.6, 0.95                     # points per make above expectation: three (vs a miss), free throw
TAU3D, TAUCTX = 0.008, 0.010            # true spread between teams: 3P% allowed (defence), own shot quality
K3, KFT = 140, 30                       # league-average pseudo-attempts in each player's estimate
CAP3, CAPFT = 500, 200                  # most (weighted) past attempts a player's history counts as
DECAY = 0.85                            # weight of a past season per year back
TAU_PRIOR, TAU_LOGSD = 4.25, 0.35       # spread of true team strength in a typical NBL season (pts per game)
HCA_MU, HCA_SD = 2.5, 1.5               # prior for the home edge (pts)
TAUS = np.arange(1.5, 9.01, 0.25)


def load(root, name):
    return json.load(open(os.path.join(root, "data", name + ".json")))


def fixture():
    """Every NBL27 regular-season match from the NBL's API: [{home, away, date (Sydney), status, round, start}]."""
    lst = O.rget(f"nbl/matches/in/season/{O.ROSETTA_YEAR}?limit=400", tempfile.mkdtemp(), fresh=True)["data"]
    out = []
    for m in lst:
        if O.sv(m.get("match_type")).lower() != "regular":
            continue
        start = O.sv(m.get("start_time"))
        d = datetime.datetime.fromisoformat(start[:19]).replace(tzinfo=datetime.timezone.utc).astimezone(SYD)
        out.append({"home": O.TEAMNAME.get(m["home_team"]["name"], m["home_team"]["name"]),
                    "away": O.TEAMNAME.get(m["away_team"]["name"], m["away_team"]["name"]),
                    "date": d.strftime("%Y-%m-%d"), "start": start, "status": O.sv(m.get("match_status")).lower(),
                    "round": O.sv(m.get("round"))})
    return out


def career(b):
    """Weighted past-season makes/attempts from a Player card (NBL seasons, ESPN NBA/college, other leagues)."""
    c = collections.Counter()
    for h in (b.get("historyNBL") or []) + (b.get("history") or []) + (b.get("historyExtra") or []):
        try:
            yr = int(str(h.get("season"))[:4])
        except ValueError:
            continue
        w = DECAY ** max(0, 2026 - yr)
        tot = h.get("tot")
        for k in ("tpm", "tpa", "ftm", "fta"):
            v = tot.get(k) if tot else (h.get(k) or 0) * (h.get("gp") or 0)
            c[k] += w * (v or 0)
    return c


def build(games, players, bios, fx):
    TEAMS = sorted({t for r in fx for t in (r["home"], r["away"])})
    ix = {t: i for i, t in enumerate(TEAMS)}
    N = len(TEAMS)
    # each played game is one fixture match: same home and away teams, the nearest date (within 2 days)
    day = lambda s: datetime.date.fromisoformat(s)
    used = set()
    for g in sorted(games, key=lambda g: g["date"]):
        c = [k for k, r in enumerate(fx) if k not in used and r["home"] == g["home"]["team"] and r["away"] == g["away"]["team"]
             and abs((day(r["date"]) - day(g["date"])).days) <= 2]
        if not c:
            raise RuntimeError(f"{g['date']} {g['away']['team']} at {g['home']['team']} is not in the NBL's fixture")
        used.add(min(c, key=lambda k: abs((day(fx[k]["date"]) - day(g["date"])).days)))
    sched = [r for k, r in enumerate(fx) if k not in used]
    late = [r for r in sched if r["status"] == "complete"]
    total_games = len(fx)

    # ---- shooting: league rates, player estimates, expected makes per team-game ----
    S = lambda k: sum(g[s][k] for g in games for s in ("home", "away"))
    lg3 = S("tpm") / S("tpa") if games else 0.345
    lgft = S("ftm") / S("fta") if games else 0.74
    s3m, s3a, sfm, sfa = (collections.Counter() for _ in range(4))
    for d in players:
        for Lp in d["teams"].values():
            for x in Lp:
                s3m[x["id"]] += x["tpm"]; s3a[x["id"]] += x["tpa"]; sfm[x["id"]] += x["ftm"]; sfa[x["id"]] += x["fta"]
    p3, pft = {}, {}
    for pid in s3a:
        c = career(bios.get(pid) or {})
        f3 = min(1.0, CAP3 / c["tpa"]) if c["tpa"] else 0
        fft = min(1.0, CAPFT / c["fta"]) if c["fta"] else 0
        p3[pid] = (f3 * c["tpm"] + s3m[pid] + K3 * lg3) / (f3 * c["tpa"] + s3a[pid] + K3)
        pft[pid] = (fft * c["ftm"] + sfm[pid] + KFT * lgft) / (fft * c["fta"] + sfa[pid] + KFT)
    Pg = {d["espnId"]: d for d in players}
    e0, ef = {}, {}
    for g in games:
        d = Pg[g["id"].replace("espn-", "")]
        for s in ("home", "away"):
            Lp = d["teams"][g[s]["team"]]
            e0[(g["id"], s)] = sum(x["tpa"] * p3[x["id"]] for x in Lp)
            ef[(g["id"], s)] = sum(x["fta"] * pft[x["id"]] for x in Lp)
    cal3 = S("tpm") / sum(e0.values()) if e0 else 1.0
    calft = S("ftm") / sum(ef.values()) if ef else 1.0
    k_of = lambda a, tau: tau ** 2 / (tau ** 2 + lg3 * (1 - lg3) / a) if a else 0.0
    dm, de, da, om, oe, oa = (collections.Counter() for _ in range(6))
    for g in games:
        for s, o in (("home", "away"), ("away", "home")):
            t, u = g[s]["team"], g[o]["team"]
            dm[u] += g[s]["tpm"]; de[u] += cal3 * e0[(g["id"], s)]; da[u] += g[s]["tpa"]
            om[t] += g[s]["tpm"]; oe[t] += cal3 * e0[(g["id"], s)]; oa[t] += g[s]["tpa"]
    def3 = {t: k_of(da[t], TAU3D) * (dm[t] - de[t]) / da[t] if da[t] else 0.0 for t in TEAMS}
    ctx3 = {t: k_of(oa[t], TAUCTX) * (om[t] - oe[t]) / oa[t] if oa[t] else 0.0 for t in TEAMS}

    rows, luck_t, gp_t = [], collections.Counter(), collections.Counter()
    shoot = {t: collections.Counter() for t in TEAMS}
    for g in games:
        luck = {}
        for s, o in (("home", "away"), ("away", "home")):
            t, u = g[s]["team"], g[o]["team"]
            x3 = cal3 * e0[(g["id"], s)] + g[s]["tpa"] * (def3[u] + ctx3[t])
            xft = calft * ef[(g["id"], s)]
            luck[s] = W3 * (g[s]["tpm"] - x3) + WFT * (g[s]["ftm"] - xft)
            shoot[t]["tpm"] += g[s]["tpm"]; shoot[t]["tpa"] += g[s]["tpa"]; shoot[t]["x3"] += x3
            shoot[u]["otpm"] += g[s]["tpm"]; shoot[u]["otpa"] += g[s]["tpa"]; shoot[u]["ox3"] += x3
            shoot[t]["ftm"] += g[s]["ftm"]; shoot[t]["fta"] += g[s]["fta"]; shoot[t]["xft"] += xft
            shoot[u]["oftm"] += g[s]["ftm"]; shoot[u]["ofta"] += g[s]["fta"]; shoot[u]["oxft"] += xft
        h, a = g["home"]["team"], g["away"]["team"]
        luck_t[h] += luck["home"] - luck["away"]; luck_t[a] += luck["away"] - luck["home"]
        gp_t[h] += 1; gp_t[a] += 1
        m = g["home"]["pts"] - g["away"]["pts"]
        rows.append((ix[h], ix[a], m, m - luck["home"] + luck["away"]))

    # ---- ratings ----
    G = len(rows)
    X = np.zeros((max(G, 1), N + 1))
    for k, (h, a, *_r) in enumerate(rows):
        X[k, h] = 1; X[k, a] = -1; X[k, N] = 1
    y_raw = np.array([r[2] for r in rows] or [0.0], float)
    y_adj = np.array([r[3] for r in rows] or [0.0], float)
    if not G:
        X[:] = 0

    def post(y, tau, sig):
        P0 = np.diag([1 / tau ** 2] * N + [1 / HCA_SD ** 2])
        m0 = np.array([0.0] * N + [HCA_MU])
        C = np.linalg.inv(X.T @ X / sig ** 2 + P0)
        return C @ (X.T @ y / sig ** 2 + P0 @ m0), C

    def ll(y, tau, sig):
        Sg = X @ np.diag([tau ** 2] * N + [HCA_SD ** 2]) @ X.T + sig ** 2 * np.eye(X.shape[0])
        d = y - X @ np.array([0.0] * N + [HCA_MU])
        return -0.5 * (np.linalg.slogdet(Sg)[1] + d @ np.linalg.solve(Sg, d))

    # game-to-game noise of the adjusted and the actual margins (fitted jointly with the spread of strength)
    sig_adj = max(((t, s) for t in TAUS for s in np.arange(5, 16.01, 0.25)), key=lambda z: ll(y_adj, *z))[1] if G >= 20 else 8.0
    sig_raw = max(((t, s) for t in TAUS for s in np.arange(8, 18.01, 0.25)), key=lambda z: ll(y_raw, *z))[1] if G >= 20 else 13.0
    lp = np.array([ll(y_adj, t, sig_adj) - 0.5 * ((math.log(t) - math.log(TAU_PRIOR)) / TAU_LOGSD) ** 2 - math.log(t) for t in TAUS])
    wt = np.exp(lp - lp.max()); wt /= wt.sum()
    POST = [post(y_adj, t, sig_adj) for t in TAUS]
    mu = sum(w * p[0] for w, p in zip(wt, POST))
    tau_m = float((wt * TAUS).sum())
    mr, Cr = post(y_raw, tau_m, sig_raw)
    hca, hca_sd = float(mr[N]), float(math.sqrt(Cr[N, N]))

    # ---- current records ----
    W, L, PF, PA = (collections.Counter() for _ in range(4))
    known = collections.defaultdict(lambda: [0, 0.0])
    close = collections.defaultdict(lambda: [0, 0])
    for g in games:
        h, a, hp, ap = g["home"]["team"], g["away"]["team"], g["home"]["pts"], g["away"]["pts"]
        PF[h] += hp; PA[h] += ap; PF[a] += ap; PA[a] += hp
        (W if hp > ap else L)[h] += 1; (W if ap > hp else L)[a] += 1
        i, j = ix[h], ix[a]
        known[(i, j)][0] += hp > ap; known[(j, i)][0] += ap > hp
        known[(i, j)][1] += hp - ap; known[(j, i)][1] += ap - hp
        if abs(hp - ap) <= 5:
            close[h][0 if hp > ap else 1] += 1; close[a][0 if ap > hp else 1] += 1

    # ---- simulate ----
    rng = np.random.default_rng(SEED)
    ti = rng.choice(len(TAUS), size=SIMS, p=wt)
    R = np.zeros((SIMS, N + 1))
    for k in range(len(TAUS)):
        sel = np.where(ti == k)[0]
        if len(sel):
            R[sel] = rng.multivariate_normal(POST[k][0], POST[k][1], size=len(sel))
    R[:, N] = rng.normal(hca, hca_sd, size=SIMS)
    sh = np.array([ix[r["home"]] for r in sched], int); sa = np.array([ix[r["away"]] for r in sched], int)
    wins = np.tile(np.array([W[t] for t in TEAMS], float), (SIMS, 1))
    pdiff = np.tile(np.array([PF[t] - PA[t] for t in TEAMS], float), (SIMS, 1))
    marg = np.zeros((SIMS, 0))
    if len(sched):
        marg = R[:, sh] - R[:, sa] + R[:, [N]] + rng.normal(0, sig_raw, size=(SIMS, len(sched)))
        marg = np.where(np.abs(marg) < 0.5, np.where(marg >= 0, 0.5, -0.5), marg)     # no draws
        hw = marg > 0
        for k in range(len(sched)):
            wins[:, sh[k]] += hw[:, k]; wins[:, sa[k]] += ~hw[:, k]
            pdiff[:, sh[k]] += marg[:, k]; pdiff[:, sa[k]] -= marg[:, k]
    pairs_k = collections.defaultdict(list)
    for k in range(len(sched)):
        pairs_k[(sh[k], sa[k])].append(k)
    pos = np.zeros((SIMS, N), int)
    for s in range(SIMS):
        w = wins[s]
        order = sorted(range(N), key=lambda i: -w[i])
        out, k0 = [], 0
        while k0 < N:
            grp = [i for i in order if w[i] == w[order[k0]]]
            if len(grp) > 1:
                def tie(i):
                    hw_, hd = 0, 0.0
                    for j in grp:
                        if j == i:
                            continue
                        hw_ += known[(i, j)][0]; hd += known[(i, j)][1]
                        for k in pairs_k.get((i, j), ()):
                            hw_ += marg[s, k] > 0; hd += marg[s, k]
                        for k in pairs_k.get((j, i), ()):
                            hw_ += marg[s, k] < 0; hd -= marg[s, k]
                    return (-hw_, -hd, -pdiff[s, i])
                grp.sort(key=tie)
            out += grp; k0 += len(grp)
        for p, i in enumerate(out):
            pos[s, i] = p + 1

    # ---- summary ----
    games_per = {t: sum(1 for r in fx if t in (r["home"], r["away"])) for t in TEAMS}
    mw = wins.mean(0)
    fl = np.floor(mw + 1e-9).astype(int)
    need = int(total_games - fl.sum())
    for i in np.argsort(-(mw - fl), kind="stable")[:max(0, need)]:
        fl[i] += 1
    poss = [0.5 * sum(g[s]["fga"] - g[s]["oreb"] + g[s]["tov"] + 0.44 * g[s]["fta"] for s in ("home", "away")) for g in games]
    pace = float(np.mean(poss)) if poss else 80.0
    rank = sorted(range(N), key=lambda i: (-fl[i], -mw[i]))
    rem_opp = {t: float(np.mean([mu[ix[r["away"]]] if r["home"] == t else mu[ix[r["home"]]] for r in sched if t in (r["home"], r["away"])]))
               if any(t in (r["home"], r["away"]) for r in sched) else None for t in TEAMS}
    hard = sorted([t for t in TEAMS if rem_opp[t] is not None], key=lambda t: -rem_opp[t])
    r3 = lambda v: round(float(v), 3)
    teams = []
    for p, i in enumerate(rank):
        t = TEAMS[i]
        lo, hi = np.percentile(wins[:, i], [10, 90])
        sc = shoot[t]
        left = [r for r in sched if t in (r["home"], r["away"])]
        teams.append({
            "team": t, "pos": p + 1, "now": [W[t], L[t]], "proj": [int(fl[i]), games_per[t] - int(fl[i])],
            "expW": round(float(mw[i]), 2), "range": [int(round(lo)), int(round(hi))],
            "top2": r3((pos[:, i] <= 2).mean()), "top6": r3((pos[:, i] <= 6).mean()),
            "posProb": [r3(v) for v in np.bincount(pos[:, i], minlength=N + 1)[1:] / SIMS],
            "avgPos": round(float(pos[:, i].mean()), 2),
            "rating": round(float(mu[i]), 2), "net100": round(float(100 * mu[i] / pace), 1),
            "margin": round((PF[t] - PA[t]) / gp_t[t], 1) if gp_t[t] else None,
            "luck": round(luck_t[t] / gp_t[t], 2) if gp_t[t] else None,
            "shoot": {"tp": r3(sc["tpm"] / sc["tpa"]) if sc["tpa"] else None, "xtp": r3(sc["x3"] / sc["tpa"]) if sc["tpa"] else None,
                      "otp": r3(sc["otpm"] / sc["otpa"]) if sc["otpa"] else None, "oxtp": r3(sc["ox3"] / sc["otpa"]) if sc["otpa"] else None,
                      "ft": r3(sc["ftm"] / sc["fta"]) if sc["fta"] else None, "xft": r3(sc["xft"] / sc["fta"]) if sc["fta"] else None,
                      "oft": r3(sc["oftm"] / sc["ofta"]) if sc["ofta"] else None, "oxft": r3(sc["oxft"] / sc["ofta"]) if sc["ofta"] else None},
            "close": close[t], "left": len(left), "leftHome": sum(r["home"] == t for r in left),
            "runHome": round(rem_opp[t], 2) if rem_opp[t] is not None else None,
            "runHomeRank": hard.index(t) + 1 if t in hard else None,
        })
    status = {
        "computedAt": datetime.datetime.now(SYD).isoformat(timespec="seconds"),
        "lastGame": max((g["date"] for g in games), default=None),
        "gamesPlayed": len(games), "gamesLeft": len(sched), "totalGames": total_games,
        "finishedNotInData": [f"{r['date']} {r['away']} at {r['home']}" for r in late],
        "sims": SIMS, "seed": SEED, "homeEdge": round(hca, 2), "homeEdgeSd": round(hca_sd, 2),
        "gameSd": float(sig_raw), "adjGameSd": float(sig_adj), "tau": round(tau_m, 2), "pace": round(pace, 1),
        "lg3": r3(lg3), "lgft": r3(lgft),
        "source": "This site's box scores and Player cards; the remaining fixture from the NBL's own API (prod.rosetta.nbl.com.au)",
        "method": ["Team strength = point margin per game adjusted for opponent and home court, with shooting luck taken out",
                   "Shooting luck = 3s and free throws made minus expected: every shooter's estimated true 3P% / FT% "
                   "(this season plus his past seasons on the Player cards), the defence's small effect on 3P% allowed "
                   "and the team's own shot-quality effect",
                   "Ratings pulled toward average by how few games each team has played",
                   f"The {len(sched)} remaining games played out {SIMS:,} times; projected record = average wins, "
                   "rounded so the ladder adds up",
                   "Ladder ties: wins, then wins between the tied teams, then points difference in those games, then overall",
                   "Injuries are not modelled"],
    }
    return {"status": status, "teams": teams}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    try:
        doc = build(load(root, "games"), load(root, "players"), {b["id"]: b for b in load(root, "bios")}, fixture())
    except Exception as ex:
        print("ladder: not rebuilt, previous projection kept:", repr(ex))
        sys.exit(1)
    json.dump(doc, open(os.path.join(root, "data", "ladder.json"), "w"), separators=(",", ":"))
    st = doc["status"]
    print(f"ladder: {st['gamesPlayed']} games played, {st['gamesLeft']} simulated {SIMS:,} times; home edge "
          f"{st['homeEdge']:+.2f}; " + ", ".join(f"{t['pos']}. {t['team']} {t['proj'][0]}-{t['proj'][1]}" for t in doc["teams"]))


if __name__ == "__main__":
    main()
