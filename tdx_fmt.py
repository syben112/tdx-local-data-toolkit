#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tdx_fmt.py -- 通达信本地 vipdoc 二进制解析器（.day 日线 / .lc1 1 分钟线）。

设计约束
--------
1) 纯标准库：本文件只 import os/sys/struct/argparse/json（见 FORMAT.md「依赖」一节）。
2) 自包含：包内每个脚本都自带所需的最小解析代码、互不 import，
   以便任何一个文件都能被单独复制出去使用。改动格式逻辑时请同步其余脚本。
3) 零网络、只读：只读本地文件，不连任何行情服务器，不做任何写入（--selftest 例外，
   它在脚本同目录下自建临时文件并在结束时删除）。

日期编码（本文件的核心）
------------------------
.day 记录前 4 字节 = uint32，十进制按 YYYYMMDD 直接读取（实测 0x01352840 -> 20260928）。
.lc1 记录前 2 字节 = uint16 v，是**位编码**，必须用整除/取模（位运算）解：

    year  = v // 2048 + 2004
    month = (v % 2048) // 100
    day   = (v % 2048) % 100

实测 v = 45984 (0xb3a0) -> 2026-09-28。
反面写法 `day = v % 100` 会解出 84 -> 2026-09-84，是不存在的日期；若用它做覆盖率
审计，等于每天都在拿一个假日期做比较。详见 FORMAT.md「踩坑清单」第 1 条。

用法
----
    python tdx_fmt.py --selftest
    python tdx_fmt.py --info FILE.day
    python tdx_fmt.py --day  FILE.day  [--last N] [--raw] [--json]
    python tdx_fmt.py --lc1  FILE.lc1  [--last N] [--json]
