"""rdsread.py - a small reader for R's XDR serialization (.rds), enough for a data frame of atomic columns.
Strings are stored as per-column codes (numpy int32) + a list of unique values, so a large frame fits in memory.
read_df(path) -> pandas.DataFrame with categorical string columns. pyreadr can't open the nblR play-by-play file
(pbp/pbp.rds), so lineups_hist.py reads it with this."""
import gzip, struct, sys
import numpy as np, pandas as pd

NILVALUE, SYM, LIST, CLO, ENV, PROM, LANG, CHAR, LGL, INT, REAL, CPLX, STR, VEC = 254, 1, 2, 3, 4, 5, 6, 9, 10, 13, 14, 15, 16, 19
REF, ALTREP, ATTRLIST, ATTRLANG, BASEENV, EMPTYENV, GLOBALENV, BASENS, NAMESPACE, PACKAGE, PERSIST = 255, 238, 239, 240, 241, 242, 253, 247, 249, 250, 247
MISSINGARG, UNBOUND = 251, 252


class Str:
    """A string vector as codes into `levels` (NA = -1)."""
    def __init__(self, codes, levels):
        self.codes, self.levels = codes, levels


class R:
    def __init__(self, buf):
        self.b, self.p, self.refs = buf, 0, []

    def i(self):
        v = struct.unpack_from(">i", self.b, self.p)[0]; self.p += 4; return v

    def charsxp(self):
        flags = self.i()
        n = self.i()
        if n == -1:
            return None
        s = self.b[self.p:self.p + n]; self.p += n
        return s.decode("utf-8", "replace")

    def item(self):
        flags = self.i()
        t, has_attr, has_tag = flags & 0xFF, bool(flags & (1 << 9)), bool(flags & (1 << 10))
        if t == NILVALUE: return None
        if t in (EMPTYENV, BASEENV, GLOBALENV, UNBOUND, MISSINGARG, BASENS): return ("special", t)
        if t == REF:
            idx = flags >> 8
            if idx == 0: idx = self.i()
            return self.refs[idx - 1]
        if t == SYM:
            name = self.item_char_after()
            sym = ("sym", name); self.refs.append(sym); return sym
        if t in (PACKAGE, NAMESPACE):
            self.i()                                  # 0
            v = self.strvec_plain(); r = ("ns", v); self.refs.append(r); return r
        if t == ENV:
            locked = self.i(); r = {"env": True}; self.refs.append(r)
            r["enclos"] = self.item(); r["frame"] = self.item(); r["hashtab"] = self.item(); r["attr"] = self.item(); return r
        if t in (LIST, ATTRLIST, LANG, ATTRLANG, CLO, PROM):
            out = []
            while True:
                attr = self.item() if has_attr else None
                tag = self.item() if has_tag else None
                car = self.item()
                out.append((tag[1] if isinstance(tag, tuple) and tag and tag[0] == "sym" else tag, car))
                flags = self.i()
                t2 = flags & 0xFF
                if t2 == NILVALUE: break
                if t2 not in (LIST, ATTRLIST, LANG, ATTRLANG):
                    self.p -= 4; out.append((None, self.item())); break
                has_attr, has_tag = bool(flags & (1 << 9)), bool(flags & (1 << 10))
            return ("pairlist", out)
        if t == CHAR:
            n = self.i()
            if n == -1: return None
            s = self.b[self.p:self.p + n]; self.p += n; return s.decode("utf-8", "replace")
        if t in (LGL, INT):
            n = self.i(); a = np.frombuffer(self.b, dtype=">i4", count=n, offset=self.p).astype(np.int32); self.p += 4 * n
            v = ("lgl" if t == LGL else "int", a)
        elif t == REAL:
            n = self.i(); a = np.frombuffer(self.b, dtype=">f8", count=n, offset=self.p).astype(np.float64); self.p += 8 * n
            v = ("real", a)
        elif t == STR:
            v = ("str", self.strvec_body())
        elif t == VEC:
            n = self.i(); v = ("vec", [self.item() for _ in range(n)])
        elif t == ALTREP:
            info = self.item(); state = self.item(); attr = self.item()
            cls = info[1][0][1][1] if isinstance(info, tuple) else None
            v = self.altrep(cls, state)
            return (v[0], v[1], attr) if attr else v
        else:
            raise ValueError(f"unsupported type {t} at {self.p}")
        attrs = self.item() if has_attr else None
        return (v[0], v[1], attrs) if attrs else v

    def item_char_after(self):
        flags = self.i(); n = self.i()
        s = self.b[self.p:self.p + max(n, 0)]; self.p += max(n, 0); return s.decode("utf-8", "replace")

    def strvec_plain(self):
        flags = self.i(); n = self.i(); return [self.charsxp() for _ in range(n)]

    def strvec_body(self):
        n = self.i()
        codes = np.empty(n, dtype=np.int32); lev, idx = [], {}
        b, p = self.b, self.p
        unpack = struct.unpack_from
        for k in range(n):
            flags, ln = unpack(">ii", b, p); p += 8
            if ln == -1:
                codes[k] = -1; continue
            s = b[p:p + ln]; p += ln
            c = idx.get(s)
            if c is None:
                c = idx[s] = len(lev); lev.append(s)
            codes[k] = c
        self.p = p
        return Str(codes, [x.decode("utf-8", "replace") for x in lev])

    def altrep(self, cls, state):
        if cls == "compact_intseq":
            n, start, inc = state[1]
            return ("int", (start + inc * np.arange(int(n))).astype(np.int32))
        if cls == "compact_realseq":
            n, start, inc = state[1]
            return ("real", start + inc * np.arange(int(n)))
        if cls in ("wrap_integer", "wrap_real", "wrap_logical", "wrap_string", "wrap_complex", "wrap_list"):
            return state[1][0][1] if isinstance(state, tuple) and state[0] == "pairlist" else state
        if cls == "deferred_string":
            arg = state[1][0][1]                    # the original vector (int or real), converted to strings
            vals = arg[1]
            return ("str", Str(np.arange(len(vals), dtype=np.int32), [("%d" % x if float(x).is_integer() else repr(float(x))) for x in vals]))
        raise ValueError(f"unsupported ALTREP class {cls}")


