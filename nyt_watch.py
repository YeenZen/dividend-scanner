#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
เฝ้าประกาศที่ NYT แจ้งตลาดหลักทรัพย์ แล้วส่งอีเมลทันทีที่มีฉบับใหม่

ทำไมต้องเฝ้าตัวนี้เป็นพิเศษ:
    สัญญาสัมปทานท่าเทียบเรือ A5 ซึ่งเป็นรายได้ ~80% ของ NYT สิ้นสุดไปแล้ว
    30 เม.ย. 2569 ปัจจุบันบริษัทเดินเครื่อง "ชั่วคราว" โดยยังไม่มีสัญญาใหม่
    และ กทท. ยังไม่เปิดประมูล (ประกาศ นยล 103/2569 ลงวันที่ 20 เม.ย. 2569)
    ข่าวว่าเซ็นสัญญาใหม่หรือเปิดประมูลจะขยับราคาแรงทั้งขึ้นและลง
    จึงคุ้มที่จะรู้ภายในชั่วโมงแทนที่จะรู้ตอนเช้าวันรุ่งขึ้น

แหล่งข้อมูล: RSS ของ listedcompany ซึ่งดึงได้ด้วย HTTP ธรรมดา ไม่ต้องมี browser
สถานะ (id ที่เคยเห็นแล้ว) เก็บใน state/nyt_seen.json แล้ว commit กลับเข้า repo

รันเอง: python nyt_watch.py --dry-run
"""

import html
import io
import json
import os
import re
import sys
import urllib.request

from alert import UA, send_email, thai_date      # ใช้ตัวส่งอีเมลร่วมกับระบบหลัก

FEED = "https://nyt.listedcompany.com/newsroom_rss.html"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "nyt_seen.json")

# คำที่ถ้าโผล่ในหัวข้อ แปลว่าเป็นข่าวสัมปทานที่รออยู่ ไม่ใช่ประกาศทั่วไป
HOT = ["สัมปทาน", "ท่าเทียบเรือ", "เอ 5", "เอ5", "ร่วมลงทุน", "ประมูล",
       "การท่าเรือ", "กทท", "สัญญา",
       "concession", "terminal", "joint investment", "bidding", "port authority"]


def fetch_items():
    """คืนรายการประกาศจาก RSS ใหม่→เก่า แต่ละตัวเป็น dict(id, title, date, link)"""
    req = urllib.request.Request(FEED, headers={"user-agent": UA})
    xml = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def grab(tag):
            m = re.search(r"<%s>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>" % (tag, tag),
                          block, re.S)
            return html.unescape(m.group(1).strip()) if m else ""
        link = grab("link")
        m = re.search(r"/id/(\d+)", link)
        if not m:
            continue
        out.append({"id": m.group(1), "title": grab("title"),
                    "date": grab("pubDate"), "link": link})
    return out


def is_hot(title):
    t = title.lower()
    return any(k.lower() in t for k in HOT)


def load_seen():
    try:
        with io.open(STATE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return None          # None = ยังไม่เคยมีสถานะ (รันครั้งแรก)


def save_seen(ids):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with io.open(STATE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=1)


def render(new_items):
    """อีเมล HTML — ข่าวสัมปทานขึ้นก่อนและเน้นสีแดง"""
    hot = [r for r in new_items if is_hot(r["title"])]
    body = ['<div style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
            'max-width:760px;margin:0 auto;padding:16px;color:#1f2328">']
    body.append('<h2 style="margin:0 0 4px">ประกาศใหม่จาก NYT</h2>')
    body.append('<p style="margin:0 0 18px;color:#656d76;font-size:14px">%s · พบ %d ฉบับ</p>'
                % (thai_date(), len(new_items)))

    if hot:
        body.append('<div style="margin:0 0 18px;padding:12px 14px;background:#ffebe9;'
                    'border-left:4px solid #cf222e;border-radius:6px">'
                    '<b>เกี่ยวกับสัมปทาน/ท่าเทียบเรือ — อ่านก่อน</b>'
                    '<p style="margin:6px 0;font-size:13px">สัญญา A5 (รายได้ ~80% ของบริษัท) '
                    'สิ้นสุดแล้ว 30 เม.ย. 2569 ปัจจุบันเดินเครื่องชั่วคราวโดยยังไม่มีสัญญาใหม่</p><ul>')
        body += ['<li><a href="%s">%s</a> <span style="color:#656d76">(%s)</span></li>'
                 % (r["link"], html.escape(r["title"]), r["date"]) for r in hot]
        body.append("</ul></div>")

    rest = [r for r in new_items if r not in hot]
    if rest:
        body.append('<h3 style="margin:16px 0 8px;font-size:15px;color:#656d76">'
                    'ประกาศอื่น</h3><ul style="font-size:14px">')
        body += ['<li><a href="%s">%s</a> <span style="color:#656d76">(%s)</span></li>'
                 % (r["link"], html.escape(r["title"]), r["date"]) for r in rest]
        body.append("</ul>")

    body.append('<p style="margin:24px 0 0;font-size:12px;color:#8c959f">'
                'เฝ้าจาก RSS ของ listedcompany ทุกชั่วโมง 07:00-20:00 น. จันทร์-ศุกร์</p></div>')
    return "".join(body)


def main():
    dry = "--dry-run" in sys.argv
    items = fetch_items()
    if not items:
        raise RuntimeError("ดึง RSS ไม่ได้หรือ feed ว่าง — โครงสร้างอาจเปลี่ยน")

    seen = load_seen()
    if seen is None:
        # รันครั้งแรก: จำของเดิมทั้งหมดไว้เฉย ๆ ไม่ส่งอีเมลย้อนหลัง
        print("ครั้งแรก — บันทึก %d รายการเดิมไว้ ไม่ส่งอีเมล" % len(items))
        for r in items[:5]:
            print("   %s | %s" % (r["date"][:20], r["title"][:70]))
        if not dry:
            save_seen({r["id"] for r in items})
        return 0

    new = [r for r in items if r["id"] not in seen]
    print("ใน feed %d รายการ | เคยเห็นแล้ว %d | ใหม่ %d" % (len(items), len(seen), len(new)))
    for r in new:
        print("   %s %s | %s" % ("🔴" if is_hot(r["title"]) else "  ",
                                 r["date"][:20], r["title"][:70]))
    if not new:
        return 0

    hot = any(is_hot(r["title"]) for r in new)
    subject = ("🔴 NYT ประกาศเรื่องสัมปทาน/ท่าเทียบเรือ — %s" % thai_date() if hot
               else "NYT มีประกาศใหม่ %d ฉบับ — %s" % (len(new), thai_date()))
    if dry:
        print("\n[dry-run] ไม่ส่งอีเมล | หัวข้อที่จะใช้: %s" % subject)
        return 0
    send_email(subject, render(new))
    save_seen(seen | {r["id"] for r in items})
    return 0


if __name__ == "__main__":
    sys.exit(main())
