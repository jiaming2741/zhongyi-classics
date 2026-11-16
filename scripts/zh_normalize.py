#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中文归一模块：把 DragonTCM 英文/拼音图谱的全部字段转中文。

依赖静态字典 zh_dict.json（由 build_zh_dict.py 从数据集官方 word_maps 生成），
运行时不读 parquet、不读 connector，也不联网。

硬保证：任何输出到用户的内容都不得含拉丁字母。归一失败的条目统一标为
「名称待核」并计入待核清单，绝不把英文原名或拼音原名透出。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
_DICT_PATH = os.path.join(HERE, "zh_dict.json")

with open(_DICT_PATH, encoding="utf-8") as _f:
    D = json.load(_f)

HERB = D["herb"]
FORMULA = D["formula"]
PREFIX1 = D["herb_prefix"]
PREFIX2 = D["herb_prefix2"]
SUFFIX = D["herb_suffix"]
TASTE = D["taste"]
TEMP = D["temperature"]
MERIDIAN = D["meridian"]
ACTION = D["action"]

# 无效条目标记：这些不进入组成，不计为药味
_INVALID = {"", "?", "NA", "NOINFORMATION", "NONE", "NULL", "UNKNOWN", "缺载"}

_HAS_LATIN = re.compile(r"[A-Za-z]")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


def key(s):
    """规范键：大写、去空格括号标点、剥离消歧后缀。"""
    s = re.sub(r"~\d+", "", str(s or "").upper())
    return re.sub(r"[^A-Z0-9]", "", s.replace("（", "").replace("）", ""))


def herb_zh(name):
    """药名拼音/英文 -> 中文。失败返回 None。"""
    k = key(name)
    if not k or k in _INVALID:
        return None
    if k in HERB:
        v = HERB[k]
        return None if v == "缺载" else v
    # 复合炮制前缀（两级优先）
    for p, z in PREFIX2.items():
        if k.startswith(p):
            rest = k[len(p):]
            if rest in HERB:
                return z + HERB[rest]
            for s, zs in SUFFIX.items():
                if rest.endswith(s) and rest[:-len(s)] in HERB:
                    return z + HERB[rest[:-len(s)]] + zs
    for p, z in PREFIX1.items():
        if k.startswith(p):
            rest = k[len(p):]
            if rest in HERB:
                return z + HERB[rest]
            for s, zs in SUFFIX.items():
                if rest.endswith(s) and rest[:-len(s)] in HERB:
                    return z + HERB[rest[:-len(s)]] + zs
    for s, zs in SUFFIX.items():
        if k.endswith(s) and k[:-len(s)] in HERB:
            return HERB[k[:-len(s)]] + zs
    # 方剂名误入药名栏
    if k in FORMULA:
        return FORMULA[k]
    return None


def formula_zh(name):
    """方名拼音/英文 -> 中文。失败返回 None。"""
    k = key(name)
    if not k or k in _INVALID:
        return None
    return FORMULA.get(k) or HERB.get(k)


def meridian_zh(name):
    """归经英文 -> 中文；无法归一返回 None（不输出）。"""
    v = MERIDIAN.get(str(name or "").strip().lower())
    return v or None


def taste_zh(name):
    return TASTE.get(str(name or "").strip().lower())


def temp_zh(name):
    return TEMP.get(str(name or "").strip().lower())


def action_zh(text):
    """英文功效/主治 -> 中文要点（只取已登录术语，未登录词不透出）。"""
    if not text or not _HAS_LATIN.search(str(text)):
        return [str(text)] if text and _HAS_CJK.search(str(text)) else []
    low = str(text).lower()
    out = []
    for en, zh in ACTION:
        if en in low and zh not in out:
            out.append(zh)
    return out[:6]


def safe_zh(name, kind="药名"):
    """输出前最后一道闸：返回纯中文字符串。归一失败给中文标注，绝不返回拉丁字母。"""
    if name is None:
        return kind + "待核"
    s = str(name).strip()
    if not s:
        return kind + "待核"
    # 纯中文名先放行：key() 会滤掉汉字得到空串，不能据此判为无效
    if not _HAS_LATIN.search(s) and _HAS_CJK.search(s):
        return s
    if key(s) in _INVALID:
        return kind + "待核"
    z = herb_zh(s) if kind == "药名" else formula_zh(s)
    if z and not _HAS_LATIN.search(z):
        return z
    return kind + "待核"


def to_zh_list(names, kind="药名"):
    """批量归一并去重，保持顺序；无法归一者以中文标注占位，不丢条目。"""
    out, seen = [], set()
    for n in names:
        z = safe_zh(n, kind)
        if z not in seen:
            seen.add(z)
            out.append(z)
    return out
