#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
แจ้งเตือนหุ้นปันผลไทยที่ราคาตกจนใกล้ได้ yield 10% — ส่งเข้าอีเมล

รันอัตโนมัติทุกเช้าด้วย GitHub Actions (ดู .github/workflows/daily-alert.yml)

ทำไมใช้ settrade.com ไม่ใช่ set.or.th:
    set.or.th/api อยู่หลัง Imperva ยิงด้วย urllib/curl ได้ 403 เสมอ ต้องเปิด browser จริง
    แต่หน้า quote ของ settrade.com เป็น Nuxt SSR ที่ฝังข้อมูลไว้ใน HTML มาเลย
    จึงดึงได้ด้วย urllib เปล่า ๆ ไม่ต้องมี browser — สำคัญมากเพราะรันบน CI runner

สูตรราคาเป้า (ไม่พึ่ง DPS):
    ราคาเป้า yield 10% = ราคาล่าสุด x yield ปัจจุบัน / 10
    อัพเดตตัวเองเมื่อบริษัทเปลี่ยนปันผล ไม่ต้องแก้ตารางราคาเป้าด้วยมือทุกปี
    (ค่า dividend ในหน้าเว็บเชื่อไม่ได้ — บางตัวให้มาแค่งวดเดียวไม่ใช่ทั้งปี)

รันเองในเครื่อง:
    python alert.py --dry-run     พิมพ์ผลออกจอ ไม่ส่งอีเมล
