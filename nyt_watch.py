#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
เฝ้าประกาศที่ NYT แจ้งตลาดหลักทรัพย์ แล้วเขียนลงไฟล์ให้รายงานเช้าในแอป Claude อ่าน

ทำไมต้องเฝ้าตัวนี้เป็นพิเศษ:
    สัญญาสัมปทานท่าเทียบเรือ A5 ซึ่งเป็นรายได้ ~80% ของ NYT สิ้นสุดไปแล้ว
    30 เม.ย. 2569 ปัจจุบันบริษัทเดินเครื่อง "ชั่วคราว" โดยยังไม่มีสัญญาใหม่
    และ กทท. ยังไม่เปิดประมูล (ประกาศ นยล 103/2569 ลงวันที่ 20 เม.ย. 2569)
    ข่าวว่าเซ็นสัญญาใหม่หรือเปิดประมูลจะขยับราคาแรงทั้งขึ้นและลง
    จึงคุ้มที่จะรู้ภายในชั่วโมงแทนที่จะรู้ตอนเช้าวันรุ่งขึ้น

แหล่งข้อมูล: หน้าข่าวของ settrade ซึ่งดึงได้ด้วย HTTP ธรรมดา ไม่ต้องมี browser
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

from alert import UA, thai_date      # ใช้ค่า user-agent และวันที่ไทยร่วมกับระบบหลัก

# ใช้ settrade ไม่ใช่ listedcompany เพราะ listedcompany ตอบ HTTP 202 เนื้อหาว่าง
# ให้ IP นอกประเทศไทย (ทดสอบบน GitHub runner แล้ว) ส่วน settrade ตอบเต็มทั้งสองที่
FEED = "https://www.settrade.com/th/equities/quote/NYT/news"

Q = chr(34)    # อัญประกาศคู่ — เลี่ยงพิมพ์ตรง ๆ ให้โค้ดอ่านง่าย
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "nyt_seen.json")

# คำที่ถ้าโผล่ในหัวข้อ แปลว่าเป็นข่าวสัมปทานที่รออยู่ ไม่ใช่ประกาศทั่วไป
HOT = ["สัมปทาน", "ท่าเทียบเรือ", "เอ 5", "เอ5", "ร่วมลงทุน", "ประมูล",
       "การท่าเรือ", "กทท", "สัญญา",
       "concession", "terminal", "joint investment", "bidding", "port authority"]


def fetch_items():
    """
    คืนรายการประกาศที่ NYT แจ้งตลาดหลักทรัพย์ เรียงใหม่ไปเก่า

    ดึงจากหน้าข่าวของ settrade ซึ่งเป็น Nuxt SSR ฝังข้อมูลมาใน HTML อยู่แล้ว
    หัวข้อกับวันที่อยู่ใต้คีย์ dataNewsSET เป็นข้อความตรง ๆ ไม่ต้องถอดตารางตัวแปร
    แบบหน้าราคา จึงแกะด้วยการตัดสตริงธรรมดาได้
    """
    req = urllib.request.Request(FEED, headers={"user-agent": UA})
    text = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")

    k = text.find("dataNewsSET")
    if k < 0:
        raise RuntimeError("ไม่พบ dataNewsSET ในหน้า settrade — โครงสร้างเว็บอาจเปลี่ยน")
    block = text[k:k + 400000]

    # settrade หนี escape บางตัวมาในรูป uXXXX ของ JS ต้องแปลงกลับให้อ่านออก
    ESC = ((chr(92) + "u002F", "/"), (chr(92) + "u0026", "&"),
           (chr(92) + "u003C", "<"), (chr(92) + "u003E", ">"))

    out, got = [], set()
    for chunk in block.split("uuid:" + Q)[1:]:
        uid = chunk.split(Q, 1)[0]
        if not uid.isdigit() or uid in got or ("title:" + Q) not in chunk:
            continue
        title = chunk.split("title:" + Q, 1)[1].split(Q, 1)[0]
        if not title.strip():
            continue
        for esc, real in ESC:
            title = title.replace(esc, real)
        pub = ""
        if ("publishDate:" + Q) in chunk:
            pub = chunk.split("publishDate:" + Q, 1)[1].split(Q, 1)[0]
        got.add(uid)
        out.append({"id": uid, "title": html.unescape(title),
                    "date": pub[:16].replace("T", " "),
                    "link": FEED + "?newsId=" + uid})
    return out

ANNOUNCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "nyt", "announcements.md")


def now_th():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M")


def write_announcements(items):
    """
    เขียนรายการประกาศล่าสุดลงไฟล์ให้ routine ใน Claude อ่าน

    ต้องผ่านไฟล์เพราะ sandbox ของ cloud routine บล็อกเน็ตขาออก ดึง RSS เองไม่ได้
    เหมือนที่บล็อก settrade.com — ฝั่ง GitHub ซึ่งเน็ตเปิดจึงเป็นคนดึงให้
    """
    os.makedirs(os.path.dirname(ANNOUNCE), exist_ok=True)
    out = ["# ประกาศของ NYT ที่แจ้งตลาดหลักทรัพย์", "",
           "ดึงล่าสุด %s (เวลาไทย) จากหน้าข่าวของ settrade" % now_th(), "",
           "🔴 = เกี่ยวกับสัมปทาน/ท่าเทียบเรือ/ประมูล ซึ่งเป็นเรื่องที่รออยู่", "",
           "| วันที่ประกาศ | หัวข้อ | ลิงก์ |", "|---|---|---|"]
    for r in items:
        mark = "🔴 " if is_hot(r["title"]) else ""
        out.append("| %s | %s%s | %s |" % (r["date"], mark, r["title"], r["link"]))
    out.append("")
    with io.open(ANNOUNCE, "w", encoding="utf-8") as f:
        f.write(chr(10).join(out))


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
                'เฝ้าจากหน้าข่าวของ settrade ทุกชั่วโมง 07:00-20:00 น. จันทร์-ศุกร์</p></div>')
    return "".join(body)


def main():
    """
    ตรวจประกาศใหม่แล้วเขียนลงไฟล์

    ไม่ส่งอีเมลและไม่เปิด issue แล้ว เพราะทดสอบแล้วทั้งสองทางไม่เวิร์ก
    (GitHub ไม่แจ้งเตือนเรื่องที่เจ้าของ repo เป็นคนเปิดเอง และผู้ใช้ไม่เอาอีเมล)
    ช่องทางเดียวที่ใช้จริงคือรายงานเช้าในแอป Claude ซึ่งอ่านไฟล์นี้
    """
    dry = "--dry-run" in sys.argv
    items = fetch_items()
    if not items:
        raise RuntimeError("ดึงหน้าข่าว settrade ไม่ได้หรือไม่มีรายการ — โครงสร้างอาจเปลี่ยน")

    write_announcements(items)

    seen = load_seen() or set()
    new = [r for r in items if r["id"] not in seen]
    print("ในหน้าข่าว %d รายการ | เคยเห็นแล้ว %d | ใหม่ %d"
          % (len(items), len(seen), len(new)))
    for r in new:
        print("   %s %s | %s" % ("[สัมปทาน]" if is_hot(r["title"]) else "         ",
                                 r["date"], r["title"][:70]))
    if new and any(is_hot(r["title"]) for r in new):
        print("*** มีประกาศเรื่องสัมปทาน/ท่าเทียบเรือ — รายงานเช้าจะชูเรื่องนี้ขึ้นก่อน ***")
    if not dry:
        save_seen({r["id"] for r in items})
    return 0

if __name__ == "__main__":
    sys.exit(main())
