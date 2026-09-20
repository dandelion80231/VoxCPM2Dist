"""
voxcpm_api.py — VoxCPM2 公共后端函数层（REST 标准化 / 模型目录 / 语料与 profile 导入导出）

Web UI（vox_web_ui.py）与 CLI（voxcpm_tts_v5_longtext.py）共用的后端业务函数，
避免同一逻辑在两处重复实现：
  - 多音字语料：user_corpus_path / read_corpus / write_corpus / validate_corpus_text
                / import_corpus_text / export_corpus_text
  - 音色档案（profile）：list_profiles / save_profile / delete_profile
                / export_profiles_to_file / import_profiles_text
  - 模型目录解析：resolve_model_dir / model_dir_info（VOXCPM_MODELS_DIR 兜底，见 commit2）

REST 映射（由 vox_web_ui.py 路由调用）：
  GET  /api/corpus            -> read_corpus()
  POST /api/corpus            -> write_corpus(content)
  POST /api/corpus/import     -> import_corpus_text(content)
  POST /api/corpus/export     -> export_corpus_to_file()
  GET  /api/norm-rules        -> read_norm_rules()
  POST /api/norm-rules        -> write_norm_rules(content)
  GET  /api/profiles          -> list_profiles()
  POST /api/profiles          -> save_profile(name, data)
  POST /api/profiles/delete   -> delete_profile(name)
  POST /api/profiles/import   -> import_profiles_text(content)
  POST /api/profiles/export   -> export_profiles_to_file()
"""

import json
import os
import re
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_ID = "openbmb/VoxCPM2"

# ── 多音字语料（overlay_user_override.txt）────────────────
# 行格式与 g2p_phoneme._OVERLAY_RE 保持一致：
#   上下文词 · 目标字: pypinyin默认=X → 强制=Y    （# 开头为注释行）
_CORPUS_RE = re.compile(
    r"^(?P<word>.+?)\s*·\s*(?P<char>.)\s*:\s*pypinyin默认=(?P<default>[a-züv]+[1-5])\s*→\s*强制=(?P<forced>[a-züv]+[1-5])"
)


def user_corpus_path() -> Path:
    """用户多音字语料文件（与 g2p_phoneme 热加载路径一致，支持 VOXCPM_OVERLAY_USER_PATH 覆盖）。"""
    env = os.environ.get("VOXCPM_OVERLAY_USER_PATH", "").strip()
    if env:
        return Path(env)
    return (
        SCRIPT_DIR
        / "training"
        / "polyphone_corpus"
        / "data"
        / "overlay_user_override.txt"
    )


def read_corpus() -> dict:
    """读取用户语料（只读，不修改）。返回 path/exists/content/mtime。"""
    p = user_corpus_path()
    exists = p.exists()
    return {
        "path": str(p),
        "exists": exists,
        "content": p.read_text(encoding="utf-8") if exists else "",
        "mtime": p.stat().st_mtime if exists else None,
    }


def write_corpus(content: str) -> dict:
    """写回用户语料（UTF-8）。保存即生效：g2p_phoneme 检测到 mtime 变化自动热加载。"""
    p = user_corpus_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "", encoding="utf-8")
    return {
        "ok": True,
        "path": str(p),
        "mtime": p.stat().st_mtime,
        "message": "已保存（保存即生效：g2p 检测到文件变化自动热加载，无需重启）",
    }


# ── 归一化规则（num_norm_extra.txt，与 text_norm_cn.py 同目录）────────────────
# 行格式与 text_norm_cn._load_user_rules 一致：
#   字面  原文 => 读法      （如 3.14 => 三点一四）
#   正则  ?pattern => repl  （如 ?0+(\d) => \1，支持反向引用；# 开头为注释）
# 按 mtime 热重载：保存后下一次合成自动生效，无需重启。


def norm_rules_path() -> Path:
    """用户归一化规则文件（与 text_norm_cn._USER_RULES_FILE 同一路径口径）。"""
    try:
        from text_norm_cn import _USER_RULES_FILE

        return Path(_USER_RULES_FILE)
    except Exception:
        return SCRIPT_DIR / "num_norm_extra.txt"


