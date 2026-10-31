"""Lineups for the Head coaches tab: every NBL season since 2015-16 that an NBL27 head coach coached (the stints in
coaches/curated.json, the same games build.py uses), rebuilt from the NBL's official play-by-play and checked game by
game against the official box scores, into data/coachlineups.json (its overseas seasons, from lineups_gleague.py, are
kept as they are).

    python3 lineups_hist.py <repo_root>

One-off, like build.py: run it again when a club changes coach. Downloads the nblR release files (official.py's box
scores and match list, plus the play-by-play file pbp/pbp.rds, read with rdsread.py because pyreadr can't open it); set
NBL_OFFICIAL_CACHE to reuse a download.

For each game the coach's team's five on the floor is followed through the play-by-play: the official starters, then
every substitution in and out, in the feed's order (action numbers where the feed has them, else its own order, which
is newest first). Each stretch of game clock goes to the five on the floor, and every shot, free throw, rebound and
turnover (team ones included) goes to the five on the floor for it (f) or against it (a), as the NBL27 lineups do.
A game counts only if it reconciles with the official box score: five on the floor whenever the clock runs, the whole
game covered (40 minutes + 5 a period of overtime, within 30 seconds), both teams' points exact, field-goal and
free-throw attempts within 3, and every player's floor time within 2 minutes of his official minutes. The others are
listed and left out, as the Lineups tab does.

Output, per coach-season ('<season>|<team>'): the games (with whether each was used), the players ([name, ESPN id when
he is on an NBL27 roster - for his NBL27 photo - else null, season photo or null]) and the five-man units: [[5 player
indexes], seconds, f, a, [game indexes]] with f and a in the order of K. The page sums units into 3- and 2-man groups.

Season photos, for the players without an NBL27 photo: the official headshot of that season - the one on his rows of
the official box scores for that team that season (the most used, if it changed); if those rows have none, the one on
his box score rows with the same team in the nearest season, else the one on that season's official player profile
(nbl_player_bios.rds, which holds his latest headshot). The official files name the image hosts of their day; the same
files are served now by Genius Sports' image host (SportingPulse and the older Genius host) and Sportradar's (Atrium,
2025-26). Each is converted as the Player cards' headshots are (bios.photo_webp) into data/coachphotos/<image id>.webp;
one already there isn't downloaded again (delete the folder to fetch them all again), and files no season uses any more
are removed.
"""
import json, os, re, sys, subprocess, tempfile, datetime, unicodedata
import concurrent.futures as cf
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import official   # noqa: E402
import rdsread    # noqa: E402
import build      # noqa: E402  (games_by_season: the official regular-season games, as the drop-down uses them)
import bios as cards   # noqa: E402  (photo_webp: the Player cards' headshot conversion)

K = ("pts", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "tov")
tn = lambda n: official.TEAMNAME.get(n, n)
txt = lambda v: v.strip() if isinstance(v, str) and v.strip() else None
GENIUS = "https://images.statsengine.playbyplay.api.geniussports.com/"
SPORTRADAR = "https://images.dc.connect.sportradar.com/"
HOSTS = ((r"^https?://(img\.wh\.sportingpulseinternational\.com|se-img\.dcd-production\.i\.geniussports\.com"
          r"|images\.statsengine\.playbyplay\.api\.geniussports\.com)/", GENIUS),
         (r"^https?://(images\.dc\.prod\.cloud\.atriumsports\.com|images\.dc\.connect\.sportradar\.com)/", SPORTRADAR))


def photo_src(u):
    """An official headshot URL -> (image id, the URL it's served from now), or None. Genius Sports' files come in
    sizes T1 (75 px), S1 (200), M1 and L1 (not kept for the oldest seasons): S1 is taken."""
    u = txt(u)
    if not u:
        return None
    for pat, new in HOSTS:
        u = re.sub(pat, new, u)
    m = re.match(re.escape(GENIUS) + r"([0-9a-f]{32})[TSML]1\.(jpe?g|png|webp)$", u)
    if m:
        return m.group(1), f"{GENIUS}{m.group(1)}S1.{m.group(2)}"
    m = re.match(re.escape(SPORTRADAR) + r"\w+/([0-9a-f]{32})(\?.*)?$", u)
    if m:
        return m.group(1), u
    return None


def sid(x):
    """A match id as text: the official files carry older ids as numbers (139002.0) and newer ones as text."""
    return str(int(x)) if isinstance(x, float) and x == x and x.is_integer() else str(x)


