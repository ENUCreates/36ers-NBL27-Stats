#!/usr/bin/env python3
"""Lineups for the Head coaches tab's B.League seasons (Trevor Gleeson's Chiba Jets), into data/coachlineups.json
next to the NBL seasons (lineups_hist.py) and the G League ones (lineups_gleague.py); each script replaces only its
own seasons.

    python3 lineups_bleague.py <repo_root>

One-off. Source: the B.League's own game pages (bleague.jp/game_detail/?ScheduleKey=<id>), which carry each game's
official play-by-play and box score. The site can't be downloaded from a script here, so the games were read in a
browser and saved, cut down to what this needs, as coaches/bleague/chiba_jets.json.gz (per season: the club's 60
regular-season games and its Championship games; only the regular season is used, as for the G League seasons).
Saved per game: k = ScheduleKey, conv = competition, t = tip-off (Unix), h/a = [team id, name, score],
ev = [period, clock, team id, player id, action, success, score], box = the full-game box-score rows
[team id, player id, number, name, starter, minutes, points, 3PM, 3PA, 2PM, 2PA, FTM, FTA, OREB, DREB, TO, +/-,
category (1 = a player)].

Who is on the floor: the play-by-play records every player in (action 86) and out (87), the starters included, so
the fives are read straight from it. Each stretch of game clock goes to the five on the floor, and every shot
(1 = three made, 2 = three missed, 3/4 = two made, 5/6 = two missed), free throw (7 made, 8 missed), rebound
(9 defensive, 10 offensive; players' only, as the box score's player rows count them) and turnover (13, and 17 = team
turnover) goes to the five on the floor for it (f) or against it (a).

A game counts only if the play-by-play reproduces the official box score: five on the floor for each side all game,
both teams' points equal to the final score, and every player's minutes within 30 seconds and plus-minus equal to the
box score's. The others are listed and left out ("ok": false), as on the NBL seasons.

Game time: 10-minute quarters, 5-minute overtimes.
"""
import collections, datetime, gzip, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
K = ("pts", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "tov")
LEAGUE, SRC, TEAM_ID = "B.League B1", "B.League play-by-play", "704"
SEASONS = {"2024": ("2024-25", "Chiba Jets", "trevor-gleeson"), "2025": ("2025-26", "Chiba Jets", "trevor-gleeson")}
NOTE = ("the B.League's own play-by-play, which records every substitution, checked game by game against its official "
        "box scores (both teams' points and every player's minutes and +/-). Regular season only")
JST = datetime.timezone(datetime.timedelta(hours=9))


def secs(t):
    if not t or ":" not in str(t):
        return 0
    m, s = str(t).split(":")[-2:]
    return int(m) * 60 + int(float(s))


def title(n):
    return " ".join(w.capitalize() for w in str(n).split())


def key(n):
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower())


def game(g):
    """-> (units {five: [sec, f, a]}, why not usable or None)."""
    box = {(r[0], r[1]): r for r in g["box"] if r[17] == 1}
    on = collections.defaultdict(set)
    units = {}
    onsec, pm = collections.Counter(), collections.Counter()
    pts = collections.Counter()
    why = []
    per, clock = None, None
    Z = lambda: dict.fromkeys(K, 0)

    def five():
        return tuple(sorted(on[TEAM_ID]))

    def unit():
        return units.setdefault(five(), [0, Z(), Z()])

    def tick(to):
        nonlocal clock
        d = clock - to
        if d > 0:
            for t, s in on.items():
                if len(s) != 5 and f"{len(s)} on the floor" not in " ".join(why):
                    why.append(f"{len(s)} on the floor for {t} in period {per} with {clock}s left")
                for p in s:
                    onsec[(t, p)] += d
            unit()[0] += d
        clock = to

    # overtime plays are filed among the first quarter's in the feed: put the periods in order (order within one kept)
    for e in sorted(g["ev"], key=lambda e: e[0]):
        p_, rest, team, pid, act, ok = e[0], e[1], e[2], e[3], e[4], e[5]
        if p_ != per:
            if per is not None and clock:
                tick(0)
            per, clock = p_, (600 if p_ <= 4 else 300)
        try:
            c = secs(rest)
        except Exception:
            c = clock
        tick(min(c, clock))
        if act in (86, 87):
            (on[team].add if act == 86 else on[team].discard)(pid)
            continue
        if team is None or act is None:
            continue
        mine = team == TEAM_ID
        u = unit()[1 if mine else 2]
        made = {1: 3, 3: 2, 4: 2, 7: 1}.get(act, 0)
        if act in (1, 2, 3, 4, 5, 6):
            u["fga"] += 1; u["fgm"] += act in (1, 3, 4)
            if act in (1, 2):
                u["tpa"] += 1; u["tpm"] += act == 1
        elif act in (7, 8):
            u["fta"] += 1; u["ftm"] += act == 7
        elif act == 9:
            u["dreb"] += 1
        elif act == 10:
            u["oreb"] += 1
        elif act in (13, 17):
            u["tov"] += 1
        if made:
            u["pts"] += made
            pts[team] += made
            for t, s in on.items():
                for p in s:
                    pm[(t, p)] += made if t == team else -made
    if clock:
        tick(0)
    for t, score in ((g["h"][0], g["h"][2]), (g["a"][0], g["a"][2])):
        if pts[t] != score:
            why.append(f"points {pts[t]} vs final score {score} for {t}")
    off = [(r[3], onsec[k] - secs(r[5])) for k, r in box.items() if r[5] and abs(onsec[k] - secs(r[5])) > 30]
    if off:
        why.append("minutes off: " + ", ".join(f"{n} {d:+d}s" for n, d in off[:4]))
    bad = [(r[3], pm[k], r[16]) for k, r in box.items() if r[16] is not None and (onsec[k] or secs(r[5] or "0:00")) and pm[k] != r[16]]
    if bad:
        why.append("plus-minus off: " + ", ".join(f"{n} {a:+d} vs {b:+d}" for n, a, b in bad[:4]))
    return units, ("; ".join(why) or None), {k[1]: r[3] for k, r in box.items() if k[0] == TEAM_ID}


