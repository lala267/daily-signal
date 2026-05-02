# Daily Signal

Daily Signal 每天抓取你配置的 YouTube 频道 RSS，整理最近内容，生成一份 Markdown 日报。设置 `OPENAI_API_KEY` 后，它会自动把视频标题、简介、来源链接交给 AI 合并成中文“大事”简报。

## 快速开始

```bash
python3 daily_signal.py --dry-run
```

生成文件：

```bash
python3 daily_signal.py
```

输出会保存到 `briefs/daily-YYYY-MM-DD.md`，已经处理过的视频会记录在 `.daily-signal/seen.sqlite3`，避免第二天重复。

## 配置

复制一份配置再改：

```bash
cp config.example.json config.json
```

运行自己的配置：

```bash
python3 daily_signal.py --config config.json
```

`youtube_sources` 支持：

- `channel_id`：YouTube 频道 ID
- `playlist_id`：YouTube 播放列表 ID

频道 ID 可以从频道页源码、RSS 地址、或很多 YouTube channel id finder 工具里找到。

## 接入 AI

```bash
export OPENAI_API_KEY="你的 key"
python3 daily_signal.py --config config.json
```

没有 key 也能跑，只是会生成基础摘要，不会做同类话题合并和重要性分析。

## 每天自动运行

推荐用 GitHub Actions，适合本地网络不稳定的情况。

1. 把项目推到 GitHub 仓库。
2. 在 GitHub 仓库页面进入 `Settings` -> `Actions` -> `General`，确认 `Workflow permissions` 允许 `Read and write permissions`。
3. 如果要启用 AI 解析，进入 `Settings` -> `Secrets and variables` -> `Actions`，添加 secret：

```txt
OPENAI_API_KEY
```

4. 修改 `config.github.json` 里的频道源。
5. Actions 会在每天北京时间 08:00 自动运行，也可以在 `Actions` 页面手动点 `Run workflow`。

生成的日报会提交到：

```txt
briefs/daily-YYYY-MM-DD.md
```

macOS/Linux 可以用 cron：

```cron
0 8 * * * cd "/Users/juanjuandog/Documents/New project 2" && /usr/bin/python3 daily_signal.py --config config.json >> .daily-signal/run.log 2>&1
```

常用参数：

```bash
python3 daily_signal.py --lookback-hours 48
python3 daily_signal.py --include-seen
python3 daily_signal.py --no-ai
python3 daily_signal.py --date 2026-05-02
```

## 生成内容

Markdown 里会包含：

- 今日大事
- 每条事件的来源链接
- 发布时间和频道
- AI 分析或基础摘要
- 原始来源列表
- 抓取警告