def nkey(first, family):
    s = f"{first or ''} {family or ''}"
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def secs_left(gt):
    try:
        m, s = str(gt).split(":")[:2]
        return int(m) * 60 + float(s)
    except Exception:
        return None


def abs_time(ptype, period, gt):
    left = secs_left(gt)
    if left is None or period != period:
        return None
    if str(ptype).upper().startswith("OVER"):
        return 2400 + (int(period) - 1) * 300 + (300 - left)
    return (int(period) - 1) * 600 + (600 - left)


def download_pbp(dest):
    fp = os.path.join(dest, "pbp.rds")
    if not (os.path.exists(fp) and os.path.getsize(fp) > 1_000_000):
        r = subprocess.run(["curl", "-sSL", "--fail", "-m", "300", "-o", fp, official.REL + "pbp/pbp.rds"], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"play-by-play download failed: {r.stderr[:200]}")
    return fp


def pidx(r):
    """A period's place in the game: 0-3 the quarters, 4 on the overtimes."""
    if r["period"] != r["period"]:
        return 99
    return 3 + int(r["period"]) if str(r["period_type"]).upper().startswith("OVER") else int(r["period"]) - 1


def ordered(rows):
    """A game's events oldest first, by the game clock: within one clock reading, in action-number order where every
    event has one (the newer feed numbers each period's events on their own, and late corrections carry later numbers
    than their game time, so the clock decides), else in the feed's own order reversed (it lists the newest first).
    Events without a clock reading (period and game markers) stay next to the event before them in their period."""
    if all(r["action_number"] == r["action_number"] for r in rows):
        base = sorted(reversed(rows), key=lambda r: (pidx(r), r["action_number"]))
    else:
        base = list(reversed(rows))
    keyed, t0, p0 = [], 0.0, None
    for n, r in enumerate(base):
        p = pidx(r)
        if p != p0:
            p0, t0 = p, (2400 + (p - 4) * 300 if p >= 4 else p * 600) if p != 99 else t0
        t = abs_time(r["period_type"], r["period"], r["gt"])
        t0 = t if t is not None else t0
        keyed.append((t0, n, r))
    return [r for _, _, r in sorted(keyed, key=lambda x: (x[0], x[1]))]


side_of = lambda r: tn(r["team_name"]) if r["team_name"] == r["team_name"] and r["team_name"] else None


