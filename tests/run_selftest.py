#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/run_selftest.py -- 一键自测：不需要任何通达信数据、不需要第三方库。

跑法（在工具包根目录下）：

    python tests/run_selftest.py

它做的事（全部真实执行、真实断言，不存在"看起来通过了"）：
  1) tdx_fmt --selftest           日期位解码/编码往返、错误解码会被拦下、读写往返
  2) sample_builder               生成 10 个合成样本文件（6 个末条日期命中目标日期）
  3) audit_coverage               覆盖率必须**恰好** 60.0%，缺口清单必须恰好 4 个文件，
                                  且四个 (市场/类型) 分组都要出现
  4) tdx_fmt --json               解析器读回来的值 == 生成器写进去的值（往返一致）
  5) check_trade_day              交易日判断回归：含"单文件空洞被误判成节假日"这个坑，
                                  以及"表覆盖不到时必须是未知，不许猜一个交易日"
  6) check_trade_day --verify-holidays
                                  用合成"指数"文件反推休市日，PASS 与 DELTA 两条路都验
  7) 合理性闸门                   .lc1 误当 .day、非法日期等必须当场 SANITY_FAIL

怎么跑的：不是开子进程，而是**在进程内调用各脚本的 main(argv)**（也就是命令行进去的那
个函数），捕获取消码与 stdout。这样绕开了 Windows 命令行引号规则（cmd 会把
`"python.exe" "script.py"` 最外层两个引号吃掉，导致路径含空格时直接报"语法不正确"），
测试结果不受 shell 影响。
工作目录在系统临时目录下（不写进工具包，也不碰任何真实 vipdoc）；有任何 FAIL 则以退出码 1 结束。

