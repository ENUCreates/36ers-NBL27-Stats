#!/usr/bin/env python3
"""Lineups for the Head coaches tab's LNB (France, Betclic Elite) seasons - Will Weaver's Paris Basketball 2022-23 -
into data/coachlineups.json next to the other leagues' (lineups_hist.py, lineups_gleague.py, lineups_bleague.py,
lineups_bbl.py); each script replaces only its own seasons.

    python3 lineups_lnb.py <repo_root>

One-off. Source: the league's own match centre (lnb.fr/fr/match-center/<id>), which shows each game's official box
score and play-by-play. The site can't be downloaded from a script here, so the pages were read in a browser and saved
as coaches/lnb/<club><season>.json.gz: the club's 34 regular-season games. Saved per game: id, t = tip-off (UTC),
rn = round, h/a = [code, name, score], box = for each side the box-score table as shown (header row, a row per player
- '*' before the name marks a starter - and the totals), pbp = per period the rows as shown, latest first:
[0 home / 1 away, "#number name", "Q4 00:02" (period and time left), what happened, score].

Who is on the floor: the box score's starters, then every substitution in the play-by-play ("Remplacement sortant" /
"Remplacement entrant"), which also records the changes made between periods. Each stretch of game clock goes to the
five on the floor, and every shot, free throw, rebound (players' only, as the box score's player rows count them) and
turnover ("Balle Perdue", the team's own included) goes to the five on the floor for it (f) or against it (a).

The substitutions are not always entered at the moment they happened (before the free throws they came between, or a
few seconds before a basket when they were really made at the stoppage after it). Where the plus-minus does not come
out, a substitution may be taken just after a score listed within 10 seconds after it, or just before one within 10
seconds before it - one such move at a time, or two where no single one helps.

A game counts only if the result reproduces the official box score: five on the floor for the club all game, both
teams' points equal to the final score, and every one of the club's players' minutes within 30 seconds and plus-minus
equal to the box score's. The others are listed and left out ("ok": false), as on the NBL seasons.

Game time: 10-minute quarters, 5-minute overtimes.
"""
import collections, gzip, itertools, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
K = ("pts", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "tov")
LEAGUE, SRC = "LNB Pro A", "LNB play-by-play"
SEASONS = {"paris2022": ("2022-23", "Paris Basketball", "PAB", "will-weaver")}
NOTE = ("the league's own play-by-play (lnb.fr match centre), which records every substitution, checked game by game "
        "against its official box scores (both teams' points and each of the club's players' minutes and +/-). "
        "Regular season only")
SHOT = re.compile(r"^([23]) pts\b.*\b(réussi|raté)$")
FT = re.compile(r"^Lancer franc\b.*\b(réussi|raté)$")


def secs(t):
    if not t or ":" not in str(t):
        return 0
    m, s = str(t).split(":")[-2:]
    return int(m) * 60 + int(float(s))


def num(who):
    m = re.match(r"#(\d+)\s+(.*)$", who or "")
    return (m.group(1), m.group(2).strip()) if m else (None, None)


def title(n):
    return " ".join(w if any(c.islower() for c in w) else w.capitalize() for w in n.split())


