"""Fill per-game makes/attempts (FGM, FGA, 3PM, 3PA, FTM, FTA) into the RealGM rows of updater/overseas.json.

The first RealGM reads (raw/<id>.rgm.jsonl, raw/<id>.rgm2.txt) took the Summary tables, which carry percentages only.
RealGM's per-game tables on the same page carry the makes and attempts. Two separate reads of them are saved as
raw/<id>.rgmpg1.json and raw/<id>.rgmpg2.json. A stored row gets its six numbers only when, in BOTH reads, exactly one
per-game row (or several identical ones) matches it on season, GP, PTS, FG%, 3P% and FT% as already stored, both reads
give the same six numbers, and the numbers are consistent with the row: FGM/FGA, 3PM/3PA and FTM/FTA reproduce the
stored percentages within rounding, and 2*FGM + 3PM + FTM gives the stored PTS within rounding (G League since 2019-20: PTS between that and
2*FGM + 3PM + 3*FTM, as one free throw there is worth the whole foul). Anything else is left
empty and listed in the report. Nothing is computed into the data: the six values are copied as RealGM prints them.

Usage: python3 fill_shooting.py <overseas.json> <raw_dir>   (prints a report; rewrites overseas.json in place)
"""
import json, os, re, sys

K6 = ("fgm", "fga", "tpm", "tpa", "ftm", "fta")


def num(v):
    try:
        return float(str(v).strip().replace("*", ""))
    except Exception:
        return None


def pctv(v):
    """'.492' / '49.2' / 0.492 -> 0.492"""
    x = num(v)
    if x is None:
        return None
    return x / 100 if x > 1.0001 else x


def season_ok(stored, read):
    r = re.sub(r"[^0-9\-]", "", str(read or ""))
    s = str(stored or "")
    if r == s:
        return True
    if re.fullmatch(r"\d{4}", s):          # calendar-year league season Y is filed by RealGM under (Y-1)-Y
        y = int(s)
        return r == f"{y-1}-{str(y)[2:]}"
    return False


def same(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def ratio_ok(m, a, p):
    """made/att per game (each rounded to 0.1) can give the stored percentage p (rounded to 0.001)"""
    if p is None:
        return m == 0 and a == 0
    if a == 0:
        return m == 0
    lo = max(m - 0.05, 0) / (a + 0.05)
    hi = (m + 0.05) / max(a - 0.05, 1e-9)
    return lo - 0.0006 <= p <= hi + 0.0006


def match(row, reads):
    out = []
    for r in reads:
        if not season_ok(row["season"], r.get("season")):
            continue
        if num(r.get("gp")) != row["gp"] or not same(num(r.get("pts")), row.get("pts"), 0.0001):
            continue
        if any(not same(pctv(r.get(k)), row.get(k), 0.0006) for k in ("fgp", "tpp", "ftp") if row.get(k) is not None):
            continue
        v = tuple(num(r.get(k)) for k in K6)
        if any(x is None for x in v):
            continue
        out.append(v)
    out = sorted(set(out))
    return out[0] if len(out) == 1 else (None if not out else "ambiguous")


def main(ovs_fp, raw):
    d = json.load(open(ovs_fp))
    rep, filled, left = [], 0, 0
    for pid, e in d.items():
        if pid.startswith("_"):
            continue
        rows = [h for h in e.get("history") or [] if h.get("src") == "RealGM" and h.get("fga") is None and h.get("gp")]
        if not rows:
            continue
        reads = []
        for n in (1, 2):
            fp = os.path.join(raw, f"{pid}.rgmpg{n}.json")
            reads.append(json.load(open(fp)).get("rows", []) if os.path.exists(fp) else None)
        for h in rows:
            tag = f"{pid} {h['season']} {h['league']} {h['team']}"
            if reads[0] is None or reads[1] is None:
                rep.append(f"{tag}: no per-game reads"); left += 1; continue
            a, b = match(h, reads[0]), match(h, reads[1])
            if a in (None, "ambiguous") or b in (None, "ambiguous"):
                rep.append(f"{tag}: read1 {a or 'no match'}, read2 {b or 'no match'} - left empty"); left += 1; continue
            if a != b:
                rep.append(f"{tag}: reads differ {a} vs {b} - left empty"); left += 1; continue
            fgm, fga, tpm, tpa, ftm, fta = a
            bad = []
            if not ratio_ok(fgm, fga, h.get("fgp")): bad.append("FG")
            if not ratio_ok(tpm, tpa, h.get("tpp")): bad.append("3P")
            if not ratio_ok(ftm, fta, h.get("ftp")): bad.append("FT")
            if tpm > fgm + 1e-9 or tpa > fga + 1e-9: bad.append("3P>FG")
            base = 2 * fgm + tpm + ftm
            if h.get("pts") is not None:
                if h["league"] == "G League" and h["season"] >= "2019-20":
                    # G League since 2019-20: one free throw is worth the whole foul (2 or 3 points), so PTS lies
                    # between 2*FGM + 3PM + FTM and 2*FGM + 3PM + 3*FTM
                    if not (base - 0.25 <= h["pts"] <= 2 * fgm + tpm + 3 * ftm + 0.25): bad.append("PTS")
                elif abs(base - h["pts"]) > 0.25:
                    bad.append("PTS")
            if bad:
                rep.append(f"{tag}: {a} fails {','.join(bad)} check - left empty"); left += 1; continue
            h.update(dict(zip(K6, a))); h["shootingSrc"] = "RealGM per game (two reads)"
            filled += 1
    d["_about"] = d["_about"].rstrip() + (" Per-game makes/attempts for RealGM rows: overseas/fill_shooting.py from"
                                          " raw/<id>.rgmpg1.json and .rgmpg2.json (2026-10-24).") if "fill_shooting" not in d["_about"] else d["_about"]
    json.dump(d, open(ovs_fp, "w"), indent=1, ensure_ascii=False)
    print(f"filled {filled}, left empty {left}")
    print("\n".join(rep))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
