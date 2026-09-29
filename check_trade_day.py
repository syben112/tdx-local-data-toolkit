#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_trade_day.py -- A 股交易日判断（纯标准库，离线）。

判断顺序（三步，缺一步就会出错）
--------------------------------
1) 周末：周六/周日 -> 非交易日。
2) 节假日表：命中 -> 非交易日。
3) 否则 -> 交易日（但要先看下面的「表覆盖边界」）。

表覆盖边界（务必读）
--------------------
本文件内置 2025 / 2026 两年的 A 股节假日表，且 **2026 年只覆盖到 2026-09-28**
（该表由 5 个指数日线文件取日期并集反推，数据末条就是这一天）。
2026-09-28 之后的节假日（例如国庆长假）没有任何数据能证明，本文件**不猜**：
落在覆盖范围外的工作日一律返回 `unknown`（未知），并提示必须自行核对交易所公告。
其它年份没有节假日表，同样只对周末给结论，工作日返回 `unknown`。

返回值是三档，不是布尔值，别把 unknown 当成「是交易日」：

    ('trade',   原因, True)   确认是交易日
    ('closed',  原因, True)   确认非交易日（周末 / 节假日表命中）
    ('unknown', 原因, False)  无表或超出表覆盖范围 —— 必须人工核对

退出码与之一一对应：0 = 交易日；1 = 非交易日；2 = 参数/日期非法；3 = 未知（不可信）。

节假日表的来源与可复核性
------------------------
表不是手抄的，是从「多份指数日线文件里某工作日整体缺记录」反推的：
如果所有被检查的指数在 2026-06-19 都没有记录，那这天就是休市。
只用一个指数文件会被数据空洞骗到（实测 5 个文件里 4 个在 2026-06-17 / 06-25 /
06-30 / 07-01 各缺一条，只有深证成指有），所以必须多文件取并集。
复核方法（用你自己的 vipdoc 日线文件重跑一遍并比对）：

    python check_trade_day.py --verify-holidays <指数1.day> <指数2.day> ...

用法
----
    python check_trade_day.py                    # 今天
    python check_trade_day.py 20260928           # 指定日期
    python check_trade_day.py --list 2025        # 列出某年全年交易日
    python check_trade_day.py 20261008 --quiet    # 只输出 交易日 / 非交易日 / 未知