def main(root):
    raw = json.load(gzip.open(os.path.join(HERE, "bleague", "chiba_jets.json.gz"), "rt"))
    bios = json.load(open(os.path.join(root, "data", "bios.json")))
    espn = {key(b["name"]): str(b["id"]) for b in bios if b.get("id")}
    fp = os.path.join(root, "data", "coachlineups.json")
    D = json.load(open(fp))
    for yr, (season, team, coach) in SEASONS.items():
        L = sorted((g for g in raw[yr] if "B1 LEAGUE GAME" in (g.get("conv") or "")), key=lambda g: (int(g["t"]), g["k"]))
        games, names, tot = [], {}, {}
        for gi, g in enumerate(L):
            home = g["h"][0] == TEAM_ID
            opp = title((g["a"] if home else g["h"])[1])
            date = datetime.datetime.fromtimestamp(int(g["t"]), JST).date().isoformat()
            units, why, nm = game(g)
            names.update(nm)
            games.append({"id": str(g["k"]), "date": date, "opp": opp, "home": home, "ok": not why})
            if why:
                print(f"  {season} {date} v {opp}: left out ({why})")
                continue
            for five, (sec, f, a) in units.items():
                if len(five) != 5:
                    continue
                t = tot.setdefault(five, [0, dict.fromkeys(K, 0), dict.fromkeys(K, 0), []])
                t[0] += sec
                for k in K:
                    t[1][k] += f[k]; t[2][k] += a[k]
                t[3].append(gi)
        ids = sorted({p for five in tot for p in five}, key=lambda p: names.get(p, ""))
        ix = {p: i for i, p in enumerate(ids)}
        players = [[names.get(p) or p, espn.get(key(names.get(p))), None] for p in ids]
        units = [[sorted(ix[p] for p in five), float(sec), [f[k] for k in K], [a[k] for k in K], gl]
                 for five, (sec, f, a, gl) in sorted(tot.items(), key=lambda kv: -kv[1][0]) if sec > 0 or any(f.values()) or any(a.values())]
        D["seasons"][f"{season}|{team}"] = {"coach": coach, "league": LEAGUE, "src": SRC, "note": NOTE, "games": games, "players": players, "units": units}
        used = sum(1 for x in games if x["ok"])
        f = {k: sum(u[2][i] for u in units) for i, k in enumerate(K)}
        a = {k: sum(u[3][i] for u in units) for i, k in enumerate(K)}
        print(f"{season} {team}: {used} of {len(games)} games used, {len(units)} fives, {len(players)} players, "
              f"{sum(u[1] for u in units) / 60:.0f} min; for {f['pts']} pts {f['fgm']}/{f['fga']} FG, against {a['pts']} pts")
    tmp = fp + ".tmp"
    json.dump(D, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, fp)


if __name__ == "__main__":
    main(sys.argv[1])
