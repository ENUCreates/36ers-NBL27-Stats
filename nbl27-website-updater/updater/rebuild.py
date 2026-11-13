"""NBL27 daily FULL REBUILD of the website data from ESPN (maximum-accuracy mode).

Every run rebuilds every NBL27 regular-season game from scratch, so any correction ESPN makes to any game,
however old, is picked up. Steps:
  1. List all completed regular-season NBL-vs-NBL games since 19 Sep 2026 (ESPN scoreboard, day by day).
  2. Skip games that tipped off less than SETTLE_HOURS ago (ESPN keeps correcting stats after the buzzer);
     they are added on the next run.
  3. Download each game's ESPN summary ONCE into a cache, so every step reads the same snapshot.
  4. Build games (official team totals via teamtotals.py + overrides.json), players, shots, lineups, pshots
     and events with the same scripts as before. Lineups are then rebuilt from the NBL's own play-by-play
     (nbl_lineups.py, read with the official box scores) wherever it reproduces the official box score; the
     ESPN-based solve (lineups.py / oncourt.py) stays for any other team-game.
  5. Verify each game before publishing: ESPN status final; points = 2*FGM + 3PM + FTM for both teams;
     player lines add up to the team box (FGM/FGA/3PM/3PA/FTM/FTA/PTS). A game that fails is HELD: its
     previously published (already verified) documents are kept and a warning is recorded.
  6. Safety: a previously published game that ESPN no longer lists is kept, with a warning. If fewer than
     90% of the previously published games come back, nothing is written at all.
  7. Write data/*.json + data/meta.json and print a change report (also stored in meta.status.changes).
  8. Refresh the Player cards (bios.py), then the projected ladder (ladder.py -> data/ladder.json) and the matchup
     facts (matchups.py -> data/matchups.json, data/matchups_played.json), then what happened in each newly played
     game (recaps.py -> the game's 'recap' in data/matchups_played.json).
     The injury list (injuries.py -> data/injuries.json) is a separate step of the daily update, after this one.

Usage: python3 rebuild.py <repo_root> [settle_hours=8]
"""
import json, os, sys, tempfile, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shotlib as S  # noqa: E402
import update as U  # noqa: E402

COLS = ["games", "shots", "players", "lineups", "pshots", "events"]
SYD = U.SYD


def key(g):
    return (g["date"], frozenset((g["home"]["team"], g["away"]["team"])))


def apply_overrides(g, eid, ov):
    for side in ("home", "away"):
        o = ov.get(eid, {}).get(g[side]["team"])
        if o:
            for k, v in o.items():
                if k in g[side]:
                    g[side][k] = v


