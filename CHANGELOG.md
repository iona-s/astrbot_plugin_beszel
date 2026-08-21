# 更新日志 (Changelog)

本项目的所有重要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，并遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

---

## [v0.1.0] - 2026-08-21

### ✨ 核心特性

#### 1. 探针指标与系统状态查询
- **探针列表 (`/beszel list`)**：纯文本快速返回所有探针节点名称和状态圆点，离线节点附带上次在线时间。
- **系统总览 (`/beszel overview`)**：卡片式多探针概览长图，直观展示各节点状态、CPU、内存、根分区磁盘、网络吞吐、温度与 GPU 等核心指标进度条。
- **单机详情 (`/beszel status <name-or-id>`)**：单探针完整实时详情长图，包含主机系统规格、负载、网络/磁盘/GPU 详细占用及外挂磁盘（EFS）等所有硬件参数。
- **历史监控 (`/beszel history <name-or-id> [range]`)**：双列高精度历史时序长图，支持 `1h`、`12h`、`24h`、`1w`、`30d` 时间跨度。

#### 2. 大模型工具调用 (LLM Tools)
- 提供与指令功能完全对称的 4 个 LLM Tool (`beszel_list_systems`、`beszel_get_overview`、`beszel_get_system_status`、`beszel_get_system_history`)，支持与 AI 机器人自然语言交互查询。
- 统一底层查询服务与权限策略，保证安全性与一致性。

#### 3. 告警 Webhook 服务与自动附图
- 内置轻量异步 Webhook 服务，支持 Bearer Token 安全鉴权与监听地址/端口配置。
- 兼容 Beszel、Watchtower 等 Shoutrrr Generic 格式及 Uptime Kuma 标准 Webhook 格式。
- 支持 Beszel 告警参数 `send_history=true`，触发告警时自动附加故障节点 1 小时历史时序长图。
- 消息支持多目标统一消息源 (UMO) 顺序投递与错误隔离。

---

[v0.1.0]: https://github.com/iona-s/astrbot_plugin_beszel/releases/tag/v0.1.0
