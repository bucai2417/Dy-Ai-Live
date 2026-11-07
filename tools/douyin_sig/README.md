# 可选抖音签名接口

本目录的 sign_server.mjs 通过标准输入/输出的 JSON 行协议提供签名接口，需要 Node.js 及与接口兼容的外部 webmssdk.js。公开 v1.0.0 源码不附带 webmssdk.js。

## 为什么不随包提供

历史版本的外部文件声称来自抖音网页 SDK 的提取修改，经 newbeejmc/Dsh_douyin 转发；历史文档又将其标为 GPL-3.0-or-later。当前未确认完整授权链，因此公开版本不重新分发它，也不将其归入项目的非商用许可。使用独立进程不意味着可以自动免除第三方许可义务。详见 ../../THIRD_PARTY_NOTICES.md。

## 运行方式

- 默认可使用 mock 演示或 bridge 外部弹幕助手，不需要本目录的 SDK。
- 如要使用 douyin_direct，须自行合法取得兼容实现，放在本目录的 webmssdk.js，并安装 Node.js。
- webmssdk.js 需要导出 get_sign(stub)；sign_server.mjs 负责传入参数与 JSON 行请求/响应。
- 缺少签名入口、SDK 或 Node.js 时，程序将直连标记为不可用，现有接入层会退回 CDP 浏览器采集。无需浏览器采集时请选择 bridge 或 mock；直播画面本身一直使用原生窗口。
- 本机配置的 SDK 被 .gitignore 忽略，不会随源码导出。应遵守该实现自己的许可，不要用项目 LICENSE 覆盖它。

接口请求示例：{"id":1,"op":"ping"}；正常响应包含 {"id":1,"ok":true,"pong":true}。完整签名接口见 sign_server.mjs。