def rebuild(rows, team, starters, box, opp=None):
    """One team's five-man units in one game -> ({frozenset: [sec, f, a]}, floor seconds per player, problems).
    Starts from the official starters. All of the team's substitutions at one clock reading are taken together and
    applied when the clock next moves (so a free throw at that reading still counts for the five that drew or gave
    the foul): whoever only goes off goes off, whoever only comes on comes on, and a player listed both going off and
    coming on is left as he was (under FIBA rules a player can't come back in the same stoppage, so the pair is the
    scorer correcting an entry). The feed's order inside one clock reading isn't reliable, which is why it's whole."""
    units, floor, why = {}, defaultdict(float), []
    on, last = set(starters), 0.0
    outs, ins, at_lab = [], [], None
    lab = lambda r: f"{'OT' if str(r['period_type']).upper().startswith('OVER') else 'Q'}{int(r['period'])} {r['gt']}"

    def cur():
        k = frozenset(on)
        if k not in units:
            units[k] = [0.0, defaultdict(float), defaultdict(float)]
        return units[k]

    def apply():
        both = set(outs) & set(ins)
        for p in outs:
            if p in both:
                continue
            if p not in on:
                why.append(f"{p} went off while not on the floor ({at_lab})")
                return False
            on.discard(p)
        for p in ins:
            if p not in both:
                on.add(p)
        outs.clear(); ins.clear()
        return True

    def run(t):
        nonlocal last
        if t <= last + 1e-9:
            return True
        if (outs or ins) and not apply():
            return False
        if len(on) != 5:
            why.append(f"{len(on)} on the floor at {at_lab or 'the start'}")
            return False
        u = cur(); u[0] += t - last
        for p in on:
            floor[p] += t - last
        last = t
        return True
    pend = None                                   # the end of the period being played (its last seconds may have no entry)
    other = lambda x: opp if x == team else team
    miss, poss = None, None                       # the side of the last missed shot, and of the side with the ball
    for r in rows:
        if r["period"] != r["period"]:
            continue
        t = abs_time(r["period_type"], r["period"], r["gt"])
        if t is not None:
            ot = str(r["period_type"]).upper().startswith("OVER")
            end = 2400 + int(r["period"]) * 300 if ot else int(r["period"]) * 600
            if pend is not None and end > pend and not run(pend):
                return None, None, why
            pend = max(pend or 0, end)
            if not run(t):
                return None, None, why
        at, side = str(r["action_type"]).lower(), side_of(r)
        if at == "substitution":
            if side == team and r["first_name"]:
                (ins if str(r["sub_type"]).lower() == "in" else outs).append(nkey(r["first_name"], r["family_name"]))
                at_lab = lab(r)
            continue
        st = str(r["sub_type"]).lower().replace(" ", "")
        if side is None and opp and at in ("rebound", "turnover"):
            # the newer feed logs team rebounds and team turnovers without a team: an offensive rebound goes to the
            # side that missed the last shot, a defensive one to the other side, a turnover to the side with the ball
            side = (miss if st.startswith("off") else other(miss) if miss else None) if at == "rebound" else poss
        if at in ("2pt", "3pt", "freethrow") and side:
            if r["success"] == 1:
                poss, miss = other(side), None
            else:
                miss = side
        elif at == "rebound" and side and "deadball" not in st:
            poss = side
        elif at in ("steal", "jumpball") and side and (at == "steal" or st == "won"):
            poss = side
        elif at == "turnover" and side:
            poss = other(side)
        if side is None or at not in ("2pt", "3pt", "freethrow", "rebound", "turnover"):
            continue
        if at == "rebound" and "deadball" in st:
            continue                              # a dead-ball team rebound (after a missed first free throw): not in the box score
        u = cur()
        d = u[1] if side == team else u[2]
        made = r["success"] == 1
        if at in ("2pt", "3pt"):
            d["fga"] += 1
            if at == "3pt":
                d["tpa"] += 1
            if made:
                d["fgm"] += 1; d["pts"] += 3 if at == "3pt" else 2
                if at == "3pt":
                    d["tpm"] += 1
        elif at == "freethrow":
            d["fta"] += 1
            if made:
                d["ftm"] += 1; d["pts"] += 1
        elif at == "rebound":
            if st.startswith("off"):
                d["oreb"] += 1
            elif st.startswith("def"):
                d["dreb"] += 1
        elif at == "turnover":
            d["tov"] += 1
    if pend is not None and not run(pend):
        return None, None, why
    if (outs or ins) and not apply():
        return None, None, why
    return units, floor, why


def mmss(v):
    try:
        m, s = str(v).split(":")[:2]
        return int(m) + float(s) / 60
    except Exception:
        return None