def read_norm_rules() -> dict:
    """读取用户归一化规则（只读，不修改）。返回 path/exists/content/rule_count/builtin。

    builtin：内置归一化规则只读速览（多行文本），供 Web UI 展示“已有哪些规则”，
    便于用户判断在 num_norm_extra.txt 里要补什么。取不到时返回空字符串（不报错）。"""
    p = norm_rules_path()
    exists = p.exists()
    rule_count = 0
    builtin = ""
    try:
        from text_norm_cn import builtin_rule_summary, user_rule_count

        rule_count = user_rule_count()
        builtin = builtin_rule_summary()
    except Exception:
        # text_norm_cn 不可用（极端缺路径场景）：规则条数记 0、内置速览置空，不影响规则文件的读写本身
        rule_count = 0
        builtin = ""
    return {
        "path": str(p),
        "exists": exists,
        "content": p.read_text(encoding="utf-8") if exists else "",
        "rule_count": rule_count,
        "builtin": builtin,
    }


def write_norm_rules(content: str) -> dict:
    """写回用户归一化规则（UTF-8）。保存即生效：text_norm_cn 按 mtime 自动热加载，无需重启。
    坏行由 text_norm_cn 解析时单条跳过 + 警告，不会崩合成任务。"""
    p = norm_rules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content or "", encoding="utf-8")
    return {
        "ok": True,
        "path": str(p),
        "mtime": p.stat().st_mtime,
        "message": "已保存（保存即生效：归一化规则自动热加载，无需重启）",
    }


def validate_corpus_text(text: str) -> dict:
    """逐行校验语料文本；不兼容行记录行号与内容（与 g2p_phoneme 静默跳过口径一致）。"""
    lines = (text or "").splitlines()
    valid, comments, bad = 0, 0, []
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            comments += 1
            continue
        m = _CORPUS_RE.match(line)
        if m and m.group("char") in m.group("word"):
            valid += 1
        else:
            bad.append({"line": i, "text": raw[:200]})
    return {
        "total": len(lines),
        "valid": valid,
        "comments": comments,
        "bad_count": len(bad),
        "bad_lines": bad,
    }


# ── 音色档案（profiles.json）──────────────────────────
PROFILE_FILE = SCRIPT_DIR / "voxcpm_profiles.json"
_profile_lock = threading.Lock()
# 允许持久化的 profile 字段（导入时清洗，避免夹带无关字段）
_PROFILE_FIELDS = (
    "voice",
    "control_text",
    "mode",
    "reference_wav_path",
    "prompt_text",
    "created_at",
)


def _load_profiles() -> list:
    with _profile_lock:
        if not PROFILE_FILE.exists():
            return []
        try:
            data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []


def _save_profiles(profiles: list) -> None:
    with _profile_lock:
        PROFILE_FILE.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def list_profiles() -> list:
    """返回全部音色档案。"""
    return _load_profiles()


def save_profile(name: str, data: dict) -> dict:
    """新增/覆盖音色档案。name 必填且唯一；重名时覆盖并提示。"""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "档案名称不能为空"}
    profiles = _load_profiles()
    existed = any(p.get("name") == name for p in profiles)
    record = {"name": name}
    for f in _PROFILE_FIELDS:
        if f in data and data[f] is not None and str(data[f]) != "":
            record[f] = data[f]
    record.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    profiles = [p for p in profiles if p.get("name") != name] + [record]
    _save_profiles(profiles)
    return {
        "ok": True,
        "name": name,
        "overwritten": existed,
        "count": len(profiles),
        "message": f"音色档案「{name}」已{'覆盖' if existed else '保存'}",
    }


def delete_profile(name: str) -> dict:
    """删除指定音色档案。"""
    profiles = _load_profiles()
    new = [p for p in profiles if p.get("name") != name]
    if len(new) == len(profiles):
        return {"ok": False, "error": f"音色档案「{name}」不存在"}
    _save_profiles(new)
    return {
        "ok": True,
        "name": name,
        "count": len(new),
        "message": f"已删除音色档案「{name}」",
    }


