#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_coverage.py -- 通达信本地数据覆盖率审计（纯标准库、只读）。

口径（本工具只认这一种口径，别用文件 mtime 判新鲜度）
----------------------------------------------------
读**每个文件最后一条记录**的日期，若 == 目标日期则该文件算「命中」；
覆盖率 = 命中文件数 / 发现的文件总数。

  .day  32 字节/条：前 4 字节 = uint32，十进制 YYYYMMDD，直接读。
  .lc1  32 字节/条：前 2 字节 = uint16 v，位编码解码：
        year = v // 2048 + 2004 ; month = (v % 2048) // 100 ; day = (v % 2048) % 100
        用 `day = v % 100` 这类写法会解出 2026-09-84 这种不存在的日期，本脚本会把
        解出的非法日期直接判为错误（见 errors 段），而不是当成一次「未命中」。

为什么不用 mtime：通达信会在批量刷新、盘后下载、复制目录时改动 mtime，
「mtime 是今天」既不保证内容有今天的数据，也不排除内容已过期。

用法
----
    python audit_coverage.py --dir <vipdoc 目录 或 样本目录> --date YYYYMMDD
    python audit_coverage.py --dir /tmp_samples --date 20260928 --json out.json
    python audit_coverage.py --dir D:/tdx/vipdoc --date 20260928 --kind both

输出：总覆盖率 + 各市场（sh/sz/bj/ds...）分项 + 缺口文件清单 + 错误清单。
--json 落盘的清单永远是**完整**的；屏幕输出受 --max-list 限制。
清单里的路径一律相对 --dir，便于贴进 issue 而不泄露本机目录结构。

