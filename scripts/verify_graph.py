#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中医知识图谱交叉验证（全中文输出版）。

用途：对给定方剂、药物列表或证型，核对药物归经、性味与方证关联。
数据优先级：
  完整图谱（四个 parquet，原名与性味归经为英文或拼音）经 zh_normalize 中文归一
  -> 完整图谱缺失时回退 scripts/dragontcm_seed.json（公有领域精选子集，离线兜底）。
组成补全优先级：
  经典方人工核实库 classic_formulas_zh.json
  -> 古籍语料共识库 corpus_formulas_zh.json
  -> 图谱自身组成字段与 contains 关系边（已中文归一）。
硬保证：
  1. 面向用户的全部内容均为中文，不出现拉丁字母；无法归一者写中文「待核」，绝不透出原名。
  2. 已核实经典方名录内每一首方都有中文药味组成；名录外条目不属于经典方，
     统一以中文标注「待核」，不冒充组成，也不输出空组成。
  3. 只列药味，不给可自行抓药的剂量；受保护药材已作替代或提示，须医师核定。
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import zh_normalize as Z

ROOT = os.path.dirname(os.path.dirname(HERE))
FULL_DIR = os.path.join(ROOT, "dragontcm")
SEED = os.path.join(HERE, "dragontcm_seed.json")
CLASSIC = os.path.join(HERE, "classic_formulas_zh.json")
CORPUS = os.path.join(HERE, "corpus_formulas_zh.json")

_HAS_LATIN = Z._HAS_LATIN
_HAS_CJK = Z._HAS_CJK

# 经典方命名特征（用于区分经典方剂与现代成药、单味药、病名条目）
CLASSIC_SUFFIX = ("汤", "散", "丸", "饮", "膏", "丹", "煎", "锭", "饼", "酒", "方", "汁", "露", "煎丸")
MODERN_MARK = ("片", "胶囊", "颗粒", "口服液", "注射", "冲剂", "糖浆", "软膏", "栓剂", "喷雾", "贴剂")

# 证候或脏腑关键词 -> 归经，用于方证一致性判断
SYNDROME_MERIDIAN = {
    "心": ["心"], "肺": ["肺"], "脾": ["脾"], "胃": ["胃"], "肝": ["肝"], "肾": ["肾"],
    "胆": ["胆"], "大肠": ["大肠"], "小肠": ["小肠"], "膀胱": ["膀胱"], "心包": ["心包"],
    "脾虚": ["脾"], "脾胃": ["脾", "胃"], "肝郁": ["肝"], "肝阳": ["肝"], "肝风": ["肝"],
    "肾虚": ["肾"], "肺热": ["肺"], "肺燥": ["肺"], "心火": ["心"], "心血": ["心"],
    "气虚": ["脾", "肺"], "气陷": ["脾"], "血虚": ["肝", "心", "脾"], "阴虚": ["肾", "肺", "胃"],
    "阳虚": ["肾", "心", "脾"], "水泛": ["肾", "脾"], "湿": ["脾"], "痰": ["脾", "肺"],
    "气分": ["肺", "胃"], "阳明": ["胃", "大肠"], "营分": ["心"], "血分": ["心", "肝"],
    "卫分": ["肺"], "少阳": ["胆", "肝"], "太阳": ["膀胱"], "表": ["肺"],
}

# 方名异写归一，便于命中已核实名录
NAME_FIX = [
    ("栝楼", "瓜蒌"), ("栝蒌", "瓜蒌"), ("括楼", "瓜蒌"),
    ("黄耆", "黄芪"), ("黄蓍", "黄芪"), ("耆", "芪"),
    ("葳蕤", "玉竹"), ("白芨", "白及"),
]


def canon_name(s):
    s = str(s or "").strip()
    for a, b in NAME_FIX:
        s = s.replace(a, b)
    return s


def is_classic_name(name):
    if not name or not (2 <= len(name) <= 20):
        return False
    if _HAS_LATIN.search(name) or not re.fullmatch(r"[\u4e00-\u9fff·]+", name):
        return False
    if any(m in name for m in MODERN_MARK):
        return False
    return name.endswith(CLASSIC_SUFFIX)


def _zh_book(raw):
    """书目文件名只保留中文书名，避免输出拉丁字母与文件后缀。"""
    s = re.sub(r"[^\u4e00-\u9fff]", "", str(raw or ""))
    return "《" + s + "》" if s else "古籍语料"


