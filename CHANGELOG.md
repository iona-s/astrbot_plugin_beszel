# 更新日志 (Changelog)

本项目的所有重要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，并遵循
[语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

## [v1.0.0] - 2026-08-29

### 新增

- 提供 `/beszel list`、`/beszel overview`、`/beszel status` 和
  `/beszel history` 四组查询指令，以及功能对等的 LLM Tools。
- 支持探针列表文本、分页总览图、单机实时详情长图和 `1h`、`12h`、
  `24h`、`1w`、`30d` 历史时序长图。
- 单机详情展示系统规格、CPU、内存、磁盘、网络、温度、GPU、外挂磁盘及
  Docker 容器资源占用。
- 内置 Webhook 服务，兼容 Beszel/Watchtower 的 Shoutrrr Generic JSON 和
  Uptime Kuma 标准 JSON Webhook，并支持多目标 UMO 投递。
- Beszel 告警可通过 `send_history=true` 自动附带告警节点过去一小时的历史图。
- 提供管理员、UMO 白名单和全员三种查询权限模式。

### 渲染

- 使用 Pytakumi 与可维护的 Jinja HTML/CSS 模板生成图片，样式大体参照 Beszel Hub。
- 随插件分发 Noto Sans SC 字体，确保 Windows、Linux 与容器环境中的中文渲染；
  同时允许配置自定义字体文件。

### 安全与可靠性

- Beszel 客户端仅调用查询所需的只读 API，并在 401 后最多重新登录重放一次。
- Webhook 使用 Bearer Token 与恒定时间比较进行鉴权，并在读取请求体前拒绝未授权请求。
- 日志严格隐藏密码、PocketBase Token、Webhook Token 和探针连接地址，保留排障所需的
  会话标识、主机名与监控指标。

[v1.0.0]: https://github.com/iona-s/astrbot_plugin_beszel/releases/tag/v1.0.0