退出码：0 = 审计完成；1 = 结构性异常（没有发现文件 / 目标日期非法 / 全部文件读取失败）。
"""
import argparse
import json
import os
import struct
import sys

RECORD_SIZE = 32
KINDS = {'day': 'lday', 'lc1': 'minline'}


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


def split_ymd(date_int):
    return date_int // 10000, date_int // 100 % 100, date_int % 100


def decode_lc1_date(v):
    """位编码解码（与 tdx_fmt.py 同一实现，本包各脚本自包含）。"""
    year = v // 2048 + 2004
    rest = v % 2048
    return year, rest // 100, rest % 100


def last_record_date(path, kind):
    """返回 (date_int, error_str)；date_int 为 None 表示读不到。"""
    size = os.path.getsize(path)
    if size < RECORD_SIZE:
        return None, '文件不足 32 字节（%d）' % size
    with open(path, 'rb') as f:
        f.seek(-RECORD_SIZE, os.SEEK_END)
        blob = f.read(RECORD_SIZE)
    if len(blob) < RECORD_SIZE:
        return None, '末条记录不完整'
    if kind == 'day':
        year, month, day = split_ymd(struct.unpack_from('<I', blob, 0)[0])
    else:
        year, month, day = decode_lc1_date(struct.unpack_from('<H', blob, 0)[0])
    if not is_valid_ymd(year, month, day):
        return None, ('日期解码结果不是合法日期: %04d-%02d-%02d'
                      '（.lc1 请检查是否误用了 day = v %% 100 之类写法）' % (year, month, day))
    return year * 10000 + month * 100 + day, None


def guess_market(rel_path, kind):
    """优先取 lday/minline 的上一级目录名，其次取文件名前缀（如 sh600000.day -> sh）。"""
    parts = rel_path.replace('\\', '/').split('/')
    marker = KINDS[kind]
    for i, p in enumerate(parts[:-1]):
        if p.lower() == marker and i > 0:
            return parts[i - 1].lower()
    stem = parts[-1].split('.')[0]
    if len(stem) > 2 and stem[:2].isalpha():
        return stem[:2].lower()
    return 'unknown'


def collect_files(root, kinds):
    """返回 [(abs_path, rel_path, kind)]，按 rel_path 排序保证输出可复现。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            low = name.lower()
            for kind in kinds:
                if low.endswith('.' + kind):
                    abs_path = os.path.join(dirpath, name)
                    found.append((abs_path, os.path.relpath(abs_path, root), kind))
    found.sort(key=lambda x: (x[2], x[1]))
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description='通达信本地数据覆盖率审计（只读、纯标准库）')
    ap.add_argument('--dir', required=True, help='vipdoc 目录或样本目录（递归扫描）')
    ap.add_argument('--date', required=True, help='目标日期 YYYYMMDD')
    ap.add_argument('--kind', default='both', choices=('day', 'lc1', 'both'),
                    help='只看哪一种文件，默认 both')
    ap.add_argument('--json', metavar='FILE', help='把完整结果写成 JSON')
    ap.add_argument('--max-list', type=int, default=100, help='屏幕上最多列多少个缺口文件')
    args = ap.parse_args(argv)

    date_int = int(args.date)
    year, month, day = split_ymd(date_int)
    if not (1990 <= year <= 2100 and is_valid_ymd(year, month, day)):
        print('SANITY_FAIL: --date %s 不是合法日期（能否解出 2026-09-84 这种值，'
              '正是 .lc1 位解码写错时的典型症状）' % args.date)
        return 1
    if not os.path.isdir(args.dir):
        print('SANITY_FAIL: --dir 不是目录: %s' % args.dir)
        return 1

    kinds = ('day', 'lc1') if args.kind == 'both' else (args.kind,)
    files = collect_files(args.dir, kinds)
    if not files:
        print('SANITY_FAIL: 在 --dir 下没有发现 %s 文件（子目录 lday/minline 递归扫描）'
              % '/'.join('.' + k for k in kinds))
        return 1

    groups = {}
    missing = []
    errors = []
    for abs_path, rel_path, kind in files:
        market = guess_market(rel_path, kind)
        key = '%s/%s' % (market, kind)
        g = groups.setdefault(key, {'market': market, 'kind': kind, 'total': 0, 'hit': 0})
        g['total'] += 1
        try:
            got, err = last_record_date(abs_path, kind)
        except OSError as exc:
            got, err = None, 'OSError: %s' % exc
        if err:
            errors.append({'file': rel_path, 'kind': kind, 'reason': err})
            continue
        if got == date_int:
            g['hit'] += 1
        else:
            missing.append({'file': rel_path, 'kind': kind, 'market': market, 'last_date': got})

    total = len(files)
    hit = sum(g['hit'] for g in groups.values())
    bad = len(errors)
    coverage = 100.0 * hit / total if total else 0.0
    # 结构性自检：命中数不可能超过文件数；有错误文件时覆盖率天然 < 100%
    if not 0 <= hit <= total:
        print('SANITY_FAIL: hit=%d 落在 [0, %d] 之外' % (hit, total))
        return 1
    if bad and hit == total:
        print('SANITY_FAIL: 存在 %d 个错误文件却算出 100%% 覆盖率' % bad)
        return 1

    result = {
        # 只记目录名，不记绝对路径：JSON 常被贴进 issue，不泄露本机目录结构
        'dir_name': os.path.basename(os.path.abspath(args.dir)),
        'target_date': date_int,
        'coverage_pct': round(coverage, 4),
        'total': total,
        'hit': hit,
        'missing_count': len(missing),
        'error_count': bad,
        'groups': [groups[k] for k in sorted(groups)],
        'missing': missing,
        'errors': errors,
    }

    print('目标日期 %d ｜ 扫描目录 %s ｜ 文件总数 %d' % (date_int, os.path.basename(
        os.path.abspath(args.dir)), total))
    for key in sorted(groups):
        g = groups[key]
        pct = 100.0 * g['hit'] / g['total'] if g['total'] else 0.0
        print('  %-10s %d/%d = %.1f%%' % (key, g['hit'], g['total'], pct))
    print('  总覆盖率 = %d/%d = %.1f%%' % (hit, total, coverage))
    if errors:
        print('  读取/解码错误 %d 个（不计入命中）：' % len(errors))
        for e in errors[:args.max_list]:
            print('    [!] %s -> %s' % (e['file'], e['reason']))
    if missing:
        print('  缺口文件 %d 个（末条日期 != 目标日期）：' % len(missing))
        for m in missing[:args.max_list]:
            print('    %s  末条日期=%s' % (m['file'], m['last_date']))
        if len(missing) > args.max_list:
            print('    ... 其余 %d 个见 --json 输出' % (len(missing) - args.max_list))

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print('JSON 已写入: %s' % os.path.basename(args.json))
    return 0


if __name__ == '__main__':
    sys.exit(main())
