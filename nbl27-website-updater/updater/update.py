"""NBL27 website updater, run by GitHub Actions (.github/workflows/update-data.yml).

Checks ESPN for newly completed NBL27 regular-season games and adds them to data/*.json:
games (team totals), shots, players, lineups, pshots, events. Uses only site.api.espn.com.
The processing logic is the same as the Claude task's stored scripts (shotlib.py, lineups.py,
pshots.py, events.py in this folder are copies of meta/shotscript, lineupscript, pshotscript,
eventscript).

Usage: python3 updater/update.py <repo_root> [days_back]
"""
import json, os, sys, subprocess, tempfile, datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shotlib as S  # noqa: E402

SYD = ZoneInfo("Australia/Sydney")
SEASON_START = datetime.date(2026, 9, 19)
CLUBS = {"Adelaide 36ers", "Brisbane Bullets", "Cairns Taipans", "Illawarra Hawks", "Melbourne United",
         "New Zealand Breakers", "Perth Wildcats", "South East Melbourne Phoenix", "Sydney Kings",
         "Tasmania JackJumpers"}
COLS = ["games", "shots", "players", "lineups", "pshots", "events"]


def load(root, name):
    fp = os.path.join(root, "data", name + ".json")
    return json.load(open(fp)) if os.path.exists(fp) else ([] if name != "meta" else {})


def num(v):
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except Exception:
        return 0


def pair(s):
    m, a = s.split("-")
    return int(m), int(a)


def summary(eid, espn_dir):
    fp = os.path.join(espn_dir, f"{eid}.json")
    if not os.path.exists(fp):
        d = S.get(f"{S.API}/summary?event={eid}")
        json.dump(d, open(fp, "w"))
    return json.load(open(fp))


