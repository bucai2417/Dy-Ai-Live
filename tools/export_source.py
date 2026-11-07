from __future__ import annotations

import argparse
import io
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def export_source(revision="HEAD", output=None):
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
        cwd=ROOT, text=True, encoding="utf-8",
    ).strip()
    archive = subprocess.check_output(["git", "archive", "--format=zip", commit], cwd=ROOT)
    destination = Path(output) if output else ROOT / "dist" / ("Deepy-source-" + commit[:7] + ".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        for member in source.infolist():
            parts = Path(member.filename).parts
            optional = ("web/vendor/", "web/models/haru/", "tools/douyin_sig/webmssdk.js")
            if member.filename.startswith(optional):
                raise ValueError("提交的 export-ignore 未排除外部组件：" + member.filename)
            blocked = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache", ".idea", ".vscode"}
            environment = parts[-1].startswith(".env") and parts[-1] != ".env.example"
            if blocked.intersection(parts) or parts[0] in ("archive", "logs", ".tools", "dist", "build") or environment:
                raise ValueError("提交包含不应发布的文件：" + member.filename)
            if parts[0] in ("data", "music") and not member.is_dir() and member.filename not in ("data/README.md", "music/README.md"):
                raise ValueError("提交包含本机运行数据：" + member.filename)
        with destination.open("xb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as package:
            for member in source.infolist():
                content = source.read(member)
                if member.filename.lower().endswith(".bat"):
                    content = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n").encode("utf-8")
                package.writestr("Deepy/" + member.filename, content)
    return destination


def main():
    parser = argparse.ArgumentParser(description="仅从Git提交导出干净源码，保留中文BAT的Windows换行")
    parser.add_argument("--ref", default="HEAD", help="导出的提交，默认HEAD")
    parser.add_argument("--output", type=Path, help="输出ZIP路径；不覆盖已有文件")
    arguments = parser.parse_args()
    destination = export_source(arguments.ref, arguments.output)
    print("源码包：" + str(destination.resolve()))
    print("仅包含提交内容，不含Git历史或被忽略的本机数据。")


if __name__ == "__main__":
    main()
