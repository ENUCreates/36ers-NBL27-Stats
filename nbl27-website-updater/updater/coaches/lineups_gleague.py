"""Lineups for the Head coaches tab's G League seasons, from ESPN's play-by-play, into data/coachlineups.json (next to
the NBL seasons lineups_hist.py builds; each script replaces only its own seasons).

    python3 lineups_gleague.py <repo_root>        (set OVERSEAS_CACHE to a folder to keep ESPN's downloads)

One-off, like overseas_espn.py: the coach-seasons are the full-season G League stints in coaches/curated.json whose
season is in overseas_espn.SEASONS, and the games are that season's regular-season games as overseas_espn.py reads them
(ESPN season type 2 between its dates), the coach's team's only.

Who is on the floor: ESPN's G League play-by-play, like its NBL one, misses some substitutions (and leaves a name out of
others), so each game is solved the way the NBL27 Lineups tab solves its games - updater/oncourt.py's optimisation:
the game is cut into stretches between dead balls; the solver picks the five for each stretch from the recorded
substitutions, every player who records an action (he must be on the floor), the box score's starters and each
player's box-score minutes and plus-minus, with the fewest unrecorded or ignored substitutions. Each stretch of game
clock goes to its five, and every shot, free throw, rebound and turnover (team turnovers included) goes to the five on
the floor for it (f) or against it (a). Rebounds are the players' only, as ESPN's box scores count them (they leave
team rebounds out), so the lineups add up to the season's numbers (overseas_espn.py's, from the same box scores), as
the NBL seasons' lineups add up to theirs. Points come from the feed's running score (ESPN keeps it right even where a
play's own point value is missing).

A game counts only on oncourt.process()'s terms (as the Lineups tab's games do): the whole game covered by fives, both
teams' points exact, field-goal and free-throw attempts within 2 of the box score, and at least 9 in 10 of the players
within 90 seconds of their box-score minutes. The others are listed and left out.

Game time: 12-minute quarters; overtime as overseas_espn.SEASONS has it (a fixed length, or 'clock': untimed, its
length read from the play-by-play clock). 2025-26's one free throw per trip, worth 2 or 3: as in overseas_espn.py such a
free throw ends a possession, so that season's units also carry 'ftx' (singles + 0.44 x the other free throws), which
the page's possession estimate uses in place of 0.44 x FTA.

Output per coach-season ('<season>|<team>', as the drop-down names it): as lineups_hist.py's, plus 'league' and 'src';
players are [name, ESPN id, null] (the page shows an NBL27 photo for a player on an NBL27 roster, else initials).
"""
import json, os, re, sys, datetime, tempfile, collections, concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import overseas_espn as OE   # noqa: E402
import oncourt               # noqa: E402  (the NBL27 Lineups tab's on-court solver)

K = ("pts", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "tov")
LEAGUE = "NBA G League"
TOV_EXTRA = {"bad pass", "traveling"}           # turnover play types without 'turnover' in their name
NOT_ON_FLOOR = re.compile(r"technical|ejection|timeout|delay", re.I)   # plays that can name a player on the bench


def ty_of(p):
    return (p.get("type") or {}).get("text") or ""


def is_tov(p):
    t = ty_of(p).lower()
    return "turnover" in t or t in TOV_EXTRA