"""

import os
import re
import smtplib
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

# ── เกณฑ์ ───────────────────────────────────────────────────────────────────────
TARGET_YIELD = 10.0   # เป้าหมายที่ต้องการ (%)
ALERT_YIELD = 8.0     # ต่ำกว่านี้ไม่ต้องส่งอีเมล

# ขาด user-agent ของ browser จริงไม่ได้ — Incapsula จะตอบ 403 ทันที
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BS = chr(92)   # backslash
QT = chr(34)   # double quote

TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
             "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

# หุ้นที่ตรวจแบบฟอร์ม 56-1 ย้อนหลัง 5 ปีแล้วว่าผ่านเกณฑ์ payout ratio + moat
# (ticker, หมายเหตุสั้น ๆ ที่จะแสดงคู่กับราคาในอีเมล)
WATCHLIST = [
    # ── พอร์ตหลัก ────────────────────────────────────────────────────────────
    ("ALUCON",   "หลอด/ภาชนะอลูมิเนียมเฉพาะทาง เบอร์ 1 เอเชีย"),
    ("TTW",      "ผลิตน้ำประปาให้ กปภ. สัญญา BOO ถึงปี 2577"),
    ("DIF",      "กองทุนเสาสัญญาณ True — DPU ลดต่อเนื่อง 4 ปี ระวัง"),
    ("HTC",      "bottler Coca-Cola ภาคใต้ ครองตลาด 78.3%"),
    ("PTTEP",    "สัมปทานปิโตรเลียมรัฐ ปริมาณสำรองเพิ่มขึ้น"),
    ("SABINA",   "ชุดชั้นใน — payout 99.8-100.3% จ่ายจากกำไรสะสม"),
    ("SCB",      "ธนาคารใหญ่อันดับ 4 กำไร/DPS โตต่อเนื่อง"),
    ("KTB",      "ธนาคารรัฐ กำไรโตต่อเนื่อง 5 ปีติด ไม่สะดุด"),
    ("ICHI",     "ชาเขียว 27.1% ตามหลังโออิชิ พึ่ง DKSH 81%"),
    ("TU",       "ทูน่ากระป๋อง แบรนด์ระดับโลก แต่แข่งขันสูง"),
    ("PROSPECT", "REIT คลังสินค้าบางนา occupancy 99.4%"),
    ("SAT",      "ชิ้นส่วนรถ — EV กระทบจริงแล้ว รายได้ core -28%"),
    ("NYT",      "ท่าเรือส่งออกรถยนต์ ~80% สัมปทานรอเซ็นใหม่"),
    ("BA",       "สายการบินเดียวที่บินตราด/สุโขทัย เจ้าของสนามบินเอง"),
    ("GULF",     "IPP 61% ของตลาด ควบรวม INTUCH แล้ว"),
    ("AMATA",    "นิคมอุตสาหกรรม — moat แคบกว่าที่เคยคิด"),
    ("SCCC",     "ปูนซีเมนต์นครหลวง ตลาดเวียดนาม 23-28%"),
    ("SCC",      "ปูนซีเมนต์ไทย — moat ปกป้องได้แค่ 16% ของรายได้"),
    ("PTT",      "รัฐถือ 51% ผูกขาดท่อก๊าซ/คลังน้ำมัน"),
    ("BOL",      "ข้อมูลเครดิตธุรกิจ moat 4 ชั้นซ้อน แข็งสุดในพอร์ต"),
    # ── ผ่านรอบผู้สมัครใหม่ ───────────────────────────────────────────────────
    ("TISCO",    "กองทุนสำรองเลี้ยงชีพเบอร์ 1 (18.4% share)"),
    ("KKP",      "โบรกเกอร์เบอร์ 1 (22.18% SET+mai)"),
    ("PRM",      "เรือขนส่งน้ำมัน 26.7% ของกองเรือไทย"),
    ("SMPC",     "ถังแก๊ส LPG top 3 โลก เบอร์ 1 ในไทย"),
    ("HMPRO",    "Home Center 32.4% + ทีมช่าง 3,032 ทีม"),
    ("KBANK",    "กองทุนรวมเบอร์ 1 22.6% + K PLUS 24.2 ล้านราย"),
    ("BBL",      "ธนาคารใหญ่สุดในไทย payout ต่ำ 31-43%"),
    ("YUASA",    "แบตเตอรี่มอเตอร์ไซค์ OEM 65% ในไทย"),
    ("CSC",      "ฝาโลหะ >60% ผู้ถือหุ้นใหญ่เป็นลูกค้าด้วย"),
    # ── รอราคาย่อ (ผ่าน moat แต่ yield ยังไม่ถึงเกณฑ์) ────────────────────────
    ("SIS",      "IT distributor อันดับ 2 — รอราคาย่อ"),
    ("MTI",      "ประกันวินาศภัย 6.63% อันดับ 6 — รอราคาย่อ"),
    ("AOT",      "ผูกขาดสนามบินหลัก รัฐถือ 70% — yield ต่ำมาก"),
    ("CPALL",    "7-Eleven ผูกขาดร้านสะดวกซื้อ — yield ต่ำมาก"),
]

DISCLAIMER = (
    "yield สูงอาจมาจากปันผลพิเศษครั้งเดียว หรือราคาร่วงเพราะข่าวร้าย "
    "(value trap แบบ SPCG ที่ payout 770%) — เช็คสาเหตุก่อนซื้อทุกครั้ง "
    "ตัวเลข yield เป็นค่าย้อนหลัง 12 เดือน ไม่ใช่การรับประกันปันผลอนาคต"
)

# กองทุน/ทรัสต์ที่สินทรัพย์บางส่วนมีวันหมดอายุ — yield อ่านแบบเดียวกับหุ้นไม่ได้
#
# หุ้นปกติจ่ายปันผลจากกำไรและยังถือกิจการไว้ตลอด แต่กองทุนพวกนี้จ่ายเงินสดที่มี
# "เงินต้นของเราเอง" ปนอยู่ เพราะมูลค่าหน่วยลงทุนจะลดลงเมื่อสินทรัพย์ทยอยหมดอายุ
# แตะ yield 10% จึงไม่ได้แปลว่าได้ผลตอบแทน 10% ต้องคิดเป็น IRR ถึงวันหมดสัญญาแทน
#
#   ticker: (ปี พ.ศ. ที่สินทรัพย์หลักเริ่มหมด, สิ่งที่หายไป)
LIMITED_LIFE = {
    "DIF": (2576, "สิทธิการเช่าเสา 39% + ไฟเบอร์ 55% หมด และหมดประกันรายได้ True "
                  "15 ก.ย. 2576 — ส่วนที่เหลือต้องหาผู้เช่าเองในตลาดที่ลดเสาซ้ำซ้อน"),
    "PROSPECT": (2582, "BFTZ1 หมด 2582, BFTZ2 หมด 2593, BFTZ6 หมด 2595-2597 "
                       "(BFTZ3 กับ X44 เป็นกรรมสิทธิ์ ไม่หมดอายุ)"),
}

RE_NAMES = re.compile(r"function[(]([^)]*)[)]")
# จับทั้ง prior (ราคาปิดก่อนหน้า) และ last (ราคาล่าสุดของ session ปัจจุบัน)
# ต้องมี prior เพราะก่อนตลาดเปิด Settrade จะรีเซ็ต last เป็น null ทุกวันทำการ
RE_PRICES = re.compile(
    r"info:[{]symbol:[A-Za-z_$]+,sign:[A-Za-z_$]+"
    r",prior:([A-Za-z_$]+|[0-9.]+),last:([A-Za-z_$]+|[0-9.]+)")
RE_YIELD = re.compile(r"highlightData:[{][^}]*?dividendYield:([A-Za-z_$]+)")
# yield ย้อนหลัง 12 เดือนที่ SET คำนวณเอง — ใช้ตัวนี้ก่อนเสมอ
RE_YIELD_12M = re.compile(r"dividendYield12M:([A-Za-z_$]+|[0-9.]+)")


# ── ดึงข้อมูล ───────────────────────────────────────────────────────────────────

def _split_args(tail):
    """
    แยกค่าที่ส่งเข้า IIFE ของ Nuxt ออกเป็นรายการ

    ใช้ .split(",") ตรง ๆ ไม่ได้ เพราะค่าที่เป็น string มี comma อยู่ข้างใน
    จึงต้องไล่ทีละตัวอักษร ข้าม comma ที่อยู่ใน string หรือในวงเล็บซ้อน
    """
    vals, buf = [], ""
    in_str = esc = False
    depth = 0
    for ch in tail:
        if esc:
            buf += ch
            esc = False
            continue
        if ch == BS:
            buf += ch
            esc = True
            continue
        if ch == QT:
            in_str = not in_str
            buf += ch
            continue
        if not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break          # ปิดวงเล็บของ IIFE แล้ว จบ
                depth -= 1
            elif ch == "," and depth == 0:
                vals.append(buf)
                buf = ""
                continue
        buf += ch
    vals.append(buf)
    return vals


def fetch_quote(symbol):
    """
    คืน (ราคาล่าสุด, yield %, ที่มาของ yield) — โยน exception พร้อมเหตุผลถ้าดึงไม่ได้

    ใช้ dividendYield12M ก่อนเสมอ เพราะ dividendYield ธรรมดาเพี้ยนกับหุ้นที่จ่าย
    ปันผลมากกว่าปีละครั้ง — มันหยิบมาไม่ครบทุกงวด ตรวจเทียบ 33 ตัวแล้วเพี้ยน 7 ตัว
    หนักสุดคือ PRM (5.24% vs 7.33%) และ PROSPECT (7.30% vs 8.98%) ซึ่งมากพอ
    จะทำให้พลาดการแจ้งเตือนไปทั้งตัว
    """
    url = "https://www.settrade.com/th/equities/quote/%s/overview" % symbol
    req = urllib.request.Request(url, headers={"user-agent": UA})
    html = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")

    i = html.find("__NUXT__")
    if i < 0:
        raise ValueError("ไม่พบ __NUXT__ ในหน้าเว็บ (โครงสร้างเว็บอาจเปลี่ยน)")

    m_names = RE_NAMES.search(html, i, i + 4000)
    if not m_names:
        raise ValueError("อ่านรายชื่อพารามิเตอร์ของ IIFE ไม่ได้")
    names = m_names.group(1).split(",")

    vals = _split_args(html[html.rfind("}(") + 2:])
    if len(names) != len(vals):
        raise ValueError("จำนวนตัวแปรไม่ตรงกับค่า (%d vs %d)" % (len(names), len(vals)))
    env = dict(zip(names, vals))

    def resolve(tok):
        """ค่าใน payload เป็นได้ทั้งตัวเลขตรง ๆ และชื่อตัวแปรที่ต้องเปิดตาราง"""
        if tok is None:
            return None
        v = tok if re.fullmatch(r"[0-9.]+", tok) else env.get(tok)
        return None if v in (None, "a", "null") else v

    def lookup(match, group=1):
        return resolve(match.group(group)) if match else None

    m_price = RE_PRICES.search(html)
    if not m_price:
        raise ValueError("หาตัวแปรราคาไม่เจอ")

    # ก่อนตลาดเปิด last เป็น null ทุกวันทำการ ต้องถอยไปใช้ราคาปิดก่อนหน้า
    # ไม่งั้นรอบ 07:00 จะพังทุกเช้า (ตลาดไทยเปิด 10:00)
    last = lookup(m_price, 2)
    price_kind = "ล่าสุด"
    if last is None:
        last = lookup(m_price, 1)
        price_kind = "ปิดก่อนหน้า"
    if last is None:
        raise ValueError("ไม่มีทั้งราคาล่าสุดและราคาปิดก่อนหน้า")

    dy = lookup(RE_YIELD_12M.search(html))
    source = "12M"
    if dy is None:
        dy = lookup(RE_YIELD.search(html))
        source = "ปกติ"
    if dy is None:
        raise ValueError("ไม่พบค่า dividendYield ทั้งสองแบบ (หุ้นอาจไม่มีข้อมูลปันผล)")
    return float(last), float(dy), source, price_kind


def scan(watchlist):
    """ดึงทุกตัวแบบขนาน คืน (ผลสำเร็จ, ผลที่พัง)"""
    def one(item):
        sym, note = item
        try:
            last, dy, source, price_kind = fetch_quote(sym)
            target = last * dy / TARGET_YIELD          # ราคาที่ทำให้ yield = 10%
            drop = (1 - target / last) * 100 if last else 0
            status = ("green" if dy >= TARGET_YIELD
                      else "yellow" if dy >= ALERT_YIELD
                      else "grey")
            return {"ticker": sym, "note": note, "last": last, "yield": dy,
                    "target": target, "drop_pct": drop, "status": status,
                    "source": source, "price_kind": price_kind}
        except Exception as exc:
            return {"ticker": sym, "note": note,
                    "error": "%s: %s" % (type(exc).__name__, exc)}

    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(one, watchlist))
    return ([r for r in rows if "error" not in r],
            [r for r in rows if "error" in r])


# ── แสดงผล ──────────────────────────────────────────────────────────────────────

def thai_date():
    """วันที่แบบไทย เช่น '19 ก.ย. 2569' — คำนวณตามเวลาไทยไม่ใช่ UTC ของ runner"""
    now = datetime.now(timezone(timedelta(hours=7)))
    return "%d %s %d" % (now.day, TH_MONTHS[now.month - 1], now.year + 543)


def _gap_text(r):
    """ตัวที่ราคาต่ำกว่าเป้าแล้ว ตัวเลข 'ต้องตกอีก' จะติดลบ อ่านแล้วสับสน"""
    if r["drop_pct"] < 0:
        return "ถึงแล้ว (ต่ำกว่าเป้า %.1f%%)" % -r["drop_pct"]
    return "%.1f%%" % r["drop_pct"]


def _life_text(ticker):
    """คืนข้อความอายุคงเหลือของกองทุนอายุจำกัด คืนค่าว่างถ้าเป็นหุ้นปกติ"""
    if ticker not in LIMITED_LIFE:
        return ""
    end_be, _ = LIMITED_LIFE[ticker]
    now_be = datetime.now(timezone(timedelta(hours=7))).year + 543
    return "⏳ เหลือ %d ปี (ถึง %d)" % (end_be - now_be, end_be)


def _limited_life_lines(alerts):
    """คำเตือนสำหรับกองทุนอายุจำกัดที่ติดอยู่ในรายการแจ้งเตือนวันนี้"""
    hits = [r for r in alerts if r["ticker"] in LIMITED_LIFE]
    if not hits:
        return []
    out = ["> **อ่าน yield ของตัวที่มี ⏳ คนละแบบกับหุ้น**",
           ">",
           "> กองทุนพวกนี้จ่ายเงินสดที่มีเงินต้นของเราปนอยู่ เพราะมูลค่าหน่วยจะลดลง",
           "> เมื่อสินทรัพย์ทยอยหมดอายุ แตะ 10% ไม่ได้แปลว่าได้ผลตอบแทน 10%",
           "> ต้องคิดเป็น IRR ถึงวันหมดสัญญาโดยประเมินมูลค่าคงเหลือเองก่อนตัดสินใจ",
           ">"]
    out += ["> - **%s** (%s) — %s" % (r["ticker"], _life_text(r["ticker"]),
                                      LIMITED_LIFE[r["ticker"]][1]) for r in hits]
    out.append("")
    return out


def _price_note(ok):
    """บอกที่มาของราคาเมื่อดึงก่อนตลาดเปิด ไม่งั้นจะงงว่าทำไมราคาไม่ขยับ"""
    if ok and all(r.get("price_kind") == "ปิดก่อนหน้า" for r in ok):
        return "ราคาที่ใช้คือราคาปิดวันทำการก่อนหน้า (ตลาดยังไม่เปิด)"
    return ""


def render_text(ok, failed):
    """รายงานแบบข้อความ ใช้ตอน --dry-run และเก็บเป็น log ใน repo"""
    icon = {"green": "[ถึงเป้า]", "yellow": "[ใกล้ถึง]", "grey": "[ยังไกล]"}
    alerts = sorted([r for r in ok if r["status"] != "grey"], key=lambda r: -r["yield"])
    lines = ["# แจ้งเตือนหุ้นปันผล %s" % thai_date(), ""]
    note = _price_note(ok)
    if note:
        lines += ["_%s_" % note, ""]

    if alerts:
        lines += ["## ถึงเป้าหรือใกล้ถึง (yield >= %.0f%%)" % ALERT_YIELD, "",
                  "| หุ้น | ราคาล่าสุด | yield | ราคาเป้า 10% | ต้องตกอีก | สถานะ | หมายเหตุ |",
                  "|---|---|---|---|---|---|---|"]
        lines += ["| %s | %.2f | %.2f%% | %.2f | %s | %s | %s |"
                  % (r["ticker"], r["last"], r["yield"], r["target"],
                     _gap_text(r), icon[r["status"]],
                     " ".join(x for x in (_life_text(r["ticker"]), r["note"]) if x))
                  for r in alerts]
        lines.append("")
        lines += _limited_life_lines(alerts)
    else:
        lines += ["วันนี้ไม่มีหุ้นตัวไหน yield ถึง %.0f%%" % ALERT_YIELD, ""]

    lines += ["## หุ้นที่เหลือในลิสต์", "",
              "| หุ้น | ราคาล่าสุด | yield | ราคาเป้า 10% | ต้องตกอีก |",
              "|---|---|---|---|---|"]
    lines += ["| %s | %.2f | %.2f%% | %.2f | %.1f%% |"
              % (r["ticker"], r["last"], r["yield"], r["target"], r["drop_pct"])
              for r in sorted([x for x in ok if x["status"] == "grey"],
                              key=lambda x: -x["yield"])]
    lines.append("")

    if failed:
        lines += ["## ดึงข้อมูลไม่สำเร็จ", ""]
        lines += ["- **%s** — %s" % (r["ticker"], r["error"]) for r in failed]
        lines.append("")

    lines += ["---", "", "_%s_" % DISCLAIMER]
    return "\n".join(lines)


def _table(rows, headers, cells):
    """สร้างตาราง HTML ที่อ่านออกบนมือถือ — inline style เพราะ Gmail ตัด <style> ทิ้ง"""
    th = ("padding:8px 10px;border:1px solid #d0d7de;background:#f6f8fa;"
          "text-align:left;font-weight:600;white-space:nowrap")
    td = "padding:8px 10px;border:1px solid #d0d7de;vertical-align:top"
    out = ['<div style="overflow-x:auto"><table style="border-collapse:collapse;'
           'width:100%;font-size:14px">', "<tr>"]
    out += ['<th style="%s">%s</th>' % (th, h) for h in headers]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>")
        out += ['<td style="%s">%s</td>' % (td, c) for c in cells(r)]
        out.append("</tr>")
    out.append("</table></div>")
    return "".join(out)


def render_html(ok, failed):
    """เนื้ออีเมล HTML"""
    icon = {"green": "🟢", "yellow": "🟡"}
    alerts = sorted([r for r in ok if r["status"] != "grey"], key=lambda r: -r["yield"])
    rest = sorted([r for r in ok if r["status"] == "grey"], key=lambda r: -r["yield"])

    body = ['<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
            'Roboto,sans-serif;max-width:760px;margin:0 auto;padding:16px;color:#1f2328">']
    body.append('<h2 style="margin:0 0 4px">แจ้งเตือนหุ้นปันผล</h2>')
    note = _price_note(ok)
    body.append('<p style="margin:0 0 18px;color:#656d76;font-size:14px">%s%s</p>'
                % (thai_date(), (" · " + note) if note else ""))

    body.append('<h3 style="margin:20px 0 8px">ถึงเป้าหรือใกล้ถึง '
                '(yield ตั้งแต่ %.0f%%)</h3>' % ALERT_YIELD)
    body.append(_table(
        alerts,
        ["หุ้น", "ราคาล่าสุด", "yield", "ราคาเป้า 10%", "ต้องตกอีก", "", "หมายเหตุ"],
        lambda r: ["<b>%s</b>" % r["ticker"], "%.2f" % r["last"],
                   "<b>%.2f%%</b>" % r["yield"], "%.2f" % r["target"],
                   _gap_text(r), icon.get(r["status"], ""),
                   (("<b>%s</b><br>" % _life_text(r["ticker"]))
                    if r["ticker"] in LIMITED_LIFE else "") + r["note"]]))

    # กองทุนอายุจำกัดต้องอ่าน yield คนละแบบ ไม่งั้นจะนึกว่า 10% คือผลตอบแทน 10%
    hits = [r for r in alerts if r["ticker"] in LIMITED_LIFE]
    if hits:
        body.append('<div style="margin:18px 0;padding:12px 14px;background:#ddf4ff;'
                    'border-left:4px solid #0969da;border-radius:6px;font-size:13px">')
        body.append('<b>อ่าน yield ของตัวที่มี ⏳ คนละแบบกับหุ้น</b>'
                    '<p style="margin:6px 0">กองทุนพวกนี้จ่ายเงินสดที่มี<b>เงินต้นของเราปนอยู่</b> '
                    'เพราะมูลค่าหน่วยจะลดลงเมื่อสินทรัพย์ทยอยหมดอายุ — แตะ 10% '
                    'ไม่ได้แปลว่าได้ผลตอบแทน 10% ต้องคิดเป็น IRR ถึงวันหมดสัญญา '
                    'โดยประเมินมูลค่าคงเหลือเองก่อนตัดสินใจ</p><ul style="margin:6px 0">')
        body += ["<li><b>%s</b> (%s) — %s</li>"
                 % (r["ticker"], _life_text(r["ticker"]), LIMITED_LIFE[r["ticker"]][1])
                 for r in hits]
        body.append("</ul></div>")

    if failed:
        body.append('<h3 style="margin:24px 0 8px;color:#bc4c00">ดึงข้อมูลไม่สำเร็จ '
                    '%d ตัว</h3><ul style="font-size:14px;color:#656d76">' % len(failed))
        body += ["<li><b>%s</b> — %s</li>" % (r["ticker"], r["error"]) for r in failed]
        body.append("</ul>")

    body.append('<h3 style="margin:28px 0 8px;color:#656d76;font-size:15px">'
                'หุ้นที่เหลือในลิสต์ (%d ตัว)</h3>' % len(rest))
    body.append(_table(
        rest, ["หุ้น", "ราคาล่าสุด", "yield", "ราคาเป้า 10%", "ต้องตกอีก"],
        lambda r: [r["ticker"], "%.2f" % r["last"], "%.2f%%" % r["yield"],
                   "%.2f" % r["target"], "%.1f%%" % r["drop_pct"]]))

    body.append('<p style="margin:28px 0 0;padding:12px;background:#fff8c5;'
                'border-radius:6px;font-size:13px;font-style:italic;color:#4d2d00">'
                '⚠️ %s</p>' % DISCLAIMER)
    body.append("</div>")
    return "".join(body)


def render_error_html(ok, failed):
    """เนื้ออีเมลกรณีระบบพัง — ต้องแจ้ง ไม่งั้นผู้ใช้จะนึกว่าไม่มีหุ้นถึงเป้า"""
    body = ['<div style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
            'max-width:760px;margin:0 auto;padding:16px;color:#1f2328">']
    body.append('<h2 style="margin:0 0 4px">ระบบแจ้งเตือนหุ้นปันผลมีปัญหา</h2>')
    body.append('<p style="margin:0 0 18px;color:#656d76;font-size:14px">%s</p>'
                % thai_date())
    body.append('<p>ดึงข้อมูลจาก settrade.com ไม่สำเร็จ <b>%d จาก %d ตัว</b> '
                'จึงยังสรุปไม่ได้ว่ามีหุ้นตัวไหนถึงเป้าหรือไม่</p>'
                % (len(failed), len(ok) + len(failed)))
    body.append('<p style="font-size:14px;color:#656d76">สาเหตุที่เป็นไปได้: '
                'Settrade เปลี่ยนโครงสร้างหน้าเว็บ, Incapsula บล็อก IP ของ runner, '
                'หรือเว็บล่มชั่วคราว</p>')
    body.append('<ul style="font-size:13px;color:#656d76">')
    body += ["<li><b>%s</b> — %s</li>" % (r["ticker"], r["error"]) for r in failed[:10]]
    body.append("</ul></div>")
    return "".join(body)


# ── ส่งอีเมล ────────────────────────────────────────────────────────────────────

def send_email(subject, html):
    """ส่งผ่าน Gmail SMTP — ต้องใช้ App Password ไม่ใช่รหัสผ่านบัญชีปกติ"""
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("ALERT_TO") or user
    missing = [n for n, v in (("GMAIL_USER", user),
                              ("GMAIL_APP_PASSWORD", password)) if not v]
    if missing:
        raise RuntimeError("ไม่ได้ตั้งค่า secret: %s" % ", ".join(missing))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content("อีเมลนี้เป็น HTML — เปิดด้วยโปรแกรมที่แสดง HTML ได้")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print("ส่งอีเมลไปที่ %s แล้ว: %s" % (to, subject))


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv

    ok, failed = scan(WATCHLIST)
    report = render_text(ok, failed)
    print(report)

    alerts = [r for r in ok if r["status"] != "grey"]
    n_green = sum(1 for r in alerts if r["status"] == "green")
    broken = len(failed) > len(WATCHLIST) / 2

    if dry_run:
        print("\n[dry-run] ไม่ส่งอีเมล — alerts=%d green=%d failed=%d"
              % (len(alerts), n_green, len(failed)))
        return 0

    if broken:
        send_email("⚠️ ระบบแจ้งเตือนหุ้นปันผลมีปัญหา — %s" % thai_date(),
                   render_error_html(ok, failed))
        return 2

    if not alerts:
        # ไม่มีอะไรถึงเกณฑ์ = ไม่ส่งอีเมล กันจดหมายขยะรายวัน
        print("\nไม่มีหุ้นถึงเกณฑ์ %.0f%% — ไม่ส่งอีเมล" % ALERT_YIELD)
        return 0

    subject = ("🟢 หุ้นปันผลถึงเป้า 10%% แล้ว %d ตัว — %s"
               % (len(alerts), thai_date()) if n_green else
               "🟡 หุ้นปันผลใกล้ถึงเป้า %d ตัว — %s" % (len(alerts), thai_date()))
    send_email(subject, render_html(ok, failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