def game(g, code):
    """-> (units {five: [sec, f, a]}, why not usable or None, {number: name})"""
    if not g.get("pbp") or not g.get("box"):
        return {}, "the league's page has no play-by-play for it", {}
    me = 0 if g["h"][0] == code else 1
    box = {}
    for r in g["box"][me][1:]:
        if len(r) < 20 or not r[0].strip().isdigit():
            continue
        box[r[0].strip()] = {"name": title(re.sub(r"\s*\(c\)\s*$", "", r[1].lstrip("* ").strip(), flags=re.I)), "s5": r[1].strip().startswith("*"), "sec": secs(r[2]),
                             "pm": int(r[-2]) if re.fullmatch(r"-?\d+", r[-2].strip()) else None}
    start = {n for n, r in box.items() if r["s5"]}
    if len(start) != 5:
        return {}, f"{len(start)} starters marked in the box score", {}
    # the rows of each period, oldest first
    per = collections.defaultdict(list)
    for p in g["pbp"]:
        for r in p["rows"]:
            per[r[2].split()[0]].append(r)
    order = sorted(per, key=lambda q: (not q.startswith("Q"), q))
    periods = []
    for q in order:
        L = 600 if q.startswith("Q") else 300
        ev = []
        for s, who, when, what, score in reversed(per[q]):
            n, _ = num(who)
            ev.append((s, min(secs(when.split()[-1]), L), what, n))
        periods.append((L, ev))

    def run(late=None):
        on = set(start)
        units = {}
        onsec, pm, pts = collections.Counter(), collections.Counter(), collections.Counter()
        bad = []
        Z = lambda: dict.fromkeys(K, 0)

        def unit():
            return units.setdefault(tuple(sorted(on)), [0, Z(), Z()])
        for pi, (L, ev) in enumerate(periods):
            mv = {a: b for (p, a), b in (late or {}).items() if p == pi}
            if mv:
                after, before = collections.defaultdict(list), collections.defaultdict(list)
                for a, b in mv.items():
                    (after if b > a else before)[b].append(ev[a])
                out = []
                for k, e in enumerate(ev):
                    out += before[k]
                    if k not in mv:
                        out.append(e)
                    out += after[k]
                ev = out
            clock = L
            for s, c, what, n in ev + [(0, 0, "end", None)]:
                c = min(c, clock)
                d = clock - c
                if d > 0:
                    if len(on) != 5 and not bad:
                        bad.append(f"{len(on)} on the floor in period {pi + 1} with {clock}s left")
                    for p in on:
                        onsec[p] += d
                    unit()[0] += d
                    clock = c
                if what.startswith("Remplacement"):
                    if s == me and n:
                        (on.add if what.endswith("entrant") else on.discard)(n)
                    continue
                u = unit()[1 if s == me else 2]
                made = 0
                m = SHOT.match(what)
                if m:
                    three, ok = m.group(1) == "3", m.group(2) == "réussi"
                    u["fga"] += 1; u["fgm"] += ok
                    if three:
                        u["tpa"] += 1; u["tpm"] += ok
                    made = ok * (3 if three else 2)
                elif FT.match(what):
                    ok = what.endswith("réussi")
                    u["fta"] += 1; u["ftm"] += ok
                    made = ok * 1
                elif what == "Rebond Offensif" and n:
                    u["oreb"] += 1
                elif what == "Rebond Défensif" and n:
                    u["dreb"] += 1
                elif what.startswith("Balle Perdue"):
                    u["tov"] += 1
                if made:
                    u["pts"] += made
                    pts[s] += made
                    for p in on:
                        pm[p] += made if s == me else -made
        return units, onsec, pm, pts, bad

    best = run()
    pmbad = lambda r: sum(abs(r[2][p] - x["pm"]) for p, x in box.items() if x["pm"] is not None and (r[1][p] or x["sec"]))
    minok = lambda r: max(abs(r[1][p] - x["sec"]) for p, x in box.items()) <= 30
    if not best[4] and pmbad(best):
        cands = {}
        for pi, (L, ev) in enumerate(periods):
            for a, e in enumerate(ev):
                if e[0] != me or not e[2].startswith("Remplacement"):
                    continue
                c = []
                for b in range(a + 1, len(ev)):
                    if ev[b][1] < e[1] - 10:
                        break
                    if ev[b][2].endswith("réussi"):
                        c.append(b)
                for b in range(a - 1, -1, -1):
                    if ev[b][1] > e[1] + 10:
                        break
                    if ev[b][2].endswith("réussi"):
                        c.append(b)
                if c:
                    cands[(pi, a)] = c
        cur, late, improved = pmbad(best), {}, True
        while improved and cur:
            improved = False
            for gk, c in cands.items():
                for b in c + [None]:
                    trial = dict(late)
                    trial.pop(gk, None)
                    if b is not None:
                        trial[gk] = b
                    r = run(trial)
                    if not r[4] and pmbad(r) < cur and minok(r):
                        cur, late, best, improved = pmbad(r), trial, r, True
            if cur and not improved:
                flat = [(gk, b) for gk, c in cands.items() for b in c if late.get(gk) != b]
                if len(flat) <= 90:
                    for (g1, b1), (g2, b2) in itertools.combinations(flat, 2):
                        if g1 == g2:
                            continue
                        trial = dict(late); trial[g1] = b1; trial[g2] = b2
                        r = run(trial)
                        if not r[4] and pmbad(r) < cur and minok(r):
                            cur, late, best, improved = pmbad(r), trial, r, True
                            break
    units, onsec, pm, pts, bad = best
    why = list(bad)
    for s, t in ((0, "h"), (1, "a")):
        if pts[s] != g[t][2]:
            why.append(f"points {pts[s]} vs final score {g[t][2]} for {g[t][0]}")
    off = [(r["name"], onsec[p] - r["sec"]) for p, r in box.items() if abs(onsec[p] - r["sec"]) > 30]
    if off:
        why.append("minutes off: " + ", ".join(f"{n} {d:+d}s" for n, d in off[:4]))
    wrong = [(r["name"], pm[p], r["pm"]) for p, r in box.items() if r["pm"] is not None and (onsec[p] or r["sec"]) and pm[p] != r["pm"]]
    if wrong:
        why.append("plus-minus off: " + ", ".join(f"{n} {a:+d} vs {b:+d}" for n, a, b in wrong[:4]))
    return units, ("; ".join(why) or None), {p: r["name"] for p, r in box.items()}


