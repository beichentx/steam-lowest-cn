# steam-lowest-cn

「北辰同学情报站」App 的 Steam 每日史低数据托管仓库。

- 由 GitHub Actions 每日 **北京时间 08:30** 自动抓取并更新（无需任何电脑开机）。
- 数据格式：`steam_lowest.json` + `icons/<appid>.jpg`。
- App 拉取地址（任选其一，国内建议用 jsDelivr）：
  - `https://raw.githubusercontent.com/beichentx/steam-lowest-cn/main/steam_lowest.json`
  - `https://cdn.jsdelivr.net/gh/beichentx/steam-lowest-cn@main/steam_lowest.json`

## 抓取策略
1. 优先 `web` 模式：抓 SteamDB，拿到真·超史低 / 史低标记（需过 Cloudflare，云端可能偶发失败）。
2. 失败则回退 `steam` 模式：Steam 官方 Store API，按折扣力度分级（超值 / 特惠），并通过官方 API 取中文名。
3. 图标优先 Steam 官方 `header.jpg`（920×430），最适合卡片缩略。

## 本地手动更新
```bash
pip install requests cloudscraper
python scripts/fetch_steam_lowest.py --mode steam --out json --json-path steam_lowest.json --icons-dir icons --limit 100
git add -A && git commit -m daily && git push
```
