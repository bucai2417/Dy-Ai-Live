"""Live2D 模型导入与管理。

现实中下载的 Live2D 模型经常「看起来正常但加载就报错」，原因通常是：
    - model3.json 里引用了不存在的文件（作者删了动作/表情但没改配置）
    - 引用了 Sound 音频、DisplayInfo 等运行时不需要的字段
    - 文件名大小写与实际不符
所以导入时不能只解压就完事，必须**校验 + 自动修复**，否则用户会以为是你程序坏了。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from pathlib import Path

from .config import WEB_DIR

log = logging.getLogger(__name__)

MODELS_DIR = WEB_DIR / "models"

# 程序化形象（无 .moc3）：由 web/assets/whale/rig.js 渲染，
# 在后台以 "rig:<id>" 形式出现，和真实 Live2D 模型并列可选。
# 拿到 moc3 后删掉这一条即可完全回到标准 Live2D 流程。
RIG_MODELS = [
    {
        "id": "rig:whale-art",
        "name": "深蓝 Deepy（AI 立绘 · 好看优先）",
        "url": "/whale-lab?art=1",
        "kind": "rig",
        "preview": "/whale-lab?art=1",
        "desc": "AI 生成立绘抠绿幕，画面精致；30 参数 / 9 动作 / 8 表情驱动整体变换（眨眼口型为近似）",
    },
    {
        "id": "rig:whale-deepy",
        "name": "深蓝 Deepy（程序化 · 局部骨骼完整）",
        "url": "/whale-lab",
        "kind": "rig",
        "preview": "/whale-lab",
        "desc": "手绘 SVG 分层，可独立眨眼/对口型/转头；30 参数 / 9 动作 / 8 表情，但画面较简陋",
    },
]

# 运行时(Cubism 3/4/5 = moc3)不支持 Cubism 2 的 .moc
SUPPORTED_EXT = {".moc3"}

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slug(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return (s or "model")[:40]


def safe_extract(zip_path: Path, dest: Path) -> int:
    """解压 zip 并防目录穿越（zip slip）。返回解压出的文件数。"""
    dest = dest.resolve()
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # 关键：拒绝 ../ 和绝对路径，否则恶意 zip 能写到系统目录
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest)):
                raise ValueError(f"压缩包内含非法路径：{info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def find_model_json(root: Path) -> list[Path]:
    """递归找出所有 model3.json（Cubism 3/4/5）。"""
    out = [p for p in root.rglob("*.model3.json")]
    # 顺便找出 Cubism 2 的 model.json，用来给出明确的不支持提示
    for p in root.rglob("*.model.json"):
        if not p.name.endswith(".model3.json"):
            out.append(p)
    return sorted(out)


def inspect_model(json_path: Path) -> dict:
    """检查模型文件引用是否完整，返回缺失清单。"""
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"ok": False, "error": f"JSON 解析失败：{exc}", "missing": [], "version": ""}

    base = json_path.parent
    version = int(raw.get("Version") or 3)
    sc = raw.get("FileReferences") or {}
    missing: list[str] = []

    def check(rel: str | None) -> None:
        if not rel or rel.startswith("http"):
            return
        # 模型内部路径是相对 model3.json 的，且可能带子目录
        p = (base / rel.replace("\\", "/")).resolve()
        if not p.exists():
            # 大小写不一致也算缺失（Windows 上能读到，Linux/打包后读不到）
            alt = base / rel.replace("\\", "/")
            if not alt.exists():
                missing.append(rel)

    if version >= 3:
        check(sc.get("Moc"))
        for t in sc.get("Textures") or []:
            check(t)
        check(sc.get("Physics"))
        check(sc.get("Pose"))
        check(sc.get("DisplayInfo"))
        for group in (sc.get("Motions") or {}).values():
            for m in group:
                check(m.get("File"))
                for s in m.get("Sound") or []:
                    check(s)
        for e in sc.get("Expressions") or []:
            check(e.get("File"))
    else:
        check(sc.get("moc"))
        for t in sc.get("textures") or []:
            check(t)
        check(sc.get("physics"))
        check(sc.get("pose"))

    moc = sc.get("Moc") or sc.get("moc") or ""
    return {
        "ok": True,
        "version": version,
        "moc": moc,
        "missing": missing,
        "textures": len(sc.get("Textures") or sc.get("textures") or []),
        "motions": sum(len(g) for g in (sc.get("Motions") or {}).values()),
        "expressions": len(sc.get("Expressions") or []),
    }


def repair_model(json_path: Path, info: dict, prune_sound: bool = True) -> list[str]:
    """把缺失的引用去掉，让模型至少能加载出来。

    返回修掉的条目，方便告诉用户「我帮你修了什么」。
    只删引用、不删文件，所以不会破坏原始资源。
    """
    fixes: list[str] = []
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fixes

    base = json_path.parent
    sc = raw.get("FileReferences") or {}

    def exists(rel: str | None) -> bool:
        if not rel or rel.startswith("http"):
            return True
        return (base / rel.replace("\\", "/")).exists()

    # 1) 动作组：整组里坏的那条删掉，组空了就整组删
    motions = sc.get("Motions")
    if isinstance(motions, dict):
        for group in list(motions.keys()):
            kept = []
            for m in motions[group]:
                bad = not exists(m.get("File"))
                if prune_sound and any(not exists(s) for s in (m.get("Sound") or [])):
                    # 音频缺失不影响画面，去掉 Sound 字段即可
                    m.pop("Sound", None)
                    fixes.append(f"去掉了 {group} 里缺失的音频引用")
                if bad:
                    fixes.append(f"移除了缺失的动作文件 {m.get('File')}")
                    continue
                kept.append(m)
            if kept:
                motions[group] = kept
            else:
                motions.pop(group)
                fixes.append(f"移除了空动作组 {group}")
        if not motions:
            sc.pop("Motions", None)

    # 2) 表情
    exps = sc.get("Expressions")
    if isinstance(exps, list):
        kept = []
        for e in exps:
            if exists(e.get("File")):
                kept.append(e)
            else:
                fixes.append(f"移除了缺失的表情 {e.get('File')}")
        if kept:
            sc["Expressions"] = kept
        else:
            sc.pop("Expressions", None)

    # 3) 物理 / 姿势 / DisplayInfo：缺了直接去掉，运行时能接受
    for key in ("Physics", "Pose", "DisplayInfo"):
        if key in sc and not exists(sc.get(key)):
            sc.pop(key)
            fixes.append(f"移除了缺失的 {key}")

    raw["FileReferences"] = sc
    json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return fixes


def list_models() -> list[dict]:
    """列出 web/models 下所有可用模型，外加程序化形象。"""
    out: list[dict] = []
    if not MODELS_DIR.exists():
        return list(RIG_MODELS)
    for d in sorted(MODELS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        jsons = find_model_json(d)
        if not jsons:
            continue
        # 优先挑 moc3 的
        jsons.sort(key=lambda p: (not p.name.endswith(".model3.json"), len(p.parts)))
        j = jsons[0]
        info = inspect_model(j)
        rel = j.relative_to(MODELS_DIR).as_posix()
        out.append({
            "id": rel,
            "name": d.name,
            "url": f"/static/models/{rel}",
            "kind": "live2d",
            "version": info.get("version", 0),
            "supported": bool(info.get("version", 0) >= 3),
            "missing": len(info.get("missing") or []),
            "motions": info.get("motions", 0),
            "expressions": info.get("expressions", 0),
            "size_kb": round(sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1024),
        })
    # 程序化形象排在最前，方便新用户第一眼选到
    return list(RIG_MODELS) + out


def _flatten_single_root(target: Path) -> Path:
    """很多人压缩时会把整个文件夹压进去，解压后变成 myfish/myfish/...

    如果解压结果只有一层目录且里面没有 model3.json，就把内容提上来，
    避免模型路径出现「名字套名字」。
    """
    for _ in range(2):  # 最多提两层，别过度
        entries = [p for p in target.iterdir() if not p.name.startswith(".")]
        dirs = [p for p in entries if p.is_dir()]
        files = [p for p in entries if p.is_file()]
        if len(dirs) == 1 and not files:
            inner = dirs[0]
            # 把内层内容搬到 target，再删掉空壳
            for child in list(inner.iterdir()):
                child.rename(target / child.name)
            inner.rmdir()
            continue
        break
    return target


def import_zip(zip_path: Path, display_name: str = "") -> dict:
    """导入一个模型 zip，返回结果详情。"""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    name = (display_name or zip_path.stem).strip() or "model"
    slug = _slug(name)

    # 同名就加后缀，别覆盖用户已有模型
    target = MODELS_DIR / slug
    n = 2
    while target.exists():
        target = MODELS_DIR / f"{slug}-{n}"
        n += 1
    target.mkdir(parents=True, exist_ok=True)

    try:
        count = safe_extract(zip_path, target)
    except Exception as exc:
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": False, "error": f"解压失败：{exc}"}

    if count == 0:
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": False, "error": "压缩包里没有文件"}

    # 处理「压缩了整个文件夹」导致的嵌套
    _flatten_single_root(target)

    jsons = find_model_json(target)
    if not jsons:
        shutil.rmtree(target, ignore_errors=True)
        return {
            "ok": False,
            "error": "压缩包里没找到 *.model3.json。请确认是把「模型文件夹」整体压缩，"
                     "且包含 .moc3 文件（Cubism 3/4/5 格式）。",
        }

    jsons.sort(key=lambda p: (not p.name.endswith(".model3.json"), len(p.parts)))
    json_path = jsons[0]
    info = inspect_model(json_path)

    if not info.get("ok"):
        return {"ok": False, "error": info.get("error", "模型配置损坏")}

    if info.get("version", 0) < 3:
        shutil.rmtree(target, ignore_errors=True)
        return {
            "ok": False,
            "error": "这是 Cubism 2 模型（.moc），当前运行时只支持 Cubism 3/4/5（.moc3）。"
                     "请换用 moc3 版本，或在 Cubism Editor 里升级后重新导出。",
        }

    fixes = repair_model(json_path, info) if info.get("missing") else []
    after = inspect_model(json_path)

    rel = json_path.relative_to(MODELS_DIR).as_posix()
    return {
        "ok": True,
        "id": rel,
        "name": target.name,
        "url": f"/static/models/{rel}",
        "files": count,
        "fixed": fixes,
        "still_missing": after.get("missing") or [],
        "motions": after.get("motions", 0),
        "expressions": after.get("expressions", 0),
    }


def delete_model(model_id: str) -> dict:
    """删除模型。只允许删 MODELS_DIR 下的目录，防止越权删文件。"""
    rel = (model_id or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        return {"ok": False, "error": "非法路径"}

    p = (MODELS_DIR / rel).resolve()
    if not str(p).startswith(str(MODELS_DIR.resolve())):
        return {"ok": False, "error": "非法路径"}

    # 找到这个模型所在的顶层目录（models/<name>/...）
    try:
        top = MODELS_DIR / Path(rel).parts[0]
    except Exception:
        return {"ok": False, "error": "非法路径"}
    if not top.is_dir():
        return {"ok": False, "error": "模型不存在"}

    shutil.rmtree(top, ignore_errors=True)
    return {"ok": True, "deleted": top.name}
