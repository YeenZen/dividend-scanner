# -*- coding: utf-8 -*-
"""ทดสอบว่าแหล่งข่าวไหนที่ IP ต่างประเทศ (GitHub runner) ดึงได้บ้าง

listedcompany.com ตอบ HTTP 202 เนื้อหาว่างให้ IP นอกไทย ต้องหาทางอื่น
สคริปต์นี้ยิงหลายแหล่งแล้วรายงานว่าอันไหนให้ข้อมูลจริง
"""
import re, urllib.request, urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HDRS = {"user-agent": UA,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "th,en-US;q=0.9,en;q=0.8"}

SOURCES = [
    ("listedcompany RSS",      "https://nyt.listedcompany.com/newsroom_rss.html"),
    ("namyong IR RSS",         "https://investor-th.namyongterminal.com/newsroom_rss.html"),
    ("namyong IR หน้าข่าว",     "https://investor-th.namyongterminal.com/newsroom_set_th.html"),
    ("เว็บบริษัท",              "https://www.namyongterminal.com/media/news"),
    ("settrade หน้าข่าว",       "https://www.settrade.com/th/equities/quote/NYT/news"),
    ("settrade overview",      "https://www.settrade.com/th/equities/quote/NYT/overview"),
    ("SET api news",           "https://www.set.or.th/api/set/news/search?symbol=NYT&lang=th"),
    ("Google News RSS",        "https://news.google.com/rss/search?q=%22%E0%B8%99%E0%B8%B2%E0%B8%A1%E0%B8%A2%E0%B8%87+%E0%B9%80%E0%B8%97%E0%B8%AD%E0%B8%A3%E0%B9%8C%E0%B8%A1%E0%B8%B4%E0%B8%99%E0%B8%B1%E0%B8%A5%22&hl=th&gl=TH&ceid=TH:th"),
    ("jina proxy -> RSS",      "https://r.jina.ai/https://nyt.listedcompany.com/newsroom_rss.html"),
]

print("%-24s %-6s %-9s %-7s %s" % ("แหล่ง", "HTTP", "ขนาด", "มีข่าว", "หมายเหตุ"))
print("-" * 78)
for name, url in SOURCES:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=HDRS), timeout=45)
        body = r.read().decode("utf-8", "replace")
        code, size = r.status, len(body)
        # นับว่ามีข่าวจริงไหม: <item> ของ RSS หรือคำที่ต้องมีในหน้าข่าว
        items = len(re.findall(r"<item[ >]", body))
        hits = items or len(re.findall(r"แจ้งวันหยุด|สัมปทาน|ประชุมสามัญ|ไตรมาส", body))
        note = "✅ ใช้ได้" if hits else ("ว่างเปล่า" if size < 500 else "ไม่พบรายการข่าว")
        print("%-24s %-6s %-9s %-7s %s" % (name, code, "%d" % size, hits, note))
    except urllib.error.HTTPError as e:
        print("%-24s %-6s %-9s %-7s %s" % (name, e.code, "-", "-", "HTTPError"))
    except Exception as e:
        print("%-24s %-6s %-9s %-7s %s" % (name, "-", "-", "-", type(e).__name__))
