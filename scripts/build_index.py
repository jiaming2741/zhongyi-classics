#!/usr/bin/env python3
# 为 848 本古籍语料构建正式倒排索引（提速）与去重报告。
# 产出：tcm-corpus/index.pkl（bigram->文档列表）、tcm-corpus/meta.json（书目元数据）、tcm-corpus/dup_report.json（去重报告）
import os, re, json, glob, gzip, pickle, hashlib

ROOT = "D:/WB/2026-11-12-16-11-17"
CORPUS = os.path.join(ROOT, "tcm-corpus", "tcm")
OUT_DIR = os.path.join(ROOT, "tcm-corpus")

PUNCT = re.compile(r"[\s，。、；：？！,.;:?!（）()\[\]《》<>~@#%&*\-_=+|\\/\"'“”‘’…·—　]")

def norm_line(s):
    return PUNCT.sub("", s).strip()

def bigrams(s):
    s = norm_line(s)
    return [s[i:i+2] for i in range(len(s)-1)] if len(s) >= 2 else ([s] if s else [])

def parse_meta(path):
    meta = {"title": "", "author": "", "dynasty": "", "year": ""}
    keymap = {"书名": "title", "作者": "author", "朝代": "dynasty", "年份": "year"}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 20:
                    break
                for cn, en in keymap.items():
                    if line.startswith(cn + "：") or line.startswith(cn + ":"):
                        val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                        if not meta[en]:
                            meta[en] = val
    except Exception:
        pass
    return meta

def main():
    files = sorted(glob.glob(os.path.join(CORPUS, "*.txt")))
    print(f"扫描文件：{len(files)}")
    meta = []
    term_doc = {}            # bigram -> set(doc_id)
    line_hash_doc = {}       # linehash -> set(doc_id) 用于去重
    for did, fp in enumerate(files):
        m = parse_meta(fp)
        line_count = 0
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line_count += 1
                    if not norm_line(line):
                        continue
                    for bg in set(bigrams(line)):
                        term_doc.setdefault(bg, set()).add(did)
                    h = hashlib.md5(norm_line(line).encode("utf-8")).hexdigest()
                    line_hash_doc.setdefault(h, set()).add(did)
        except Exception as e:
            print("  读错误", fp, e)
        meta.append({
            "id": did, "file": os.path.basename(fp),
            "title": m["title"], "author": m["author"],
            "dynasty": m["dynasty"], "year": m["year"],
            "line_count": line_count, "size": os.path.getsize(fp),
        })
        if did % 100 == 0:
            print(f"  索引中 {did}/{len(files)}")

    # 去重报告：重复行、疑似同名异本
    dup_lines = {h: sorted(d) for h, d in line_hash_doc.items() if len(d) > 1}
    # 同名书分组
    title_groups = {}
    for mm in meta:
        key = norm_line(mm["title"])
        if key:
            title_groups.setdefault(key, []).append(mm["id"])
    dup_titles = {k: v for k, v in title_groups.items() if len(v) > 1}

    # 序列化
    term_doc_ser = {k: sorted(v) for k, v in term_doc.items()}
    with gzip.open(os.path.join(OUT_DIR, "index.pkl.gz"), "wb") as f:
        pickle.dump({"term_doc": term_doc_ser}, f)
    json.dump(meta, open(os.path.join(OUT_DIR, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({
        "dup_line_count": len(dup_lines),
        "dup_line_samples": dict(list(dup_lines.items())[:5]),
        "dup_title_groups": dup_titles,
    }, open(os.path.join(OUT_DIR, "dup_report.json"), "w", encoding="utf-8"),
        ensure_ascii=False, indent=1)

    print(f"完成。文档数={len(meta)} 倒排词项数={len(term_doc_ser)} 重复行组={len(dup_lines)} 同名书组={len(dup_titles)}")
    print("产物：index.pkl.gz / meta.json / dup_report.json （位于 tcm-corpus/）")

if __name__ == "__main__":
    main()
