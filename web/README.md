# 网页兼容入口与共享素材

默认直播画面已使用 app/native 的 PySide6 实现。本目录仍被后台挂载，不能整体删除。

| 路径 | 当前用途 |
| --- | --- |
| stage.html、console.html、settings.html、whale-lab.html | 保留的网页入口 |
| assets/stage.js | 网页舞台逻辑 |
| assets/whale/rig.js、art.js、whale.css | 网页 SVG / 立绘实现 |
| assets/whale/art/deepy.png、deepy.patches.json、deepy.patch-*.png | 原生与网页共用的运行素材 |
| models/haru/、vendor/ | 可选的网页 Live2D 模型与库，公开源码不附带，需另行合法配置 |

原生人物不会根据网页导入的 .moc3 自动切换。后续分层骨架路线见 ../docs/模型骨架与换装实施方案.md。

旧 .bak 文件留在本机 archive/2026-11-07-cleanup/web-backups。2026-11-08 将旧设计源图和未引用的 pixi-live2d-display.min.js 移入 archive/2026-11-08-open-source/。原开发目录中的外部文件保留在本机；v1.0.0 公开源码跳过 vendor 中的 JS 和 haru 模型。原生使用的现有立绘和贴片仍随包提供。生成文件和私人上传模型不应混入运行源代码。
