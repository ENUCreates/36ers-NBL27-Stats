#!/usr/bin/env python3
"""Lineups for the Head coaches tab's Basketball Bundesliga (Germany) seasons, into data/coachlineups.json next to the
NBL seasons (lineups_hist.py), the G League ones (lineups_gleague.py) and the B.League ones (lineups_bleague.py); each
script replaces only its own seasons.

    python3 lineups_bbl.py <repo_root>

One-off. Source: the league's own game pages (easycredit-bbl.de/spiele/<id>), which show each game's official box score
and play-by-play. The site can't be downloaded from a script here, so the pages were read in a browser and saved as
coaches/bbl/<club><season>.json.gz, one list of games per club season (the regular season, "Hauptrunde", as for the
other leagues). Saved per game: id, season, t = tip-off (UTC), h/a = [team id, code, name, score], box = for each side
the columns and rows of the box score as shown (the first value of a row is the player's id), pbp = per period the
rows as shown, latest first: [0 home / 1 guest, "clock<TAB>text"].

Who is on the floor: the play-by-play names every substitution ("Spielerwechsel - Raus: A, Rein: B") but not the five
who start a period. The first quarter's five are the box score's starters (S5). For the later periods the five are
listed at the top of the period in the older seasons (to 2016-17); in the newer ones they are
read from the period itself: anyone who does something, or is taken off, before being brought on started it. Where
that finds only four (someone played the period's opening stretch without touching the ball sheet), the fifth is the
one that makes the game's minutes agree with the box score.

The feed's substitutions are not always entered at the moment they happened: one is listed before the free throws it
came between, or a few seconds before a basket when it was really made at the stoppage after it. Where the plus-minus
does not come out, a substitution may be taken just after a score listed within 10 seconds after it (or just before
one within 10 seconds before it) - one such move at a time, or two where no single one helps - and the game counts
only if that reproduces every plus-minus exactly.

A game counts only if the result reproduces the official box score: five on the floor for the club all game, both
teams' points equal to the final score, and every one of the club's players' minutes within 30 seconds and plus-minus
equal to the box score's. The others are listed and left out ("ok": false), as on the NBL seasons.

Game time: 10-minute quarters, 5-minute overtimes.

Which seasons: those whose game pages name the players in the substitutions - 2008-09 to 2016-17 and from 2023-24.
The pages of 2017-18 to 2022-23 show "Spielerwechsel" without the names, and there is no play-by-play before 2008-09,
so Herbert's 2017-18 and 2018-19 in Frankfurt, his seasons there up to 2004 and Josh King's 2022-23 in Ludwigsburg
can't be done from this source.
"""
import collections, gzip, itertools, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
K = ("pts", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "tov")
LEAGUE, SRC = "Basketball Bundesliga", "Basketball Bundesliga play-by-play"
# file -> (season, team as the page names the season, club code(s) on the league's site, coach, last date coached or
# None[, first date coached])
SEASONS = {
    "frs2010": ("2010-11", "Deutsche Bank Skyliners Frankfurt", ("FRS", "FRA"), "gordon-herbert", None),
    "ber2011": ("2011-12", "Alba Berlin", "BER", "gordon-herbert", None),
    "frs2013": ("2013-14", "Fraport Skyliners Frankfurt", ("FRS", "FRA"), "gordon-herbert", None),
    "frs2014": ("2014-15", "Fraport Skyliners Frankfurt", ("FRS", "FRA"), "gordon-herbert", None),
    "frs2015": ("2015-16", "Fraport Skyliners Frankfurt", ("FRS", "FRA"), "gordon-herbert", None),
    "frs2016": ("2016-17", "Fraport Skyliners Frankfurt", ("FRS", "FRA"), "gordon-herbert", None),
    "lud2023": ("2023-24", "MHP Riesen Ludwigsburg", "LUD", "josh-king", None),
    "fcb2024": ("2024-25", "Bayern Munich", "FCB", "gordon-herbert", None),
    # Not here: his part seasons (Frankfurt from April 2010, Bayern to 20 December 2025). The Head coaches tab lists
    # whole seasons only. A part season takes a last date coached, and a first one, as below.
}
# seasons on the Head coaches tab that this source can't do: the page says why
NONE = {"2017-18|Fraport Skyliners Frankfurt": "gordon-herbert", "2018-19|Fraport Skyliners Frankfurt": "gordon-herbert",
        "2022-23|MHP Riesen Ludwigsburg": "josh-king"}
