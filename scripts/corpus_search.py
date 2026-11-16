#!/usr/bin/env python3
# 基于倒排索引（或回退全文）检索 848 本语料，返回去重后的相关片段。
import os, re, glob, gzip, pickle, json

ROOT = "D:/WB/2026-11-12-16-11-17"
CORPUS = os.path.join(ROOT, "tcm-corpus", "tcm")
OUT_DIR = os.path.join(ROOT, "tcm-corpus")
PUNCT = re.compile(r"[\s，。、；：？！,.;:?!（）()\[\]《》<>~@#%&*\-_=+|\\/\"'“”‘’…·—　]")

def norm(s):
    return PUNCT.sub("", s).strip()

def bigrams(s):
    s = norm(s)
    return [s[i:i+2] for i in range(len(s)-1)] if len(s) >= 2 else ([s] if s else [])

def _load_index():
    idxp = os.path.join(OUT_DIR, "index.pkl.gz")
    metap = os.path.join(OUT_DIR, "meta.json")
    if os.path.exists(idxp) and os.path.exists(metap):
        try:
            with gzip.open(idxp, "rb") as f:
                idx = pickle.load(f)
            meta = json.load(open(metap, encoding="utf-8"))
            return idx.get("term_doc", {}), meta
        except Exception:
            return None, None
    return None, None

def search_corpus(query, top=8, use_index=True):
    """返回命中列表：[{file,title,author,dynasty,year,line_count,lines:[原文片段]}]。"""
    if not os.path.isdir(CORPUS):
        return []
    term_doc, meta = _load_index() if use_index else (None, None)
    files = sorted(glob.glob(os.path.join(CORPUS, "*.txt")))
    if not files:
        return []

    # 查询词：整体去空格 + 按空格拆分的有效词
    q_full = norm(query)
    q_terms = [t for t in re.split(r"[\s,，。;；]+", query) if len(norm(t)) >= 2]
    if not q_terms:
        q_terms = [q_full] if q_full else []

    # 用索引收窄候选文档：按单个查询词各自求倒排交集，再取并集，避免跨词的杂散二元组挤掉真实命中
    candidates = None
    if term_doc:
        per_term = []
        for t in q_terms:
            sets = [set(term_doc[bg]) for bg in set(bigrams(t)) if bg in term_doc]
            if sets:
                s = set.intersection(*sets) if len(sets) > 1 else sets[0]
                if s:
                    per_term.append(s)
        if per_term:
            candidates = set().union(*per_term)
        if candidates is not None and len(candidates) > 400:   # 候选太多，退化全扫
            candidates = None
    scan_files = [files[i] for i in sorted(candidates)] if candidates is not None else files

    hits = []
    seen_line = set()
    for fp in scan_files:
        matched = []
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    nl = norm(line)
                    if not nl:
                        continue
                    hit_terms = sum(1 for t in q_terms if t in nl) if q_full not in nl else len(q_terms) + 1
                    if hit_terms == 0:
                        continue
                    h = hash(nl)
                    if h in seen_line:
                        continue
                    seen_line.add(h)
                    matched.append((hit_terms, line.strip()[:120]))
        except Exception:
            continue
        if matched:
            matched.sort(key=lambda x: -x[0])
            base = os.path.basename(fp)
            m = next((x for x in (meta or []) if x["file"] == base), {})
            hits.append({
                "file": base,
                "title": m.get("title", ""),
                "author": m.get("author", ""),
                "dynasty": m.get("dynasty", ""),
                "year": m.get("year", ""),
                "lines": [t for _, t in matched[:top]],
            })
    hits.sort(key=lambda x: -len(x["lines"]))
    return hits[:top * 2]

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "怕冷 无汗 身痛"
    for h in search_corpus(q, top=3):
        print(f"《{h['title'] or h['file']}》{h['author']}{h['dynasty']} ({h['file']})")
        for ln in h["lines"]:
            print("   ", ln)
        print()
