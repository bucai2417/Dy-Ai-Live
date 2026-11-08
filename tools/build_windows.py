from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def application_version():
    namespace = {}
    exec((ROOT / "app/__init__.py").read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


def build_name(version, revision, tag=""):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("版本号必须为 major.minor.patch")
    if tag and tag != "v" + version:
        raise ValueError("版本标签与 app.__version__ 不一致")
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise ValueError("需要有效的 Git 提交号")
    label = version if tag else version + "+" + revision[:7]
    return f"Dy-Ai-Live-{label}-windows-x64"


def copy_notices(destination):
    license_dir = destination / "THIRD_PARTY_LICENSES"
    shutil.copytree(ROOT / "packaging/licenses", license_dir, dirs_exist_ok=True)
    dependencies = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        dependencies.append({"name": name, "version": distribution.version,
                             "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License", ""),
                             "project_urls": distribution.metadata.get_all("Project-URL") or []})
        for filename in distribution.files or []:
            if not re.match(r"^(license|copying|notice)([._-]|$)", Path(filename).name, re.IGNORECASE):
                continue
            source = Path(distribution.locate_file(filename))
            if source.is_file():
                target = license_dir / name / str(filename).replace("../", "").replace("..\\", "")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, license_dir / "Python-LICENSE.txt")
    (license_dir / "build-environment.json").write_text(json.dumps(dependencies, ensure_ascii=False, indent=2), encoding="utf-8")
    return dependencies


def verify_package(executable, report):
    with tempfile.TemporaryDirectory(prefix="Deepy-中文自检-") as temporary:
        environment = dict(os.environ, DEEPY_HOME=temporary, QT_QPA_PLATFORM="offscreen",
                           PYTHONUTF8="1", PYTHONIOENCODING="utf-8", INGEST="mock", TTS_ENGINE="none", LLM_API_KEY="")
        result = subprocess.run([str(executable), "--self-check", str(report)], cwd=temporary,
                                env=environment, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not report.is_file():
            details = report.read_text(encoding="utf-8") if report.exists() else "未生成检查报告"
            raise RuntimeError("EXE 检查失败：" + details)
        check = json.loads(report.read_text(encoding="utf-8"))
        if not check.get("ok") or Path(check["runtime_root"]).resolve() != Path(temporary).resolve():
            raise RuntimeError("EXE 运行目录或画面检查失败")


def main():
    parser = argparse.ArgumentParser(description="构建并检查 Windows x64 免安装包")
    parser.add_argument("--tag", default="")
    arguments = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        parser.error("请在 Windows x64 上构建")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    version = application_version()
    name = build_name(version, revision, arguments.tag)
    output = ROOT / "dist/windows"
    output.mkdir(parents=True, exist_ok=True)
    package = output / (name + ".zip")
    if package.exists():
        raise FileExistsError("构建产物已存在，不覆盖：" + str(package))
    build_dir = ROOT / "build/windows" / name
    if build_dir.exists():
        raise FileExistsError("构建目录已存在，请保留或移走后重试：" + str(build_dir))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(build_dir / "dist"), "--workpath", str(build_dir / "work"),
                    str(ROOT / "packaging/windows.spec")], cwd=ROOT, check=True)
    application = build_dir / "dist/Dy-Ai-Live"
    for filename in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", ".env.example"):
        shutil.copyfile(ROOT / filename, application / filename)
    shutil.copyfile(ROOT / "docs/用户使用教程.md", application / "使用说明.md")
    shutil.copyfile(ROOT / "docs/Windows自动构建.md", application / "Windows自动构建.md")
    for filename in ("README.md", "package.json", "sign_server.mjs"):
        target = application / "tools/douyin_sig" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "tools/douyin_sig" / filename, target)
    dependencies = copy_notices(application)
    build_info = {"version": version, "commit": revision, "tag": arguments.tag,
                  "python": platform.python_version(), "architecture": "windows-x64",
                  "qt": importlib.metadata.version("PySide6"), "external_ffmpeg": True,
                  "dependency_count": len(dependencies)}
    (application / "build-info.json").write_text(json.dumps(build_info, ensure_ascii=False, indent=2), encoding="utf-8")
    report = ROOT / "build/windows-check" / (name + ".json")
    report.parent.mkdir(parents=True, exist_ok=True)
    verify_package(application / "Dy-Ai-Live.exe", report)
    with zipfile.ZipFile(package, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted(application.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(application)
            if relative.parts[0] in ("data", "logs", "tests", "archive") or source.name in (".env", "webmssdk.js"):
                raise ValueError("运行包包含不应发布的文件：" + str(relative))
            if source.suffix.lower() == ".exe" and source.name.lower().startswith("ffmpeg"):
                raise ValueError("FFmpeg 应由使用者独立配置，不随本包分发")
            archive.write(source, Path("Dy-Ai-Live") / relative)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (output / (name + ".sha256")).write_text(digest + "  " + package.name + "\n", encoding="utf-8")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as stream:
            stream.write("artifact_name=" + name + "\n")
    print(json.dumps({"package": str(package), "sha256": digest, "check": str(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