退出码：0 = 交易日；1 = 非交易日；2 = 参数/日期非法；3 = 未知（表覆盖不到，不可信）。
"""
import argparse
import datetime
import os
import struct
import sys

RECORD_SIZE = 32

# 2025 年 A 股节假日（18 天）
HOLIDAYS_2025 = (
    20250101,
    20250128, 20250129, 20250130, 20250131, 20250203, 20250204,
    20250404,
    20250501, 20250502, 20250505,
    20250602,
    20251001, 20251002, 20251003, 20251006, 20251007, 20251008,
)

# 2026 年 A 股节假日（14 天，只覆盖到 2026-09-28；再往后的节假日必须自行核对公告）
HOLIDAYS_2026 = (
    20260101, 20260102,
    20260216, 20260217, 20260218, 20260219, 20260220, 20260223,
    20260406,
    20260501, 20260504, 20260505,
    20260619,
    20260925,
)

HOLIDAYS = {2025: HOLIDAYS_2025, 2026: HOLIDAYS_2026}

# 每个年份表的覆盖边界说明：(年份, 覆盖到的最后一天 或 None=全年完整)
COVERAGE_NOTE = {
    2025: (None, '2025 年全年表'),
    2026: (20260928, '2026 年表只到 2026-09-28（数据末条），之后的节假日需自行核对公告'),
}

WEEKDAY_NAME = ('星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日')


def split_ymd(date_int):
    return date_int // 10000, date_int // 100 % 100, date_int % 100


def _days_in_month(year, month):
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if month in (4, 6, 9, 11):
        return 30
    if month == 2:
        return 29 if ((year % 4 == 0 and year % 100 != 0) or year % 400 == 0) else 28
    return 0


def is_valid_ymd(year, month, day):
    return 1 <= month <= 12 and 1 <= day <= _days_in_month(year, month)


def _to_date(date_int):
    year, month, day = split_ymd(date_int)
    if not (1990 <= year <= 2100 and is_valid_ymd(year, month, day)):
        raise ValueError('非法日期: %r' % (date_int,))
    return datetime.date(year, month, day)


def is_trade_day(date_int):
    """返回 (verdict, 原因, 是否可信)。

    verdict 取值：'trade'（确认交易日）/ 'closed'（确认非交易日）/ 'unknown'（不可信，需人工核对）。
    """
    try:
        d = _to_date(date_int)
    except ValueError as exc:
        raise SystemExit('SANITY_FAIL: %s' % exc)
    year = d.year
    if d.weekday() >= 5:
        return 'closed', '周末（%s）' % WEEKDAY_NAME[d.weekday()], True
    table = HOLIDAYS.get(year)
    if table is None:
        return 'unknown', '没有 %d 年节假日表，无法判断工作日是否休市' % year, False
    limit, note = COVERAGE_NOTE[year]
    if limit is not None and date_int > limit:
        return 'unknown', '超出表覆盖范围（%s）' % note, False
    if date_int in table:
        return 'closed', '节假日表命中（%d 年）' % year, True
    return 'trade', '%d 年交易日（节假日表未命中）' % year, True


def trade_days_of_year(year):
    """列出某年 1/1..12/31 中**确认**的交易日；返回 (交易日列表, 无法判断的工作日列表)。

    已经确认非交易日的有：周末、表内节假日。
    表覆盖不到、又没法确认的工作日会进第二个返回值，别悄悄当成交易日。
    """
    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    days, unknown = [], []
    cur = start
    while cur <= end:
        value = cur.year * 10000 + cur.month * 100 + cur.day
        verdict, _reason, _conf = is_trade_day(value)
        if verdict == 'trade':
            days.append(value)
        elif verdict == 'unknown':
            unknown.append(value)
        cur += datetime.timedelta(days=1)
    return days, unknown


def _sanity_check_tables():
    """表结构自检：节假日必须是工作日、单调递增、年份匹配、无重复。"""
    problems = []
    for year, table in HOLIDAYS.items():
        seen = set()
        for value in table:
            y, m, d = split_ymd(value)
            if y != year:
                problems.append('%d 年表里混入了 %d' % (year, value))
            if value in seen:
                problems.append('重复条目 %d' % value)
            seen.add(value)
            if not is_valid_ymd(y, m, d):
                problems.append('非法日期 %d' % value)
            elif datetime.date(y, m, d).weekday() >= 5:
                problems.append('%d 是周末，按定义不放进节假日表（周末已单独判定）' % value)
        if list(table) != sorted(table):
            problems.append('%d 年表未按升序排列' % year)
    if problems:
        raise SystemExit('SANITY_FAIL: 节假日表自检不通过 -> ' + '; '.join(problems))


def _read_day_dates(path):
    """只读一个 .day 文件的日期列（YYYYMMDD 整数列表）。"""
    size = os.path.getsize(path)
    if size < RECORD_SIZE:
        raise SystemExit('SANITY_FAIL: %s 不足 32 字节' % os.path.basename(path))
    if size % RECORD_SIZE:
        print('注意: %s 文件长度不是 32 的整数倍，末条可能截断，已按完整记录解析'
              % os.path.basename(path))
    dates = []
    with open(path, 'rb') as f:
        data = f.read(size - size % RECORD_SIZE)
    for i in range(0, len(data), RECORD_SIZE):
        dates.append(struct.unpack_from('<I', data, i)[0])
    # 合理性闸门：.day 的日期列必须是 1990..2100 的合法 YYYYMMDD。
    # 把 .lc1（前 2 字节是位编码的 uint16）误当 .day 传入时，这里会当场拦下，
    # 而不是把 45984 之类的值当成日期参与反推。
    bad = [v for v in dates if not (19900000 < v < 21010000 and is_valid_ymd(*split_ymd(v)))]
    if bad:
        raise SystemExit('SANITY_FAIL: %s 的日期列出现非日期值（例如 %d）——'
                         '请确认这是 .day 日线文件，不是 .lc1/其它文件'
                         % (os.path.basename(path), bad[0]))
    return dates


def verify_holidays(paths):
    """用给定的 .day 文件（建议多份指数）反推节假日并与内置表比对。"""
    per_file = []
    union = set()
    for p in paths:
        dates = _read_day_dates(p)
        per_file.append((os.path.basename(p), set(dates)))
        union |= set(dates)
    print('参与反推的日线文件 %d 份，日期并集 %d 天' % (len(paths), len(union)))
    for name, dates in per_file:
        print('  %-20s 记录=%d 相对并集缺=%d' % (name, len(dates), len(union - dates)))
    if not union:
        return 2
    first = _to_date(min(union))
    last = _to_date(max(union))
    derived = []
    cur = first
    while cur <= last:
        value = cur.year * 10000 + cur.month * 100 + cur.day
        if cur.weekday() < 5 and value not in union:
            derived.append(value)
        cur += datetime.timedelta(days=1)
    # 只比较「有内置表的年份 + 在表覆盖范围内」的日期：
    # 数据窗口常见的 2021~2024 年本文件没有表，那不是"不一致"，只是"没比"。
    comparable, skipped = [], 0
    for value in derived:
        year = value // 10000
        table = HOLIDAYS.get(year)
        if table is None:
            skipped += 1
            continue
        limit, _note = COVERAGE_NOTE[year]
        if limit is not None and value > limit:
            skipped += 1
            continue
        comparable.append(value)
    baked = []
    for year, table in sorted(HOLIDAYS.items()):
        limit, _note = COVERAGE_NOTE[year]
        for value in table:
            if value < min(union) or value > max(union):
                continue          # 表里有、但反推窗口没覆盖到，跳过比较
            if limit is not None and value > limit:
                continue
            baked.append(value)
    derived_set, baked_set = set(comparable), set(baked)
    only_derived = sorted(derived_set - baked_set)
    only_baked = sorted(baked_set - derived_set)
    print('反推出的休市工作日 %d 天；其中 %d 天属于没有内置表的年份（不参与比较）'
          % (len(derived), skipped))
    print('参与比较：反推 %d 天 vs 内置表 %d 天' % (len(comparable), len(baked)))
    if not only_derived and not only_baked:
        print('PASS: 内置节假日表与反推结果完全一致')
        return 0
    if only_derived:
        print('DELTA: 反推有、表里没有 -> ' + ' '.join(str(x) for x in only_derived))
        print('  提示: 若只传了 1 个文件，DELTA 往往是该文件自身的数据空洞（当天其实开市），'
              '请多传几个指数文件、按并集再看一次')
    if only_baked:
        print('DELTA: 表里有、反推没有 -> ' + ' '.join(str(x) for x in only_baked))
    return 1


def main(argv=None):
    _sanity_check_tables()
    ap = argparse.ArgumentParser(description='A 股交易日判断（离线，内置 2025/2026 节假日表）')
    ap.add_argument('date', nargs='?', help='日期 YYYYMMDD，缺省为今天')
    ap.add_argument('--list', type=int, metavar='YEAR', help='列出某年全部交易日')
    ap.add_argument('--quiet', action='store_true', help='只输出 交易日 / 非交易日')
    ap.add_argument('--verify-holidays', nargs='+', metavar='DAY_FILE',
                    help='用指定 .day 文件反推休市日并与内置表比对')
    args = ap.parse_args(argv)

    if args.verify_holidays:
        return verify_holidays(args.verify_holidays)

    if args.list:
        days, unknown = trade_days_of_year(args.list)
        if args.quiet:
            print('\n'.join(str(d) for d in days))
        else:
            print('%d 年**确认**交易日 %d 天（首 %d / 末 %d）'
                  % (args.list, len(days), days[0], days[-1]))
            _limit, note = COVERAGE_NOTE.get(args.list, (None, ''))
            if args.list not in HOLIDAYS:
                print('注意: 没有 %d 年节假日表，上表只排除了周末' % args.list)
            elif note:
                print('注意: %s' % note)
            print(' '.join(str(d) for d in days))
        if unknown:
            print('注意: 另有 %d 个工作日无法判断（%d ~ %d），未列入上表，'
                  '请自行核对交易所公告'
                  % (len(unknown), unknown[0], unknown[-1]))
        return 0

    if args.date:
        raw = args.date.strip()
        if not (raw.isdigit() and len(raw) == 8):
            print('SANITY_FAIL: 日期必须是 8 位 YYYYMMDD，收到 %r' % args.date)
            return 2
        date_int = int(raw)
    else:
        # 只认本机系统时间，不做任何推算
        now = datetime.datetime.now()
        date_int = now.year * 10000 + now.month * 100 + now.day

    verdict, reason, confident = is_trade_day(date_int)
    label = {'trade': '交易日', 'closed': '非交易日', 'unknown': '未知'}[verdict]
    code = {'trade': 0, 'closed': 1, 'unknown': 3}[verdict]
    if not confident:
        # 行首一行说明：结论不可信，别把猜测当事实
        print('注意: 结论不可信（该年份/该日期超出内置节假日表覆盖范围，需自行核对交易所公告）')
    if args.quiet:
        print(label)
        return code
    d = _to_date(date_int)
    print('%d %s -> %s（%s）' % (date_int, WEEKDAY_NAME[d.weekday()], label, reason))
    return code


if __name__ == '__main__':
    sys.exit(main())
