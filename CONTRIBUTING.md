# 贡献指南

感谢你愿意改进 `astrbot_plugin_beszel`。提交 Issue 或 Pull Request 前，请先搜索
现有内容，避免重复反馈或实现。

## 开始开发前

- 较大的功能、行为变更或兼容性调整应先通过 Issue 讨论。
- 每个 Pull Request 应只处理一个明确问题。
- 行为、配置或依赖发生变化时，应同步更新用户文档和更新日志。
- 插件运行代码应保留在 `core/`，根目录的 `main.py` 仅作为 AstrBot 加载入口。

## 使用 Coding Agent

你可以使用 Coding Agent 辅助分析、编写代码或整理文档，但不能将开发与审核
过程完全交由 Agent 完成。

提交者必须：

- 理解并人工审核提交中的全部代码、配置和文档；
- 检查差异中是否包含无关改动、敏感信息或不符合项目约束的内容；
- 自行执行适用的验证，并如实记录实际结果和未验证内容；
- 对提交内容、验证结论和潜在影响负责。

使用 Coding Agent 开发时，还应阅读仓库根目录的
[AGENTS.md](AGENTS.md)。

## 开发环境

项目支持 Python `>=3.12,<4`、AstrBot `>=4.25,<5`、Beszel Hub
`>=v0.18.8`。本地开发与验证建议使用 Python 3.12。

## 提交前验证

提交 Python 或模板改动时，至少执行：

```text
ruff check --config ruff.toml .
ruff format --check --config ruff.toml .
python -m compileall -q main.py core
pre-commit run --all-files --show-diff-on-failure
```

渲染、Beszel API 或 Webhook 行为发生变化时，还应在适用的本地环境执行聚焦的
导入、渲染或现场验证，并在 Pull Request 中如实记录结果和未验证内容。

## Commit 信息

Commit 首行采用 Conventional Commit 形式：

```text
<type>: <summary>
```

`type` 使用小写，例如 `fix`、`feat`、`docs`、`refactor` 或 `chore`。

## Pull Request

- 说明改动动机、实现范围和关联 Issue。
- 列出实际执行的命令及结果，不要把未执行的检查报告为通过。
- 明确破坏性变更、新依赖和兼容性影响。
- 检查差异中不存在密码、Token、私有连接地址或无关文件。