def validate_profiles_text(text: str) -> dict:
    """校验 profile 导入文本：必须是 JSON 数组；逐项检查 name 与字段合法性。"""
    try:
        data = json.loads(text or "")
    except Exception as e:
        return {"ok": False, "error": f"JSON 解析失败: {e}"}
    if not isinstance(data, list):
        return {"ok": False, "error": "导入内容必须是 JSON 数组（音色档案列表）"}
    ok_items, bad_items = [], []
    for idx, item in enumerate(data, 1):
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            ok_items.append(item)
        else:
            bad_items.append({"index": idx, "reason": "缺少 name 或格式错误"})
    return {
        "ok": True,
        "total": len(data),
        "valid": len(ok_items),
        "bad": bad_items,
        "items": ok_items,
    }


def import_profiles_text(text: str) -> dict:
    """导入音色档案：坏项跳过，合法项清洗后合并写入（同名覆盖，保留字段白名单）。"""
    v = validate_profiles_text(text)
    if not v.get("ok"):
        return {"ok": False, "error": v.get("error")}
    items = v["items"]
    profiles = _load_profiles()
    existing_names = {p.get("name") for p in profiles}
    imported = 0
    for item in items:
        name = str(item["name"]).strip()
        record = {"name": name}
        for f in _PROFILE_FIELDS:
            if f in item and item[f] is not None and str(item[f]) != "":
                record[f] = item[f]
        record.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        if name in existing_names:
            profiles = [p for p in profiles if p.get("name") != name]
            existing_names.discard(name)
        profiles.append(record)
        imported += 1
    _save_profiles(profiles)
    return {
        "ok": True,
        "imported": imported,
        "skipped": v["total"] - v["valid"],
        "skipped_detail": v["bad"],
        "count": len(profiles),
        "message": f"导入完成：成功 {imported} 条，跳过 {v['total'] - v['valid']} 条格式错误项",
    }


# ── 导出目录（运行时产物，不入库）────────────────────
def export_dir() -> Path:
    d = SCRIPT_DIR / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_corpus_to_file() -> dict:
    """导出当前语料为 UTF-8 文本文件（写入 exports/，返回文件路径）。"""
    data = read_corpus()
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = export_dir() / f"corpus_user_override_{ts}.txt"
    out.write_text(data["content"] or "", encoding="utf-8")
    return {
        "ok": True,
        "path": str(out),
        "exists": data["exists"],
        "bytes": out.stat().st_size,
        "message": f"语料已导出：{out.name}",
    }


def export_corpus_text() -> dict:
    """返回当前语料内容与建议文件名（供 Web UI 前端直接下载）。"""
    data = read_corpus()
    return {
        "ok": True,
        "content": data["content"] or "",
        "filename": "corpus_user_override.txt",
        "exists": data["exists"],
    }


def import_corpus_text(text: str, merge: bool = True) -> dict:
    """导入语料文本：逐行校验（与 g2p_phoneme 同口径），坏行跳过并统计；
    有效行合并写入现有语料（保留原内容，last-wins 语义一致）。"""
    v = validate_corpus_text(text)
    lines = (text or "").splitlines()
    kept = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            kept.append(line)
            continue
        m = _CORPUS_RE.match(line)
        if m and m.group("char") in m.group("word"):
            kept.append(line)
    if kept:
        existing = read_corpus()["content"].strip()
        merged = (existing + "\n\n" if existing else "") + "\n".join(kept) + "\n"
        p = user_corpus_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(merged, encoding="utf-8")
    return {
        "ok": True,
        "imported": len(kept),
        "skipped": v["bad_count"],
        "skipped_detail": v["bad_lines"],
        "message": f"语料导入完成：合并 {len(kept)} 条，跳过 {v['bad_count']} 条坏行（格式不符）",
    }


def export_profiles_text() -> dict:
    """返回全部音色档案 JSON 文本（供 Web UI 前端直接下载）。"""
    return {
        "ok": True,
        "content": json.dumps(_load_profiles(), ensure_ascii=False, indent=2),
        "filename": "voxcpm_profiles.json",
    }


def export_profiles_to_file() -> dict:
    """导出全部音色档案为 JSON 文件（写入 exports/，返回文件路径）。"""
    profiles = _load_profiles()
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = export_dir() / f"voxcpm_profiles_{ts}.json"
    out.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "path": str(out),
        "count": len(profiles),
        "bytes": out.stat().st_size,
        "message": f"已导出 {len(profiles)} 条音色档案：{out.name}",
    }
