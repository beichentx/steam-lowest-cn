#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Steam 每日史低播报 —— App 集成用独立脚本
=========================================
不依赖 WorkBuddy 的 WebFetch，纯 Python 输出结构化 JSON + Markdown。

数据源模式（--mode）：
  web   抓 SteamDB 网页 HTML（需 cloudscraper 绕过 Cloudflare，能拿到真·史低标记）
  steam  Steam 官方 Store Web API（纯 API 无需 token，但无史低标记，按折扣力度代理分级）
  demo   内置样例，验证全链路（解析/汇率/图标/JSON/MD），无需联网抓 SteamDB

用法：
  python fetch_steam_lowest.py --mode demo  --out both
  python fetch_steam_lowest.py --mode web   --out json
  python fetch_steam_lowest.py --mode steam --out both --limit 100

输出 JSON schema（每款）：
  {
    "generated_at": "ISO 时间",
    "rate_uah_to_cny": 0.1508,
    "summary": {"super_low": N, "history_low": M},
    "games": [{
       "appid", "name_en", "name_cn", "developer", "publisher",
       "developer_cn", "publisher_cn", "tier"("super_low"|"history_low"|"super_value"|"value"),
       "cn_price", "ua_price_uah", "ua_price_cny", "icon_local", "steam_url"
    }]
  }

注意：SteamDB 没有官方 API，直连网页会被 Cloudflare 403（实测 urllib/cloudscraper 均失败）。
      work 模式在沙箱环境不可用，需所在网络能过 Cloudflare；否则用 --mode steam 或复用 WorkBuddy 产出。
