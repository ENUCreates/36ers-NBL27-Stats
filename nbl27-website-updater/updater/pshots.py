"""NBL27 per-player shot extractor (pshots). Reuses meta/shotscript logic exactly:
team_name/TEAMS, roster-based team attribution (fallback score delta, then play team.id), voided-play skip,
classify() incl. y>47 mirror safeguard; basket (25,0). Frame: x 0..50 ft across, y ft from baseline/basket toward halfway.
Usage: python3 -I pshots.py <data_dir> <shots_dir> <players_dir> <out_dir>
  shots_dir/players_dir: JSON docs from the artifact's 'shots' and 'players' collections (<id>.json)
  writes out_dir/<id>.json (pshots doc) and out_dir/_report.json
"""
import json, sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shotlib as S
ZC = {"ra": 0, "paint": 1, "mid": 2, "c3": 3, "ab3": 4, "unk": 5}

def norm_xy(p):
    c = p.get('coordinate') or {}
    x, y = c.get('x'), c.get('y')
    ok = x is not None and y is not None and -100 < x < 150 and -100 < y < 150
    if not ok: return None, None
    if y > 47: x, y = 50 - x, 94 - y
    return round(x, 1), round(y, 1)

def game(eid, sdoc, pdoc):
    j = S.get(f"{S.API}/summary?event={eid}")
    comp = j['header']['competitions'][0]
    idmap, side = {}, {}
    for c in comp['competitors']:
        n = S.team_name(c['team']['displayName']); idmap[str(c['team']['id'])] = n; side[c['homeAway']] = n
    roster, names = {}, {}
    for bt in j['boxscore'].get('players', []):
        tn = idmap.get(str(bt['team']['id']))
        for st in bt.get('statistics', []):
            for a in st.get('athletes', []):
                roster[a['athlete']['id']] = tn; names[a['athlete']['id']] = a['athlete'].get('displayName')
    plays = j.get('plays', [])
    sc = [(int(p.get('homeScore') or 0), int(p.get('awayScore') or 0)) for p in plays]
    def voided(i):
        if not (plays[i].get('scoreValue', 0) > 0) or i == 0: return False
        dh, da = sc[i][0] - sc[i-1][0], sc[i][1] - sc[i-1][1]
        for k in range(i+1, len(plays)):
            if sc[k] != sc[i]:
                return (dh > 0 and sc[k][0] < sc[i][0]) or (da > 0 and sc[k][1] < sc[i][1])
        return False
    teams = {n: {"ok": True, "p": {}} for n in idmap.values()}
    prev = (0, 0)
    for i, p in enumerate(plays):
        cur = sc[i]; dh, da = cur[0]-prev[0], cur[1]-prev[1]; prev = cur
        if not p.get('shootingPlay') or (S.USE_VOID and voided(i)): continue
        if 'free throw' in p['text'].lower(): continue
        parts = p.get('participants') or []
        aid = parts[0].get('athlete', {}).get('id') if parts else None
        n = roster.get(aid) if aid else None
        if n is None and p.get('scoreValue', 0) > 0:
            if dh > 0 and da == 0: n = side['home']
            elif da > 0 and dh == 0: n = side['away']
        if n is None: n = idmap.get(str(p.get('team', {}).get('id')))
        if n is None: continue
        z, made = S.classify(p)
        x, y = norm_xy(p)
        key = aid or "0"
        pl = teams[n]['p'].setdefault(key, {"n": names.get(aid) or "Unknown", "s": []})
        pl['s'].append([x, y, int(bool(made)), ZC[z]])
    rep = {}
    for tn, plist in pdoc['teams'].items():
        T = teams.get(tn)
        if T is None: rep[tn] = 'team missing'; continue
        mism = []; tot = sum(len(v['s']) for v in T['p'].values()); box = 0
        for pl in plist:
            box += pl['fga']
            s = T['p'].get(pl['id'], {"s": []})['s']
            m, a = sum(q[2] for q in s), len(s)
            if (m, a) != (pl['fgm'], pl['fga']): mism.append(f"{pl['name']} {m}-{a} vs {pl['fgm']}-{pl['fga']}")
        T['ok'] = abs(tot - box) <= 3
        rep[tn] = {"fga": tot, "box": box, "ok": T['ok'], "mism": mism}
    return {"espnId": eid, "date": sdoc['date'], "teams": teams}, rep

if __name__ == '__main__':
    D, SD, PD, OD = sys.argv[1:5]
    os.makedirs(OD, exist_ok=True); report = {}
    for f in sorted(os.listdir(SD)):
        eid = f[:-5]
        doc, rep = game(eid, json.load(open(os.path.join(SD, f))), json.load(open(os.path.join(PD, f))))
        json.dump(doc, open(os.path.join(OD, f), 'w'), separators=(',', ':'))
        report[eid] = rep; print(eid, json.dumps(rep)[:300])
    json.dump(report, open(os.path.join(OD, '_report.json'), 'w'), indent=1)
