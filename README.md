# Burn Rate (claude-usage-tray)

A small Windows tray app that shows your **Claude Code usage as a heatmap**, with totals in
**API-equivalent US dollars** or **tokens** (click the pill to switch). Left-click the tray icon to
open the popup, click anywhere else to close it.

Everything is computed locally from the session logs Claude Code keeps in
`~/.claude/projects/**/*.jsonl` and, optionally, the OpenAI Codex CLI logs in `~/.codex/sessions`.
Nothing is uploaded and no API key is needed. Click the **Claude / Codex / Both** pill to pick the source.

## Run

```bash
pip install -r requirements.txt
pythonw claude_usage_tray.pyw      # or: python claude_usage_tray.pyw
```

Windows 10/11 only (it uses the Win32 tray and DWM APIs). Python 3.9+.
The icon may land in the tray overflow (`^`); drag it onto the taskbar to keep it visible.

## What the token numbers include

**Claude (Claude Code logs)** - for every assistant reply, summed per local day:

| Counted | Not counted |
|---|---|
| fresh input tokens | anything that left no local log: claude.ai chats, the desktop chat app, API calls from your own code |
| output tokens (thinking is part of output) | sessions whose log files were deleted (Claude Code purges old logs; see below) |
| cache writes (5 min and 1 h) | usage from other computers |
| cache reads | tool fees such as web search, and any billing adjustments |
| sub-agent replies stored in the same logs | tokens of replies that were never written to the log (interrupted mid-stream) |

**Codex (OpenAI Codex CLI logs)** - input tokens (the cached part is already inside this figure) plus output
tokens (reasoning is part of output), taken as the growth of each session's running total.

**Why the totals look huge:** every request resends the whole conversation, and cache reads count each time,
so a long session adds up fast. Cache reads are also priced at about a tenth of normal input, which is why the
dollar figure is far smaller than the token figure suggests.

**Duplicates:** Claude replies are de-duplicated by message id, so a reply streamed in several log lines counts once.

## Notes

- **Codex prices** are in the `CODEX_PRICE` table (input / cached input / output, $ per 1M tokens), copied
  from public third-party pages that disagree in places, so treat them as rough. `gpt-5.6-sol` uses a
  promotional rate. Models missing from the table count as $0. Long-context surcharges are ignored.
- Codex `input_tokens` already includes cached tokens; the app prices the cached part at the cached rate.
- Only usage that leaves a local log is counted: editor plugins of Claude Code and the Codex CLI, yes;
  direct API calls from your own code, claude.ai chats and the desktop chat app, no.

- **Dollar figures are list-price equivalents, not what you were billed.** Subscription users pay a
  flat fee. Prices live in the `PRICE` table near the top of the script; edit it when prices change.
  Cache writes are priced at 1.25x input (5 min) or 2x (1 h). Unknown models count as $0.
- Claude Code deletes old session logs after a while (default 30 days), so the heatmap only reaches
  back as far as the logs on disk. Raise `cleanupPeriodDays` in `~/.claude/settings.json` to keep more.
- Double-click the title to cycle colour themes, or tray menu > **Colors...** to set every colour yourself.
  Theme, custom colours, USD/tokens and source are saved in `claude_usage_tray.json` next to the script.
- `--show` opens the popup on start.

## 中文说明

Windows 托盘小工具：用热力图显示你的 Claude Code（可选 Codex）用量，可在"等价美元"和"token"之间切换，来源可选 Claude / Codex / 合计（点右上角小标签），配色可在托盘菜单 Colors... 里自由设置。
左键点托盘图标弹出，点别处收起。数据全部在本地从 `~/.claude/projects` 的会话记录计算，不联网。
美元是按 API 标价折算的等价金额，不是实际账单。

### 记录了哪些 token

- **Claude：** 每条回复的新输入、输出（含思考）、缓存写入、缓存读取，按本地日期汇总。同一条回复按消息 id 去重。
- **Codex：** 输入（已含缓存命中部分）加输出（含推理），按每个会话累计值的增量计算。
- **不包括：** claude.ai 网页、桌面聊天、你自己代码里的 API 调用；已被删除的本地日志；其他电脑上的用量；联网搜索等工具费用；没写进日志的中断回复。
- 缓存读取每次都会重复计入，所以总数很大；但它的单价只有普通输入的十分之一左右，折算成美元会小得多。