def main(root):
    key = lambda n: re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower())
    bios = json.load(open(os.path.join(root, "data", "bios.json")))
    espn = {key(b["name"]): str(b["id"]) for b in bios if b.get("id")}
    fp = os.path.join(root, "data", "coachlineups.json")
    D = json.load(open(fp))
    for f, (season, team, code, coach) in SEASONS.items():
        raw = json.load(gzip.open(os.path.join(HERE, "lnb", f + ".json.gz"), "rt"))
        L = sorted(raw, key=lambda g: (g["t"], g["id"]))
        games, tot, byname = [], {}, {}
        for gi, g in enumerate(L):
            home = g["h"][0] == code
            opp = (g["a"] if home else g["h"])[1]
            units, why, nm = game(g, code)
            games.append({"id": g["id"], "date": g["t"][:10], "opp": opp, "home": home, "ok": not why})
            if why:
                print(f"  {season} {g['t'][:10]} v {opp}: left out ({why})")
                continue
            for five, (sec, fo, ag) in units.items():
                if len(five) != 5:
                    continue
                # shirt numbers can change hands during a season: a player is his name
                k5 = tuple(sorted(key(nm[p]) for p in five))
                for p in five:
                    byname[key(nm[p])] = nm[p]
                t = tot.setdefault(k5, [0, dict.fromkeys(K, 0), dict.fromkeys(K, 0), []])
                t[0] += sec
                for k in K:
                    t[1][k] += fo[k]; t[2][k] += ag[k]
                t[3].append(gi)
        ids = sorted({p for five in tot for p in five}, key=lambda p: byname[p])
        ix = {p: i for i, p in enumerate(ids)}
        players = [[byname[p], espn.get(p), None] for p in ids]
        units = [[sorted(ix[p] for p in five), float(sec), [fo[k] for k in K], [ag[k] for k in K], gl]
                 for five, (sec, fo, ag, gl) in sorted(tot.items(), key=lambda kv: -kv[1][0]) if sec > 0 or any(fo.values()) or any(ag.values())]
        D["seasons"][f"{season}|{team}"] = {"coach": coach, "league": LEAGUE, "src": SRC, "note": NOTE, "games": games, "players": players, "units": units}
        used = sum(1 for x in games if x["ok"])
        fo = {k: sum(u[2][i] for u in units) for i, k in enumerate(K)}
        ag = {k: sum(u[3][i] for u in units) for i, k in enumerate(K)}
        print(f"{season} {team}: {used} of {len(games)} games used, {len(units)} fives, {len(players)} players, "
              f"{sum(u[1] for u in units) / 60:.0f} min; for {fo['pts']} pts {fo['fgm']}/{fo['fga']} FG {fo['oreb']}+{fo['dreb']} reb {fo['tov']} tov, against {ag['pts']} pts")
    if "--dry" not in sys.argv:
        tmp = fp + ".tmp"
        json.dump(D, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, fp)


if __name__ == "__main__":
    main(sys.argv[1])