def attr_of(obj, name):
    if not isinstance(obj, tuple) or len(obj) < 3 or not obj[2]:
        return None
    for tag, val in obj[2][1]:
        if tag == name:
            return val
    return None


def read_df(path):
    raw = gzip.open(path).read()
    r = R(raw)
    assert raw[:2] == b"X\n", raw[:2]
    r.p = 2
    ver = r.i(); r.i(); r.i()
    if ver == 3:
        n = r.i(); r.p += n
    obj = r.item()
    del raw
    names = attr_of(obj, "names")
    names = [names[1].levels[c] for c in names[1].codes] if names and names[0] == "str" else None
    cols = {}
    for nm, col in zip(names, obj[1]):
        kind, val = col[0], col[1]
        if kind == "str":
            cols[nm] = pd.Categorical.from_codes(val.codes, categories=pd.Index(val.levels).astype(object)) if len(set(val.levels)) == len(val.levels) else pd.Series([val.levels[c] if c >= 0 else None for c in val.codes])
        elif kind == "lgl":
            a = val.astype(np.float64); a[val == -2147483648] = np.nan; cols[nm] = a
        elif kind == "int":
            a = val.astype(np.float64); a[val == -2147483648] = np.nan
            lv = attr_of(col, "levels")
            if lv is not None:      # a factor
                L = [lv[1].levels[c] for c in lv[1].codes]
                cols[nm] = pd.Categorical.from_codes(np.where(np.isnan(a), -1, a - 1).astype(int), categories=L)
            else:
                cols[nm] = a
        elif kind == "real":
            cols[nm] = val
        else:
            cols[nm] = pd.Series([None] * len(obj[1][0][1].codes if obj[1][0][0] == "str" else obj[1][0][1]))
    return pd.DataFrame(cols)


if __name__ == "__main__":
    import time
    t = time.time()
    df = read_df(sys.argv[1])
    print(df.shape, f"{time.time() - t:.1f}s")
    print(df.dtypes.to_string())
    print(df.head(3).T.to_string()[:4000])
    if len(sys.argv) > 2:
        df.to_pickle(sys.argv[2])