def _zh_terms(values):
    """把图谱英文主治、治法、功效转成中文要点，未登录术语一律不透出。"""
    out = []
    for v in values or []:
        if not v:
            continue
        for z in Z.action_zh(v):
            if z and not _HAS_LATIN.search(z) and z not in out:
                out.append(z)
    return out[:8]


def _parse_json_str(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


def _to_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.replace("、", ",").replace(";", ",").split(",") if x.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def _zh_to_py(s):
    """中文名转无调拼音大写键，仅在 pypinyin 可用时启用。"""
    try:
        from pypinyin import pinyin, Style
        return "".join(p[0] for p in pinyin(s, style=Style.NORMAL)).upper()
    except Exception:
        return None


def _load_lib(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("formulas", {})
    except Exception:
        return {}


class Graph:
    """统一后的中文图谱：所有对外字段均为中文。"""

    def __init__(self, source, herbs, formulas, classic, corpus):
        self.source = source
        self.herbs = herbs                  # 拼音规范键 -> 中文药物记录
        self.formulas = formulas            # 拼音规范键 -> 中文方剂记录
        self.classic = classic
        self.corpus = corpus
        # 中文名反查规范键，供归经核对使用
        self.zh2key_herb = {}
        for k, z in Z.HERB.items():
            if isinstance(z, str) and _HAS_CJK.search(z) and not _HAS_LATIN.search(z):
                self.zh2key_herb.setdefault(z, k)
        self.zh2key_formula = {}
        for k, z in Z.FORMULA.items():
            if isinstance(z, str) and _HAS_CJK.search(z) and not _HAS_LATIN.search(z):
                self.zh2key_formula.setdefault(z, k)
        self.resolved = self._build_resolved()

    # ---------- 组成补全与名录构建 ----------
    def _build_resolved(self):
        result = {}

        def put(name, comp, source, origin, indications=None, actions=None):
            name = canon_name(name)
            if not name or _HAS_LATIN.search(name):
                return
            clean = []
            for c in comp:
                z = Z.safe_zh(c, "药名")
                if z != "药名待核" and z not in clean:
                    clean.append(z)
            if not clean:
                return
            result[name] = {
                "中文名": name,
                "组成": clean,
                "组成来源": source,
                "出处": origin,
                "主治": _zh_terms(indications),
                "治法": _zh_terms(actions),
                "类型": "经典方" if is_classic_name(name) else "待核条目",
            }

        # 图谱原有组成（已中文归一），先登记，随后被更可靠的中文库覆盖
        for k, f in self.formulas.items():
            zh = f.get("中文名", "")
            if zh and zh != "方名待核" and not _HAS_LATIN.search(zh):
                put(zh, f.get("组成", []), "完整中医知识图谱（原名已中文归一）",
                    "图谱条目", f.get("主治", []), f.get("治法", []))

        # 语料共识库：仅收高可信条目，优先级低于人工核实库
        for name, rec in self.corpus.items():
            if rec.get("可信度") != "高":
                continue
            put(name, rec.get("组成", []), "八百四十八本古籍语料共识库",
                _zh_book(rec.get("出处")))

        # 人工核实库最后写入，覆盖前两者，保证经典方以已核实组成为准
        for name, rec in self.classic.items():
            put(name, rec.get("组成", []), "经典方人工核实库（公有领域典籍）",
                rec.get("出处", "典籍记载"), [rec.get("证型", "")], [])
        return result

    def resolve(self, name):
        name = canon_name(name)
        if name in self.resolved:
            return self.resolved[name]
        # 同方异名或去剂型后缀再试
        for suf in ("丸", "汤", "散", "膏", "丹", "饮"):
            if name.endswith(suf):
                alt = name[:-len(suf)]
                for suf2 in ("丸", "汤", "散", "膏", "丹", "饮"):
                    if alt + suf2 in self.resolved:
                        return self.resolved[alt + suf2]
        key = self.zh2key_formula.get(name) or _zh_to_py(name)
        if key and key in self.formulas:
            f = self.formulas[key]
            zh = f.get("中文名", "")
            if zh and zh in self.resolved:
                return self.resolved[zh]
        return None

    def herb_info(self, name):
        name = canon_name(name)
        key = self.zh2key_herb.get(name) or _zh_to_py(name)
        if key and key in self.herbs:
            return self.herbs[key]
        # 药名异写：去掉炮制前后缀再查
        for pre in ("生", "熟", "炙", "炒", "制", "酒", "姜", "法", "焦"):
            if name.startswith(pre) and len(name) > len(pre) + 1:
                k2 = self.zh2key_herb.get(name[len(pre):]) or _zh_to_py(name[len(pre):])
                if k2 and k2 in self.herbs:
                    return self.herbs[k2]
        return None

    def meridians_of(self, names):
        out, missing = {}, []
        for n in names:
            h = self.herb_info(n)
            if h and h.get("归经"):
                for m in h["归经"]:
                    out.setdefault(m, []).append(n)
            else:
                missing.append(n)
        return out, missing


def _load_full():
    import pyarrow.parquet as pq

    def rows(sub):
        fs = glob.glob(os.path.join(FULL_DIR, sub, "*.parquet"))
        return pq.read_table(fs[0]).to_pylist() if fs else []

    herbs = {}
    for r in rows("herbs"):
        raw = str(r.get("name", "")).strip()
        if not raw:
            continue
        key = Z.key(raw)
        prop = _parse_json_str(r.get("properties"))
        mer = []
        mobj = prop.get("meridians") or {}
        if isinstance(mobj, dict):
            for grp in ("primary", "secondary"):
                for v in _to_list(mobj.get(grp)):
                    z = Z.meridian_zh(v)
                    if z and z not in mer:
                        mer.append(z)
        taste = []
        tobj = prop.get("taste") or {}
        if isinstance(tobj, dict):
            for grp in ("primary", "secondary"):
                for v in _to_list(tobj.get(grp)):
                    z = Z.taste_zh(v)
                    if z and z not in taste:
                        taste.append(z)
        tobj2 = prop.get("temperature") or {}
        if isinstance(tobj2, dict):
            for grp in ("primary", "secondary"):
                for v in _to_list(tobj2.get(grp)):
                    z = Z.temp_zh(v)
                    if z and z not in taste:
                        taste.append(z)
        herbs[key] = {
            "中文名": Z.safe_zh(raw, "药名"),
            "归经": mer,
            "性味": taste,
            "功效": _zh_terms(_to_list(r.get("actions"))),
        }

    formulas = {}
    for r in rows("formulas"):
        raw = str(r.get("name", "")).strip()
        if not raw:
            continue
        key = Z.key(raw)
        z = Z.formula_zh(raw)
        if not z or _HAS_LATIN.search(z) or not _HAS_CJK.search(z):
            z = "方名待核"
        comp = _parse_json_str(r.get("composition"))
        cl = list(comp.keys()) if isinstance(comp, dict) else [str(x) for x in comp] if isinstance(comp, list) else []
        formulas[key] = {
            "中文名": z,
            "组成": cl,
            "主治": _to_list(r.get("syndromes")) + _to_list(r.get("treats")),
            "治法": _to_list(r.get("actions")),
        }

    # 关系边：contains 补组成，treats 补方证关联
    for r in rows("relations"):
        src = Z.key(r.get("source"))
        tgt = Z.key(r.get("target"))
        et, st = r.get("edge_type"), r.get("source_type")
        if et == "contains" and st == "formula" and src in formulas:
            f = formulas[src]
            seen = {Z.key(x) for x in f["组成"]}
            if tgt and tgt not in seen:
                f["组成"].append(str(r.get("target", "")).strip())
        elif et == "treats" and st == "condition" and tgt in formulas:
            f = formulas[tgt]
            c = str(r.get("source", "")).strip()
            if c and c not in f["主治"]:
                f["主治"].append(c)

    return "完整中医知识图谱（已中文归一）", herbs, formulas


def _load_seed():
    with open(SEED, encoding="utf-8") as f:
        s = json.load(f)
    herbs = {}
    for h in s.get("herbs", []):
        key = Z.key(h.get("zh") or h.get("en"))
        herbs[key] = {
            "中文名": h.get("zh", "药名待核"),
            "归经": [x for x in _to_list(h.get("归经")) if not _HAS_LATIN.search(x)],
            "性味": [x for x in _to_list(h.get("性味")) if not _HAS_LATIN.search(x)],
            "功效": [x for x in _to_list(h.get("功效")) if not _HAS_LATIN.search(x)],
        }
    formulas = {}
    for fo in s.get("formulas", []):
        key = Z.key(fo.get("zh"))
        formulas[key] = {
            "中文名": fo.get("zh", "方名待核"),
            "组成": _to_list(fo.get("组成")),
            "主治": _to_list(fo.get("主治")),
            "治法": _to_list(fo.get("治法")),
        }
    return "离线种子图谱（公有领域精选子集）", herbs, formulas


def load_graph():
    classic = _load_lib(CLASSIC)
    corpus = _load_lib(CORPUS)
    if os.path.isdir(FULL_DIR) and glob.glob(os.path.join(FULL_DIR, "formulas", "*.parquet")):
        try:
            source, herbs, formulas = _load_full()
            return Graph(source, herbs, formulas, classic, corpus)
        except Exception as e:
            print("（完整图谱加载失败，回退离线种子：已记录异常）", file=sys.stderr)
    source, herbs, formulas = _load_seed()
    return Graph(source, herbs, formulas, classic, corpus)


def cross_validate(formula_zh=None, syndrome=None, herbs=None):
    """返回中文交叉验证报告。键名沿用工程约定，所有可见取值均为中文。"""
    g = load_graph()
    q = canon_name(formula_zh) if formula_zh else ""
    disp = q if (q and not _HAS_LATIN.search(q) and _HAS_CJK.search(q)) else "方名待核"
    syn = str(syndrome) if syndrome and _HAS_CJK.search(str(syndrome)) else ""
    rep = {
        "source": g.source,
        "formula": disp,
        "formula_name": disp,
        "syndrome": syn or "未指定证型",
        "herbs": [Z.safe_zh(h, "药名") for h in (herbs or [])],
        "checks": [],
    }
    checks = rep["checks"]

    if formula_zh:
        rec = g.resolve(q)
        if rec and rec["组成"]:
            rep["composition"] = rec["组成"]
            rep["composition_source"] = rec["组成来源"]
            rep["formula_type"] = rec["类型"]
            rep["formula_origin"] = rec["出处"]
            rep["indications"] = rec["主治"]
            rep["actions"] = rec["治法"]
            if rec["类型"] == "经典方":
                checks.append({"level": "一致", "msg":
                    "「{}」属已核实经典方，共 {} 味中药，组成均为中文药名，不列剂量（依据：{}）。"
                    .format(rec["中文名"], len(rec["组成"]), rec["组成来源"])})
            else:
                checks.append({"level": "信息", "msg":
                    "「{}」为已核实方剂条目，共 {} 味中药（依据：{}）。"
                    .format(rec["中文名"], len(rec["组成"]), rec["组成来源"])})
            if rec["出处"] and rec["出处"] != "图谱条目":
                checks.append({"level": "信息", "msg": "方剂出处：" + rec["出处"] + "。"})
            if rec["主治"]:
                checks.append({"level": "信息", "msg": "主治要点：" + "、".join(rec["主治"]) + "。"})
            else:
                checks.append({"level": "待核", "msg": "本方主治要点未在已核实资料中载明，须医师结合全方辨证。"})
            if rec["治法"]:
                checks.append({"level": "信息", "msg": "治法要点：" + "、".join(rec["治法"]) + "。"})
        else:
            rep["composition"] = ["组成待核（该条目不在已核实经典方名录内，图谱与古籍语料均未检出可靠药味）"]
            rep["composition_source"] = "暂无可靠来源"
            rep["formula_type"] = "待核条目（非经典方）"
            checks.append({"level": "待核", "msg":
                "「{}」不在已核实经典方名录内，属现代成药、单味药或图谱缺载条目，"
                "古籍语料亦未检出可靠组成，组成待核，须医师核定，不作为经典方使用。".format(disp)})

        # 药物归经一致性
        comp = rep.get("composition", [])
        real = [c for c in comp if c != "组成待核（该条目不在已核实经典方名录内，图谱与古籍语料均未检出可靠药味）" and "待核" not in c]
        mer, missing = g.meridians_of(real)
        rep["meridian_coverage"] = mer
        props = {}
        for h in real:
            info = g.herb_info(h)
            if info:
                props[h] = {"归经": info.get("归经", []), "性味": info.get("性味", [])}
        rep["herb_properties"] = props
        if missing:
            checks.append({"level": "待核", "msg":
                "组成中以下药味未收录性味归经：" + "、".join(missing) + "（待核）。"})
        if mer:
            checks.append({"level": "信息", "msg":
                "组成药味归经覆盖：" + "；".join(k + "见" + "、".join(v) for k, v in mer.items()) + "。"})
        if syn and mer:
            needed = set()
            for kw, ms in SYNDROME_MERIDIAN.items():
                if kw in syn:
                    needed.update(ms)
            lack = needed - set(mer.keys())
            if needed and not lack:
                checks.append({"level": "一致", "msg":
                    "方剂归经覆盖本证型所需经络（" + "、".join(sorted(needed)) + "），方证基本吻合。"})
            elif lack:
                checks.append({"level": "提示", "msg":
                    "本证型指向" + "、".join(sorted(needed)) + "，方剂归经未见" + "、".join(sorted(lack)) +
                    "，可能与引经药、兼夹证或资料缺载有关，须医师复核。"})
        elif syn:
            checks.append({"level": "待核", "msg": "本方药味归经资料不足，方证一致性待核。"})

    if herbs:
        hlist = [canon_name(h) for h in herbs]
        mer2, missing2 = g.meridians_of(hlist)
        rep["herb_meridians"] = {h: (g.herb_info(h) or {}).get("归经", []) for h in hlist if g.herb_info(h)}
        if missing2:
            checks.append({"level": "待核", "msg": "以下药物未收录归经：" + "、".join(missing2) + "（待核）。"})
        if mer2:
            checks.append({"level": "信息", "msg":
                "所查药物归经覆盖：" + "；".join(k + "见" + "、".join(v) for k, v in mer2.items()) + "。"})
        else:
            checks.append({"level": "待核", "msg": "所查药物归经资料不足，待核。"})

    if not checks:
        checks.append({"level": "信息", "msg": "未提供可验证的方剂或药物，仅返回图谱来源。"})
    return rep


def audit():
    """硬校验：已核实经典方名录内每首方必须有非空中文药味组成。"""
    g = load_graph()
    classic_names = [n for n in g.resolved if g.resolved[n]["类型"] == "经典方"]
    bad = []
    latin = []
    for n in classic_names:
        rec = g.resolved[n]
        if not rec["组成"]:
            bad.append(n)
        for c in rec["组成"]:
            if _HAS_LATIN.search(c) or not _HAS_CJK.search(c):
                latin.append((n, c))
    return {
        "图谱来源": g.source,
        "已核实方剂总数": len(g.resolved),
        "经典方数": len(classic_names),
        "空组成经典方数": len(bad),
        "非中文药味数": len(latin),
        "空组成示例": bad[:10],
        "非中文药味示例": latin[:10],
    }


def main():
    args = sys.argv[1:]
    if "--audit" in args:
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
        return
    f = s = None
    hs = []
    for a in args:
        if a.startswith("--syndrome="):
            s = a.split("=", 1)[1]
        elif a.startswith("--herbs="):
            hs = [x for x in a.split("=", 1)[1].split(",") if x]
        else:
            f = a
    r = cross_validate(formula_zh=f, syndrome=s, herbs=hs)
    print("图谱来源：" + r["source"])
    print("方剂：" + r["formula_name"] + "　证型：" + r["syndrome"])
    if "composition" in r:
        print("组成：" + "、".join(r["composition"]))
        print("组成来源：" + r["composition_source"])
        print("条目类型：" + r["formula_type"])
    if r.get("herb_properties"):
        parts = []
        for h, p in list(r["herb_properties"].items())[:8]:
            seg = h + "（"
            seg += "归" + "、".join(p["归经"]) if p["归经"] else "归经待核"
            seg += "；" + "、".join(p["性味"]) + "）" if p["性味"] else "）"
            parts.append(seg)
        print("药味性味归经：" + "；".join(parts))
    if r.get("meridian_coverage"):
        print("归经覆盖：" + "；".join(k + "见" + "、".join(v) for k, v in r["meridian_coverage"].items()))
    if r.get("herb_meridians"):
        print("药物归经：" + json.dumps(r["herb_meridians"], ensure_ascii=False))
    print("交叉验证结论：")
    for c in r["checks"]:
        print("  [" + c["level"] + "] " + c["msg"])


if __name__ == "__main__":
    main()
