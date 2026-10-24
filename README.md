# Claude usage tray

A small Windows tray app that shows your **Claude Code usage as a heatmap**, with totals in
**API-equivalent US dollars** or **tokens** (click the pill to switch). Left-click the tray icon to
open the popup, click anywhere else to close it.

Everything is computed locally from the session logs Claude Code already keeps in
`~/.claude/projects/**/*.jsonl`. Nothing is uploaded and no API key is needed.

## Run

```bash
pip install -r requirements.txt
pythonw claude_usage_tray.pyw      # or: python claude_usage_tray.pyw
```

Windows 10/11 only (it uses the Win32 tray and DWM APIs). Python 3.9+.
The icon may land in the tray overflow (`^`); drag it onto the taskbar to keep it visible.

## Notes

- **Dollar figures are list-price equivalents, not what you were billed.** Subscription users pay a
  flat fee. Prices live in the `PRICE` table near the top of the script; edit it when prices change.
  Cache writes are priced at 1.25x input (5 min) or 2x (1 h). Unknown models count as $0.
- Claude Code deletes old session logs after a while (default 30 days), so the heatmap only reaches
  back as far as the logs on disk. Raise `cleanupPeriodDays` in `~/.claude/settings.json` to keep more.
- Double-click the title to cycle colour themes. The theme and the USD/tokens choice are saved next
  to the script in two small dotfiles.
- `--show` opens the popup on start.

## 中文说明

Windows 托盘小工具：用热力图显示你的 Claude Code 用量，可在"等价美元"和"token"之间切换（点右上角小标签）。
左键点托盘图标弹出，点别处收起。数据全部在本地从 `~/.claude/projects` 的会话记录计算，不联网。
美元是按 API 标价折算的等价金额，不是实际账单。