依赖：只用 os / sys / json（见 FORMAT.md「依赖」一节）。
"""
import json
import os
import sys

# 不要生成 __pycache__/*.pyc：.pyc 里会烙进本机绝对路径，既污染工具包目录，
# 也会让"包内不得出现本机私有路径"的安检误报。必须在 import 各脚本之前设置。
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(os.environ.get('TMPDIR') or os.environ.get('TEMP') or '/tmp',
                    'tdx_local_data_toolkit_selftest')
sys.path.insert(0, ROOT)

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, ('  <- ' + detail) if detail else ''))
    return bool(ok)


def call(module_name, argv, out_name):
    """在进程内调用工具包脚本的 main(argv)，返回 (退出码, 输出文本)。

    等价于命令行执行：脚本的 __main__ 分支就是 `sys.exit(main())`。
    SystemExit 的字符串（脚本用 `raise SystemExit('SANITY_FAIL: ...')` 抛的那种）
    按 CPython 的行为写进 stderr，这里一并抓进输出文本，好做断言。
    """
    module = __import__(module_name)
    out_path = os.path.join(WORK, out_name)
    saved = sys.stdout
    code = 0
    with open(out_path, 'w', encoding='utf-8') as f:
        sys.stdout = f
        try:
            code = module.main(list(argv))
        except SystemExit as exc:
            if exc.code is None:
                code = 0
            elif isinstance(exc.code, int):
                code = exc.code
            else:
                code = 1
                print(exc.code)          # 真实运行时这个消息走 stderr
        except Exception as exc:         # 未捕获异常也要算成一次失败，不能让整个自测中断
            code = 70
            print('EXCEPTION: %r' % (exc,))
        finally:
            sys.stdout = saved
    if code is None:
        code = 0
    with open(out_path, encoding='utf-8') as f:
        return code, f.read()


def reset_workdir():
    if not os.path.isdir(WORK):
        os.makedirs(WORK)
        return
    for name in os.listdir(WORK):
        p = os.path.join(WORK, name)
        if os.path.isdir(p):
            for sub in os.listdir(p):
                os.remove(os.path.join(p, sub))
            os.rmdir(p)
        else:
            os.remove(p)


def main():
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    print('工作目录: %s' % WORK)
    print('工具包  : %s' % ROOT)
    reset_workdir()

    # ---- 1) 解析器自测 ----------------------------------------------------
    code, text = call('tdx_fmt', ['--selftest'], 'fmt_selftest.txt')
    check('tdx_fmt --selftest 退出码 0', code == 0, 'exit=%d' % code)
    check('解析器自测内部没有 FAIL 行', 'FAIL' not in text,
          '输出里出现 FAIL' if 'FAIL' in text else '')

    # ---- 2) 生成样本 ------------------------------------------------------
    samples = os.path.join(WORK, 'samples')
    code, text = call('sample_builder',
                      ['--out', samples, '--n', '10', '--date', '20260928', '--hit', '6'],
                      'build.txt')
    check('sample_builder 退出码 0', code == 0, 'exit=%d' % code)
    mpath = os.path.join(samples, 'sample_manifest.json')
    check('样本清单已生成', os.path.isfile(mpath), mpath)
    with open(mpath, encoding='utf-8') as f:
        manifest = json.load(f)
    check('样本文件数 == 10', len(manifest['files']) == 10,
          '实际 %d' % len(manifest['files']))
    check('样本里末条日期命中目标日期的文件数 == 6',
          sum(1 for m in manifest['files'] if m['hit']) == 6,
          '实际 %d' % sum(1 for m in manifest['files'] if m['hit']))
    check('每个样本文件都写入了记录', all(m['records'] > 0 for m in manifest['files']))

    # ---- 3) 覆盖率审计（60.0% 是硬断言） ---------------------------------
    audit_json = os.path.join(WORK, 'audit.json')
    code, text = call('audit_coverage',
                      ['--dir', samples, '--date', '20260928', '--json', audit_json],
                      'audit.txt')
    check('audit_coverage 退出码 0', code == 0, 'exit=%d' % code)
    check('审计输出里出现 60.0%', '60.0%' in text, text.strip().replace('\n', ' | '))
    with open(audit_json, encoding='utf-8') as f:
        audit = json.load(f)
    check('total == 10', audit['total'] == 10, 'total=%s' % audit['total'])
    check('hit == 6', audit['hit'] == 6, 'hit=%s' % audit['hit'])
    check('覆盖率 == 60.0（不是四舍五入来的近似值）', audit['coverage_pct'] == 60.0,
          'coverage_pct=%s' % audit['coverage_pct'])
    check('缺口清单恰好 4 个文件', audit['missing_count'] == 4,
          'missing_count=%s' % audit['missing_count'])
    check('没有读取/解码错误', audit['error_count'] == 0, 'error_count=%s' % audit['error_count'])
    check('缺口清单条目数 == missing_count', len(audit['missing']) == audit['missing_count'])
    check('缺口清单里的路径都是相对路径',
          all(not os.path.isabs(m['file']) for m in audit['missing']))
    check('JSON 里不记录本机绝对路径', 'dir_name' in audit and 'dir' not in audit)
    groups = {('%s/%s' % (g['market'], g['kind'])): (g['hit'], g['total'])
              for g in audit['groups']}
    check('四个分组 (sh|sz)/(day|lc1) 都出现', len(groups) == 4, str(sorted(groups)))
    check('分组统计: sh/day 2/3', groups.get('sh/day') == (2, 3), str(groups.get('sh/day')))
    check('分组统计: sz/lc1 1/2', groups.get('sz/lc1') == (1, 2), str(groups.get('sz/lc1')))
    check('分组命中数之和 == 总命中数',
          sum(h for h, _t in groups.values()) == audit['hit'])
    check('缺口清单里的末条日期都不等于目标日期',
          all(m['last_date'] != 20260928 for m in audit['missing']))

    # ---- 4) 往返一致：生成器写进去的 == 解析器读回来的 -------------------
    by_name = {m['file']: m for m in manifest['files']}
    day_name = 'sh600000.day'      # i=0: base_price = 1000
    lc1_name = 'sh600001.lc1'      # i=1: base_price = 1137
    code, text = call('tdx_fmt',
                      ['--day', os.path.join(samples, day_name), '--last', '1', '--json'],
                      'rt_day.txt')
    check('tdx_fmt --day --json 退出码 0', code == 0, 'exit=%d' % code)
    day_rec = json.loads(text)[0]
    check('日线往返: 日期 == 20260928', day_rec['date'] == 20260928, str(day_rec.get('date')))
    check('日线往返: date_str == 20260928', day_rec.get('date_str') == '20260928')
    exp_close = (by_name[day_name]['base_price'] + manifest['days'] - 1 + 2) * 100
    check('日线往返: 收盘价原始整数刻度 == 生成值', day_rec['close_int'] == exp_close,
          '读到 %s 期望 %s' % (day_rec.get('close_int'), exp_close))
    check('日线往返: 成交量 == 300（整手倍数）', day_rec['volume'] == 300,
          'volume=%s' % day_rec.get('volume'))

    code, text = call('tdx_fmt',
                      ['--lc1', os.path.join(samples, lc1_name), '--last', '1', '--json'],
                      'rt_lc1.txt')
    check('tdx_fmt --lc1 --json 退出码 0', code == 0, 'exit=%d' % code)
    lc1_rec = json.loads(text)[0]
    exp_price = by_name[lc1_name]['base_price'] + manifest['days'] - 1 + 239 / 100.0
    check('分钟线往返: 日期 == 20260928', lc1_rec['date'] == 20260928, str(lc1_rec.get('date')))
    check('分钟线往返: 最后一条 minute == 900（15:00）', lc1_rec['minute'] == 900,
          'minute=%s' % lc1_rec.get('minute'))
    check('分钟线往返: clock == 15:00', lc1_rec.get('clock') == '15:00',
          str(lc1_rec.get('clock')))
    check('分钟线往返: 收盘价 == 生成值（float32 精度容差 1e-3）',
          abs(lc1_rec['close'] - (exp_price + 0.02)) < 1e-3,
          '读到 %s 期望 %s' % (lc1_rec.get('close'), exp_price + 0.02))

    # ---- 5) 交易日判断 ----------------------------------------------------
    for value, want_code, want_word in (
            ('20260928', 0, '交易日'),        # 数据末条那天（周一）
            ('20260617', 0, '交易日'),        # 坑：单文件反推会把它当节假日，实际是交易日
            ('20260619', 1, '非交易日'),      # 端午，表里命中
            ('20260625', 0, '交易日'),        # 同一个坑的另一天
            ('20250101', 1, '非交易日'),
            ('20251008', 1, '非交易日'),
            ('20260102', 1, '非交易日'),
            ('20260301', 1, '非交易日'),      # 周日 -> 周末规则
            ('20261001', 3, '未知'),          # 国庆在表覆盖范围外：不许猜
            ('20240102', 3, '未知'),          # 没有 2024 年表：不许猜
    ):
        code, text = call('check_trade_day', [value, '--quiet'], 'day_%s.txt' % value)
        lines = [x for x in text.strip().splitlines() if x.strip()]
        got = lines[-1] if lines else ''
        check('check_trade_day %s -> %s（退出码 %d）' % (value, want_word, want_code),
              code == want_code and got == want_word,
              'exit=%d 输出=%r' % (code, got))

    code, text = call('check_trade_day', ['20261001'], 'day_unknown.txt')
    lines = [x for x in text.strip().splitlines() if x.strip()]
    check('表覆盖不到时行首就是不可信提示',
          lines and lines[0].startswith('注意: 结论不可信') and '未知' in text,
          text.strip().replace('\n', ' | '))

    code, text = call('check_trade_day', ['--list', '2025', '--quiet'], 'list2025.txt')
    days_2025 = [x for x in text.split() if x.isdigit()]
    check('2025 年确认交易日 243 天', len(days_2025) == 243, '实际 %d' % len(days_2025))
    check('2025 交易日列表里没有节假日', not ({'20250101', '20251008'} & set(days_2025)))
    check('2025 年列表不带"无法判断"提示', '无法判断' not in text)

    code, text = call('check_trade_day', ['--list', '2026', '--quiet'], 'list2026.txt')
    days_2026 = [int(x) for x in text.split() if x.isdigit()]
    check('2026 年列表排除了节假日',
          '20260619' not in days_2026 and '20260101' not in days_2026)
    check('2026 年列表末尾在表覆盖范围内（<=20260928）',
          max(days_2026) <= 20260928, 'max=%s' % max(days_2026))
    check('2026 年列表带"无法判断"提示（10-01 及以后）', '无法判断' in text,
          text.strip().splitlines()[0])

    code, text = call('check_trade_day', ['2026010'], 'day_bad.txt')
    check('非法日期参数被拒绝（退出码 2）', code == 2, 'exit=%d' % code)

    # ---- 6) 用合成"指数"文件反推节假日 -----------------------------------
    idx = os.path.join(WORK, 'idx_ok')
    code, text = call('sample_builder',
                      ['--out', idx, '--n', '1', '--hit', '1', '--days', '8', '--bars', '3',
                       '--date', '20260622', '--skip-dates', '20260619'], 'idx_ok_build.txt')
    check('构造含节假日的合成日线文件成功', code == 0, 'exit=%d' % code)
    code, text = call('check_trade_day',
                      ['--verify-holidays', os.path.join(idx, 'sh600000.day')], 'vfy_ok.txt')
    check('--verify-holidays 正确识别节假日（PASS, 退出码 0）',
          code == 0 and 'PASS' in text, text.strip().replace('\n', ' | '))

    idx2 = os.path.join(WORK, 'idx_hole')
    call('sample_builder',
         ['--out', idx2, '--n', '1', '--hit', '1', '--days', '8', '--bars', '3',
          '--date', '20260622', '--skip-dates', '20260617'], 'idx_hole_build.txt')
    code, text = call('check_trade_day',
                      ['--verify-holidays', os.path.join(idx2, 'sh600000.day')], 'vfy_hole.txt')
    check('--verify-holidays 能报出"数据空洞被当成休市日"（DELTA, 退出码 1）',
          code == 1 and 'DELTA' in text and '20260617' in text,
          text.strip().replace('\n', ' | '))

    # ---- 7) 合理性闸门 ----------------------------------------------------
    code, text = call('tdx_fmt', ['--day', os.path.join(samples, lc1_name), '--last', '1'],
                      'guard1.txt')
    check('.lc1 文件当 .day 解析会被拒绝（SANITY_FAIL）',
          code != 0 and 'SANITY_FAIL' in text and '不是 .day' in text,
          text.strip().replace('\n', ' | ')[:160])
    code, text = call('tdx_fmt', ['--lc1', os.path.join(samples, day_name), '--last', '1'],
                      'guard1b.txt')
    check('.day 文件当 .lc1 解析会被拒绝（SANITY_FAIL）',
          code != 0 and 'SANITY_FAIL' in text,
          text.strip().replace('\n', ' | ')[:160])
    code, text = call('audit_coverage', ['--dir', samples, '--date', '20260984'],
                      'guard2.txt')
    check('审计器拒绝 20260984 这种非法目标日期',
          code == 1 and 'SANITY_FAIL' in text, text.strip().replace('\n', ' | ')[:200])
    code, text = call('audit_coverage',
                      ['--dir', os.path.join(WORK, 'no_such_dir'), '--date', '20260928'],
                      'guard3.txt')
    check('目录不存在时 SANITY_FAIL（退出码 1）', code == 1 and 'SANITY_FAIL' in text,
          text.strip().replace('\n', ' | ')[:160])
    empty = os.path.join(WORK, 'empty_dir')
    os.makedirs(empty)
    code, text = call('audit_coverage', ['--dir', empty, '--date', '20260928'], 'guard4.txt')
    check('目录里没有 .day/.lc1 时 SANITY_FAIL',
          code == 1 and 'SANITY_FAIL' in text, text.strip().replace('\n', ' | ')[:200])
    code, text = call('check_trade_day',
                      ['--verify-holidays', os.path.join(samples, lc1_name)], 'guard5.txt')
    check('--verify-holidays 拿到 .lc1（不是 .day）时 SANITY_FAIL',
          code != 0 and 'SANITY_FAIL' in text, text.strip().replace('\n', ' | ')[:200])

    # ---- 汇总 -------------------------------------------------------------
    failed = [n for n, ok in RESULTS if not ok]
    print('')
    print('合计 %d 项，通过 %d 项，失败 %d 项' % (len(RESULTS), len(RESULTS) - len(failed),
                                                 len(failed)))
    for name in failed:
        print('  FAIL: %s' % name)
    print('结论: %s' % ('全部通过' if not failed else '存在失败项'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