def games_of(season, cfg, team, cache):
    """The team's regular-season games that season, oldest first -> [(event id, date, opponent, home)]."""
    day, end, days = datetime.date.fromisoformat(cfg["from"]), datetime.date.fromisoformat(cfg["to"]), []
    while day <= end:
        days.append(day.strftime("%Y%m%d")); day += datetime.timedelta(days=1)
    name = lambda n: cfg["names"].get(n, n)
    out = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for dd, d in ex.map(lambda dd: (dd, OE.get(f"{OE.BASE}/scoreboard?dates={dd}&limit=200", os.path.join(cache, f"sb_{dd}.json"))), days):
            for e in d.get("events", []):
                if e.get("season", {}).get("type") != 2:
                    continue
                cs = e["competitions"][0]["competitors"]
                nm = {c["homeAway"]: name(c["team"]["displayName"]) for c in cs}
                if team in nm.values() and e["competitions"][0]["status"]["type"]["name"] == "STATUS_FINAL":
                    home = nm.get("home") == team
                    out[e["id"]] = (e["id"], f"{dd[:4]}-{dd[4:6]}-{dd[6:]}", nm["away"] if home else nm["home"], home)
    return sorted(out.values(), key=lambda g: (g[1], g[0]))


def sub_ids(p, names):
    """A substitution -> (coming on, going off), either None where the feed leaves the name out."""
    m = re.match(r"^(.*?)\s*enters the game for\s*(.*)$", p.get("text") or "")
    ids = [x["athlete"]["id"] for x in p.get("participants") or [] if (x.get("athlete") or {}).get("id")]
    if not m:
        return None, None
    a, b = m.group(1).strip(), m.group(2).strip()
    pick = lambda n: next((i for i in ids if names.get(i) == n), None)
    inn, out = (pick(a) if a else None), (pick(b) if b else None)
    rest = [i for i in ids if i not in (inn, out)]
    if a and inn is None and rest:
        inn = rest.pop(0)
    if b and out is None and rest:
        out = rest.pop(0)
    return inn, out