NONE_WHY = "the league's play-by-play for this season lists the substitutions without the players' names"
NOTE = ("the league's own play-by-play (easycredit-bbl.de), which names every substitution; the five who start each "
        "later quarter are listed there (to 2016-17) or worked out from it. Checked game by game against the official box scores (both teams' "
        "points and each of the club's players' minutes and +/-). Regular season only")
SHOT = re.compile(r"^(Erfolgreicher|Vergebener) (.+)$")
BLOCKED = re.compile(r"^(.+) (3er|2-Punkte-Wurf|Korbleger|Halbdistanzwurf|Nahdistanzwurf|Dunking|Alley Oop|Tip-In) wurde geblockt$")
SCORE = re.compile(r"\s*(?:-\s*)?\d+\s*:\s*\d+\s*\(\+\d\)\s*(?:-\s*)?")


def secs(t):
    if not t or ":" not in str(t):
        return 0
    m, s = str(t).split(":")[-2:]
    return int(m) * 60 + int(float(s))


def norm(n):
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower().replace("-", " ")).split()


def parse(text):
    """one play-by-play row -> (clock seconds, kind, names, extra)"""
    p = text.split("\t")
    raw = " ".join(p[1:])
    body = SCORE.sub(" ", raw).strip()
    c = secs(p[0])
    m = BLOCKED.match(body)
    if m:
        if SCORE.search(raw):      # a blocked shot that counted (goaltending)
            return c, "Erfolgreicher " + m.group(2), [m.group(1)], None
        return c, "blocked", [m.group(1)], m.group(2)
    if body.startswith("- "):      # older seasons: the players on the floor as a period starts (and as it ends)
        return c, "floor", [body[2:].strip()], None
    head, _, rest = body.partition(" - ")
    head = head.strip()
    if head == "Spielerwechsel":
        m = re.match(r"Raus: (.*), Rein: (.*)$", rest)
        if m:
            return c, "sub", [m.group(1).strip(), m.group(2).strip()], None
        m = re.match(r"Raus: (.*)$", rest)
        return c, "sub", [m.group(1).strip() if m else UNNAMED, UNNAMED], None
    names = []
    if rest:
        a, _, b = rest.partition(", Assist: ")
        names = [a.strip()] + ([b.strip()] if b else [])
    if head.startswith("Sprungballsituation"):
        names = []
    if head.startswith("Sprungball gewonnen"):
        names = [x.strip() for x in rest.split(" gegen ")]
    return c, head, names, None


class Side:
    def __init__(self, box):
        cols = box["cols"]
        ix = {c: i + 1 for i, c in enumerate(cols)}
        self.rows = {}
        for r in box["rows"]:
            if not r[0]:
                continue
            nm = re.sub(r"\s*\([A-Z/]+\)\s*$", "", r[ix["Spieler"]])
            pm = r[ix["plusMinus"]]
            self.rows[r[0]] = {"box": nm, "s5": r[ix["S5"]] == "S5", "sec": secs(r[ix["MIN"]]), "pm": int(pm) if pm not in ("", None) else None,
                               "pts": int(r[ix["points"]] or 0), "fga": int(r[ix["fieldGoalsAttempted"]] or 0), "name": None}
        self.by = {}

    def find(self, name, sig=None):
        if name == UNNAMED:
            return "?"
        """play-by-play name -> player id (the box score has 'Surname I.', the play-by-play the full name)"""
        if name in self.by:
            return self.by[name]
        t = norm(name)
        hit = []
        for pid, r in self.rows.items():
            b = norm(r["box"])
            if not b:
                continue
            sur, ini = b[:-1], b[-1]
            if len(ini) > 1:
                sur, ini = b, ""
            n = len(sur)
            # 'First Sur name' or 'Sur name First'
            if (t[-n:] == sur and (not ini or (t[:-n] and t[0][0] == ini[0]))) or (t[:n] == sur and (not ini or (t[n:] and t[n][0] == ini[0]))):
                hit.append(pid)
        if len(hit) != 1:
            return None if not hit else hit      # ambiguous: the caller settles it
        self.by[name] = hit[0]
        return hit[0]


