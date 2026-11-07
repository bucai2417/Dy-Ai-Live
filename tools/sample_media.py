from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg


def create_sample_video(destination: Path) -> Path:
    width, height, count, fps = 96, 128, 55, 30
    frames = bytearray()
    for index in range(count):
        frame = bytearray(width * height * 4)
        left = 25 + index % 9
        for vertical in range(28, 100):
            start = (vertical * width + left) * 4
            frame[start:start + 36 * 4] = bytes((72, 190, 218, 255)) * 36
        frames.extend(frame)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
         "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000", "-frames:v", str(count), "-t", str(count / fps),
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-threads", "1",
         "-c:a", "libopus", "-b:a", "32k", "-af", "volume=0.03", "-shortest", "-n", str(destination)],
        input=bytes(frames), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("生成测试视频失败：" + result.stderr.decode("utf-8", errors="replace"))
    return destination
