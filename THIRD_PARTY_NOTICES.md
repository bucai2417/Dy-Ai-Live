# 第三方组件与素材说明

项目根级 [LICENSE](LICENSE) 只适用于项目贡献者有权授权的代码和素材，不覆盖第三方组件。第三方材料继续适用各自的版权和许可；独立进程或不同目录本身不能保证免除第三方许可义务。

## v1.0.0 随附内容

公开源码包含项目 Python 代码、网页兼容入口、自有 Deepy 立绘/眼嘴贴片/骨架及服装、测试、工具和文档。依赖包通过 requirements 文件安装，源码包不捆绑依赖二进制。Windows 自动构建产物另外包含 Python、Qt/PySide6 等运行依赖及 THIRD_PARTY_LICENSES 声明；动态库继续适用自身许可，详见 [Windows 构建说明](docs/Windows自动构建.md)。外部 FFmpeg 可执行文件、Node.js、签名 SDK 和旧网页 Live2D 资源不随 EXE 包分发。

## 未随公开源码分发的可选材料

| 本机路径 | 用途 | 发布处理 |
| --- | --- | --- |
| tools/douyin_sig/webmssdk.js | 外部抖音签名实现 | 历史说明称经 newbeejmc/Dsh_douyin 转发且涉及 GPL；本次未核实完整授权链，不重新分发、不改成项目非商用许可 |
| web/vendor/pixi.min.js | PixiJS 网页渲染库 | 原文件标识 v7.4.2 / MIT；原生界面不需要，公开包不附带 |
| web/vendor/cubism4.min.js | 网页 Live2D 适配代码 | 原生界面不需要；公开包不附带，不能仅凭适配项目 MIT 声明覆盖内嵌 SDK |
| web/vendor/live2dcubismcore.min.js | Live2D Cubism Core | 适用 Live2D 专有协议；公开包不附带 |
| web/models/haru/ | Live2D 示例模型 | 原生界面不需要；公开包不附带，模型素材需另行确认授权 |

上述文件在原开发机器上保留。
.gitattributes 的 export-ignore 规则使源码导出跳过它们，.gitignore 防止在新的公开仓库中意外加入这些本机文件。公开仓库使用全新源码快照，不包含原开发仓库中保存过这些文件的历史提交。

原生直播画面、模拟弹幕和外部助手桥接不需要上述材料。抖音直连需使用者合法取得兼容的签名实现并安装 Node.js；缺少时现有接入层回退到 CDP 采集，这会使用浏览器采集弹幕，但直播显示仍是原生窗口。不使用浏览器采集时请选择 bridge 或 mock。旧网页 Live2D 页面需自行配置合法取得的运行库和模型才能工作；原生 Deepy 不会加载该模型。

## 上游参考

- PolyForm Noncommercial：<https://polyformproject.org/licenses/noncommercial/1.0.0/>
- PixiJS v7.4.2：<https://github.com/pixijs/pixijs/tree/v7.4.2>
- pixi-live2d-display：<https://github.com/guansss/pixi-live2d-display>
- Live2D 专有软件协议：<https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html>
- 历史签名文件转发项目：<https://github.com/newbeejmc/Dsh_douyin>

这些链接用于记录来源，不表示项目获得其额外授权，也不代表上游为本项目背书。运行时下载安装的第三方包请保留其自带许可证；用户自行加入的模型、音视频等内容不适用项目自有素材的授权声明。