def game(g, code):
    """-> (units {five: [sec, f, a]}, why not usable or None, {player id: name})"""
    if not g.get("pbp") or not g.get("box"):
        return {}, "the league's page has no play-by-play for it", {}
    sides = [Side(g["box"][0]), Side(g["box"][1])]
    code = (code,) if isinstance(code, str) else code
    me = 0 if g["h"][1] in code else 1
    why = []
    periods = []
    for q in g["pbp"]:
        ev = [(s,) + parse(t) for s, t in reversed(q["rows"])]
        periods.append((300 if re.search(r"Verl|OT", q["lab"]) else 600, ev))
    # A player the league's pages no longer carry: his substitutions show as "Raus: A" (he comes on for A) and as a
    # bare "Spielerwechsel" (he goes off), and the box score has no row for him. The one who comes on at a bare
    # substitution is the next of that side to do something, or be taken off, without having been brought on.
    for L, ev in periods:      # the other side's are of no consequence for the club's fives: left alone
        ev[:] = [e for e in ev if not (e[0] != me and e[2] == "sub" and UNNAMED in e[3])]
    for s in (me,):
        if not any(e[0] == s and e[2] == "sub" and UNNAMED in e[3] for L, ev in periods for e in ev):
            continue
        length = sum(L for L, ev in periods)
        sides[s].rows["?"] = {"box": UNNAMED, "s5": False, "sec": 5 * length - sum(r["sec"] for r in sides[s].rows.values()), "pm": None, "pts": 0, "fga": 0, "name": None}
        for L, ev in periods:
            off = set()
            for i, e in enumerate(ev):
                if e[0] != s or e[2] != "sub":
                    continue
                if e[3][0] != UNNAMED:
                    off.add(e[3][0]); off.discard(e[3][1])
                    continue
                came = set()
                for f in ev[i + 1:]:
                    if f[0] != s or f[2] in ("Technisches Foul", "Trainerfoul"):
                        continue
                    if f[2] == "sub":
                        n = f[3][:1]
                        came.add(f[3][1])
                    else:
                        n = f[3][:1]
                    n = [x for x in n if x in off and x not in came and x != UNNAMED]
                    if n:
                        e[3][1] = n[0]
                        off.discard(n[0])
                        break
                else:
                    # nobody shows himself: whoever is off and is not brought on by name later in the period
                    later = {f[3][1] for f in ev[i + 1:] if f[0] == s and f[2] == "sub"}
                    c = sorted(off - later - {UNNAMED})
                    if len(c) == 1:
                        e[3][1] = c[0]
                        off.discard(c[0])
                    else:
                        why.append(f"can't tell who came on for the player the league's pages don't name ({len(c)} possible)")
    # names -> ids; two players a box score can't tell apart (same surname and initial) are settled by their points
    amb = collections.defaultdict(lambda: collections.Counter())
    for L, ev in periods:
        for s, c, kind, names, x in ev:
            for i, n in enumerate(names):
                side = sides[s] if kind not in ("Sprungball gewonnen",) or i == 0 else sides[1 - s]
                r = side.find(n)
                if isinstance(r, list):
                    made = kind.startswith("Erfolgreicher") and i == 0
                    amb[(s, n)]["pts"] += (3 if "3er" in kind else 1 if "Freiwurf" in kind else 2) if made else 0
                    if kind == "floor":
                        continue
                    amb[(s, n)]["fga"] += bool(i == 0 and (kind == "blocked" or (SHOT.match(kind) and "Freiwurf" not in kind)))
                    amb[(s, n)]["cand"] = tuple(r)
    for (s, n), a in amb.items():
        c = [p for p in a["cand"] if sides[s].rows[p]["pts"] == a["pts"] and sides[s].rows[p]["fga"] == a["fga"] and p not in sides[s].by.values()]
        if len(c) == 1:
            sides[s].by[n] = c[0]
    for (s, n), a in amb.items():      # still open: the id the same name had in the club's other games
        h = HINT.get(n)
        if n not in sides[s].by and h in a["cand"] and h not in sides[s].by.values():
            sides[s].by[n] = h
    left = [(s, n) for (s, n) in amb if n not in sides[s].by]
    for s, n in left:      # the one left over takes the one row left over
        c = [p for p in amb[(s, n)]["cand"] if p not in sides[s].by.values()]
        if len(c) == 1:
            sides[s].by[n] = c[0]
        else:
            why.append(f"can't tell which player '{n}' is")
    if why:
        return {}, "; ".join(why), {}

    def pid(s, n):
        r = sides[s].find(n)
        return r if isinstance(r, str) else None

    # who starts each period
    starts = []
    for pi, (L, ev) in enumerate(periods):
        st = [set(), set()]
        seen = [set(), set()]
        act = [set(), set()]
        listed = [set(), set()]
        for s, c, kind, names, x in ev:
            if kind in ("Technisches Foul", "Trainerfoul", "sub?"):
                continue
            if kind == "floor":
                if c == L and names:
                    p = pid(s, names[0])
                    if p:
                        listed[s].add(p)
                continue
            if kind == "sub":
                o, i = pid(s, names[0]), pid(s, names[1])
                if o is None or i is None:
                    why.append(f"unknown player in a substitution ({names})")
                    continue
                if o not in seen[s]:
                    st[s].add(o)
                seen[s].add(i); act[s].update((o, i))
                continue
            for k, n in enumerate(names):
                ss = 1 - s if kind == "Sprungball gewonnen" and k == 1 else s
                p = pid(ss, n)
                if p is None:
                    p2 = pid(1 - ss, n)
                    if p2 is None:
                        why.append(f"unknown player '{n}'")
                        continue
                    ss, p = 1 - ss, p2
                if p not in seen[ss]:
                    st[ss].add(p)
                act[ss].add(p)
        for s in (0, 1):      # older seasons list the five who start each period
            if len(listed[s]) == 5:
                st[s] = listed[s]
        if pi == 0:
            for s in (0, 1):
                s5 = {p for p, r in sides[s].rows.items() if r["s5"]}
                if len(s5) == 5:
                    st[s] = s5
        starts.append((st, act))
    if why:
        return {}, "; ".join(sorted(set(why))[:3]), {}

    def run(starts_, late=None):
        on = [set(), set()]
        units = {}
        onsec, pm, pts = collections.Counter(), collections.Counter(), collections.Counter()
        bad = []
        Z = lambda: dict.fromkeys(K, 0)
        for pi, (L, ev) in enumerate(periods):
            on = [set(starts_[pi][0]), set(starts_[pi][1])]
            clock = L

            def unit():
                return units.setdefault(tuple(sorted(on[me])), [0, Z(), Z()])
            mv = {a: b for (p, a), b in (late or {}).items() if p == pi}
            if mv:      # these substitutions are taken just after (or just before) a score listed near them
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
            for s, c, kind, names, x in ev + [(0, 0, "end", [], None)]:
                c = min(c, clock)
                d = clock - c
                if d > 0:
                    for t in (0, 1):
                        if t == me and len(on[t]) != 5 and not bad:
                            bad.append(f"{len(on[t])} on the floor for {g['a' if t else 'h'][1]} in period {pi + 1} with {clock}s left")
                        for p in on[t]:
                            onsec[(t, p)] += d
                    unit()[0] += d
                    clock = c
                if kind == "sub":
                    on[s].discard(pid(s, names[0])); on[s].add(pid(s, names[1]))
                    continue
                if kind == "floor":
                    continue
                u = unit()[1 if s == me else 2]
                m = SHOT.match(kind)
                made = 0
                if m:
                    ok, what = m.group(1) == "Erfolgreicher", m.group(2)
                    if what == "Freiwurf":
                        u["fta"] += 1; u["ftm"] += ok; made = ok * 1
                    else:
                        three = what == "3er"
                        u["fga"] += 1; u["fgm"] += ok
                        if three:
                            u["tpa"] += 1; u["tpm"] += ok
                        made = ok * (3 if three else 2)
                elif kind == "blocked" and BLOCK_IS_SHOT:
                    u["fga"] += 1
                    u["tpa"] += x == "3er"
                elif kind == "Offensiv-Rebound":
                    u["oreb"] += 1
                elif kind == "Defensiv-Rebound":
                    u["dreb"] += 1
                elif kind in ("Ballverlust", "Team-Ballverlust", "Offensiv-Foul") or kind.startswith("Ballverlust,") or kind.startswith("Team-Ballverlust,"):
                    u["tov"] += 1
                if made:
                    u["pts"] += made
                    pts[s] += made
                    for t in (0, 1):
                        for p in on[t]:
                            pm[(t, p)] += made if t == s else -made
        err = sum(abs(onsec[(t, p)] - r["sec"]) for t in (0, 1) for p, r in sides[t].rows.items())
        return units, onsec, pm, pts, bad, err

    # a period where only four (or fewer) show themselves: try the players who did nothing in it, keep the best fit
    opts = []
    for pi, (st, act) in enumerate(starts):
        per = []
        for s in (0, 1):
            need = 5 - len(st[s])
            if need < 0:
                return {}, f"{len(st[s])} players look like starters of period {pi + 1} for {g['a' if s else 'h'][1]}", {}
            cand = [p for p, r in sides[s].rows.items() if r["sec"] > 0 and p not in act[s]]
            per.append([st[s] | set(c) for c in itertools.combinations(cand, need)] or [st[s]])
        opts.append([(a, b) for a in per[0] for b in per[1]])
    n = 1
    for o in opts:
        n *= len(o)
    if n > 20000:
        return {}, f"too many ways to fill the periods' starting fives ({n})", {}
    best = None
    for combo in itertools.product(*opts):
        r = run(combo)
        if best is None or (len(r[4]), r[5]) < (len(best[4]), best[5]):
            best = r
            if not r[4] and r[5] == 0:
                break
    units, onsec, pm, pts, bad, err = best
    best_combo = combo if (not best[4] and best[5] == 0) else None
    pmbad = lambda r: sum(abs(r[2][(t, p)] - x["pm"]) for t in (me,) for p, x in sides[t].rows.items() if x["pm"] is not None and (r[1][(t, p)] or x["sec"]))
    if best_combo is None:
        # keep the best-fitting combination for the order search below
        scored = [(len(r[4]), r[5], i) for i, r in ((i, run(c)) for i, c in enumerate(itertools.product(*opts)))] if n <= 64 else []
        if scored:
            best_combo = list(itertools.product(*opts))[min(scored)[2]]
    if best_combo is not None and not bad and pmbad(best):
        # The feed's substitutions are not always entered at the moment they happened: one is listed before the free
        # throws it came between, or a few seconds before a basket when it was made at the stoppage after it. Where
        # the plus-minus does not come out, a substitution may be taken just after a score listed within 10 seconds
        # after it, or just before one listed within 10 seconds before it.
        cands = {}
        for pi, (L, ev) in enumerate(periods):
            for a, e in enumerate(ev):
                if e[0] != me or e[2] != "sub":
                    continue
                c = []
                for b in range(a + 1, len(ev)):
                    if ev[b][1] < e[1] - 10:
                        break
                    if ev[b][2].startswith("Erfolgreicher"):
                        c.append(b)
                for b in range(a - 1, -1, -1):
                    if ev[b][1] > e[1] + 10:
                        break
                    if ev[b][2].startswith("Erfolgreicher"):
                        c.append(b)
                if c:
                    cands[(pi, a)] = c
        cur, late = pmbad(best), {}
        improved = True
        while improved and cur:
            improved = False
            for gk, c in cands.items():
                for b in c + [None]:
                    trial = dict(late)
                    trial.pop(gk, None)
                    if b is not None:
                        trial[gk] = b
                    r = run(best_combo, trial)
                    if not r[4] and pmbad(r) < cur and max(abs(r[1][(me, p)] - x["sec"]) for p, x in sides[me].rows.items()) <= 30:
                        cur, late, best, improved = pmbad(r), trial, r, True
            if cur and not improved:      # no single one helps: two at once
                flat = [(gk, b) for gk, c in cands.items() for b in c if late.get(gk) != b]
                if len(flat) <= 80:
                    for (g1, b1), (g2, b2) in itertools.combinations(flat, 2):
                        if g1 == g2:
                            continue
                        trial = dict(late); trial[g1] = b1; trial[g2] = b2
                        r = run(best_combo, trial)
                        if not r[4] and pmbad(r) < cur and max(abs(r[1][(me, p)] - x["sec"]) for p, x in sides[me].rows.items()) <= 30:
                            cur, late, best, improved = pmbad(r), trial, r, True
                            break
        units, onsec, pm, pts, bad, err = best
    why += bad
    for s, t in ((0, "h"), (1, "a")):
        if pts[s] != g[t][3]:
            why.append(f"points {pts[s]} vs final score {g[t][3]} for {g[t][1]}")
    off = [(r["box"], onsec[(t, p)] - r["sec"]) for t in (me,) for p, r in sides[t].rows.items() if abs(onsec[(t, p)] - r["sec"]) > 30]
    if off:
        why.append("minutes off: " + ", ".join(f"{n} {d:+d}s" for n, d in off[:4]))
    wrong = [(r["box"], pm[(t, p)], r["pm"]) for t in (me,) for p, r in sides[t].rows.items() if r["pm"] is not None and (onsec[(t, p)] or r["sec"]) and pm[(t, p)] != r["pm"]]
    if wrong:
        why.append("plus-minus off: " + ", ".join(f"{n} {a:+d} vs {b:+d}" for n, a, b in wrong[:4]))
    for s in (0, 1):
        HINT.update(sides[s].by)
    inv = {v: k for k, v in sides[me].by.items()}
    names = {p: inv.get(p) or r["box"] for p, r in sides[me].rows.items()}
    return units, ("; ".join(why) or None), names


