#!/usr/bin/env python3
"""Matchup facts for every NBL27 game -> data/matchups.json (Projected ladder tab: click a game in the round-by-round
tips). Everything comes from this site's own data (games, players, play-by-play events, shot zones, the Player cards,
the injury list) and data/ladder.json (the model's team strengths, home edge and each game's tip), so the facts
always match the percentage shown.

For each game:
  why     how the model got the percentage: both teams' strength (point margin a game on a neutral court, with
          shooting luck taken out, adjusted for opponents and pulled toward average for small samples), the home
          edge, the expected margin and the chance; plus how each team's strength is built (actual margin ->
          without shooting luck -> after opponents and sample size)
  facts   the key matchup facts, most important first, each tagged: in the % (strength, shooting luck) or not
          (injuries, rest), or context (head to head, form, style of play, home/away, close games)
  tape    both teams' season numbers with league ranks (the same formulas as the League table)
  h2h     this season's earlier meetings; rest days; injury list; leading scorers

Games still to play are worked out fresh every day from the latest data. A played game keeps the facts as they stood
before it (frozen from the previous run, like its tip); games played before facts were kept get back-tested facts
(only the games before that day; no injury list). Run after ladder.py and after injuries.py (both call it).

Writes data/matchups.json (games still to play + today's team numbers) and data/matchups_played.json (played games).
Usage: python3 matchups.py <repo_root>
"""
import collections, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ladder as L  # noqa: E402

SHORT = {"Adelaide 36ers": "Adelaide", "Brisbane Bullets": "Brisbane", "Cairns Taipans": "Cairns",
         "Illawarra Hawks": "Illawarra", "Melbourne United": "Melbourne", "New Zealand Breakers": "NZ Breakers",
         "Perth Wildcats": "Perth", "South East Melbourne Phoenix": "SE Melbourne", "Sydney Kings": "Sydney",
         "Tasmania JackJumpers": "Tasmania"}
# stat -> True if higher is better, False if lower is better, None if neither (ranked highest first)
DIRS = {"rating": True, "net": True, "ortg": True, "drtg": False, "pace": None, "margin": True, "efg": True,
        "oefg": False, "tov": False, "otov": True, "orb": True, "drb": True, "ftr": True, "oftr": False, "tpar": None,
        "otpar": None, "tpp": True, "otpp": False, "rim": None, "orim": None, "trans": True, "otrans": False, "sc": True,
        "osc": False, "luck": None, "ppg": True, "oppg": False}


