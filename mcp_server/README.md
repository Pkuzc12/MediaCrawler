# mediacrawler-mcp

把所在的 MediaCrawler 仓库包装成一个 stdio MCP 服务，供 Claude Code 调用。
支持 MediaCrawler 的全部 7 个平台：小红书 `xhs`、抖音 `dy`、快手 `ks`、B 站 `bili`、
微博 `wb`、贴吧 `tieba`、知乎 `zhihu`。

代码改写自 [dsh-mediacrawler](https://github.com/xwh-01/dsh-mediacrawler) v0.3.0（MIT），
本目录沿用 MIT 协议，见 [LICENSE](LICENSE)。外层 MediaCrawler 仍受其非商业学习许可约束，
只能用于学习研究，不得大规模爬取。

## 工作方式

```
Claude Code ──stdio──▶ mcp_server（本目录，自己的 .venv）
                          │ 用 MediaCrawler 的 .venv 启动 mc_mcp/runner.py
                          ▼
                       main.py ──CDP──▶ 你日常用的 Chrome（已登录各平台）
```

- 不修改任何上游文件。`runner.py` 在启动 `main.py` 前直接给 `config` 赋值，
  以补上命令行没有的选项（排序、时间范围、知乎博主）。
- 每次任务写入 `../data/mcp_runs/<run_id>/`：`manifest.json`、`logs.jsonl`（已脱敏）
  和 `data/`（MediaCrawler 原样输出的 jsonl，以及下载的媒体）。`/data/` 已被上游 `.gitignore` 忽略。
- 同一时间只允许一个任务。

## 工具

| 工具 | 作用 |
|---|---|
| `check` | 检查仓库、MediaCrawler 的 `.venv`、Chrome 调试端口、上游命令行参数 |
| `crawl` | 启动任务，最多等待 `wait_seconds` 秒，返回状态 |
| `status` | 进度、各类记录条数、最近日志、需要用户处理的事项（扫码 / 允许调试） |
| `stop` | 停止任务及其整个进程树 |
| `results` | 分页读取结果，默认统一字段，`raw=true` 返回原始字段 |
| `runs` | 历史任务 |

`crawl` 的主要参数：

- `mode`：`search`（`keywords`，最多 5 个）、`detail`（`targets` 为帖子链接或 ID）、
  `creator`（`targets` 为博主主页，最多 5 个）。小红书链接必须保留 `xsec_token`。
- `max_items`：每个关键词的条数；关键词数 × `max_items` ≤ 100。搜索按整页抓取，
  条数太少时仍会抓满一页（小红书 / 快手 / B 站 / 知乎 20 条，抖音 / 微博 / 贴吧 10 条）。
- 仅搜索模式可用的筛选，其余平台上游不支持，传了会报错：
  - `sort`：小红书 `general` / `popular` / `latest`；微博 `default` / `realtime` / `popular` / `video`
  - `publish_within`：抖音 `day` / `week` / `half_year`
  - `date_from` / `date_to`：B 站，逐日搜索，条数平均分到每一天，跨度最多 366 天
- `comments` / `max_comments` / `sub_comments`：评论、每帖评论数、楼中楼（慢）
- `media`：下载图片和视频
- `timeout_minutes`：硬性超时，默认 30

## 安装

前提：MediaCrawler 本身已装好依赖（仓库根目录有 `.venv`），Chrome 已打开
`chrome://inspect/#remote-debugging` 的远程调试开关，并且登录了要抓的平台。

```powershell
cd mcp_server
uv sync
claude mcp add --scope user mediacrawler -- "<仓库路径>\mcp_server\.venv\Scripts\python.exe" -m mc_mcp
```

本目录的 `pyproject.toml` 里有 `[tool.uv]` 段，所以 uv 不会继承上层 MediaCrawler 设置的清华镜像。

环境变量（都可选，测试用）：`MEDIACRAWLER_ROOT`、`MEDIACRAWLER_PYTHON`、`MC_MCP_STATE_DIR`、
`MC_MCP_CDP_PORT`（默认 9222）。

## 测试

```powershell
cd mcp_server
uv run pytest
```

测试用 `tests/fake_mediacrawler/` 代替真实爬虫，不会访问网络。

## 同步上游

```powershell
git fetch upstream
git merge upstream/main
git push
```

上游更新后，先调用一次 `check`：如果上游改了命令行参数，它会列出缺少的参数。
