# astrbot_plugin_beszel

<div align="center">

[![License: AGPL](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://opensource.org/licenses/agpl-3.0)
[![CI](https://github.com/iona-s/astrbot_plugin_beszel/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/iona-s/astrbot_plugin_beszel/actions/workflows/ci.yml)
![Python Version](https://img.shields.io/badge/Python-%3E%3D3.12%2C%3C4-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.25%2C%3C5-green)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey)

**基于 AstrBot 的 Beszel 探针监控与告警推送插件**

[安装](#-安装) • [功能特性](#-功能特性) • [效果预览](#-效果预览) • [指令与工具](#-指令与-llm-工具) • [配置说明](#-配置说明) • [Webhook 接入](#-webhook-告警接入) • [版本兼容](#-版本兼容说明) • [常见问题](#-常见问题)

</div>

---

## 📖 简介

`astrbot_plugin_beszel` 是一款用于 AstrBot 的轻量级运维监控插件。通过对接 [Beszel](https://beszel.dev/) 监控平台的只读 API，
可在聊天会话中快速查询探针运行状态、多节点概览与高精度历史时序图表，并支持接收 Beszel、Watchtower 及 Uptime Kuma 的 Webhook 告警通知。

---

## 📦 安装

### 推荐通过 AstrBot 插件市场安装

在 AstrBot 管理面板的插件市场中搜索 `astrbot_plugin_beszel` 并安装，随后打开
插件配置填写 Beszel Hub 地址和登录凭据。

---

## ✨ 功能特性

- **探针列表 (`/beszel list`)**：纯文本返回全部探针节点名称和状态圆点；离线节点额外显示上次在线时间。
- **系统总览 (`/beszel overview`)**：卡片式多节点总览长图，直观展示各探针的 CPU、内存、根分区磁盘、网络流量、温度、GPU 等核心指标进度条。
- **单机详情 (`/beszel status`)**：单探针完整实时详情长图，展示系统规格、运行时间、多核心负载、网络/磁盘/GPU 明细占用、外挂磁盘（EFS）和 Docker 容器资源占用。
- **历史监控 (`/beszel history`)**：双列历史时序长图，支持多指标聚合卡片（网络 Rx/Tx、磁盘读写 I/O、1m/5m/15m 负载、GPU 功耗与显存、外挂盘独立 I/O 等）。
- **监控 Webhook 接收**：支持 Bearer Token 鉴权，兼容 Beszel 及其他 Shoutrrr 服务（如 Watchtower），并额外支持 Uptime Kuma 的标准 JSON Webhook，自动将告警消息转发至 AstrBot 会话中。

---

## 📸 效果预览

<details>
  <summary><strong>点击展开示例图片</strong></summary>
  <br/>

<div align="center">
  <table>
    <tr>
      <td align="center" colspan="2">
        <img src="https://fastly.jsdelivr.net/gh/iona-s/astrbot_plugin_beszel@master/assets/overview.png" width="800" alt="Beszel 探针概览"/>
        <br/>
        <sub>探针概览</sub>
      </td>
    </tr>
    <tr>
      <td align="center" valign="top">
        <img src="https://fastly.jsdelivr.net/gh/iona-s/astrbot_plugin_beszel@master/assets/status.png" width="400" alt="Beszel 单机详情"/>
        <br/>
        <sub>单机详情</sub>
      </td>
      <td align="center" valign="top">
        <img src="https://fastly.jsdelivr.net/gh/iona-s/astrbot_plugin_beszel@master/assets/history.png" width="400" alt="Beszel 一小时历史状态"/>
        <br/>
        <sub>一小时历史状态</sub>
      </td>
    </tr>
  </table>
</div>

</details>

---

## ⌨️ 指令与 LLM 工具

### 1. 聊天指令

| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| `/beszel list` | 纯文本列出所有探针节点及其状态 | `/beszel list` |
| `/beszel overview` | 渲染多节点卡片式状态概览长图 | `/beszel overview` |
| `/beszel status <name-or-id>` | 渲染指定探针的当前实时详细状态长图 | `/beszel status server` |
| `/beszel history <name-or-id> [range]` | 渲染指定探针的历史时序长图（省略范围时读取配置，初始默认 `1h`） | `/beszel history server 24h` |

> 💡 **时间跨度支持**：`1h`（1小时）、`12h`（12小时）、`24h`（24小时）、`1w`（1周）、`30d`（30天）。

### 2. 大模型工具调用 (Function Calling)

插件注册了 4 个与指令完全对等的 LLM Tool，AI 助手可在对话中自动调用：
- `beszel_list_systems`：查询探针列表与在线状态；
- `beszel_get_overview`：生成多探针状态概览图片；
- `beszel_get_system_status`：查询单探针实时详情长图；
- `beszel_get_system_history`：查询单探针历史时序监控图表。

---

## ⚙️ 配置说明

在 AstrBot 管理面板的插件配置中填写以下参数：

### 1. `beszel`（Hub 连接配置）
- **`base_url`**：Beszel Hub 地址（如 `http://127.0.0.1:8090`、`https://beszel.example.com`）
- **`email` / `password`**：登录用户的账号密码。**建议在 Beszel Hub 创建只读角色的专用账号**。AstrBot 会在插件配置中以明文保存并显示密码，请限制 Dashboard 和配置文件的访问权限
- **`timeout_seconds`**：请求超时时间（秒，默认 `10`）
- **`verify_tls`**：是否验证 TLS 证书（默认 `true`；使用自签名证书时可设为 `false`）
- **`history_default_range`**：省略 range 参数时的默认历史跨度（默认 `1h`）
- **`cache_ttl_seconds`**：探针列表内存缓存过期时间（秒，默认 `60`，范围 `0 ~ 300`，设为 `0` 禁用缓存）

### 2. `access`（权限控制）
- **`mode`**：
  - `admin_only`（默认）：仅 AstrBot 管理员可查询
  - `umo_allowlist`：仅允许 `allowed_umos` 列表中的会话及管理员查询
  - `all`：允许所有会话查询
- **`allowed_umos`**：授权的完整统一消息源列表（如 `default:FriendMessage:123456789`）

### 3. `display` & `render`（显示与渲染）
- **`timezone`**：时间显示时区（例如 `Asia/Shanghai`，留空则继承 AstrBot 系统时区）
- **`page_size`**：概览图每页最大展示探针数（默认 `20`，可调范围 `10 ~ 40`）
- **`show_connection_address`**：单机详情长图中是否显示连接 IP 与端口（默认 `false` 隐藏以保护隐私）
- **`font_path`**：可选自定义字体文件路径；留空使用插件自带字体，容器部署时应填写容器内可访问的路径

### 4. `webhook`（告警推送）
- **`enabled`**：是否启用 Webhook 接收服务（默认 `false`）
- **`host`** / **`port`**：监听地址与端口（默认 `127.0.0.1:8899`）
- **`path`**：接收路径（默认 `/`）
- **`token`**：Bearer Token 鉴权密钥（留空保存后插件将自动生成随机安全密钥）。该值同样会由 AstrBot 明文保存并显示，请按凭据管理
- **`target_umos`**：接收告警推送的目标 UMO 列表（至少填写一个）

> 将 `webhook.host` 改为 `0.0.0.0` 会让监听端口暴露给所有可达网络。请配合防火墙或容器网络隔离，并在公网入口使用 HTTPS 反向代理，只转发配置的 Webhook 路径。

---

## 🔐 Beszel 只读用户授权指引

Beszel 新建用户后，默认不会自动将已有系统共享给该用户：

1. **单人自建 Hub**：
   可以直接在 Beszel Hub 的 Docker 环境变量中加入：
   ```yaml
   environment:
     SHARE_ALL_SYSTEMS: "true"
   ```
2. **多用户协作 Hub**：
   在 Hub WebUI 中进入用户管理，或在系统设置中将对应探针节点的共享权限勾选给该只读账号。详见 [Beszel 官方文档：共享系统](https://beszel.dev/zh/guide/user-accounts#%E4%B8%8E%E5%A4%9A%E4%B8%AA%E7%94%A8%E6%88%B7%E5%85%B1%E4%BA%AB%E7%B3%BB%E7%BB%9F)。

---

## 🔔 Webhook 告警接入

插件内置轻量级 Webhook 接收服务，可直接接收 Beszel 告警并将通知消息主动转发至指定的 AstrBot 目标会话。
同时兼容 Shoutrrr 规范（如 Watchtower）及 Uptime Kuma 的 Webhook 推送。

### 1. Beszel 监控告警 (Generic Webhook)

在 Beszel Hub 的 **Settings -> Notifications -> Generic** 中添加通知 URL：

#### ① 基础纯文本告警（标准示例）
最基础通用的纯文本告警，节点发生离线/上线或资源超限告警时发送结构化纯文本通知：
```text
generic://127.0.0.1:8899/?template=json&disabletls=yes&@Authorization=Bearer%20<TOKEN>
```

#### ② 进阶增强：告警自动附加 1 小时历史时序长图（插件特殊特性）
通过在 URL 中附加 `$source=beszel` 与 `$send_history=true` 参数，插件在收到该告警后，**会自动查询对应故障探针并额外生成发送该节点过去 1 小时的多曲线历史长图**，便于快速定位故障前资源突发原因：
```text
generic://127.0.0.1:8899/?template=json&disabletls=yes&@Authorization=Bearer%20<TOKEN>&$source=beszel&$send_history=true
```

> 📌 **参数说明**：
> - `<TOKEN>`：替换为插件配置中配置或自动生成的 `webhook.token`（需为 ASCII 字符）；
> - `disabletls=yes`：直连内网 HTTP 端口时使用；若经由 HTTPS 反向代理请移除此项；
> - `$source=beszel` 与 `$send_history=true`：**插件内部特殊触发参数**，用于显式声明来源并启用告警额外附带 1 小时历史监控图片。

---

<details>
<summary><b>2. Watchtower 容器更新通知 (纯文本)</b></summary>

在 Watchtower 的环境变量 `WATCHTOWER_NOTIFICATION_URL` 中填入：
```text
generic://127.0.0.1:8899/?template=json&disabletls=yes&@Authorization=Bearer%20<TOKEN>
```
</details>

<details>
<summary><b>3. Uptime Kuma 服务监控告警（标准 JSON）</b></summary>

在 Uptime Kuma 的 **设置 -> 通知 -> 设置通知** 中，通知类型选择 **Webhook**：
- **Post URL**：`http://127.0.0.1:8899/`
- **HTTP 方法**：`POST`
- **请求体**：`预设 - application/json`
- **额外请求头 (Headers)**：
  ```json
  {
    "Authorization": "Bearer <TOKEN>"
  }
  ```
</details>

---

## 📌 版本兼容说明

| 上游服务 / 依赖 | 兼容范围 | 兼容性说明 |
| :--- |:-------------| :--- |
| **Beszel Hub** | `>=v0.18.8` | 验证通过 PocketBase REST API 只读接口、多指标时序数据与容器指标解析 |
| **Uptime Kuma** | `>=v2.3`    | 验证通过标准 JSON Webhook 状态告警与心跳载荷推送 |
| **AstrBot** | `>=4.25,<5`    | 插件运行依赖 Python `>=3.12,<4` |

> ℹ️ **说明**：上述版本为本插件开发与实机验证的版本。更低版本的 Beszel Hub 或 Uptime Kuma 实际运行可能没有问题，但未经专门验证。

---

## 🙏 致谢

感谢以下优秀的开源项目与社区：

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) - 优雅、强大且易于扩展的多平台大语言模型聊天机器人框架
- [Beszel](https://github.com/henrygd/beszel) - 轻量、美观的现代化服务器资源与容器监控平台
- [pytakumi](https://github.com/KimigaiiWuyi/pytakumi) - 高性能、轻量级的本地 HTML/CSS 图像渲染引擎

---

## 🤝 开发与贡献

欢迎提交 Issue 和 Pull Request。参与开发前请阅读[贡献指南](CONTRIBUTING.md)。

---

## 📄 开源协议

本项目采用 [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE) 开源协议。

随包分发的 `NotoSansSC-Regular.otf` 来自 Noto Sans CJK 2.004，使用
[SIL Open Font License 1.1](core/assets/fonts/OFL.txt)。

图片中使用的 Beszel 标志衍生自 Beszel Hub 前端，相关版权与 MIT 许可见
[BESZEL-LICENSE.txt](core/assets/BESZEL-LICENSE.txt)。
