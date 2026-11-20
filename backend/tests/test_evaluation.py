"""评估算法回归：用穷举作参照校验配对最优性与同优均值上下界。

运行：backend 目录下 `pytest tests/test_evaluation.py`（需要 backend/requirements 之外无额外依赖）。
"""
import itertools
import random
from fractions import Fraction

import pytest

from src.services import evaluation as ev


def _brute(retrofit_output, control_output, limit, retrofit_reduction, control_reduction):
    best = None
    candidates = []
    for choice in itertools.product(range(-1, len(control_output)), repeat=len(retrofit_output)):
        used = [c for c in choice if c >= 0]
        if len(set(used)) != len(used):
            continue
        pairs, gap, ok = [], Fraction(0), True
        for i, j in enumerate(choice):
            if j < 0:
                continue
            difference = abs(retrofit_output[i] - control_output[j])
            if difference > limit:
                ok = False
                break
            gap += difference
            pairs.append((i, j))
        if not ok:
            continue
        key = (len(pairs), -gap)
        if best is None or key > best:
            best, candidates = key, [pairs]
        elif key == best:
            candidates.append(pairs)
    return best, candidates


def _case(trial):
    rng = random.Random(trial)
    retrofit_count, control_count = rng.randint(1, 4), rng.randint(1, 4)
    retrofit_output = [Fraction(rng.randint(1, 40)) for _ in range(retrofit_count)]
    control_output = [Fraction(rng.randint(1, 40)) for _ in range(control_count)]
    retrofit_reduction = [Fraction(rng.randint(-1000, 1000), 7) for _ in range(retrofit_count)]
    control_reduction = [Fraction(rng.randint(-1000, 1000), 11) for _ in range(control_count)]
    limit = Fraction(rng.randint(3, 30))
    allowed = {
        (i, j): (abs(r - c),)
        for i, r in enumerate(retrofit_output)
        for j, c in enumerate(control_output)
        if abs(r - c) <= limit
    }
    if not allowed:
        return None
    retrofit = [{'reduction': value} for value in retrofit_reduction]
    control = [{'reduction': value} for value in control_reduction]

    def net(i, j):
        return retrofit_reduction[i] - control_reduction[j]

    lower = ev._min_cost_max_matching(retrofit, control, allowed, net)
    upper = ev._min_cost_max_matching(retrofit, control, allowed, lambda i, j: -net(i, j))
    best, candidates = _brute(retrofit_output, control_output, limit, retrofit_reduction, control_reduction)
    count, negative_gap = best
    if count == 0:
        return None
    assert ev._max_cardinality(retrofit, control, allowed) == count
    index = lambda pairs: sorted((retrofit_reduction.index(r['reduction']), control_reduction.index(c['reduction'])) for r, c in pairs)
    for pairs in (lower, upper):
        chosen = index(pairs)
        assert len(chosen) == count
        gap = sum(abs(retrofit_output[i] - control_output[j]) for i, j in chosen)
        assert gap == -negative_gap
    means = [sum(net(i, j) for i, j in pairs) / len(pairs) for pairs in candidates]
    lower_mean = sum(net(i, j) for i, j in index(lower)) / len(lower)
    upper_mean = sum(net(i, j) for i, j in index(upper)) / len(upper)
    assert lower_mean == min(means)
    assert upper_mean == max(means)
    return count


def test_matching_matches_bruteforce():
    checked = sum(1 for trial in range(400) if _case(trial))
    assert checked > 200


def _basis(departments, outputs, early_kg, late_kg, factors, limit):
    sources = lambda period, key: [
        {'period': period, 'department': name, 'activity': '电力', 'quantity': key[name] / 0.57, 'unit': 'kWh'}
        for name in departments
    ]
    snapshot = lambda period, key, run_id: {
        'run_id': run_id, 'name': period, 'period': period, 'checksum': str(run_id), 'approved': True,
        'policy_version': 'v1', 'policy': {'version': 'v1', 'scope': '范围说明', 'factors': factors},
        'sources': sources(period, key),
    }
    return {
        'earlier': snapshot('2026-10', early_kg, 1),
        'later': snapshot('2026-11', late_kg, 2),
        'gap_limit': str(limit),
        'departments': [
            {'department': name, 'retrofit': name.startswith('R'),
             'outputs': {'2026-10': str(outputs[name]), '2026-11': str(outputs[name])}}
            for name in departments
        ],
    }


def test_bounds_non_degenerate():
    departments = ['R1', 'R2', 'R3', 'C1', 'C2']
    outputs = {'R1': 100, 'R2': 100, 'R3': 200, 'C1': 100, 'C2': 200}
    early_kg = {'R1': 500, 'R2': 100, 'R3': 0, 'C1': 0, 'C2': 0}
    late_kg = {name: 0 for name in departments}
    result = ev.evaluate(_basis(departments, outputs, early_kg, late_kg, {'电力': {'unit': 'kWh', 'kg_per_unit': 0.57}}, 500))
    summary = result['summary']
    assert summary['最大配对数'] == 2
    assert summary['同优均值下界'] == 0.5
    assert summary['同优均值上界'] == 2.5
    assert result['bounds']['lower']['mean_net_reduction'] == 0.5
    assert result['bounds']['upper']['mean_net_reduction'] == 2.5
    assert not summary['均值区间是否退化为单值']


def test_single_pair_interval_degenerates():
    departments = ['R1', 'C1']
    result = ev.evaluate(_basis(
        departments, {'R1': 100, 'C1': 100}, {'R1': 285, 'C1': 0}, {'R1': 0, 'C1': 0},
        {'电力': {'unit': 'kWh', 'kg_per_unit': 0.57}}, 50))
    assert result['summary']['同优均值下界'] == result['summary']['同优均值上界'] == 2.85
    assert result['summary']['均值区间是否退化为单值']
