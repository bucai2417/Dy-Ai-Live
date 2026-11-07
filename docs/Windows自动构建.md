# Windows 自动构建与便携版

## 使用构建好的程序

在 GitHub 的 Actions → Windows build → 成功的运行中，下载 Dy-Ai-Live-版本+提交号-windows-x64 构建产物，解压其中的 ZIP。正式版本标签构建成功后，也会将 ZIP 和 SHA256 文件上传到对应 Release。

完整解压到有写入权限的目录后，双击 Dy-Ai-Live.exe 即可打开原生工作台，无需另外安装 Python。请保留旁边的 _internal 文件夹，不能只复制一个 EXE。这是 Windows x64 免安装目录包，当前未做代码签名。

配置、统计和缓存保存在 EXE 旁的 data/，日志保存在 logs/，音乐可放在 music/。可复制 .env.example 为 .env，也可以直接在工作台设置中配置。升级前备份这些本机文件，不要用旧版 _internal 覆盖新版。需要单独存放用户数据时，可设置 DEEPY_HOME 环境变量为自选目录。

## 可选组件

- 原生人物素材、Qt 窗口和基础音频播放随包提供。
- 透明视频解码需要独立的 FFmpeg。本包不附带 imageio-ffmpeg 中的 GPL 静态可执行文件；请自行合法安装 FFmpeg 并加入 PATH，或设置 IMAGEIO_FFMPEG_EXE 为 ffmpeg.exe 的绝对路径。自行配置的视频和音乐不会打包上传。
- 抖音直连仍需要 Node.js 和合法取得的 webmssdk.js。默认可使用 bridge 或 mock，不会使用浏览器显示直播画面。缺少签名组件时 douyin_direct 会回退到浏览器采集弹幕。
- 旧网页 Live2D 库、模型、测试脚本和开发工具不进入 Windows 运行包。

## 工作流行为

| 触发方式 | 自动执行 |
| --- | --- |
| 向 main 推送提交 | 回归测试、构建 EXE、运行真实 EXE 检查、上传 ZIP 与校验文件 |
| 向 main 提交 PR | 回归测试和测试报告；不发布 Release |
| Actions 中手动 Run workflow | 测试并构建所选分支，上传 Actions 构建产物 |
| 推送 v* 标签 | 检查标签与 app.__version__ 相同，完成测试和构建后上传对应 Release |

构建产物保留 30 天，测试报告保留 14 天；Release 附件不受 Actions 产物保留时间限制。正常构建只有仓库读取权限，上传 Release 的独立任务才具有 contents:write 权限。所有 Actions 固定到已核实的提交号，不使用个人密钥。

构建会实际启动打包后的后台与原生工作台，验证资源加载、WebSocket 连接和渲染画面，并从不同工作目录和含中文的临时用户目录运行。失败时不发布 ZIP，检查报告上传供排查。无真实直播房间、在线 AI/TTS 或私人视频参与自动检查。

v1.0.0 是最初的源码发布，已经发布的标签保持不变。当前 main 的构建产物名称带提交号，不能把它当作旧标签的同一份二进制。发布下一版时，先修改 app/__init__.py 中的版本号、提交并推送，然后创建一致的版本标签，例如 v1.0.1。工作流不会移动旧标签，也不会覆盖已有同名 Release 附件。

## 本地构建

在 Windows x64、Python 3.10 环境中执行：

~~~powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
python -m pip install -r requirements-build.txt
python -m pytest tests -q
python tools/build_windows.py
~~~

建议在干净的虚拟环境内构建。输出位于 dist/windows/，构建中间目录位于 build/windows/。相同提交的已有构建目录和 ZIP 不会被覆盖，重新构建前请先移动旧产物。程序运行目录和 _internal 资源目录分离，打包不会读取或收集本机 data、logs、.env 或音乐。

## 第三方许可证与源代码

项目自有代码继续采用 PolyForm Noncommercial 1.0.0。随包的 Python、Qt/PySide6、其他依赖及 PyInstaller 引导程序保留各自许可，见 THIRD_PARTY_LICENSES/。目录中的 build-environment.json 记录构建环境包版本与上游地址，build-info.json 记录程序版本、提交和 Qt 版本。

Qt/PySide6 的 LGPL 部分不受项目非商业限制；可以按其许可证替换 _internal 中的相应动态库，也保留 LGPL 所要求的调试库修改所需的逆向工程权利。Qt 的 LGPLv3 及其引用的 GPLv3 完整文本随包提供，这不表示项目本身改用 GPL。对应版本源代码及构建说明可从 Qt 官方取得：

- Qt 源代码版本目录：https://download.qt.io/archive/qt/
- PySide / Shiboken 源代码版本目录：https://download.qt.io/official_releases/QtForPython/pyside6/
- Qt for Python 许可说明：https://doc.qt.io/qtforpython-6/licenses.html
- PyInstaller 源代码及引导程序许可例外：https://github.com/pyinstaller/pyinstaller

构建不携带外部 FFmpeg 可执行文件、外部签名 SDK 或网页 Live2D 资源。对自行添加的第三方组件，应继续遵守其各自许可，不能以项目 LICENSE 代替。