"""
import argparse
import json
import os
import struct
import sys

RECORD_SIZE = 32
DAY_STRUCT = struct.Struct('<IIIIIfII')     # 日期/开/高/低/收/成交额/成交量/保留
LC1_STRUCT = struct.Struct('<HHfffffII')    # 日期字/分钟数/开/高/低/收/成交额/成交量/保留
DAY_MAGIC_FIELDS = ('date', 'open_int', 'high_int', 'low_int', 'close_int',
                    'amount', 'volume', 'reserved')
LC1_MAGIC_FIELDS = ('date_word', 'minute', 'open', 'high', 'low', 'close',
                    'amount', 'volume', 'reserved')


# ---------------------------------------------------------------- 日期编解码

def _days_in_month(year, month):
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if month in (4, 6, 9, 11):
        return 30
    if month == 2:
        leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
        return 29 if leap else 28
    return 0


def is_valid_ymd(year, month, day):
    """日历上是否真实存在这一天（不依赖 calendar 模块）。"""
    return 1 <= month <= 12 and 1 <= day <= _days_in_month(year, month)


def decode_lc1_date(v):
    """.lc1 日期字 -> (year, month, day)。位运算版本。"""
    if not 0 <= v <= 0xFFFF:
        raise ValueError('日期字必须是 uint16，收到 %r' % (v,))
    year = v // 2048 + 2004
    rest = v % 2048
    return year, rest // 100, rest % 100


def decode_lc1_date_int(v):
    """.lc1 日期字 -> YYYYMMDD 整数。"""
    year, month, day = decode_lc1_date(v)
    return year * 10000 + month * 100 + day


def encode_lc1_date(year, month, day):
    """(year, month, day) -> .lc1 日期字。decode_lc1_date 的逆运算。"""
    if not is_valid_ymd(year, month, day):
        raise ValueError('不是合法日期: %04d-%02d-%02d' % (year, month, day))
    if not 2004 <= year <= 2035:
        raise ValueError('年份 %d 超出该字段可编码范围 2004..2035' % year)
    return (year - 2004) * 2048 + month * 100 + day


def decode_lc1_date_naive(v):
    """**故意写错的对照实现**（`day = v % 100`），只用于说明坑点与自测，不要用于生产。"""
    return v // 2048 + 2004, (v % 2048) // 100, v % 100


def decode_day_date(raw):
    """.day 前 4 字节 uint32 -> YYYYMMDD 整数（原样读取，不做位运算）。"""
    return int(raw)


def date_int_to_str(d):
    return '%08d' % d


def minute_to_clock(m):
    """.lc1 第 2 个字段 -> 'HH:MM'。实测该字段是自 00:00 起的分钟数
    （571 -> 09:31，900 -> 15:00；午休造成的缺口是 690 -> 781）。"""
    return '%02d:%02d' % (m // 60, m % 60)


# ---------------------------------------------------------------- 文件读取

def file_record_count(path):
    size = os.path.getsize(path)
    return size // RECORD_SIZE, size % RECORD_SIZE


def read_day(path, last=None):
    """.day -> list[dict]，字段名见 DAY_MAGIC_FIELDS。last=N 只返回末尾 N 条。"""
    count, _ = file_record_count(path)
    start = 0 if not last else max(0, count - last)
    out = []
    with open(path, 'rb') as f:
        f.seek(start * RECORD_SIZE)
        blob = f.read((count - start) * RECORD_SIZE)
    for i in range(len(blob) // RECORD_SIZE):
        rec = DAY_STRUCT.unpack_from(blob, i * RECORD_SIZE)
        item = dict(zip(DAY_MAGIC_FIELDS, rec))
        item['date_str'] = date_int_to_str(item['date'])
        out.append(item)
    return out


def read_lc1(path, last=None):
    """.lc1 -> list[dict]，字段名见 LC1_MAGIC_FIELDS。"""
    count, _ = file_record_count(path)
    start = 0 if not last else max(0, count - last)
    out = []
    with open(path, 'rb') as f:
        f.seek(start * RECORD_SIZE)
        blob = f.read((count - start) * RECORD_SIZE)
    for i in range(len(blob) // RECORD_SIZE):
        rec = LC1_STRUCT.unpack_from(blob, i * RECORD_SIZE)
        item = dict(zip(LC1_MAGIC_FIELDS, rec))
        item['date'] = decode_lc1_date_int(item['date_word'])
        item['date_str'] = date_int_to_str(item['date'])
        item['clock'] = minute_to_clock(item['minute'])
        out.append(item)
    return out


def last_day_date(path):
    """.day 末条记录日期（YYYYMMDD 整数）；空文件/坏文件返回 None。"""
    count, _ = file_record_count(path)
    if count == 0:
        return None
    with open(path, 'rb') as f:
        f.seek((count - 1) * RECORD_SIZE)
        blob = f.read(RECORD_SIZE)
    if len(blob) < RECORD_SIZE:
        return None
    return decode_day_date(struct.unpack_from('<I', blob, 0)[0])


def last_lc1_date(path):
    """.lc1 末条记录日期（YYYYMMDD 整数）。注意 .lc1 是滚动窗口，日期标签必须取自
    记录自身的日期字，不能靠「日历尾部对齐」推算。"""
    count, _ = file_record_count(path)
    if count == 0:
        return None
    with open(path, 'rb') as f:
        f.seek((count - 1) * RECORD_SIZE)
        blob = f.read(RECORD_SIZE)
    if len(blob) < RECORD_SIZE:
        return None
    return decode_lc1_date_int(struct.unpack_from('<H', blob, 0)[0])


# ---------------------------------------------------------------- 合理性闸门

def guard_day_rows(path, rows):
    """把非 .day 文件（例如 .lc1）误传进来时当场失败，而不是输出一堆假日期。"""
    for r in rows:
        d = r['date']
        if not (19900000 < d < 21010000 and is_valid_ymd(d // 10000, d // 100 % 100, d % 100)):
            raise SystemExit('SANITY_FAIL: %s 的日期列出现非日期值（%d），'
                             '看起来不是 .day 日线文件' % (os.path.basename(path), d))
    return rows


def guard_lc1_rows(path, rows):
    """.lc1 日期字解出的必须是真实存在的日期，否则说明文件类型/解码方式不对。"""
    for r in rows:
        if not is_valid_ymd(r['date'] // 10000, r['date'] // 100 % 100, r['date'] % 100):
            raise SystemExit('SANITY_FAIL: %s 解出的日期字 %d 不是合法日期（%s），'
                             '看起来不是 .lc1 分钟线文件'
                             % (os.path.basename(path), r['date_word'], r['date']))
    return rows


# ---------------------------------------------------------------- 命令行

def _print_day(rows, raw):
    for r in rows:
        if raw:
            print('%s O=%d H=%d L=%d C=%d amount=%s volume=%d reserved=%d'
                  % (r['date_str'], r['open_int'], r['high_int'], r['low_int'],
                     r['close_int'], repr(r['amount']), r['volume'], r['reserved']))
        else:
            print('%s O=%.2f H=%.2f L=%.2f C=%.2f amount=%s volume=%d'
                  % (r['date_str'], r['open_int'] / 100.0, r['high_int'] / 100.0,
                     r['low_int'] / 100.0, r['close_int'] / 100.0, repr(r['amount']),
                     r['volume']))


def _print_lc1(rows):
    for r in rows:
        print('%s %s O=%.4f H=%.4f L=%.4f C=%.4f amount=%s volume=%d'
              % (r['date_str'], r['clock'], r['open'], r['high'], r['low'], r['close'],
                 repr(r['amount']), r['volume']))


def _info(path):
    count, rest = file_record_count(path)
    print('文件: %s' % os.path.basename(path))
    print('大小: %d 字节  记录数: %d  余数: %d 字节（应为 0）' % (os.path.getsize(path), count, rest))
    if path.lower().endswith('.day'):
        rows = read_day(path, last=2)
        print('首/末日期: %s' % ' '.join(r['date_str'] for r in rows))
    else:
        rows = read_lc1(path, last=2)
        print('末条日期字解码: v=%d -> %s %s' % (rows[-1]['date_word'], rows[-1]['date_str'],
                                                rows[-1]['clock']) if rows else '空文件')
        if rows:
            print('末条原始字段: %r' % (LC1_STRUCT.unpack(struct.pack(
                '<HHfffffII', rows[-1]['date_word'], rows[-1]['minute'], rows[-1]['open'],
                rows[-1]['high'], rows[-1]['low'], rows[-1]['close'], rows[-1]['amount'],
                rows[-1]['volume'], rows[-1]['reserved'])),))
    return 0


def _selftest():
    """无 TDX 环境也能跑：只用自造数据做编解码与读写 round-trip。"""
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    # 1) 日期解码：实测值 + 位运算 vs 反面写法
    check('decode_lc1_date(45984) == (2026, 9, 28)', decode_lc1_date(45984) == (2026, 9, 28))
    check('decode_lc1_date_int(45984) == 20260928', decode_lc1_date_int(45984) == 20260928)
    ny, nm, nd = decode_lc1_date_naive(45984)
    check('反面写法 v%%100 得到不存在的日期(2026-09-84)', not is_valid_ymd(ny, nm, nd))
    check('is_valid_ymd(2026, 9, 84) is False', not is_valid_ymd(2026, 9, 84))
    check('is_valid_ymd(2024, 2, 29) is True', is_valid_ymd(2024, 2, 29))
    check('is_valid_ymd(2026, 2, 29) is False', not is_valid_ymd(2026, 2, 29))

    # 2) 编解码 round-trip（含年/月/日边界）
    dates = [(2004, 1, 1), (2021, 8, 2), (2026, 1, 1), (2026, 9, 28), (2026, 12, 31),
             (2035, 12, 31), (2032, 2, 29)]
    ok = True
    for y, m, d in dates:
        v = encode_lc1_date(y, m, d)
        ok = ok and 0 <= v <= 0xFFFF and decode_lc1_date(v) == (y, m, d)
    check('encode/decode round-trip %d 个日期' % len(dates), ok)
    check('超范围年份被拒绝', _raises(lambda: encode_lc1_date(2036, 1, 1)))
    check('非法月日被拒绝', _raises(lambda: encode_lc1_date(2026, 2, 30)))

    # 3) 分钟数 -> 时钟
    check("minute_to_clock(571) == '09:31'", minute_to_clock(571) == '09:31')
    check("minute_to_clock(900) == '15:00'", minute_to_clock(900) == '15:00')

    # 4) 读写 round-trip（自造样本，写在脚本同目录的临时子目录里，结束即删）
    here = os.path.dirname(os.path.abspath(__file__))
    tmpdir = os.path.join(here, '_selftest_tmp')
    if not os.path.isdir(tmpdir):
        os.makedirs(tmpdir)
    made = []
    try:
        day_path = os.path.join(tmpdir, 'sh600000.day')
        want = []
        with open(day_path, 'wb') as f:
            for k, (y, m, d) in enumerate([(2026, 9, 24), (2026, 9, 25), (2026, 9, 28)]):
                price = 1000 + k
                rec = DAY_STRUCT.pack(y * 10000 + m * 100 + d, price * 100, (price + 5) * 100,
                                      (price - 5) * 100, (price + 2) * 100, float(price * 12345),
                                      100 * (k + 1), 0)
                f.write(rec)
                want.append((y * 10000 + m * 100 + d, (price + 2) * 100))
        made.append(day_path)
        rows = read_day(day_path)
        check('--day 读取条数 == 3', len(rows) == 3)
        check('--day 日期/价格逐个 round-trip 相等',
              [(r['date'], r['close_int']) for r in rows] == want)
        check('last_day_date 取末条', last_day_date(day_path) == 20260928)
        check('file_record_count 余数为 0', file_record_count(day_path) == (3, 0))

        lc1_path = os.path.join(tmpdir, 'sz000001.lc1')
        minutes = [571, 572, 573, 574]
        with open(lc1_path, 'wb') as f:
            for i, mm in enumerate(minutes):
                f.write(LC1_STRUCT.pack(encode_lc1_date(2026, 9, 28), mm, 10.0 + i, 11.0 + i,
                                        9.0 + i, 10.5 + i, float(1050 + i), 100 * (i + 1), 0))
        made.append(lc1_path)
        lrows = read_lc1(lc1_path)
        check('--lc1 读取条数 == 4', len(lrows) == 4)
        check('--lc1 日期字解码 == 20260928', all(r['date'] == 20260928 for r in lrows))
        check('--lc1 分钟数 round-trip', [r['minute'] for r in lrows] == minutes)
        check('--lc1 价格 float round-trip', [r['close'] for r in lrows] == [10.5, 11.5, 12.5, 13.5])
        check('last_lc1_date 取末条', last_lc1_date(lc1_path) == 20260928)
    finally:
        for p in made:
            if os.path.exists(p):
                os.remove(p)
        if os.path.isdir(tmpdir):
            os.rmdir(tmpdir)

    bad = [n for n, ok_ in checks if not ok_]
    for name, ok_ in checks:
        print('%s  %s' % ('PASS' if ok_ else 'FAIL', name))
    print('selftest: %d/%d 通过' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    except Exception:
        return False
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description='通达信本地 vipdoc 二进制解析器（纯标准库）')
    ap.add_argument('--day', metavar='FILE', help='解析 .day 日线文件')
    ap.add_argument('--lc1', metavar='FILE', help='解析 .lc1 1 分钟线文件')
    ap.add_argument('--info', metavar='FILE', help='打印文件结构信息')
    ap.add_argument('--last', type=int, default=None, help='只输出末尾 N 条记录')
    ap.add_argument('--raw', action='store_true', help='.day 输出未除 100 的原始整数')
    ap.add_argument('--json', action='store_true', help='以 JSON 输出（便于管道处理）')
    ap.add_argument('--selftest', action='store_true', help='自测（无需任何 TDX 数据）')
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.info:
        return _info(args.info)
    if args.day:
        rows = guard_day_rows(args.day, read_day(args.day, last=args.last))
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _print_day(rows, args.raw)
        return 0
    if args.lc1:
        rows = guard_lc1_rows(args.lc1, read_lc1(args.lc1, last=args.last))
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _print_lc1(rows)
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