def verify(g, p, j):
    probs = []
    st = j["header"]["competitions"][0].get("status", {}).get("type", {})
    if not st.get("completed"):
        probs.append("ESPN status not final")
    for side in ("home", "away"):
        t = g[side]
        if 2 * t["fgm"] + t["tpm"] + t["ftm"] != t["pts"]:
            probs.append(f"{t['team']} points don't reconcile")
    if p.get("mismatch"):
        probs.append("player lines don't add up: " + p["mismatch"])
    if set(p["teams"]) != {g["home"]["team"], g["away"]["team"]}:
        probs.append("player teams don't match")
    return probs


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    settle = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    prev = {c: U.load(root, c) for c in COLS}
    meta = U.load(root, "meta")
    ov = json.load(open(os.path.join(HERE, "overrides.json")))
    ov = {k: v for k, v in ov.items() if not k.startswith("_")}
    now = datetime.datetime.now(SYD)

    evs = S.events(U.SEASON_START, now.date() + datetime.timedelta(1))
    todo, pending = [], []
    for eid, e in sorted(evs.items()):
        if not e["status"]["type"].get("completed") or e.get("season", {}).get("type") != 2:
            continue
        side = {x["homeAway"]: x["team"]["displayName"] for x in e["competitions"][0]["competitors"]}
        if len(side) != 2 or set(side.values()) - U.CLUBS:
            continue
        tip = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(SYD)
        if tip.date() < U.SEASON_START:
            continue
        date = tip.strftime("%Y-%m-%d")
        if now - tip < datetime.timedelta(hours=settle):
            pending.append(f"{date} {side['away']} at {side['home']}")
            continue
        todo.append((eid, e, date, side["home"], side["away"]))

    tmp = tempfile.mkdtemp()
    espn = os.path.join(tmp, "espn"); os.makedirs(espn)
    os.environ["NBL_ESPN_CACHE"] = espn
    # solved on-court lineups (oncourt.py), reused while a game's inputs are unchanged; committed with data/
    os.environ["NBL_ONCOURT_CACHE"] = os.path.join(root, "data", "oncourt.json")
    db = os.path.join(tmp, "db"); os.makedirs(os.path.join(db, "players")); os.makedirs(os.path.join(db, "shots"))
    new = {c: {} for c in COLS}          # games keyed by (date, pair); others by espnId
    warnings, held = [], []
    prev_games = {key(g): g for g in prev["games"]}
    prev_by = {c: {d.get("espnId"): d for d in prev[c]} for c in COLS[1:]}

    ok_ids = []
    for eid, e, date, home, away in todo:
        try:
            j = U.summary(eid, espn)
            g, err = U.game_doc(eid, j, date)
            if err:
                raise ValueError(err)
            apply_overrides(g, eid, ov)
            p = U.player_doc(eid, j, date, home, away)
            probs = verify(g, p, j)
            if probs:
                raise ValueError("; ".join(probs))
            sd = S.game(eid, e)
        except Exception as ex:
            held.append(f"{date} {away} at {home}: {ex}")
            k = (date, frozenset((home, away)))
            if k in prev_games:
                new["games"][k] = prev_games[k]
                for c in COLS[1:]:
                    if eid in prev_by[c]:
                        new[c][eid] = prev_by[c][eid]
            continue
        new["games"][key(g)] = g
        new["players"][eid] = p
        new["shots"][eid] = sd
        json.dump(p, open(os.path.join(db, "players", f"{eid}.json"), "w"))
        json.dump(sd, open(os.path.join(db, "shots", f"{eid}.json"), "w"))
        ok_ids.append(eid)

    # play-by-play derived collections, built from the same cached snapshots
    if ok_ids:
        for col, script in (("lineups", "lineups.py"), ("events", "events.py")):
            out = os.path.join(tmp, col)
            U.run_script(script, [db, espn, out] + ok_ids)
            for i in ok_ids:
                fp = os.path.join(out, f"{i}.json")
                if os.path.exists(fp):
                    new[col][i] = json.load(open(fp))
                elif i in prev_by[col]:
                    new[col][i] = prev_by[col][i]; warnings.append(f"{i}: {col} not rebuilt, kept previous")
        out = os.path.join(tmp, "pshots")
        U.run_script("pshots.py", [tmp, os.path.join(db, "shots"), os.path.join(db, "players"), out])
        for i in ok_ids:
            fp = os.path.join(out, f"{i}.json")
            if os.path.exists(fp):
                new["pshots"][i] = json.load(open(fp))
            elif i in prev_by["pshots"]:
                new["pshots"][i] = prev_by["pshots"][i]; warnings.append(f"{i}: pshots not rebuilt, kept previous")

    # safety: keep previously published games ESPN no longer lists (unless they are only waiting to settle)
    pend_keys = set()
    for eid, e in evs.items():
        side = {x["homeAway"]: x["team"]["displayName"] for x in e["competitions"][0]["competitors"]}
        tip = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(SYD)
        pend_keys.add((tip.strftime("%Y-%m-%d"), frozenset(side.values())))
    for k, g in prev_games.items():
        if k not in new["games"] and k not in pend_keys:
            new["games"][k] = g
            warnings.append(f"{g['date']} {g['away']['team']} at {g['home']['team']}: not listed by ESPN, kept previous")
    if prev["games"] and len([k for k in prev_games if k in new["games"]]) < 0.9 * len(
            [k for k in prev_games if k not in pend_keys or k in new["games"]]):
        print("ABORT: rebuild lost too many games; nothing written. Held:", held)
        sys.exit(2)

    # Official NBL box scores (official.py: the NBL's own API, same day; nblR on GitHub for earlier seasons): the
    # official spelling of every player's name, and a few counting fields where ESPN's box score differs from the
    # league's (assists, turnovers, steals, blocks, rebounds and team rebound splits). Scoring, minutes and +/- are only
    # compared, never changed. A player the official box score has on the floor but ESPN's leaves out (a few seconds
    # show as "-" minutes there) is added with his official line. If the official data can't be read, the
    # corrections, added lines and spellings from the previous run are reused.
    prev_off = (meta.get("status") or {}).get("official") or {}
    off_status = {}
    import nbl_lineups as NL
    import nbl_shots as NS
    import nbl_events as NE
    import nbl_periods as NP
    pe_rep = []
    lu_rep, lu_n, sh_rep, ev_rep = [], {}, [], []
    try:
        import official as O
        os.environ["NBL_OFFICIAL_CACHE"] = os.path.join(tmp, "official")
        off = O.load(os.environ["NBL_OFFICIAL_CACHE"])
        glist, plist = list(new["games"].values()), list(new["players"].values())
        cmp = O.compare(glist, plist, off)
        fixes = O.apply(glist, plist, cmp)
        # a game whose official team totals the NBL's feed doesn't have this run (it drops them for a while at times):
        # last run's team corrections stay, where ESPN still has the value that was corrected
        miss = {tuple(x) for x in cmp.get("no_team_totals") or []}
        G_ = {g["id"].replace("espn-", ""): g for g in glist}
        for c in prev_off.get("fixes") or []:
            if "id" in c or (c["eid"], c["team"]) not in miss or c["eid"] not in G_:
                continue
            g = G_[c["eid"]]
            T = g["home"] if g["home"]["team"] == c["team"] else g["away"]
            if T.get(c["field"]) == c["ours"]:
                T[c["field"]] = c["official"]
                cmp["fix_teams"].append(c)
                fixes.append(f"{g['date']} {c['team']} team {c['field']} {c['ours']}->{c['official']} (last run's official figure)")
        # lineups: the NBL's own play-by-play wherever it reproduces the official box score (every substitution is
        # recorded there); the ESPN-based solve built above stays for any other team-game
        lu_rep, lu_n = NL.apply(new["lineups"], glist, plist, off, cmp["pairs"], O.nbl27_index(off), prev_by["lineups"])
        # shots (shot charts and shot-zone totals): the NBL's own play-by-play, which carries every shot's position
        # and reproduces the official box score; ESPN's shot logs stay only where the NBL's doesn't add up
        sh_rep, _ = NS.apply(new["pshots"], new["shots"], glist, plist, off, cmp["pairs"], O.nbl27_index(off),
                             prev_by["pshots"], prev_by["shots"])
        # rotations, clutch, possessions and the assist network: the NBL's play-by-play too, wherever its lineups
        # reproduce the official box score; ESPN's (events.py) stay for any other team-game
        ev_rep, _ = NE.apply(new["events"], glist, plist, off, cmp["pairs"], O.nbl27_index(off), prev_by["events"])
        # team stats by period (quarters, overtime) for the tips' played games: the NBL's feed, where they add up
        pe_rep = NP.apply(glist, off, O.nbl27_index(off), prev.get("games"))
        names = {i: p["name"] for i, p in cmp["pairs"].items()}
        for i, n in (prev_off.get("names") or {}).items():
            names.setdefault(i, n)          # keep spellings learned from games the official files no longer list
        off_status = {"games": len(cmp["checked"]), "pending": len(cmp["missing"]), "names": names,
                      "fixes": cmp["fix_players"] + cmp["fix_teams"], "added": cmp["add_players"], "applied": fixes,
                      "notes": cmp["notes"]}
    except Exception as ex:
        warnings.append(f"official NBL box scores unavailable ({ex}); previous official corrections reused")
        NL.keep_previous(new["lineups"], prev_by["lineups"])
        NS.keep_previous(new["pshots"], new["shots"], prev_by["pshots"], prev_by["shots"])
        NE.keep_previous(new["events"], prev_by["events"])
        NP.keep_previous(list(new["games"].values()), prev.get("games"))
        pe_rep = ["official data unavailable: last run's team stats by period kept"]
        ev_rep = ["official data unavailable: last run's NBL play-by-play events kept"]
        lu_rep = ["official data unavailable: last run's NBL play-by-play lineups kept"]
        sh_rep = ["official data unavailable: last run's NBL play-by-play shots kept"]
        off_status = {k: prev_off.get(k) for k in ("games", "pending", "names", "fixes", "added", "applied", "notes")}
        off_status["error"] = str(ex)[:300]
        G_ = {g["id"].replace("espn-", ""): g for g in new["games"].values()}
        for c in prev_off.get("fixes") or []:      # re-apply only where ESPN still has the value we corrected
            g = G_.get(c["eid"])
            if not g:
                continue
            if "id" in c:
                for x in (new["players"].get(c["eid"]) or {}).get("teams", {}).get(c["team"], []):
                    if x["id"] == c["id"] and x.get(c["field"]) == c["ours"]:
                        x[c["field"]] = c["official"]
            else:
                T = g["home"] if g["home"]["team"] == c["team"] else g["away"]
                if T.get(c["field"]) == c["ours"]:
                    T[c["field"]] = c["official"]
        # minutes to the second (the official box score's): keep last run's where ESPN's whole minutes still agree
        for eid, d in new["players"].items():
            pv = (prev_by["players"].get(eid) or {}).get("teams") or {}
            for team, L in (d.get("teams") or {}).items():
                old = {x["id"]: x.get("min") for x in pv.get(team, [])}
                for x in L:
                    m = old.get(x["id"])
                    if isinstance(m, float) and abs(m - (x.get("min") or 0)) <= 0.51:
                        x["min"] = m
        for c in prev_off.get("added") or []:      # re-add only where ESPN's box score still leaves the player out
            g, r = G_.get(c["eid"]), c["row"]
            L = ((new["players"].get(c["eid"]) or {}).get("teams") or {}).get(c["team"])
            if not g or L is None or any(x["id"] == r["id"] for x in L):
                continue
            T = g["home"] if g["home"]["team"] == c["team"] else g["away"]
            if all(sum(x.get(f, 0) for x in L) + r.get(f, 0) == T.get(f) for f in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta")):
                L.append(dict(r))
    if off_status.get("names"):
        O_names = off_status["names"]
        import official as O
        O.rename({c: list(new[c].values()) for c in COLS[1:]}, O_names)

    # change report
    changes = []
    for k, g in new["games"].items():
        if k not in prev_games:
            changes.append(f"added {g['date']} {g['away']['team']} {g['away']['pts']} at {g['home']['team']} {g['home']['pts']}")
            continue
        pg = prev_games[k]
        for side in ("home", "away"):
            t = g[side]
            ps = pg["home"] if pg["home"]["team"] == t["team"] else pg["away"]
            d = {f: (ps.get(f), t[f]) for f in t if f != "team" and ps.get(f) != t[f]}
            if d:
                changes.append(f"corrected {g['date']} {t['team']}: " + ", ".join(f"{f} {a}->{b}" for f, (a, b) in d.items()))
    for k, g in prev_games.items():
        if k not in new["games"]:
            changes.append(f"removed until settled {g['date']} {g['away']['team']} at {g['home']['team']}")
    for c in COLS[1:]:
        n = sum(1 for i, d in new[c].items() if i in prev_by[c] and json.dumps(d.get("teams"), sort_keys=True)
                != json.dumps(prev_by[c][i].get("teams"), sort_keys=True))
        if n:
            changes.append(f"{c}: {n} game(s) updated")
    for c in off_status.get("added") or []:
        pv = ((prev_by["players"].get(c["eid"]) or {}).get("teams") or {}).get(c["team"], [])
        if not any(x["id"] == c["row"]["id"] for x in pv):
            changes.append(f"added from the official box score: {c['date']} {c['row']['name']} ({c['team']}, {c['official']}) - "
                           f"ESPN's box score leaves him out")

    data = {"games": sorted(new["games"].values(), key=lambda g: (g["date"], g["id"]))}
    for c in COLS[1:]:
        data[c] = sorted(new[c].values(), key=lambda d: d.get("espnId", ""))
    nowiso = now.replace(microsecond=0).isoformat()
    st = meta.setdefault("status", {})
    st.update(updatedAt=nowiso, lastGameDate=max(g["date"] for g in data["games"]),
              source="ESPN box scores, checked against the NBL's official box scores; lineups, shots, rotations, clutch "
                     "and assists from the NBL's own play-by-play (ESPN's where it doesn't add up); full rebuild once a day",
              games=len(data["games"]), changes=changes[:60])
    if off_status:
        st["official"] = {**{k: v for k, v in off_status.items() if v is not None}, "checkedAt": nowiso}
    else:
        st.pop("official", None)
    msgs = [f"held (failed checks, previous version kept): {h}" for h in held] + warnings
    if msgs:
        st["warning"] = "; ".join(msgs)
    else:
        st.pop("warning", None)
    if pending:
        st["pending"] = pending
    else:
        st.pop("pending", None)
    st.pop("github_error", None)
    ssrc = collections.Counter(v for d in data["shots"] for v in (d.get("src") or {}).values())
    meta.setdefault("shots_status", {}).update(checkedAt=nowiso, games=len(data["shots"]),
                                                mismatches=sum(1 for d in data["shots"] if d.get("mismatch")),
                                                sources={"nbl": ssrc.get("nbl", 0), "espn": ssrc.get("espn", 0)})
    meta.setdefault("players_status", {}).update(checkedAt=nowiso, games=len(data["players"]),
                                                  mismatches=sum(1 for d in data["players"] if d.get("mismatch")))
    fits = [f for d in data["lineups"] for f in (d.get("fit") or {}).values() if f]
    srcs = collections.Counter(v for d in data["lineups"] for v in (d.get("src") or {}).values())
    meta.setdefault("lineups_status", {}).update(checkedAt=nowiso, games=len(data["lineups"]),
                                                  invalid=sum(1 for d in data["lineups"] if d.get("invalid")),
                                                  sources={"nbl": srcs.get("nbl", 0), "espn": srcs.get("espn", 0)},
                                                  notes=lu_rep[:40],
                                                  method="Who is on the floor: the NBL's own play-by-play (every substitution "
                                                         "recorded) wherever it reproduces the official box score - every "
                                                         "player's minutes and plus-minus (nbl_lineups.py); otherwise solved "
                                                         "from ESPN play-by-play and matched to each player's official minutes "
                                                         "and plus-minus (oncourt.py)",
                                                  fit={"teamGames": len(fits),
                                                       "minutesOver30s": sum(1 for f in fits if f.get("maxMinErr", 0) > 30),
                                                       "plusMinusOff": sum(1 for f in fits if f.get("maxPmErr", 0) > 0),
                                                       "actionsOffCourt": sum(f.get("offcourt", 0) for f in fits),
                                                       "unrecordedSwapsPerTeamGame": round(sum(f.get("unrecorded", 0) for f in fits) / max(1, len(fits)), 1)})
    psrc = collections.Counter(v for d in data["pshots"] for v in (d.get("src") or {}).values())
    pbad = [f"{d['date']} {t}" for d in data["pshots"] for t, x in d.get("teams", {}).items() if x.get("ok") is False]
    meta["pshots_status"] = {"checkedAt": nowiso, "games": len(data["pshots"]), "basket": {"x": 25, "y": 0},
                             "sources": {"nbl": psrc.get("nbl", 0), "espn": psrc.get("espn", 0)},
                             "badTeamGames": len(pbad), "notes": sh_rep[:40] + ([f"left out: {', '.join(pbad)}"] if pbad else []),
                             "method": "Every shot from the NBL's own play-by-play (position, shooter, two or three) wherever it "
                                       "reproduces this site's box score (nbl_shots.py); otherwise ESPN's play-by-play (pshots.py)",
                             "frame": "x across the court 0-50 ft with the basket at 25, y = feet from the basket toward halfway"}
    bad = [f"{d['date']} {t}" for d in data["events"] for t, x in d.get("teams", {}).items() if x.get("ok") is False]
    esrc = collections.Counter(v for d in data["events"] for v in (d.get("src") or {}).values())
    meta["events_status"] = {"checkedAt": nowiso, "games": len(data["events"]), "badTeamGames": len(bad),
                             "sources": {"nbl": esrc.get("nbl", 0), "espn": esrc.get("espn", 0)},
                             "notes": ev_rep[:40] + ([f"left out (don't reconcile with the box score): {', '.join(bad)}"] if bad else []),
                             "method": "Rotations, clutch, possessions and assists from the NBL's own play-by-play wherever its "
                                       "lineups reproduce the official box score (nbl_events.py); otherwise from ESPN's (events.py)"}
    meta["periods_status"] = {"checkedAt": nowiso, "games": sum(1 for g in data["games"] if g.get("periods")),
                              "notes": pe_rep[:40],
                              "method": "Each team's stats by quarter (and overtime) from the NBL's own feed, kept only where they "
                                        "add up to its full-game row and to this site's box score (nbl_periods.py)"}
    for c in COLS:
        json.dump(data[c], open(os.path.join(root, "data", c + ".json"), "w"), separators=(",", ":"), sort_keys=True)
    json.dump(meta, open(os.path.join(root, "data", "meta.json"), "w"), indent=1)
    print(json.dumps({"games": len(data["games"]), "held": held, "pending": pending, "warnings": warnings,
                      "changes": changes, "lineups": lu_rep, "shots": sh_rep, "events": ev_rep}, indent=1))
    # Player cards: bios + NBA/NCAA history (bios.py; refreshes new players and a few stale ones per run).
    # A failure here never affects the game data written above.
    try:
        U.run_script("bios.py", [root])
    except Exception as ex:
        print("bios step failed:", ex)
    # Projected ladder (ladder.py): simulates the rest of the season from the data just written and the NBL's own
    # fixture. If it fails, the previous data/ladder.json stays (its status.computedAt shows when it was made).
    try:
        r = U.run_script("ladder.py", [root])
        if r.returncode:
            print("ladder step failed: previous projection kept")
    except Exception as ex:
        print("ladder step failed: previous projection kept:", ex)
    # Matchup facts for every game (matchups.py; injuries.py runs it again once today's injury list is in)
    try:
        r = U.run_script("matchups.py", [root])
        if r.returncode:
            print("matchups step failed: previous facts kept")
    except Exception as ex:
        print("matchups step failed: previous facts kept:", ex)
    # What happened in each game played since the last run (recaps.py: into data/matchups_played.json, once per game)
    try:
        r = U.run_script("recaps.py", [root])
        if r.returncode:
            print("recaps step failed: the new games have no recap yet")
    except Exception as ex:
        print("recaps step failed: the new games have no recap yet:", ex)


if __name__ == "__main__":
    main()