def prepare(d, cfg):
    """ESPN game -> oncourt's game structure (P), with the G League's clock."""
    name = lambda n: cfg["names"].get(n, n)
    tid2club = {t["team"]["id"]: name(t["team"]["displayName"]) for t in d["boxscore"]["teams"]}
    comp = d["header"]["competitions"][0]
    home = next(tid2club[c["team"]["id"]] for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(tid2club[c["team"]["id"]] for c in comp["competitors"] if c["homeAway"] == "away")
    final = {tid2club[c["team"]["id"]]: int(c["score"]) for c in comp["competitors"]}
    pteam, box, names = {}, {}, {}
    for t in d["boxscore"]["players"]:
        st = t["statistics"][0]
        lab = st.get("names") or st.get("labels")
        iM, iP = lab.index("MIN"), lab.index("+/-")
        for a in st["athletes"]:
            pid = a["athlete"]["id"]
            names[pid] = a["athlete"]["displayName"]
            pteam[pid] = tid2club[t["team"]["id"]]
            s = a.get("stats") or []
            num = lambda v: int(str(v).replace("+", "")) if str(v).lstrip("+-").isdigit() else None
            box[pid] = {"min": (num(s[iM]) or 0) if s else 0, "pm": num(s[iP]) if s else None, "starter": bool(a.get("starter")),
                        "name": names[pid]}
    raw = list(d.get("plays") or [])
    pos = {id(x): i for i, x in enumerate(raw)}
    plays = sorted(raw, key=lambda x: (x["period"]["number"], -OE.clk(x["clock"]["displayValue"]), pos[id(x)]))
    start, length = {}, {}
    for k in sorted({p["period"]["number"] for p in plays}):
        pl = [p for p in plays if p["period"]["number"] == k]
        if k <= 4:
            start[k] = 720.0
        elif cfg["ot"] == "clock":
            start[k] = OE.clk(pl[0]["clock"]["displayValue"])
        else:
            start[k] = cfg["ot"] * 60.0
        length[k] = start[k] - (OE.clk(pl[-1]["clock"]["displayValue"]) if (k > 4 and cfg["ot"] == "clock") else 0.0)
    rows, ph, pa = [], 0, 0
    for pl in plays:
        tm = tid2club.get((pl.get("team") or {}).get("id"))
        ins, outs, acts = {}, {}, {}
        if ty_of(pl) == "Substitution":
            inn, out = sub_ids(pl, names)
            if inn and pteam.get(inn):
                ins.setdefault(pteam[inn], []).append(inn)
            if out and pteam.get(out):
                outs.setdefault(pteam[out], []).append(out)
        elif not NOT_ON_FLOOR.search(ty_of(pl)) and not NOT_ON_FLOOR.search(pl.get("text") or ""):
            for x in pl.get("participants") or []:
                a = (x.get("athlete") or {}).get("id")
                if a and pteam.get(a):
                    acts.setdefault(pteam[a], []).append(a)
        h = pl.get("homeScore"); a_ = pl.get("awayScore")
        h = ph if h is None else int(h); a_ = pa if a_ is None else int(a_)
        k = pl["period"]["number"]
        rows.append({"per": k, "L": length[k], "t": start[k] - OE.clk(pl["clock"]["displayValue"]), "type": ty_of(pl),
                     "pl": pl, "tm": tm, "ins": ins, "outs": outs, "acts": acts, "pts": {home: h - ph, away: a_ - pa},
                     "score": (h, a_), "ok": oncourt.change_ok(pl)})
        ph, pa = h, a_
    return {"home": home, "away": away, "clubs": [home, away], "final": final, "pteam": pteam, "box": box,
            "rows": rows, "tid2club": tid2club, "comp": comp, "length": length, "names": names}


def units_of(P, team, sol, single):
    """One team's solved game -> ({frozenset ids: [sec, f, a]}, floor seconds by player, problems)."""
    rows, opp = P["rows"], (P["away"] if team == P["home"] else P["home"])
    during = [None] * len(rows)
    for s, sl in enumerate(sol["slots"]):
        for i in sl["plays"]:
            during[i] = sol["lineup"][s]
    units, floor = {}, collections.Counter()
    tot = {team: collections.Counter(), opp: collections.Counter()}

    def add(lu, sec=0.0, side=None, **kw):
        u = units.setdefault(lu, [0.0, collections.Counter(), collections.Counter()])
        u[0] += sec
        for x in lu:
            floor[x] += sec
        if side is not None:
            for k_, v in kw.items():
                u[1 if side == team else 2][k_] += v
                tot[side][k_] += v
    for per in sorted({r["per"] for r in rows}):
        idx = [i for i, r in enumerate(rows) if r["per"] == per]
        last_t, lu = 0.0, None
        for i in idx:
            r, lu = rows[i], during[i]
            if r["t"] > last_t:
                add(lu, r["t"] - last_t)
                last_t = r["t"]
            for c, v in r["pts"].items():
                if v:
                    add(lu, side=c, pts=v)
            ty, tm, pl = r["type"], r["tm"], r["pl"]
            if ty == "Substitution" or tm not in tot:
                continue
            text = (pl.get("text") or "").lower()
            made = r["pts"].get(tm, 0) > 0
            if "free throw" in ty.lower():
                one = re.search(r"free throw 1 for [23]\b", text) is not None
                add(lu, side=tm, fta=1, ftm=int(made), ftx=1 if one else 0.44)
            elif pl.get("shootingPlay"):
                three = r["pts"].get(tm) == 3 or "three point" in text
                add(lu, side=tm, fga=1, tpa=int(three), fgm=int(made), tpm=int(made and r["pts"].get(tm) == 3))
            elif ty in ("Offensive Rebound", "Defensive Rebound"):
                if "team rebound" not in text:     # players' rebounds only, as ESPN's box scores count them
                    add(lu, side=tm, **{"oreb" if ty.startswith("Off") else "dreb": 1})
            elif is_tov(pl):
                add(lu, side=tm, tov=1)
        if P["length"][per] > last_t and lu is not None:
            add(lu, P["length"][per] - last_t)
    why = []
    total = sum(P["length"].values())
    if abs(sum(u[0] for u in units.values()) - total) > 30:
        why.append(f"floor time {sum(u[0] for u in units.values()) / 60:.1f} of {total / 60:.1f} minutes")
    if any(len(k) != 5 for k in units):
        why.append("a lineup without five")
    for c in (team, opp):
        if tot[c]["pts"] != P["final"][c]:
            why.append(f"{c} points {tot[c]['pts']} in the play-by-play, final score {P['final'][c]}")
    return units, floor, tot, why


def check(d, P, team, floor, tot):
    """oncourt.process()'s tests against ESPN's box score -> problems (empty: the game counts)."""
    why = []
    for t in d["boxscore"]["teams"]:
        c = P["tid2club"][t["team"]["id"]]
        st = {x["name"]: x["displayValue"] for x in t["statistics"]}
        mk = lambda k: [int(v) for v in st[k].split("-")]
        fga, fta = mk("fieldGoalsMade-fieldGoalsAttempted")[1], mk("freeThrowsMade-freeThrowsAttempted")[1]
        if abs(tot[c]["fga"] - fga) > 2 or abs(tot[c]["fta"] - fta) > 2:
            why.append(f"{c} FGA/FTA {tot[c]['fga']}/{tot[c]['fta']} in the play-by-play, box {fga}/{fta}")
    played = [a for a, b in P["box"].items() if P["pteam"][a] == team and ((b["min"] or 0) > 0 or floor.get(a))]
    ok = [a for a in played if abs(floor.get(a, 0) - 60 * (P["box"][a]["min"] or 0)) <= 90]
    if played and len(ok) / len(played) < 0.9:
        why.append("minutes within 90 seconds for " + f"{len(ok)} of {len(played)} players")
    return why


def _solve(args):
    """Worker: one game's solution for one team -> (event id, the five per stretch, fit) or (event id, None, None)."""
    fp, cfg, team = args
    d = json.load(open(fp))
    if not d.get("plays"):
        return os.path.basename(fp), None, None
    sol = oncourt.solve_team(prepare(d, cfg), team)
    if sol is None:
        return os.path.basename(fp), None, None
    return os.path.basename(fp), [sorted(x) for x in sol["lineup"]], sol["fit"]


def solve_all(G, cfg, team, cache):
    """Solve every game (two at a time), keeping the solutions in <cache>/solved.json so a run can pick up again."""
    sp = os.path.join(cache, "solved.json")
    done = json.load(open(sp)) if os.path.exists(sp) else {}
    todo = [(os.path.join(cache, f"sum_{g[0]}.json"), cfg, team) for g in G if f"{g[0]}|{team}" not in done]
    with cf.ProcessPoolExecutor(2) as ex:
        for n, (fn, lineup, fit) in enumerate(ex.map(_solve, todo), 1):
            eid = fn[4:-5]
            done[f"{eid}|{team}"] = {"lineup": lineup, "fit": fit}
            json.dump(done, open(sp, "w"))
            print(f"  solved {n}/{len(todo)} {team} {eid}: {fit}", flush=True)
    return done


def main():
    root = sys.argv[1]
    cache = os.environ.get("OVERSEAS_CACHE") or tempfile.mkdtemp()
    os.makedirs(cache, exist_ok=True)
    cur = json.load(open(os.path.join(HERE, "curated.json")))
    want = [(c["id"], r["season"], r["club"]) for c in cur["coaches"] for r in c.get("career") or []
            if r.get("lg") == "NBA G League" and r.get("season") in OE.SEASONS and r.get("w") is not None and not r.get("part")]
    fp = os.path.join(root, "data", "coachlineups.json")
    res = json.load(open(fp, encoding="utf-8"))
    rep = []
    for cid, season, team in want:
        cfg = OE.SEASONS[season]
        G = games_of(season, cfg, team, cache)
        with cf.ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda g: OE.get(f"{OE.BASE}/summary?event={g[0]}", os.path.join(cache, f"sum_{g[0]}.json")), G))
        keys = K + (("ftx",) if cfg["single"] else ())
        solved = solve_all(G, cfg, team, cache)
        P_, U, games, left, fits = {}, {}, [], [], collections.Counter()
        for gi, (eid, date, opp, home) in enumerate(G):
            d = json.load(open(os.path.join(cache, f"sum_{eid}.json")))
            info = {"id": eid, "date": date, "opp": opp, "home": home}
            games.append(info)
            why = []
            if not d.get("plays"):
                why = ["no play-by-play"]
            else:
                P = prepare(d, cfg)
                if team not in P["clubs"]:
                    sys.exit(f"{eid}: {team} not in the box score")
                s = solved.get(f"{eid}|{team}") or {}
                slots = oncourt.team_slots(P, team)
                sol = {"slots": slots, "lineup": [frozenset(x) for x in s["lineup"]], "fit": s["fit"]} \
                    if s.get("lineup") and len(s["lineup"]) == len(slots) else None
                if sol is None:
                    why = ["no on-court solution"]
                else:
                    units, floor, tot, why = units_of(P, team, sol, cfg["single"])
                    why += check(d, P, team, floor, tot)
                    f = sol["fit"]
                    fits["games"] += 1; fits["unrecorded"] += f["unrecorded"]; fits["ignored"] += f["ignored"]
                    fits["pm_exact"] += sum(1 for a, b in P["box"].items() if P["pteam"][a] == team and b["pm"] is not None
                                            and (b["min"] or 0) > 0 and sum(sl["mar"] for s, sl in enumerate(sol["slots"]) if a in sol["lineup"][s]) == b["pm"])
                    fits["pm_all"] += sum(1 for a, b in P["box"].items() if P["pteam"][a] == team and b["pm"] is not None and (b["min"] or 0) > 0)
            if not why:
                for k, (s_, ff, aa) in units.items():
                    if s_ <= 0 and not any(ff.values()) and not any(aa.values()):
                        continue
                    idx = []
                    for pid in sorted(k):
                        if pid not in P_:
                            P_[pid] = [len(P_), P["names"].get(pid, pid)]
                        idx.append(P_[pid][0])
                    key = tuple(sorted(idx))
                    u = U.get(key)
                    if u is None:
                        u = U[key] = [list(key), 0.0, [0.0] * len(keys), [0.0] * len(keys), []]
                    u[1] += s_
                    for j, x in enumerate(keys):
                        u[2][j] += ff.get(x, 0); u[3][j] += aa.get(x, 0)
                    if gi not in u[4]:
                        u[4].append(gi)
            info["ok"] = not why
            if why:
                left.append(f"{date} v {opp}: {'; '.join(why)}")
        players = [None] * len(P_)
        for pid, (i, nm) in P_.items():
            players[i] = [nm, pid, None]
        num = lambda v: int(v) if float(v).is_integer() else round(v, 2)
        units = [[u[0], round(u[1], 1), [num(v) for v in u[2]], [num(v) for v in u[3]], sorted(u[4])] for u in U.values()]
        entry = {"coach": cid, "league": LEAGUE, "src": "ESPN play-by-play", "games": games, "players": players,
                 "units": sorted(units, key=lambda u: -u[1])}
        if keys != K:
            entry["keys"] = list(keys)
        res["seasons"][f"{season}|{team}"] = entry
        used = sum(1 for g in games if g["ok"])
        rep.append(f"{cid} {season} {team}: {used} of {len(games)} games reconcile, {len(units)} five-man units; solver: "
                   f"{fits['unrecorded']} unrecorded and {fits['ignored']} ignored substitutions in {fits['games']} games, "
                   f"+/- exact for {fits['pm_exact']} of {fits['pm_all']} player-games"
                   + "".join(f"\n   left out {x}" for x in left))
    json.dump(res, open(fp, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
    print("\n".join(rep))
    print(f"-> {fp} ({os.path.getsize(fp) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