"""
import argparse, json, os, re, sys, datetime, urllib.request, urllib.error

# ---------- 配置 ----------
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
RATE_DEFAULT = 0.1508  # 1 UAH = 0.1508 CNY（兜底值，联网时实时获取）
XIAOHEIHE_ICON = "https://cdn.steamchina.eccdnx.com/steam/apps/{}/capsule_184x69.jpg"  # 小黑盒/蒸汽平台国内镜像
STEAM_STORE   = "https://store.steampowered.com/app/{}/"

# 公司英文名 -> 小黑盒/3DM 中文名 映射（按需补充）
COMPANY_CN = {
    "CAPCOM": "卡普空", "SEGA": "世嘉", "Square Enix": "史克威尔艾尼克斯",
    "SQUARE ENIX": "史克威尔艾尼克斯", "Crystal Dynamics": "水晶动力",
    "Behaviour Interactive": "行为互动", "Rockstar Games": "Rockstar",
    "CD PROJEKT RED": "CD Projekt Red", "PlatinumGames": "白金工作室",
    "Bandai Namco": "万代南梦宫", "Bandai Namco Entertainment": "万代南梦宫",
    "Xbox Game Studios": "Xbox游戏工作室", "Playground Games": "游乐场游戏",
    "PlayWay": "PlayWay", "Noble Muffins": "诺布尔松饼", "SEGA": "世嘉",
    "Sonic Team": "索尼克团队", "CAPCOM Co., Ltd.": "卡普空",
}

def http_get(url, timeout=30, use_cloud=False):
    if use_cloud:
        try:
            import cloudscraper
            return cloudscraper.create_scraper().get(url, timeout=timeout).text
        except Exception:
            pass
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")

def fetch_rate():
    try:
        txt = http_get("https://open.er-api.com/v6/latest/UAH", timeout=20)
        return float(json.loads(txt)["rates"]["CNY"])
    except Exception:
        return RATE_DEFAULT

def cn_company(name):
    if not name:
        return ""
    name = name.strip()
    if name in COMPANY_CN:
        return COMPANY_CN[name]
    # 开发=发行时只写一次
    return name

def download_icon(appid, icons_dir):
    """下载图标到本地。优先 Steam 官方 header.jpg（920×430 横图，最适合卡片缩略），
    其次 library_600x900 竖图，再次小黑盒国内镜像，全失败返回 None。"""
    if not icons_dir:
        return None
    os.makedirs(icons_dir, exist_ok=True)
    path = os.path.join(icons_dir, f"{appid}.jpg")
    candidates = [
        f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
        f"https://cdn.steamchina.eccdnx.com/steam/apps/{appid}/header.jpg",
        f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
        XIAOHEIHE_ICON.format(appid),
    ]
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=25).read()
            if data and len(data) > 500:  # 过小通常是 404 占位
                with open(path, "wb") as f:
                    f.write(data)
                return f"./icons/{appid}.jpg"
        except Exception:
            continue
    return None


def cn_name_from_steam(appid):
    """通过 Steam 官方 Store API（cc=cn, l=schinese）取官方中文名，失败返回 ''。"""
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=cn&l=schinese&filters=basic"
        j = json.loads(http_get(url, timeout=20))
        node = j.get(str(appid), {}).get("data", {})
        name = (node.get("name") or "").strip()
        return name
    except Exception:
        return ""

# Steam package 英文名 -> 中文（与 App 端翻译保持一致）
PKG_TRANSLATE_FULL = [
    ("Game of the Year Edition", "年度最佳版"),
    ("Digital Deluxe Edition", "数字豪华版"),
    ("Deluxe Edition", "豪华版"),
    ("Gold Edition", "黄金版"),
    ("Ultimate Edition", "终极版"),
    ("Complete Edition", "完全版"),
    ("Anniversary Edition", "周年纪念版"),
    ("Premium Edition", "高级版"),
    ("Standard Edition", "标准版"),
    ("Digital Edition", "数字版"),
    ("Collector's Edition", "收藏家版"),
    ("Special Edition", "特别版"),
    ("Season Pass", "季票"),
]
PKG_TRANSLATE_KW = {
    "Deluxe": "豪华", "Gold": "黄金", "Ultimate": "终极", "Complete": "完全",
    "Premium": "高级", "Standard": "标准", "Anniversary": "周年", "Special": "特别",
    "Digital": "数字", "Upgrade": "升级", "Bundle": "捆绑包", "Edition": "版", "Pack": "包",
}

def translate_pkg_cn(en):
    """Steam package 英文名 → 中文（豪华版/年度版/终极版/季票等）。"""
    if not en:
        return en
    s = en.strip()
    for full, cn in PKG_TRANSLATE_FULL:
        if full.lower() in s.lower():
            s = re.sub(re.escape(full), cn, s, flags=re.IGNORECASE)
            break
    else:
        for kw, cn in PKG_TRANSLATE_KW.items():
            s = re.sub(re.escape(kw), cn, s, flags=re.IGNORECASE)
    return s.strip()

def fetch_packages(appid):
    """拉取该游戏的版本（packages）价格列表。
    从 appdetails 的 package_groups.subs 解析（option_text 含中文版名+原价+现价）。
    注：packagedetails 接口已被 Valve 限制（400），不依赖它。
    返回 [{packageid, name_en, name_cn, price_initial, price_final, discount, discount_end, has_price}]，
    失败或空则返回 []。价格单位：分。"""
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=cn&l=schinese"
        j = json.loads(http_get(url, timeout=20))
        node = j.get(str(appid), {})
        if node.get("success") is not True:
            return []
        data = node.get("data") or {}
        groups = data.get("package_groups") or []
        po = data.get("price_overview") or {}
        po_end = po.get("discount_expiration")
        versions = []
        seen = set()
        for grp in groups:
            if not isinstance(grp, dict):
                continue
            for sub in (grp.get("subs") or []):
                if not isinstance(sub, dict):
                    continue
                pid = sub.get("packageid")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                cents = sub.get("price_in_cents_with_discount")
                if cents is None:
                    continue
                option = sub.get("option_text") or ""
                # 原价：discount_original_price span 内容（如 ¥ 198.00）
                m_orig = re.search(
                    r'<span class="discount_original_price">\s*[¥￥]\s*([\d,]+\.?\d*)\s*</span>', option)
                initial_cents = 0
                if m_orig:
                    initial_cents = int(round(float(m_orig.group(1).replace(",", "")) * 100))
                # 名称：去 HTML 标签、去价格尾段（"- ¥xx" 或 "¥xx"）
                text = re.sub(r"<[^>]+>", "", option).replace("\u00a0", " ").strip()
                name = re.sub(r"\s*-\s*.*$", "", text).strip()
                name = re.sub(r"\s*[¥￥]\s*[\d.,]+\s*$", "", name).strip()
                if not name or re.match(r"^[¥￥₴$€]", name):  # 订阅类(如 GTA+ 月付)名称兜底
                    name = f"版本 {pid}"
                final_cents = int(cents)
                if initial_cents <= 0:
                    initial_cents = final_cents
                discount = 0
                if initial_cents > final_cents > 0:
                    discount = int(round((1 - final_cents / initial_cents) * 100))
                versions.append({
                    "packageid": pid,
                    "name_en": name,
                    "name_cn": name,  # option_text 在 l=schinese 下已是中文版名
                    "price_initial": initial_cents,
                    "price_final": final_cents,
                    "discount": discount,
                    "discount_end": datetime.datetime.fromtimestamp(po_end, tz=datetime.timezone.utc).isoformat() if po_end else None,
                    "has_price": True,
                })
        versions.sort(key=lambda v: v["price_final"])
        return versions
    except Exception:
        return []

def gen_markdown(report):
    lines = [f"# Steam 每日史低播报（{report['generated_at']}）", ""]
    s = report["summary"]
    lines.append(f"> 汇率：1 乌克兰格里夫纳 ≈ ¥{report['rate_uah_to_cny']} ｜ 今日共 **超史低 {s.get('super_low',0)} 款**、**史低 {s.get('history_low',0)} 款**")
    lines.append("")
    def table(title, games):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| 图标 | 游戏名称(中文) | 公司(中文) | 国服价格¥ | 乌克兰价(¥等值) | 链接 |")
        lines.append("|---|---|---|---|---|---|")
        for g in games:
            icon = f"![{g['name_cn']}]({g['icon_local']})" if g.get("icon_local") else "（无图标）"
            comp = g.get("developer_cn") or ""
            if g.get("publisher_cn") and g["publisher_cn"] != comp:
                comp += f" / {g['publisher_cn']}"
            lines.append(f"| {icon} | {g['name_cn']} | {comp} | ¥{g['cn_price']} | ¥{g['ua_price_cny']} | [Steam]({g['steam_url']}) |")
        lines.append("")
    table("超史低游戏（new historical low）", [g for g in report["games"] if g["tier"] == "super_low"])
    table("史低游戏（all-time / historical low）", [g for g in report["games"] if g["tier"] == "history_low"])
    # steam 模式只有代理分级
    sv = [g for g in report["games"] if g["tier"] in ("super_value", "value")]
    if sv:
        table("超值折扣（Steam 官方，按折扣力度分级）", sv)
    return "\n".join(lines)

# ---------------- web 模式：抓 SteamDB 网页 ----------------
def parse_steamdb_sales(html):
    """从 sales 页解析带史低标记的游戏。返回 [(appid, name_en, tier)]"""
    rows = []
    # 简单按 app 链接切分
    for m in re.finditer(r'steamdb\.info/app/(\d+)/', html):
        appid = m.group(1)
        # 取该游戏附近标签
        seg = html[max(0, m.start()-400): m.start()+200]
        low = seg.lower()
        if "new historical low" in low:
            tier = "super_low"
        elif "all-time low" in low or "historical low" in low:
            tier = "history_low"
        else:
            continue
        name_m = re.search(r'"name"\s*:\s*"([^"]+)"', seg)
        name = name_m.group(1) if name_m else appid
        rows.append((appid, name, tier))
    # 去重
    seen, out = set(), []
    for a, n, t in rows:
        if a not in seen:
            seen.add(a); out.append((a, n, t))
    return out

def parse_steamdb_detail(html):
    """从详情页解析中文名、公司、国服价、乌克兰价。"""
    name_cn = ""
    m = re.search(r'"schinese"\s*:\s*"([^"]+)"', html)
    if m:
        name_cn = m.group(1)
    dev = re.search(r'"developer"\s*:\s*"([^"]+)"', html)
    pub = re.search(r'"publisher"\s*:\s*"([^"]+)"', html)
    dev = dev.group(1) if dev else ""
    pub = pub.group(1) if pub else ""
    # 价格：在 Chinese Yuan / Ukrainian Hryvnia 行附近取金额
    def price_after(token):
        idx = html.find(token)
        if idx < 0:
            return None
        seg = html[idx: idx+400]
        pm = re.search(r'(?:¥|₴|US\$|\$)\s*([\d,]+\.?\d*)', seg)
        if pm:
            return float(pm.group(1).replace(",", ""))
        return None
    cn = price_after("Chinese Yuan")
    ua = price_after("Ukrainian Hryvnia")
    return name_cn, dev, pub, cn, ua

def mode_web(limit, icons_dir):
    print("[web] 抓取 SteamDB sales 页（需过 Cloudflare）...", file=sys.stderr)
    html = http_get("https://steamdb.info/sales/", use_cloud=True)
    pool = parse_steamdb_sales(html)
    print(f"[web] 解析到 {len(pool)} 款史低游戏", file=sys.stderr)
    games, cnt = [], {"super_low": 0, "history_low": 0}
    for appid, name_en, tier in pool[:limit*2]:
        try:
            d = http_get(f"https://steamdb.info/app/{appid}/?l=schinese", use_cloud=True)
            name_cn, dev, pub, cn, ua = parse_steamdb_detail(d)
        except Exception as e:
            print(f"[web] 详情失败 {appid}: {e}", file=sys.stderr)
            name_cn, dev, pub, cn, ua = "", "", "", None, None
        ua_cny = round(ua * rate, 2) if ua else None
        games.append({
            "appid": int(appid), "name_en": name_en, "name_cn": name_cn or name_en,
            "developer": dev, "publisher": pub, "developer_cn": cn_company(dev),
            "publisher_cn": cn_company(pub), "tier": tier,
            "cn_price": cn, "ua_price_uah": ua, "ua_price_cny": ua_cny,
            "icon_local": download_icon(appid, icons_dir),
            "steam_url": STEAM_STORE.format(appid),
            "versions": fetch_packages(int(appid)),
        })
        cnt[tier] = cnt.get(tier, 0) + 1
    return games, cnt

# ---------------- steam 模式：官方 Store API ----------------
def mode_steam(limit, icons_dir):
    print("[steam] 调用 Steam 官方 Store API（无史低标记，按折扣力度分级）...", file=sys.stderr)
    # 特惠列表
    url = "https://store.steampowered.com/api/featuredcategories?cc=cn&l=schinese"
    data = json.loads(http_get(url))
    specials = data.get("specials", {}).get("items", [])[:limit]
    games, cnt = [], {"super_low": 0, "history_low": 0}
    for it in specials:
        appid = it.get("id")
        name_cn = it.get("name", "")
        disc = it.get("discount_pct", 0)
        final = it.get("final", 0) / 100.0 if it.get("final") else None
        tier = "super_value" if disc >= 50 else "value"
        # 用 Steam 官方 API 取权威中文名（覆盖 featuredcategories 的英文默认名）
        cn_api = cn_name_from_steam(appid)
        if cn_api:
            name_cn = cn_api
        # 乌克兰价
        ua = None
        try:
            ua_data = json.loads(http_get(f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=ua&l=schinese&filters=price_overview"))
            node = ua_data.get(str(appid), {}).get("data", {}).get("price_overview", {})
            if node.get("final"):
                ua = node["final"] / 100.0
        except Exception:
            pass
        # 开发/发行
        dev = pub = ""
        try:
            d_data = json.loads(http_get(f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=cn&l=schinese&filters=basic"))
            node = d_data.get(str(appid), {}).get("data", {})
            dev = node.get("developers", [""])[0]
            pub = node.get("publishers", [""])[0]
        except Exception:
            pass
        ua_cny = round(ua * rate, 2) if ua else None
        games.append({
            "appid": appid, "name_en": name_cn, "name_cn": name_cn,
            "developer": dev, "publisher": pub, "developer_cn": cn_company(dev),
            "publisher_cn": cn_company(pub), "tier": tier,
            "cn_price": final, "ua_price_uah": ua, "ua_price_cny": ua_cny,
            "icon_local": download_icon(appid, icons_dir),
            "steam_url": STEAM_STORE.format(appid),
            "versions": fetch_packages(appid),
        })
        cnt[tier] = cnt.get(tier, 0) + 1
    return games, cnt

# ---------------- demo 模式 ----------------
def mode_demo(icons_dir):
    """内置今天实测的真实样例，验证全链路。"""
    raw = [
        (1551360, "Forza Horizon 5", "极限竞速：地平线 5", "Playground Games", "Xbox Game Studios", "super_low", 79.20, 479.0),
        (2486820, "Sonic Racing: CrossWorlds", "索尼克赛车 交叉世界", "Sonic Team", "SEGA", "super_low", 119.20, 959.0),
        (704850, "Thief Simulator", "神偷模拟器", "Noble Muffins", "PlayWay", "super_low", 6.84, 37.0),
        (381210, "Dead by Daylight", "黎明杀机", "Behaviour Interactive", "Behaviour Interactive", "history_low", 37.50, 171.0),
        (601150, "Devil May Cry 5", "鬼泣5", "CAPCOM", "CAPCOM", "history_low", 37.00, 237.0),
        (391220, "Rise of the Tomb Raider", "古墓丽影：崛起", "Crystal Dynamics", "SQUARE ENIX", "history_low", 22.05, 82.0),
    ]
    games, cnt = [], {"super_low": 0, "history_low": 0}
    for appid, en, cn, dev, pub, tier, cn_p, ua_p in raw:
        ua_cny = round(ua_p * rate, 2)
        games.append({
            "appid": appid, "name_en": en, "name_cn": cn,
            "developer": dev, "publisher": pub, "developer_cn": cn_company(dev),
            "publisher_cn": cn_company(pub), "tier": tier,
            "cn_price": cn_p, "ua_price_uah": ua_p, "ua_price_cny": ua_cny,
            "icon_local": download_icon(appid, icons_dir),
            "steam_url": STEAM_STORE.format(appid),
        })
        cnt[tier] = cnt.get(tier, 0) + 1
    return games, cnt

# ---------------- 主流程 ----------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Steam 每日史低播报 (App 集成脚本)")
    ap.add_argument("--mode", choices=["web", "steam", "demo"], default="demo")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--out", choices=["json", "md", "both"], default="both")
    ap.add_argument("--json-path", default="steam_lowest.json")
    ap.add_argument("--md-path", default="steam_lowest.md")
    ap.add_argument("--icons-dir", default="./icons")
    ap.add_argument("--no-icons", action="store_true")
    ap.add_argument("--rate", type=float, default=None, help="覆盖汇率(1 UAH=CNY)")
    args = ap.parse_args()

    rate = args.rate if args.rate else fetch_rate()
    print(f"[info] 汇率 1 UAH = ¥{rate}", file=sys.stderr)
    icons_dir = None if args.no_icons else args.icons_dir

    if args.mode == "web":
        games, cnt = mode_web(args.limit, icons_dir)
    elif args.mode == "steam":
        games, cnt = mode_steam(args.limit, icons_dir)
    else:
        games, cnt = mode_demo(icons_dir)

    report = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "rate_uah_to_cny": rate,
        "summary": cnt,
        "games": games,
    }
    if args.out in ("json", "both"):
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[ok] 写入 JSON: {args.json_path}", file=sys.stderr)
    if args.out in ("md", "both"):
        with open(args.md_path, "w", encoding="utf-8") as f:
            f.write(gen_markdown(report))
        print(f"[ok] 写入 MD:   {args.md_path}", file=sys.stderr)
    if args.out == "json":
        print(json.dumps(report, ensure_ascii=False))