def game_doc(eid, j, date):
    """Team totals from the ESPN box score (same fields as the RealGM 'games' docs)."""
    comp = j["header"]["competitions"][0]
    side = {c["homeAway"]: c for c in comp["competitors"]}
    periods = max(len(c.get("linescores", [])) for c in comp["competitors"])
    mins = 200 + 25 * max(0, periods - 4)
    stats = {t["team"]["displayName"]: {s["name"]: s["displayValue"] for s in t["statistics"]}
             for t in j["boxscore"]["teams"]}
    out = {"id": f"espn-{eid}", "date": date, "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
    for ha in ("away", "home"):
        name = side[ha]["team"]["displayName"]
        T = stats[name]
        fgm, fga = pair(T["fieldGoalsMade-fieldGoalsAttempted"])
        tpm, tpa = pair(T["threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
        ftm, fta = pair(T["freeThrowsMade-freeThrowsAttempted"])
        pts = int(side[ha]["score"])
        if 2 * fgm + tpm + ftm != pts:
            return None, f"{name} points don't reconcile ({2 * fgm + tpm + ftm} vs {pts})"
        out[ha] = {"team": name, "min": mins, "fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa, "ftm": ftm,
                   "fta": fta, "oreb": int(num(T["offensiveRebounds"])), "dreb": int(num(T["defensiveRebounds"])),
                   "ast": int(num(T["assists"])), "pf": int(num(T["fouls"])), "stl": int(num(T["steals"])),
                   "tov": int(num(T["turnovers"])), "blk": int(num(T["blocks"])), "pts": pts}
    return out, None


def player_doc(eid, j, date, home, away):
    comp = j["header"]["competitions"][0]
    score = {c["team"]["displayName"]: int(c["score"]) for c in comp["competitors"]}
    tt = {t["team"]["displayName"]: {s["name"]: s["displayValue"] for s in t["statistics"]}
          for t in j["boxscore"]["teams"]}
    teams, probs = {}, []
    for p in j["boxscore"]["players"]:
        tn = p["team"]["displayName"]
        s = p["statistics"][0]
        k = s["keys"]
        L = []
        for a in s["athletes"]:
            if a.get("didNotPlay") or not a.get("stats"):
                continue
            st = dict(zip(k, a["stats"]))
            mn = num(st.get("minutes", "0"))
            if not mn:
                continue
            fgm, fga = pair(st["fieldGoalsMade-fieldGoalsAttempted"])
            tpm, tpa = pair(st["threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
            ftm, fta = pair(st["freeThrowsMade-freeThrowsAttempted"])
            L.append({"id": a["athlete"]["id"], "name": a["athlete"]["displayName"],
                      "pos": a["athlete"].get("position", {}).get("abbreviation", "NA"),
                      "starter": bool(a.get("starter")), "min": mn, "fgm": fgm, "fga": fga, "tpm": tpm,
                      "tpa": tpa, "ftm": ftm, "fta": fta, "oreb": int(num(st["offensiveRebounds"])),
                      "dreb": int(num(st["defensiveRebounds"])), "ast": int(num(st["assists"])),
                      "stl": int(num(st["steals"])), "blk": int(num(st["blocks"])),
                      "tov": int(num(st["turnovers"])), "pf": int(num(st["fouls"])),
                      "pm": int(num(str(st.get("plusMinus", "0")).replace("+", ""))), "pts": int(num(st["points"]))})
        teams[tn] = L
        T = tt[tn]
        for f, key, i in [("fgm", "fieldGoalsMade-fieldGoalsAttempted", 0), ("fga", "fieldGoalsMade-fieldGoalsAttempted", 1),
                          ("tpm", "threePointFieldGoalsMade-threePointFieldGoalsAttempted", 0),
                          ("tpa", "threePointFieldGoalsMade-threePointFieldGoalsAttempted", 1),
                          ("ftm", "freeThrowsMade-freeThrowsAttempted", 0), ("fta", "freeThrowsMade-freeThrowsAttempted", 1)]:
            tot = sum(x[f] for x in L)
            if tot != pair(T[key])[i]:
                probs.append(f"{tn} {f} {tot} vs {T[key]}")
        if sum(x["pts"] for x in L) != score[tn]:
            probs.append(f"{tn} pts {sum(x['pts'] for x in L)} vs {score[tn]}")
    doc = {"espnId": eid, "date": date, "home": home, "away": away, "teams": teams,
           "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
    if probs:
        doc["mismatch"] = "; ".join(probs)
    return doc


def run_script(script, args):
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, script)] + args, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode:
        print(r.stderr.strip())
    return r


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    back = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    data = {c: load(root, c) for c in COLS}
    meta = load(root, "meta")
    have = {c: {d.get("espnId") for d in data[c]} for c in COLS[1:]}
    have_games = {(g["date"], frozenset((g["away"]["team"], g["home"]["team"]))) for g in data["games"]}

    today = datetime.datetime.now(SYD).date()
    evs = S.events(today - datetime.timedelta(back), today + datetime.timedelta(1))
    tmp = tempfile.mkdtemp()
    espn = os.path.join(tmp, "espn"); os.makedirs(espn)
    db = os.path.join(tmp, "db"); os.makedirs(os.path.join(db, "players")); os.makedirs(os.path.join(db, "shots"))
    warnings, added = [], {c: [] for c in COLS}

    todo = []
    for eid, e in sorted(evs.items()):
        if not e["status"]["type"].get("completed") or e.get("season", {}).get("type") != 2:
            continue
        c = e["competitions"][0]["competitors"]
        side = {x["homeAway"]: x["team"]["displayName"] for x in c}
        if set(side.values()) - CLUBS or len(side) != 2:
            continue
        tip = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(SYD)
        if tip.date() < SEASON_START:
            continue
        todo.append((eid, e, tip.strftime("%Y-%m-%d"), side["home"], side["away"]))

    for eid, e, date, home, away in todo:
        if all(eid in have[c] for c in COLS[1:]) and (date, frozenset((home, away))) in have_games:
            continue
        print(f"== {eid} {date} {away} @ {home}")
        try:
            j = summary(eid, espn)
        except Exception as ex:
            warnings.append(f"{eid}: ESPN summary not available ({ex})"); continue
        if (date, frozenset((home, away))) not in have_games:
            g, err = game_doc(eid, j, date)
            if err:
                warnings.append(f"{date} {away} at {home} (ESPN {eid}): {err}"); continue
            data["games"].append(g); added["games"].append(eid); have_games.add((date, frozenset((home, away))))
        if eid not in have["shots"]:
            try:
                sd = S.game(eid, e); data["shots"].append(sd); added["shots"].append(eid)
            except Exception as ex:
                warnings.append(f"{eid}: shots failed ({ex})")
        if eid not in have["players"]:
            data["players"].append(player_doc(eid, j, date, home, away)); added["players"].append(eid)

    # dependent docs: need players (+ shots for pshots)
    P = {d["espnId"]: d for d in data["players"]}
    Sh = {d["espnId"]: d for d in data["shots"]}
    ids = [t[0] for t in todo if t[0] in P]
    for eid in ids:
        json.dump(P[eid], open(os.path.join(db, "players", f"{eid}.json"), "w"))
        if eid in Sh:
            json.dump(Sh[eid], open(os.path.join(db, "shots", f"{eid}.json"), "w"))

    for col, script in (("lineups", "lineups.py"), ("events", "events.py")):
        need = [i for i in ids if i not in have[col]]
        if not need:
            continue
        out = os.path.join(tmp, col); run_script(script, [db, espn, out] + need)
        for i in need:
            fp = os.path.join(out, f"{i}.json")
            if os.path.exists(fp):
                data[col].append(json.load(open(fp))); added[col].append(i)
            else:
                warnings.append(f"{i}: {col} not produced")

    need = [i for i in ids if i not in have["pshots"] and i in Sh]
    if need:
        sd, pdir, out = (os.path.join(tmp, x) for x in ("ps_s", "ps_p", "ps_out"))
        os.makedirs(sd); os.makedirs(pdir)
        for i in need:
            json.dump(Sh[i], open(os.path.join(sd, f"{i}.json"), "w"))
            json.dump(P[i], open(os.path.join(pdir, f"{i}.json"), "w"))
        run_script("pshots.py", [tmp, sd, pdir, out])
        for i in need:
            fp = os.path.join(out, f"{i}.json")
            if os.path.exists(fp):
                data["pshots"].append(json.load(open(fp))); added["pshots"].append(i)

    print("added:", {c: v for c, v in added.items() if v}, "warnings:", warnings)
    if not any(added.values()) and ("; ".join(warnings) or None) == meta.get("status", {}).get("warning"):
        print("nothing new"); return

    now = datetime.datetime.now(SYD).replace(microsecond=0).isoformat()
    st = meta.setdefault("status", {})
    st["updatedAt"] = now
    st["lastGameDate"] = max(g["date"] for g in data["games"])
    st["source"] = "RealGM / ESPN"
    if warnings:
        st["warning"] = "; ".join(warnings)
    else:
        st.pop("warning", None)
    st.pop("github_error", None)
    ss = meta.setdefault("shots_status", {})
    ss.update(checkedAt=now, games=len(data["shots"]), mismatches=sum(1 for d in data["shots"] if d.get("mismatch")))
    meta.setdefault("players_status", {}).update(checkedAt=now, games=len(data["players"]),
                                                  mismatches=sum(1 for d in data["players"] if d.get("mismatch")))
    meta.setdefault("lineups_status", {}).update(checkedAt=now, games=len(data["lineups"]),
                                                  invalid=sum(1 for d in data["lineups"] if d.get("invalid")))
    meta.setdefault("pshots_status", {}).update(checkedAt=now, games=len(data["pshots"]))
    bad = [f"{d['espnId']} {t}" for d in data["events"] for t, x in d.get("teams", {}).items() if x.get("ok") is False]
    meta.setdefault("events_status", {}).update(checkedAt=now, games=len(data["events"]), badTeamGames=len(bad),
                                                 notes="Team-games failing reconciliation: " + ", ".join(bad))

    data["games"].sort(key=lambda g: (g["date"], g["id"]))
    for c in COLS[1:]:
        data[c].sort(key=lambda d: d.get("espnId", ""))
    for c in COLS:
        json.dump(data[c], open(os.path.join(root, "data", c + ".json"), "w"), separators=(",", ":"))
    json.dump(meta, open(os.path.join(root, "data", "meta.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