def ordn(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def pct(x, d=1):
    return "–" if x is None else f"{100 * x:.{d}f}%"


def sgn(x, d=1):
    return "–" if x is None else f"{x:+.{d}f}".replace("-", "−")


def dshort(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.day} {d:%b}"


def poss(name):
    return name + ("'" if name.endswith("s") else "'s")


def load(root, name, default=None):
    fp = os.path.join(root, "data", name + ".json")
    return json.load(open(fp)) if os.path.exists(fp) else default


# ---------------------------------------------------------------- season numbers
def season(games, events, shots, players, TEAMS):
    """Each team's season-to-date numbers from these games, the same formulas as the League table, with league ranks."""
    lo = ld = 0
    for g in games:
        lo += g["home"]["oreb"] + g["away"]["oreb"]; ld += g["home"]["dreb"] + g["away"]["dreb"]
    lg_orb = lo / (lo + ld) if lo + ld else 0.27
    est = lambda s: s["oreb"] if s.get("oreb") is not None else lg_orb * (s["fga"] - s["fgm"])
    poss = lambda s: s["fga"] - est(s) + s["tov"] + 0.44 * s["fta"]
    K = ("fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "tov", "pts")
    T = {t: {"own": collections.Counter(), "opp": collections.Counter(), "poss": 0.0, "mins": 0, "log": [], "gp": 0}
         for t in TEAMS}
    for g in sorted(games, key=lambda g: g["date"]):
        p = (poss(g["away"]) + poss(g["home"])) / 2
        mins = round(max(g["away"]["min"], g["home"]["min"]) / 25) * 5
        for s, o, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
            t = T[s["team"]]
            t["gp"] += 1; t["poss"] += p; t["mins"] += mins
            for k in K:
                t["own"][k] += s.get(k) or 0; t["opp"][k] += o.get(k) or 0
            t["log"].append({"date": g["date"], "opp": o["team"], "home": home, "pts": s["pts"], "opts": o["pts"]})
    ev = {t: collections.Counter() for t in TEAMS}
    for d in events:
        for t, X in d["teams"].items():
            O = [Y for u, Y in d["teams"].items() if u != t]
            if t in ev and X.get("ok") and O and O[0].get("ok"):
                ev[t]["g"] += 1; ev[t]["tr"] += X["trans"][1]; ev[t]["sc"] += X["sc"][1]
                ev[t]["otr"] += O[0]["trans"][1]; ev[t]["osc"] += O[0]["sc"][1]
    zn = {t: collections.Counter() for t in TEAMS}
    for d in shots:
        for t, Z in d["teams"].items():
            O = [Y for u, Y in d["teams"].items() if u != t]
            if t in zn and O and Z.get("ok", True) is not False and O[0].get("ok", True) is not False:
                fga = sum(Z[k]["a"] for k in ("ra", "paint", "mid", "c3", "ab3"))
                ofga = sum(O[0][k]["a"] for k in ("ra", "paint", "mid", "c3", "ab3"))
                if fga and ofga:
                    zn[t]["ra"] += Z["ra"]["a"]; zn[t]["fga"] += fga; zn[t]["ora"] += O[0]["ra"]["a"]; zn[t]["ofga"] += ofga
    S = {}
    div = lambda a, b: a / b if b else None
    for t, x in T.items():
        s, o, n = x["own"], x["opp"], x["gp"]
        if not n:
            S[t] = {"gp": 0}
            continue
        lg = x["log"]
        home = [r for r in lg if r["home"]]; away = [r for r in lg if not r["home"]]
        wl = lambda rs: [sum(r["pts"] > r["opts"] for r in rs), sum(r["pts"] < r["opts"] for r in rs)]
        l5 = lg[-5:]
        streak = 0
        for r in reversed(lg):
            w = r["pts"] > r["opts"]
            if streak == 0 or (streak > 0) == w:
                streak += 1 if w else -1
            else:
                break
        e = ev[t]
        S[t] = {"gp": n, "wl": wl(lg), "home": wl(home), "away": wl(away), "last5": "".join("W" if r["pts"] > r["opts"] else "L" for r in l5),
                "m5": round(sum(r["pts"] - r["opts"] for r in l5) / len(l5), 1), "streak": streak,
                "close": wl([r for r in lg if abs(r["pts"] - r["opts"]) <= 5]), "lastDate": lg[-1]["date"],
                "ppg": s["pts"] / n, "oppg": o["pts"] / n, "margin": (s["pts"] - o["pts"]) / n,
                "pace": 40 * x["poss"] / x["mins"], "ortg": 100 * s["pts"] / x["poss"], "drtg": 100 * o["pts"] / x["poss"],
                "net": 100 * (s["pts"] - o["pts"]) / x["poss"],
                "efg": div(s["fgm"] + .5 * s["tpm"], s["fga"]), "oefg": div(o["fgm"] + .5 * o["tpm"], o["fga"]),
                "tov": div(s["tov"], s["fga"] + .44 * s["fta"] + s["tov"]), "otov": div(o["tov"], o["fga"] + .44 * o["fta"] + o["tov"]),
                "orb": div(s["oreb"], s["oreb"] + o["dreb"]), "drb": div(s["dreb"], s["dreb"] + o["oreb"]),
                "ftr": div(s["fta"], s["fga"]), "oftr": div(o["fta"], o["fga"]), "tpar": div(s["tpa"], s["fga"]),
                "otpar": div(o["tpa"], o["fga"]), "tpp": div(s["tpm"], s["tpa"]), "otpp": div(o["tpm"], o["tpa"]),
                "rim": div(zn[t]["ra"], zn[t]["fga"]), "orim": div(zn[t]["ora"], zn[t]["ofga"]),
                "trans": div(e["tr"], e["g"]), "otrans": div(e["otr"], e["g"]), "sc": div(e["sc"], e["g"]), "osc": div(e["osc"], e["g"])}
    # ladder position now: win %, then points difference
    have = [t for t in TEAMS if S[t]["gp"]]
    lad = sorted(have, key=lambda t: (-S[t]["wl"][0] / S[t]["gp"], -S[t]["margin"]))
    for i, t in enumerate(lad):
        S[t]["ladder"] = i + 1
    for k, good in DIRS.items():
        vals = [(t, S[t].get(k)) for t in have if S[t].get(k) is not None]
        order = sorted(vals, key=lambda tv: -tv[1] if good in (True, None) else tv[1])
        for i, (t, v) in enumerate(order):
            S[t].setdefault("rk", {})[k] = i + 1
    return S


def player_lines(players, TEAMS):
    P = {t: {} for t in TEAMS}
    for d in players:
        for t, Lp in d["teams"].items():
            for x in Lp:
                if t not in P or not (x.get("min") or 0) > 0:
                    continue
                p = P[t].setdefault(x["id"], {"id": x["id"], "name": x["name"], "gp": 0, "min": 0, "pts": 0})
                p["gp"] += 1; p["min"] += x["min"]; p["pts"] += x.get("pts") or 0
    return P


# ---------------------------------------------------------------- one game
def ratings_now(lad):
    """The model's numbers per team from data/ladder.json (what today's tips use)."""
    R = {}
    for t in lad["teams"]:
        R[t["team"]] = {"rating": t["rating"], "margin": t["margin"], "luck": t["luck"], "shoot": t["shoot"]}
    return R, lad["status"]["homeEdge"], lad["status"].get("gameSd")


def ratings_then(M, TEAMS):
    """The same numbers from a fit on only earlier games (back-tested facts)."""
    R = {}
    for t in TEAMS:
        i = M["ix"][t]; n = M["gp_t"][t]; sc = M["shoot"][t]
        r3 = lambda a, b: round(a / b, 3) if b else None
        R[t] = {"rating": round(float(M["mu"][i]), 2), "margin": None, "luck": round(M["luck_t"][t] / n, 2) if n else None,
                "shoot": {"tp": r3(sc["tpm"], sc["tpa"]), "xtp": r3(sc["x3"], sc["tpa"]), "otp": r3(sc["otpm"], sc["otpa"]),
                          "oxtp": r3(sc["ox3"], sc["otpa"]), "ft": r3(sc["ftm"], sc["fta"]), "xft": r3(sc["xft"], sc["fta"]),
                          "oft": r3(sc["oftm"], sc["ofta"]), "oxft": r3(sc["oxft"], sc["ofta"])}}
    return R, round(M["hca"], 2), M["sig_raw"]


def rest(fx, g, team):
    """Days since this team's previous game before this one, and how many games in a row it has been away/at home."""
    prior = [x for x in fx if x["start"] < g["start"] and team in (x["home"], x["away"])]
    if not prior:
        return {"days": None}
    last = prior[-1]
    days = (datetime.date.fromisoformat(g["date"]) - datetime.date.fromisoformat(last["date"])).days
    home_now = g["home"] == team
    run = 1
    for x in reversed(prior):
        if (x["home"] == team) == home_now:
            run += 1
        else:
            break
    return {"days": days, "last": last["date"], "lastVs": last["away"] if last["home"] == team else last["home"],
            "lastHome": last["home"] == team, "run": run}


def snapshot(g, fx, games_before, S, R, hca, gsd, P, inj, state, asof, p=None, m=None):
    H, A = g["home"], g["away"]
    nH, nA = SHORT.get(H, H), SHORT.get(A, A)
    sh, sa = S[H], S[A]
    ranked = sorted([t for t in R if R[t]["rating"] is not None], key=lambda t: -R[t]["rating"])
    rk = {t: i + 1 for i, t in enumerate(ranked)}
    lk = sorted([t for t in R if R[t]["luck"] is not None], key=lambda t: R[t]["luck"])
    if p is None:
        p, m = g.get("pHome"), g.get("margin")
    why = {"rH": R[H]["rating"], "rA": R[A]["rating"], "rkH": rk.get(H), "rkA": rk.get(A), "hca": hca, "margin": m,
           "p": p, "sd": gsd, "build": {}}
    for t in (H, A):
        mg = S[t].get("margin") if S[t]["gp"] else None
        why["build"][t] = {"margin": round(mg, 1) if mg is not None else None, "luck": R[t]["luck"],
                           "adj": round(mg - R[t]["luck"], 1) if mg is not None and R[t]["luck"] is not None else None,
                           "rating": R[t]["rating"]}
    F = []
    # 1. shooting luck, the biggest single driver of the gap between record and rating (in the %)
    for t in (H, A):
        x, nm = R[t], SHORT.get(t, t)
        if x["luck"] is None or abs(x["luck"]) < 1.5 or not S[t]["gp"]:
            continue
        s = x["shoot"] or {}
        parts = []
        ok = lambda a, b: s.get(a) is not None and s.get(b) is not None
        if ok("tp", "xtp") and abs(s["tp"] - s["xtp"]) >= 0.01:
            parts.append(f"they're shooting {pct(s['tp'])} from three, {'below' if s['tp'] < s['xtp'] else 'above'} the {pct(s['xtp'])} their shooters usually make")
        if ok("otp", "oxtp") and abs(s["otp"] - s["oxtp"]) >= 0.01:
            parts.append(f"opponents have hit {'just ' if s['otp'] < s['oxtp'] else ''}{pct(s['otp'])} from three against them ({pct(s['oxtp'])} expected)")
        if ok("ft", "xft") and abs(s["ft"] - s["xft"]) >= 0.03:
            parts.append(f"they're making {pct(s['ft'])} of their free throws (usually {pct(s['xft'])})")
        if ok("oft", "oxft") and abs(s["oft"] - s["oxft"]) >= 0.03:
            parts.append(f"opponents are making {pct(s['oft'])} of their free throws ({pct(s['oxft'])} expected)")
        most = ""
        if x["luck"] < 0 and lk and lk[0] == t:
            most = " (the most in the league)"
        elif x["luck"] > 0 and lk and lk[-1] == t:
            most = " (the most in the league)"
        verb = f"lost {abs(x['luck']):.1f} points a game to" if x["luck"] < 0 else f"gained {x['luck']:.1f} points a game from"
        F.append({"k": "model", "t": f"{nm} have {verb} shooting luck{most}" + (f": {'; '.join(parts)}" if parts else "")
                  + f". The model takes luck out, so it rates them {ordn(rk[t])} on strength; they're {ordn(S[t]['ladder'])} on the ladder at {S[t]['wl'][0]}-{S[t]['wl'][1]}."})
    # 2. injury list (not in the %)
    if inj is not None:
        for t in (H, A):
            nm, lst = SHORT.get(t, t), [r for r in inj.get(t, []) if r["cls"] != "retired"]
            if not lst:
                F.append({"k": "out", "t": f"Nobody on {poss(nm)} injury list."})
                continue
            lab = lambda r: f"{r['name']} ({r['injury'] or 'injury'}, " + ("out for the season" if r["cls"] == "season" else "day-to-day" if r["cls"] == "dtd" else (r["ret"] or "TBC")) + ")"
            lst = sorted(lst, key=lambda r: -(r.get("mpg") or 0))
            txt = f"{nm} injury list: " + ", ".join(lab(r) for r in lst) + "."
            new = [r for r in lst if (r.get("gp") or 0) >= 3 and (r.get("mpg") or 0) >= 15 and r.get("missed", 0) <= 2 and r["cls"] != "dtd"]
            old = [r for r in lst if (r.get("mpg") or 0) >= 15 and r.get("teamGames") and r.get("missed", 0) >= r["teamGames"] / 2]
            if new:
                txt += " " + " and ".join(f"{r['name']} ({r['ppg']:.1f} points, {r['mpg']:.0f} minutes a game)" for r in new) \
                       + (" has" if len(new) == 1 else " have") + f" only just gone out, so {poss(nm)} numbers still include " + ("him." if len(new) == 1 else "them.")
            if old:
                txt += " " + " and ".join(f"{r['name']} has missed {r['missed']} of {r['teamGames']} games" for r in old) \
                       + ", so that absence is already mostly in their numbers."
            F.append({"k": "out", "t": txt})
    # 3. head to head this season
    met = sorted([x for x in games_before if {x["home"]["team"], x["away"]["team"]} == {H, A}], key=lambda x: x["date"])
    h2h = [{"date": x["date"], "home": x["home"]["team"], "away": x["away"]["team"], "hs": x["home"]["pts"], "as": x["away"]["pts"]} for x in met]
    if not met:
        F.append({"k": "h2h", "t": "First meeting this season."})
    else:
        wH = sum((x["home"]["team"] == H) == (x["home"]["pts"] > x["away"]["pts"]) for x in met)
        lead = f"{nH} lead {wH}-{len(met) - wH}" if wH > len(met) - wH else f"{nA} lead {len(met) - wH}-{wH}" if wH < len(met) - wH else f"Split {wH}-{wH}"
        res = []
        for x in met:
            hw = x["home"]["pts"] > x["away"]["pts"]
            win = x["home"]["team"] if hw else x["away"]["team"]
            hi, lo_ = max(x["home"]["pts"], x["away"]["pts"]), min(x["home"]["pts"], x["away"]["pts"])
            res.append(f"{SHORT.get(win, win)} won {hi}–{lo_} {'at home' if hw else 'away'} on {dshort(x['date'])}")
        F.append({"k": "h2h", "t": f"Head to head this season: {lead}. " + "; ".join(res) + "."})
    # 4. style of play: where one side's strength meets the other's weakness (biggest rank gaps)
    pairs = [("efg", "oefg", "Shooting", "have a {v} eFG%", "allow {v}"),
             ("tov", "otov", "Turnovers", "turn it over on {v} of possessions", "force turnovers on {v}"),
             ("orb", "drb", "Offensive rebounds", "rebound {v} of their own misses", "rebound {v} of opponents' misses"),
             ("ftr", "oftr", "Free throws", "get {v} free throws a shot", "allow {v}"),
             ("trans", "otrans", "Transition", "score {v} transition points a game", "allow {v}"),
             ("sc", "osc", "Second chances", "score {v} second-chance points a game", "allow {v}")]
    fmt = {"efg": pct, "oefg": pct, "tov": pct, "otov": pct, "orb": pct, "drb": pct, "ftr": lambda v: f"{v:.2f}",
           "oftr": lambda v: f"{v:.2f}", "trans": lambda v: f"{v:.1f}", "otrans": lambda v: f"{v:.1f}",
           "sc": lambda v: f"{v:.1f}", "osc": lambda v: f"{v:.1f}"}
    edges = []
    for X, Y in ((H, A), (A, H)):
        for ko, kd, label, to, td in pairs:
            ro, rd = S[X].get("rk", {}).get(ko), S[Y].get("rk", {}).get(kd)
            if ro is None or rd is None:
                continue
            e = rd - ro           # > 0: the offence is better ranked than the defence it meets
            if abs(e) >= 5:
                nX, nY = SHORT.get(X, X), SHORT.get(Y, Y)
                win = nX if e > 0 else nY
                edges.append((abs(e), {"k": "style", "t": f"{label}: {nX} {to.format(v=fmt[ko](S[X][ko]))} ({ordn(ro)}); "
                                                         f"{nY} {td.format(v=fmt[kd](S[Y][kd]))} ({ordn(rd)}). Edge {win}."}))
    for _, f in sorted(edges, key=lambda z: -z[0])[:3]:
        F.append(f)
    rpH, rpA = sh.get("rk", {}).get("pace"), sa.get("rk", {}).get("pace")
    if rpH and rpA and abs(rpH - rpA) >= 6:
        fast, slow = (H, A) if rpH < rpA else (A, H)
        n_ = len([t for t in S if S[t]["gp"]])
        F.append({"k": "style", "t": f"Pace: {SHORT.get(fast, fast)} play the {ordn(S[fast]['rk']['pace'])}-fastest pace ({S[fast]['pace']:.1f} possessions a game), "
                                     f"{SHORT.get(slow, slow)} the {ordn(n_ + 1 - S[slow]['rk']['pace'])}-slowest ({S[slow]['pace']:.1f})."})
    # 5. form (the model weights every game the same)
    if sh["gp"] >= 3 and sa["gp"] >= 3:
        w5 = lambda s: f"{s['last5'].count('W')}-{s['last5'].count('L')}"
        txt = f"Last five: {nH} {w5(sh)} (average margin {sgn(sh['m5'])}), {nA} {w5(sa)} ({sgn(sa['m5'])})."
        for t, s in ((H, sh), (A, sa)):
            if abs(s["streak"]) >= 3:
                txt += f" {SHORT.get(t, t)} have {'won' if s['streak'] > 0 else 'lost'} {abs(s['streak'])} straight."
        F.append({"k": "form", "t": txt})
    # 6. rest (not in the %)
    rH, rA = rest(fx, g, H), rest(fx, g, A)
    if rH["days"] is not None and rA["days"] is not None and (min(rH["days"], rA["days"]) <= 1 or abs(rH["days"] - rA["days"]) >= 3):
        d_ = lambda r: f"{r['days']} day{'s' if r['days'] != 1 else ''}"
        F.append({"k": "rest", "t": f"Rest: {nH} last played {dshort(rH['last'])} ({d_(rH)} before), {nA} {dshort(rA['last'])} ({d_(rA)})."
                  + (" Back-to-back for " + " and ".join(SHORT.get(t, t) for t, r in ((H, rH), (A, rA)) if r["days"] <= 1) + "." if min(rH["days"], rA["days"]) <= 1 else "")})
    if rA["days"] is not None and rA["run"] >= 3:
        F.append({"k": "rest", "t": f"{poss(nA)} {ordn(rA['run'])} straight away game."})
    # 7. home and away records
    if sh["gp"] and sa["gp"]:
        hw, aw = sh["home"], sa["away"]
        ext = lambda r: sum(r) >= 3 and (r[0] / sum(r) <= 0.25 or r[0] / sum(r) >= 0.75)
        if ext(hw) or ext(aw):
            F.append({"k": "split", "t": f"{nH} are {hw[0]}-{hw[1]} at home; {nA} are {aw[0]}-{aw[1]} on the road."})
    # 8. close games (they tend to even out)
    for t, s in ((H, sh), (A, sa)):
        if s["gp"] and abs(s["close"][0] - s["close"][1]) >= 3:
            F.append({"k": "close", "t": f"{SHORT.get(t, t)} are {s['close'][0]}-{s['close'][1]} in games decided by five points or fewer; close-game records tend to even out."})

    return {"state": state, "asOf": asof, "why": why, "facts": F, "h2h": h2h, "rest": {H: rH, A: rA}}


RATE3 = ("efg", "oefg", "tov", "otov", "orb", "drb", "ftr", "oftr", "tpar", "tpp", "otpp", "rim")
PER1 = ("ppg", "oppg", "margin", "pace", "ortg", "drtg", "net", "trans", "otrans", "sc", "osc")


def team_part(t, S, R, P, inj):
    """One team's season numbers with ranks, model strength, leading scorers and injury list (as of these data)."""
    s = S[t]
    ranked = sorted([u for u in R if R[u]["rating"] is not None], key=lambda u: -R[u]["rating"])
    tape = {k: s.get(k) for k in ("gp", "wl", "home", "away", "last5", "streak", "close", "ladder")}
    tape.update({k: (round(s[k], 3) if s.get(k) is not None else None) for k in RATE3})
    tape.update({k: (round(s[k], 1) if s.get(k) is not None else None) for k in PER1})
    tape["rk"] = {k: v for k, v in s.get("rk", {}).items() if k in RATE3 + PER1}
    tape.update(rating=R[t]["rating"], ratingRk=ranked.index(t) + 1 if t in ranked else None, luck=R[t]["luck"],
                shoot={k: (R[t]["shoot"] or {}).get(k) for k in ("tp", "xtp", "otp", "oxtp")})
    ps = [x for x in P[t].values() if x["gp"] >= min(3, max(1, s["gp"] // 2))]
    out = {r["id"]: r for r in (inj or {}).get(t, []) if r.get("id")} if inj is not None else {}
    lead = [{"id": x["id"], "name": x["name"], "gp": x["gp"], "ppg": round(x["pts"] / x["gp"], 1), "mpg": round(x["min"] / x["gp"], 1),
             **({"out": out[x["id"]]["cls"]} if x["id"] in out else {})} for x in sorted(ps, key=lambda x: -x["pts"] / x["gp"])[:3]]
    part = {"tape": tape, "leaders": lead}
    if inj is not None:
        part["injuries"] = [{k: r.get(k) for k in ("id", "name", "cls", "status", "injury", "ret", "roundDate", "gp", "mpg", "ppg", "missed", "teamGames", "playedLast")}
                            for r in inj.get(t, [])]
    return part


# ---------------------------------------------------------------- all games
def build(root):
    lad = load(root, "ladder")
    if not lad or not lad.get("games"):
        raise RuntimeError("data/ladder.json has no games (run ladder.py first)")
    games, players = load(root, "games", []), load(root, "players", [])
    bios = {b["id"]: b for b in load(root, "bios", [])}
    events, shots = load(root, "events", []), load(root, "shots", [])
    injd = load(root, "injuries")
    inj = collections.defaultdict(list)
    for r in (injd or {}).get("players", []):
        inj[r["team"]].append(r)
    prev = {**((load(root, "matchups_played") or {}).get("games", {})), **((load(root, "matchups") or {}).get("games", {}))}
    TEAMS = sorted(t["team"] for t in lad["teams"])
    fx = sorted(lad["games"], key=lambda x: x["start"])
    gid = {}
    for x in fx:                                  # fixture id -> the game in games.json (same teams, date within 2 days)
        if x.get("hs") is None:
            continue
        c = [g for g in games if g["home"]["team"] == x["home"] and g["away"]["team"] == x["away"]
             and abs((datetime.date.fromisoformat(g["date"]) - datetime.date.fromisoformat(x["date"])).days) <= 2]
        if c:
            gid[x["id"]] = min(c, key=lambda g: abs((datetime.date.fromisoformat(g["date"]) - datetime.date.fromisoformat(x["date"])).days))
    now = datetime.datetime.now(L.SYD).isoformat(timespec="seconds")
    S_now = season(games, events, shots, players, TEAMS)
    P_now = player_lines(players, TEAMS)
    R_now, hca_now, gsd_now = ratings_now(lad)
    inj_now = inj if injd else None
    teams_now = {t: team_part(t, S_now, R_now, P_now, inj_now) for t in TEAMS}
    prev_teams = (load(root, "matchups") or {}).get("teams", {})
    out, cache, n = {}, {}, collections.Counter()
    for x in fx:
        pv = prev.get(x["id"])
        if x.get("hs") is None:
            out[x["id"]] = snapshot(x, fx, games, S_now, R_now, hca_now, gsd_now, P_now, inj_now, "pre", now)
            n["pre"] += 1
        elif pv and pv.get("state") == "pre":
            # played since the last run: keep the facts as they stood before it, with both teams' numbers of that day
            out[x["id"]] = dict(pv, state="frozen", teams={t: prev_teams.get(t) for t in (x["home"], x["away"])}); n["frozen now"] += 1
        elif pv and pv.get("state") in ("frozen", "backtest"):
            out[x["id"]] = pv; n["kept"] += 1
        else:
            g = gid.get(x["id"])
            d = g["date"] if g else x["date"]
            if d not in cache:
                gb = [y for y in games if y["date"] < d]
                pb = [y for y in players if y["date"] < d]
                M = L.rate(gb, pb, bios, TEAMS)
                Rd, hd, sd = ratings_then(M, TEAMS)
                cache[d] = (gb, season(gb, [y for y in events if y["date"] < d], [y for y in shots if y["date"] < d], pb, TEAMS),
                            Rd, hd, sd, player_lines(pb, TEAMS))
            gb, Sd, Rd, hd, sd, Pd = cache[d]
            tip = x.get("tip") or {}
            snap = snapshot(x, fx, gb, Sd, Rd, hd, sd, Pd, None, "backtest", d, tip.get("p"), tip.get("m"))
            snap["teams"] = {t: team_part(t, Sd, Rd, Pd, None) for t in (x["home"], x["away"])}
            out[x["id"]] = snap
            n["backtest"] += 1
    st = {"computedAt": now, "games": len(out), "counts": dict(n), "injuriesRead": (injd or {}).get("status", {}).get("listRead")}
    # two files: games still to play (with today's team numbers, read when the tab opens a game) and games played
    # (frozen or back-tested, each with its own team numbers; read only when a played game is opened)
    return ({"status": st, "teams": teams_now, "games": {k: v for k, v in out.items() if v["state"] == "pre"}},
            {"status": st, "games": {k: v for k, v in out.items() if v["state"] != "pre"}})


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    try:
        doc = build(root)
    except Exception as ex:
        print("matchups: not rebuilt, previous facts kept:", repr(ex))
        sys.exit(1)
    for name, part in zip(("matchups", "matchups_played"), doc):
        fp = os.path.join(root, "data", name + ".json")
        with open(fp + ".tmp", "w") as f:
            json.dump(part, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(fp + ".tmp", fp)
    doc = doc[0]
    c = doc["status"]["counts"]
    print(f"matchups: {doc['status']['games']} games ({', '.join(f'{v} {k}' for k, v in c.items())}); "
          f"injury list read {doc['status']['injuriesRead']}")


if __name__ == "__main__":
    main()
