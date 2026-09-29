#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sample_builder.py -- 生成**合成**的通达信样本文件（.day / .lc1），供没有 TDX 数据的机器自测。

为什么需要它：本工具包的解析器只在「有 vipdoc 的机器」上才有真实输入。
没有通达信的机器也要能验证「解析器读回来的东西 == 写进去的东西」，
所以这里用完全人造的数值造出格式合法的样本文件。

样本内容为合成数（base = 1000 + 137*i 一类的确定性递推），
不是任何真实标的、也不是任何真实行情，禁止当作行情数据使用。

用法
----
    python sample_builder.py --out /tmp_samples --n 10 --date 20260928 --hit 6
    python audit_coverage.py --dir /tmp_samples --date 20260928
      -> 期望覆盖率恰好 60.0%（10 个文件里 6 个末条记录日期 == 目标日期）

约定：第 i 个文件（i 从 0 开始）中，i < --hit 的末条记录日期 == --date，
其余文件的末条记录日期 == --date 的前一个日历日；市场按 (i//2)%2 交替、
类型按 i%2 交替。默认参数（--n 10 --hit 6）下：
  sh/day 2/3、sh/lc1 2/3、sz/day 1/2、sz/lc1 1/2 -> 总计 6/10 = 60.0%，
  按市场看 sh 4/6 = 66.7%、sz 2/4 = 50.0%（故意做成不等，好确认审计器不是把 60% 写死的）。
"""
import argparse
import json
import os
import struct
import sys

RECORD_SIZE = 32
DAY_STRUCT = struct.Struct('<IIIIIfII')
LC1_STRUCT = struct.Struct('<HHfffffII')
SESSION_MINUTES = list(range(571, 691)) + list(range(781, 901))   # 09:31..11:30 + 13:01..15:00


def encode_lc1_date(year, month, day):
    """与 tdx_fmt.py 保持一致（本包各脚本自包含，不互相 import）。"""
    return (year - 2004) * 2048 + month * 100 + day


def _shift_date(date_int, delta_days):
    """YYYYMMDD 整数做日历平移（自实现，不依赖 calendar 模块）。"""
    year, month, day = date_int // 10000, date_int // 100 % 100, date_int % 100
    month_len = [31, 29 if ((year % 4 == 0 and year % 100 != 0) or year % 400 == 0) else 28,
                 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day += delta_days
    while day < 1:
        month -= 1
        if month < 1:
            year, month = year - 1, 12
            month_len[1] = 29 if ((year % 4 == 0 and year % 100 != 0) or year % 400 == 0) else 28
        day += month_len[month - 1]
    while day > month_len[month - 1]:
        day -= month_len[month - 1]
        month += 1
        if month > 12:
            year, month = year + 1, 1
            month_len[1] = 29 if ((year % 4 == 0 and year % 100 != 0) or year % 400 == 0) else 28
    return year * 10000 + month * 100 + day


def build_day_file(path, last_date, days, base_price, skip=()):
    """写一个 .day 文件：连续 days 个日历日，末条 == last_date（skip 里的日期不写入）。

    返回 (写入条数, 实际末条日期)。
    """
    rows = 0
    written = []
    with open(path, 'wb') as f:
        for k in range(days):
            d = _shift_date(last_date, k - (days - 1))
            if d in skip:
                continue
            year, month, day = d // 10000, d // 100 % 100, d % 100
            price = base_price + k
            volume = 100 * (k + 1)                       # 整手（100 股）的倍数
            amount = float(round(price * volume, 2))
            f.write(DAY_STRUCT.pack(year * 10000 + month * 100 + day,
                                    price * 100, (price + 5) * 100, (price - 5) * 100,
                                    (price + 2) * 100, amount, volume, 0))
            rows += 1
            written.append(d)
    return rows, (max(written) if written else None)


def build_lc1_file(path, last_date, days, bars, base_price, skip=()):
    """写一个 .lc1 文件：连续 days 个日历日 × bars 条分钟线，末条 == last_date。

    返回 (写入条数, 实际末条日期)。
    """
    if bars > len(SESSION_MINUTES):
        raise ValueError('bars 最大 %d（一个交易日 240 分钟内），收到 %d'
                         % (len(SESSION_MINUTES), bars))
    rows = 0
    written = []
    with open(path, 'wb') as f:
        for k in range(days):
            d = _shift_date(last_date, k - (days - 1))
            if d in skip:
                continue
            year, month, day = d // 10000, d // 100 % 100, d % 100
            word = encode_lc1_date(year, month, day)
            # 用「当日第几根」bar_index 而不是「自 09:31 起的分钟差」：后者会把 11:30~13:00
            # 的午休算成 90 根，序号凭空多 90。真实数据里午休不产生记录。
            for bar_index, mm in enumerate(SESSION_MINUTES[:bars]):
                price = base_price + k + bar_index / 100.0
                volume = 100 * (bar_index + 1)
                amount = float(round(price * volume, 2))
                f.write(LC1_STRUCT.pack(word, mm, price, price + 0.05, price - 0.05,
                                        price + 0.02, amount, volume, 0))
                rows += 1
            written.append(d)
    return rows, (max(written) if written else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成合成 .day/.lc1 样本文件（无 TDX 环境自测用）')
    ap.add_argument('--out', required=True, help='输出目录（不存在则创建）')
    ap.add_argument('--n', type=int, default=10, help='文件个数，默认 10')
    ap.add_argument('--date', type=int, default=20260928, help='目标日期 YYYYMMDD，默认 20260928')
    ap.add_argument('--hit', type=int, default=6, help='末条日期命中目标日期的文件数，默认 6')
    ap.add_argument('--days', type=int, default=3, help='每个文件包含的日历天数，默认 3')
    ap.add_argument('--bars', type=int, default=240, help='.lc1 每日分钟条数（<=240），默认 240')
    ap.add_argument('--skip-dates', nargs='*', type=int, default=[],
                    help='故意不写入的日期（YYYYMMDD），用于伪造「数据空洞」来测试反推逻辑')
    args = ap.parse_args(argv)

    if args.n < 1:
        raise SystemExit('--n 必须 >= 1')
    if not 0 <= args.hit <= args.n:
        raise SystemExit('--hit 必须在 [0, --n] 区间内')
    if args.days < 1:
        raise SystemExit('--days 必须 >= 1')
    if not 1 <= args.bars <= len(SESSION_MINUTES):
        raise SystemExit('--bars 必须在 [1, %d] 区间内' % len(SESSION_MINUTES))
    if not (1990 <= args.date // 10000 <= 2100 and 1 <= args.date // 100 % 100 <= 12
            and 1 <= args.date % 100 <= 31):
        raise SystemExit('--date 必须是形如 20260928 的合法日期')

    if not os.path.isdir(args.out):
        os.makedirs(args.out)

    manifest = []
    skip = set(args.skip_dates)
    for i in range(args.n):
        # 市场按 (i//2)%2 交替、类型按 i%2 交替：n=10 时四个分组 (sh|sz)/(day|lc1) 都有样本，
        # 每组 2~3 个文件，便于验证分组统计而不是只看一个总数。
        market = 'sh' if (i // 2) % 2 == 0 else 'sz'
        kind = 'day' if i % 2 == 0 else 'lc1'
        code = ('%06d' % (600000 + i)) if market == 'sh' else ('%06d' % (1 + i))
        name = '%s%s.%s' % (market, code, kind)
        path = os.path.join(args.out, name)
        last_date = args.date if i < args.hit else _shift_date(args.date, -1)
        base_price = 1000 + 137 * i
        if kind == 'day':
            rows, written_last = build_day_file(path, last_date, args.days, base_price, skip)
        else:
            rows, written_last = build_lc1_file(path, last_date, args.days, args.bars,
                                                base_price, skip)
        manifest.append({'file': name, 'market': market, 'kind': kind,
                         'records': rows, 'last_date': written_last,
                         'hit': written_last == args.date, 'base_price': base_price})

    mpath = os.path.join(args.out, 'sample_manifest.json')
    with open(mpath, 'w', encoding='utf-8') as f:
        json.dump({'target_date': args.date, 'n': args.n, 'hit': args.hit,
                   'days': args.days, 'bars': args.bars, 'files': manifest},
                  f, ensure_ascii=False, indent=2)

    hits = sum(1 for m in manifest if m['hit'])
    print('已生成 %d 个样本文件到 %s（目标日期 %d，命中 %d 个 -> 理论覆盖率 %.1f%%）'
          % (len(manifest), os.path.basename(os.path.abspath(args.out)), args.date, hits,
             100.0 * hits / len(manifest)))
    print('清单: %s' % os.path.basename(mpath))
    return 0


if __name__ == '__main__':
    sys.exit(main())