BLOCK_IS_SHOT = True
HINT = {}
UNNAMED = "Unnamed player"      # a player the league's pages no longer name (see game())
# Who that is, where it is known. Ludwigsburg 2023-24: every one of the club's players of that season is named on the
# league's pages except the centre Michael Hughes, signed in January 2024 (eurohoops.net, "Ludwigsburg adds Michael
# Hughes") and in the match squads from then to mid-March; the unnamed player appears in exactly those games
# (27 Jan to 15 Mar 2024) and always comes on for the centre.
UNNAMED_IS = {"lud2023": "Michael Hughes"}


def main(root):
    bios = json.load(open(os.path.join(root, "data", "bios.json")))
    key = lambda n: "".join(norm(n))
    espn = {key(b["name"]): str(b["id"]) for b in bios if b.get("id")}
    fp = os.path.join(root, "data", "coachlineups.json")
    D = json.load(open(fp))
    for f, (season, team, code, coach, until, *since) in SEASONS.items():
        since = since[0] if since else None
        if not os.path.exists(os.path.join(HERE, "bbl", f + ".json.gz")):
            continue
        raw = json.load(gzip.open(os.path.join(HERE, "bbl", f + ".json.gz"), "rt"))
        L = sorted((g for g in raw if g.get("stage") == "MAIN_ROUND" and g.get("comp") == "BBL" and (not until or g["t"][:10] <= until) and (not since or g["t"][:10] >= since)), key=lambda g: (g["t"], g["id"]))
        games, names, tot = [], {}, {}
        for g in L:      # a first pass, so that every game knows the names settled in the others
            game(g, code)
        for gi, g in enumerate(L):
            home = g["h"][1] in ((code,) if isinstance(code, str) else code)
            opp = (g["a"] if home else g["h"])[2]
            units, why, nm = game(g, code)
            names.update({k: v for k, v in nm.items() if k not in names or len(v) > len(names[k])})
            games.append({"id": str(g["id"]), "date": g["t"][:10], "opp": opp, "home": home, "ok": not why})
            if why:
                print(f"  {season} {g['t'][:10]} v {opp}: left out ({why})")
                continue
            for five, (sec, fo, ag) in units.items():
                if len(five) != 5:
                    continue
                t = tot.setdefault(five, [0, dict.fromkeys(K, 0), dict.fromkeys(K, 0), []])
                t[0] += sec
                for k in K:
                    t[1][k] += fo[k]; t[2][k] += ag[k]
                t[3].append(gi)
        if "?" in names:
            names["?"] = UNNAMED_IS.get(f, UNNAMED)
        ids = sorted({p for five in tot for p in five}, key=lambda p: names.get(p, ""))
        ix = {p: i for i, p in enumerate(ids)}
        players = [[names.get(p) or p, espn.get(key(names.get(p) or "")), None] for p in ids]
        units = [[sorted(ix[p] for p in five), float(sec), [fo[k] for k in K], [ag[k] for k in K], gl]
                 for five, (sec, fo, ag, gl) in sorted(tot.items(), key=lambda kv: -kv[1][0]) if sec > 0 or any(fo.values()) or any(ag.values())]
        D["seasons"][f"{season}|{team}"] = {"coach": coach, "league": LEAGUE, "src": SRC, "note": NOTE + (f", up to his last game before leaving the club on {int(until[8:])} {['January','February','March','April','May','June','July','August','September','October','November','December'][int(until[5:7]) - 1]} {until[:4]}" if until else "") + (f", from his first game after taking over in April {since[:4]}" if since else "") + (f". {UNNAMED_IS[f]}, whom the league's pages no longer list, is placed from the substitutions that leave his name blank" if f in UNNAMED_IS else ""), "games": games, "players": players, "units": units}
        used = sum(1 for x in games if x["ok"])
        fo = {k: sum(u[2][i] for u in units) for i, k in enumerate(K)}
        ag = {k: sum(u[3][i] for u in units) for i, k in enumerate(K)}
        print(f"{season} {team}: {used} of {len(games)} games used, {len(units)} fives, {len(players)} players, "
              f"{sum(u[1] for u in units) / 60:.0f} min; for {fo['pts']} pts {fo['fgm']}/{fo['fga']} FG {fo['oreb']}+{fo['dreb']} reb {fo['tov']} tov, against {ag['pts']} pts")
    for k, coach in NONE.items():
        D["seasons"][k] = {"coach": coach, "league": LEAGUE, "none": NONE_WHY}
    if "--dry" not in sys.argv:
        tmp = fp + ".tmp"
        json.dump(D, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, fp)


if __name__ == "__main__":
    main(sys.argv[1])
