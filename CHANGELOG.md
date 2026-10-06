# 更新日志 (Changelog)

本项目的所有重要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，并遵循
[语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

## [v1.2.0] - 2026-10-07

### 新增

- Webhook 告警支持 AI 辅助诊断：通过 `send_analysis: true` 或 Shoutrrr 参数 `$send_analysis=true`
  开启，告警后额外推送一条基于节点近 1 小时监控数据的中文诊断。
- 新增配置项 `webhook.analysis_prompt`（自定义诊断提示词）与 `webhook.analysis_timeout_seconds`
  （诊断超时，默认 60 秒）。

### 变更

- Webhook 历史图与 AI 诊断改为后台推送，不再拖慢告警原文的送达。
- 历史图时间轴与 Beszel 网页保持一致：同一张图的所有图表共用时间范围，刻度落在整点。
- 状态图与概览的容量单位统一为 KB / MB / GB / TB。
- 升级 Pytakumi 至 0.1.5，提升图片渲染的稳定性。

### 修复

- 修复运行一段时间后查询结果变为空（找不到探针）的问题。
- 修复个别探针数据异常时整个探针列表无法显示的问题。
- 修复状态图中的探针在线状态可能滞后的问题。
- 修复所有容器占用都很低时容器图表整张消失的问题。
- 修复状态图与概览中多余的边框线。
- 修复未配置时区时夏令时切换后显示时间偏差一小时的问题。

## [v1.1.0] - 2026-09-10

### 新增

- 支持配置图片渲染精细度缩放（`render.render_scale`，50% ~ 300%，默认 100%）。
- 为 `/beszel history`、LLM 工具及 Webhook 历史图新增 Docker/Podman 容器 CPU 与内存历史趋势折线图（按图表 Y 轴最大值百分比阈值过滤低占用容器）。

### 优化

- 为探针列表查询增加可配置的短时内存 TTL 缓存（默认 60 秒），降低频繁查询对 Beszel Hub 的重复网络开销并加速连续指令响应。
- 优化历史图表的排列、动态配色、坐标轴间距、曲线与面积样式，并改善稀疏时序数据的展示。

## [v1.0.0] - 2026-08-30

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

[未发布]: https://github.com/iona-s/astrbot_plugin_beszel/compare/v1.2.0...HEAD
[v1.2.0]: https://github.com/iona-s/astrbot_plugin_beszel/compare/v1.1.0...v1.2.0
[v1.1.0]: https://github.com/iona-s/astrbot_plugin_beszel/compare/v1.0.0...v1.1.0
[v1.0.0]: https://github.com/iona-s/astrbot_plugin_beszel/releases/tag/v1.0.0