def main():
    root = sys.argv[1]
    dest = os.environ.get("NBL_OFFICIAL_CACHE") or tempfile.mkdtemp()
    cur = json.load(open(os.path.join(HERE, "curated.json")))
    seasons, _, _ = build.games_by_season(dest)
    import pyreadr
    files = official.download(dest)
    bx = pyreadr.read_r(files["box"])[None]
    print("reading the play-by-play ...", flush=True)
    pbp = rdsread.read_df(download_pbp(dest))
    want = []                                      # (coach id, season, team, games)
    for c in cur["coaches"]:
        for st in c.get("stints") or []:
            season, team, frm = st["season"], st["team"], st.get("from")
            G = [g for g in seasons.get(season, []) if team in (g["home"], g["away"]) and (not frm or (g["date"] and g["date"] >= frm))]
            want.append((c["id"], season, team, G))
    ids = {sid(g["id"]) for _, _, _, G in want for g in G}
    pbp = pbp[pbp["match_id"].astype(str).isin(ids)]
    cols = ["match_id", "team_name", "period_type", "period", "gt", "action_type", "sub_type", "success", "first_name", "family_name", "action_number"]
    by_match = defaultdict(list)
    for rec in pbp[cols].astype(object).where(pbp[cols].notna(), None).to_dict("records"):
        for k in ("period", "success", "action_number"):
            if rec[k] is None:
                rec[k] = float("nan")
        by_match[sid(rec["match_id"])].append(rec)
    box = defaultdict(lambda: defaultdict(dict))  # match -> team -> player key -> (minutes, starter, name, headshot)
    seasonph = defaultdict(Counter)               # (season, team, player key) -> his box score headshots, every season
    for r in bx.to_dict("records"):
        u, k = txt(r.get("photo_s")) or txt(r.get("photo_t")), nkey(r["first_name"], r["family_name"])
        if u:
            seasonph[(str(r["season"])[:5] + str(r["season"])[7:], tn(r["team_name"]), k)][u] += 1
        if sid(r["match_id"]) in ids:
            box[sid(r["match_id"])][tn(r["team_name"])][k] = (mmss(r["minutes"]), int(r["starter"] or 0) if r["starter"] == r["starter"] else 0, f"{r['first_name']} {r['family_name']}".strip(), u)
    # each season's official player profiles (2016-17 on): the headshot for a player whose box score rows have none
    prof = {}
    for r in pyreadr.read_r(files["bios"])[None].to_dict("records"):
        u = txt(r.get("S1_url")) or txt(r.get("M1_url")) or txt(r.get("L1_url"))
        if u:
            prof[(r["season"], tn(r["teamName"]), nkey(r["firstName"], r["familyName"]))] = u
    have_photo = {x["id"] for x in json.load(open(os.path.join(root, "data", "photos.json")))}   # NBL27 headshots
    # ESPN ids of NBL27 players, by name, for their photos
    bios = json.load(open(os.path.join(root, "data", "bios.json")))
    espn = {}
    for b in bios:
        for nm in (b.get("name"), b.get("nblName")):
            if nm:
                espn.setdefault(re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", nm).encode("ascii", "ignore").decode().lower()), b["id"])
    out, rep = {}, []
    shots, who = {}, defaultdict(set)              # season photos: image id -> URL, image id -> players
    for cid, season, team, G in want:
        P, U, games, left = {}, {}, [], []
        for gi, g in enumerate(sorted(G, key=lambda g: (g["date"] or "", sid(g["id"])))):
            mid, opp = sid(g["id"]), g["away"] if g["home"] == team else g["home"]
            info = {"id": mid, "date": g["date"], "opp": opp, "home": g["home"] == team}
            games.append(info)
            rows, B = by_match.get(mid), box.get(mid, {}).get(team)
            why = []
            if not rows:
                why.append("no play-by-play")
            elif not g["box"] or not B:
                why.append("no official box score")
            else:
                starters = [p for p, v in B.items() if v[1] == 1]
                if len(starters) != 5:
                    why.append(f"{len(starters)} starters in the box score")
                else:
                    units, floor, why = rebuild(ordered(rows), team, starters, B, opp)
                    if units is not None:
                        f = defaultdict(float); a = defaultdict(float); sec = 0.0
                        for k, (s_, ff, aa) in units.items():
                            sec += s_
                            for x, v in ff.items():
                                f[x] += v
                            for x, v in aa.items():
                                a[x] += v
                        ot = max(0, round((g["mins"] - 40) / 5))
                        exp = 2400 + 300 * ot
                        o, oo = g["box"][team], g["box"][opp]
                        if abs(sec - exp) > 30:
                            why.append(f"floor time {sec / 60:.1f} of {exp / 60:.0f} minutes")
                        if f["pts"] != o["pts"] or a["pts"] != oo["pts"]:
                            why.append(f"points {f['pts']:.0f}-{a['pts']:.0f}, official {o['pts']:.0f}-{oo['pts']:.0f}")
                        if abs(f["fga"] - o["fga"]) > 3 or abs(f["fta"] - o["fta"]) > 3 or abs(a["fga"] - oo["fga"]) > 3 or abs(a["fta"] - oo["fta"]) > 3:
                            why.append("shot attempts off the box score")
                        # 2 minutes, plus whatever the official minutes fall short of five players' full game (the
                        # official count sometimes leaves a player out for a stretch, so the five on the floor don't add up)
                        short = max(0.0, 5 * exp / 60 - sum(v[0] or 0 for v in B.values()))
                        off = [B[p][2] for p in B if B[p][0] is not None and abs(floor.get(p, 0) / 60 - B[p][0]) > 2 + short]
                        if off:
                            why.append("minutes off for " + ", ".join(off))
                        if not why:
                            for k, (s_, ff, aa) in units.items():
                                if s_ <= 0 and not any(ff.values()) and not any(aa.values()):
                                    continue
                                idx = []
                                for p in sorted(k):
                                    if p not in P:
                                        nm = B[p][2] if p in B else p
                                        P[p] = [len(P), nm, espn.get(p)]
                                    idx.append(P[p][0])
                                key = tuple(sorted(idx))
                                u = U.get(key)
                                if u is None:
                                    u = U[key] = [list(key), 0.0, [0] * len(K), [0] * len(K), []]
                                u[1] += s_
                                for j, x in enumerate(K):
                                    u[2][j] += ff.get(x, 0); u[3][j] += aa.get(x, 0)
                                if gi not in u[4]:
                                    u[4].append(gi)
            info["ok"] = not why
            if why:
                left.append(f"{g['date']} v {opp}: {'; '.join(why)}")
        players, need, nophoto = [None] * len(P), 0, []
        long_season = f"{season[:4]}-{int(season[:4]) + 1}"
        dated = sorted(G, key=lambda g: (g["date"] or "", sid(g["id"])))
        for p, (i, nm, e) in P.items():
            key = None
            if e not in have_photo:
                need += 1
                urls = [v[3] for v in (box.get(sid(g["id"]), {}).get(team, {}).get(p) for g in dated) if v and v[3]]
                n = Counter(urls)            # the season's most used headshot on his box score rows (ties: the later)
                u = max(n, key=lambda u: (n[u], max(j for j, x in enumerate(urls) if x == u))) if n else None
                if not u:
                    # none on his rows that season: his headshot with the same team in the nearest season that has
                    # one (the later on a tie), else that season's profile (which holds his latest headshot, not
                    # necessarily that season's)
                    y0 = int(season[:4])
                    for y in sorted((y for y in range(2015, y0 + 13) if y != y0), key=lambda y: (abs(y - y0), -y)):
                        c = seasonph.get((f"{y}-{str(y + 1)[2:]}", team, p))
                        if c:
                            u = max(c, key=c.get)
                            break
                    u = u or prof.get((long_season, team, p))
                src = photo_src(u)
                if src:
                    key = src[0]
                    shots.setdefault(key, src[1]); who[key].add(f"{nm} {season}")
                else:
                    nophoto.append(nm)
            players[i] = [nm, e, key]
        units = [[u[0], round(u[1], 1), [int(v) for v in u[2]], [int(v) for v in u[3]], sorted(u[4])] for u in U.values()]
        out[f"{season}|{team}"] = {"coach": cid, "games": games, "players": players, "units": sorted(units, key=lambda u: -u[1])}
        used = sum(1 for g in games if g["ok"])
        rep.append(f"{cid} {season} {team}: {used} of {len(games)} games reconcile, {len(units)} five-man units, "
                   f"season photos for {need - len(nophoto)} of the {need} players without an NBL27 photo"
                   + (f" (none on file: {', '.join(sorted(nophoto))})" if nophoto else "")
                   + "".join(f"\n   left out {x}" for x in left))
    # the season photos -> data/coachphotos/<image id>.webp (kept from earlier runs), as the Player cards convert theirs
    pdir = os.path.join(root, "data", "coachphotos")
    os.makedirs(pdir, exist_ok=True)

    def fetch(item):
        key, url = item
        fp = os.path.join(pdir, key + ".webp")
        if not os.path.exists(fp):
            b = cards.photo_webp(url)
            if b:
                open(fp, "wb").write(b)
        return key, os.path.exists(fp)
    with cf.ThreadPoolExecutor(8) as ex:
        got = dict(ex.map(fetch, sorted(shots.items())))
    for v in out.values():
        for pl in v["players"]:
            if pl[2] and not got.get(pl[2]):
                pl[2] = None
    for f in os.listdir(pdir):
        if f.endswith(".webp") and not got.get(f[:-5]):
            os.remove(os.path.join(pdir, f))
    rep.extend(f"photo not downloaded ({', '.join(sorted(who[k]))}): {shots[k]}" for k in sorted(got) if not got[k])
    res = {"_about": ("Head coaches tab lineups, built once by updater/coaches/lineups_hist.py: each NBL27 head coach's NBL "
                      "seasons since 2015-16 (coaches/curated.json stints), rebuilt from the official play-by-play (nblR) and "
                      "checked game by game against the official box scores; players = [name, ESPN id (NBL27 roster) or "
                      "null, season photo data/coachphotos/<id>.webp (official headshot of that season) or null]; units = "
                      "[player indexes, seconds, f, a, game indexes], f/a = " + ", ".join(K) + " (or the season's own "
                      "'keys'). Overseas seasons ('league'): updater/coaches/lineups_gleague.py, ESPN's play-by-play. Built "
                      + datetime.date.today().isoformat() + "."),
           "keys": list(K), "seasons": out}
    fp = os.path.join(root, "data", "coachlineups.json")
    old = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else {}
    for k, v in (old.get("seasons") or {}).items():
        if v.get("league") and k not in out:
            out[k] = v                                # overseas seasons, built by lineups_gleague.py, stay as they are
    json.dump(res, open(fp, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
    print("\n".join(rep))
    print(f"{len(out)} coach-seasons -> {fp} ({os.path.getsize(fp) / 1e6:.2f} MB); {sum(got.values())} season photos -> {pdir}")


if __name__ == "__main__":
    main()
