"""改造部门净减排配对评估。

口径要点（与研究报告冻结原则一致）：
- 只用「前期月报」提交时冻结的因子版本，重算两份已复核月报的部门排放，避免因子版本差混入改造效果。
- 部门单位产量排放 = 该期部门排放 kgCO2e / 该期部门产量；
  单位产量排放降幅 = 前期强度 − 后期强度；
  一对（改造部门 r，对照部门 c）的净减排 = 降幅(r) − 降幅(c)。
- 配对：改造部门与对照部门一对一；仅允许前期产量绝对差 ≤ 上限的配对；
  先最大化配对数，再最小化各对前期产量绝对差之和。
- 同优配对：配对数与产量绝对差总和都取到最优的全部配对方案。
  结果给出这些方案「每对净减排均值」的下界与上界，并各附一组达到该界的配对。
- 全程使用 Fraction 精确算术判定最优与同优，浮点只用于展示。
"""

import hashlib
import json
import time
from collections import deque
from decimal import Decimal
from fractions import Fraction

import redis
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.config.settings import REDIS_URL
from src.models.evaluation import Department, DepartmentOutput, Evaluation
from src.models.run import Run
from src.services.audit import log
from src.services.calculation import emissions

SCALE = 6
JOB_QUEUE = 'evaluation-jobs'


def _round(value):
    return float(round(value, SCALE))


def _exact(value):
    """有理数的精确表示：能写成有限小数就给十进制，否则给既约分数。"""
    for places in range(0, 19):
        scaled = value * (10 ** places)
        if scaled.denominator == 1:
            digits = str(abs(scaled.numerator)).rjust(places + 1, '0')
            sign = '-' if scaled.numerator < 0 else ''
            if places == 0:
                return sign + digits
            frac = digits[-places:].rstrip('0')
            return sign + digits[:-places] + ('.' + frac if frac else '')
    return f'{value.numerator}/{value.denominator}'


def _decimal(value):
    return Decimal(str(value))


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _checksum(obj):
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


# ---------------------------------------------------------------- 冻结依据

def _run_basis(run):
    return {
        'run_id': run.id,
        'name': run.name,
        'period': run.snapshot['period'],
        'checksum': run.checksum,
        'approved': bool(run.approved),
        'policy_version': run.snapshot['policy']['version'],
        'policy': run.snapshot['policy'],
        'sources': run.snapshot['sources'],
    }


def department_rows(db, periods=None):
    flags = {d.name: bool(d.retrofit) for d in db.query(Department).all()}
    rows = db.query(DepartmentOutput).order_by(DepartmentOutput.department, DepartmentOutput.period).all()
    if periods is not None:
        rows = [r for r in rows if r.period in periods]
    return [
        {'department': r.department, 'period': r.period, 'output': str(r.output), 'retrofit': flags.get(r.department, False)}
        for r in rows
    ]


def build_basis(db, earlier_id, later_id, gap_limit, lock=False):
    earlier = db.get(Run, earlier_id)
    later = db.get(Run, later_id)
    if not earlier or not later:
        raise HTTPException(404, '报告不存在')
    for run in (earlier, later):
        if run.status != 'completed':
            raise HTTPException(409, f'报告「{run.name}」尚未完成')
        if not run.approved:
            raise HTTPException(409, f'报告「{run.name}」尚未复核')
    if earlier.id == later.id:
        raise HTTPException(422, '前期与后期不能是同一份报告')
    ep, lp = earlier.snapshot['period'], later.snapshot['period']
    if ep == '全部' or lp == '全部':
        raise HTTPException(422, '评估要求两份月报各自对应单一月份，不能使用“全部”期间')
    if ep >= lp:
        raise HTTPException(422, f'前期月份 {ep} 必须早于后期月份 {lp}')
    if lock:
        # 与产量登记串行：发布核对时锁住相关产量行，避免核对与改表交错。
        db.query(DepartmentOutput).filter(DepartmentOutput.period.in_([ep, lp])).with_for_update().all()
        db.query(Department).with_for_update().all()
    rows = department_rows(db, {ep, lp})
    by_dept = {}
    for row in rows:
        by_dept.setdefault(row['department'], {})['retrofit'] = row['retrofit']
        by_dept.setdefault(row['department'], {}).setdefault('outputs', {})[row['period']] = row['output']
    missing = sorted(name for name, item in by_dept.items() if set(item.get('outputs', {})) != {ep, lp})
    has_missing_rows = len(rows) != 2 * len(by_dept)
    if missing or has_missing_rows:
        raise HTTPException(422, '部门产量未按两期完整登记：' + ('、'.join(missing) if missing else '存在缺行'))
    if not by_dept:
        raise HTTPException(422, '尚未登记部门产量，无法评估')
    source_departments = {s['department'] for run in (earlier, later) for s in run.snapshot['sources']}
    unregistered = sorted(source_departments - set(by_dept))
    if unregistered:
        raise HTTPException(422, '以下部门在快照中出现但未登记两期产量：' + '、'.join(unregistered))
    # 只评估在两份月报中实际出现过的部门；已登记但两期都无活动记录的部门不参与配对，避免以0排放凑对。
    excluded = sorted(set(by_dept) - source_departments)
    departments = [
        {'department': name, 'retrofit': item['retrofit'], 'outputs': {p: item['outputs'][p] for p in (ep, lp)}}
        for name, item in sorted(by_dept.items()) if name in source_departments
    ]
    if not any(x['retrofit'] for x in departments):
        raise HTTPException(422, '本月两期报告中没有已标记为改造的部门')
    if not any(not x['retrofit'] for x in departments):
        raise HTTPException(422, '本月两期报告中没有可作对照的部门')
    return {
        'earlier': _run_basis(earlier),
        'later': _run_basis(later),
        'gap_limit': str(_decimal(gap_limit).quantize(Decimal(1).scaleb(-SCALE))),
        'departments': departments,
        'excluded': excluded,
    }


# ---------------------------------------------------------------- 部门排放

def _group_emissions(run_basis, factors):
    grouped = {}
    for source in run_basis['sources']:
        try:
            _, kg = emissions(source['activity'], source['quantity'], source['unit'], factors)
        except ValueError as exc:
            raise ValueError(f"报告「{run_basis['name']}」重算失败：{exc}") from exc
        key = source['department']
        grouped[key] = grouped.get(key, Fraction(0)) + Fraction(kg)
    return grouped


# ---------------------------------------------------------------- 精确匹配

class _Flow:
    def __init__(self, size):
        self.size = size
        self.graph = [[] for _ in range(size)]

    def add(self, u, v, cap, cost):
        self.graph[u].append([v, cap, cost, len(self.graph[v])])
        self.graph[v].append([u, 0, (-cost[0], -cost[1]), len(self.graph[u]) - 1])

    def shortest(self, source, sink):
        zero = (Fraction(0), Fraction(0))
        dist = [None] * self.size
        prev = [None] * self.size
        queued = [False] * self.size
        dist[source] = zero
        queue = deque([source])
        queued[source] = True
        while queue:
            u = queue.popleft()
            queued[u] = False
            for index, edge in enumerate(self.graph[u]):
                v, cap, cost, _ = edge
                if cap <= 0:
                    continue
                candidate = (dist[u][0] + cost[0], dist[u][1] + cost[1])
                if dist[v] is None or candidate < dist[v]:
                    dist[v] = candidate
                    prev[v] = (u, index)
                    if not queued[v]:
                        queued[v] = True
                        queue.append(v)
        if dist[sink] is None:
            return None
        path = []
        node = sink
        while node != source:
            u, index = prev[node]
            path.append((u, index))
            node = u
        return path


def _min_cost_max_matching(retrofit, control, allowed, secondary):
    """最大配对数下最小 (产量差, secondary 之和)。

    cost 为二元组并按字典序比较，加法逐分量进行；这是全序阿贝尔群，
    因此连续最短增广路在最大化流量的同时得到字典序最小费用。
    """
    r_count, c_count = len(retrofit), len(control)
    source, sink = 0, 1 + r_count + c_count
    flow = _Flow(sink + 1)
    for i in range(r_count):
        flow.add(source, 1 + i, 1, (Fraction(0), Fraction(0)))
    for j in range(c_count):
        flow.add(1 + r_count + j, sink, 1, (Fraction(0), Fraction(0)))
    for i, r in enumerate(retrofit):
        for j, c in enumerate(control):
            if (i, j) not in allowed:
                continue
            flow.add(1 + i, 1 + r_count + j, 1, allowed[(i, j)] + (secondary(i, j),))
    while True:
        path = flow.shortest(source, sink)
        if path is None:
            break
        for u, index in path:
            edge = flow.graph[u][index]
            edge[1] -= 1
            flow.graph[edge[0]][edge[3]][1] += 1
    pairs = []
    for i, r in enumerate(retrofit):
        for edge in flow.graph[1 + i]:
            v, cap, _, rev = edge
            if 1 + r_count <= v < 1 + r_count + c_count and cap == 0 and flow.graph[v][rev][1] == 1:
                pairs.append((r, control[v - 1 - r_count]))
    return pairs


def _max_cardinality(retrofit, control, allowed):
    """独立校验最大配对数（Kuhn 增广路）。"""
    match = {}

    def augment(i, seen):
        for j in range(len(control)):
            if (i, j) not in allowed or j in seen:
                continue
            seen.add(j)
            if j not in match or augment(match[j], seen):
                match[j] = i
                return True
        return False

    total = sum(1 for i in range(len(retrofit)) if augment(i, set()))
    return total


# ---------------------------------------------------------------- 评估

def evaluate(basis):
    earlier, later = basis['earlier'], basis['later']
    ep, lp = earlier['period'], later['period']
    factors = earlier['policy']['factors']
    early_emissions = _group_emissions(earlier, factors)
    late_emissions = _group_emissions(later, factors)

    warnings = []
    excluded = basis.get('excluded') or []
    if excluded:
        warnings.append('以下部门已登记产量，但在两份月报中均无活动记录，不参与本次配对：' + '、'.join(excluded))
    if later['policy_version'] != earlier['policy_version']:
        warnings.append(
            f"后期报告提交时使用因子版本 {later['policy_version']}；本评估统一按前期冻结因子 {earlier['policy_version']} 重算两份月报。"
        )
    if earlier['period'] == later['period']:
        raise ValueError('两期相同，无法计算降幅')

    rows = []
    for dept in basis['departments']:
        name = dept['department']
        out_e = Fraction(_decimal(dept['outputs'][ep]))
        out_l = Fraction(_decimal(dept['outputs'][lp]))
        if out_e <= 0 or out_l <= 0:
            raise ValueError(f'部门「{name}」的产量必须大于0，不能以0替代')
        kg_e = early_emissions.get(name)
        kg_l = late_emissions.get(name)
        if kg_e is None:
            warnings.append(f'部门「{name}」在 {ep} 无活动记录，按0排放计')
            kg_e = Fraction(0)
        if kg_l is None:
            warnings.append(f'部门「{name}」在 {lp} 无活动记录，按0排放计')
            kg_l = Fraction(0)
        intensity_e = kg_e / out_e
        intensity_l = kg_l / out_l
        rows.append({
            'department': name,
            'retrofit': dept['retrofit'],
            'output_earlier': out_e,
            'output_later': out_l,
            'emissions_earlier': kg_e,
            'emissions_later': kg_l,
            'intensity_earlier': intensity_e,
            'intensity_later': intensity_l,
            'reduction': intensity_e - intensity_l,
        })

    retrofit = [r for r in rows if r['retrofit']]
    control = [r for r in rows if not r['retrofit']]
    gap_limit = Fraction(_decimal(basis['gap_limit']))
    allowed = {}
    for i, r in enumerate(retrofit):
        for j, c in enumerate(control):
            gap = abs(r['output_earlier'] - c['output_earlier'])
            if gap <= gap_limit:
                allowed[(i, j)] = (gap,)

    cardinality = _max_cardinality(retrofit, control, allowed)
    if cardinality == 0:
        raise ValueError('在前述产量差上限下没有任何可配对的改造/对照部门')

    def net(i, j):
        return retrofit[i]['reduction'] - control[j]['reduction']

    lower_pairs = _min_cost_max_matching(retrofit, control, allowed, net)
    upper_pairs = _min_cost_max_matching(retrofit, control, allowed, lambda i, j: -net(i, j))
    if len(lower_pairs) != cardinality or len(upper_pairs) != cardinality:
        raise ValueError('配对数校验失败')

    def describe(pairs):
        items = []
        total_gap = Fraction(0)
        total_net = Fraction(0)
        for r, c in pairs:
            gap = abs(r['output_earlier'] - c['output_earlier'])
            value = r['reduction'] - c['reduction']
            total_gap += gap
            total_net += value
            items.append({
                'retrofit': r['department'],
                'control': c['department'],
                'retrofit_output_earlier': _round(r['output_earlier']),
                'control_output_earlier': _round(c['output_earlier']),
                'output_gap': _round(gap),
                'retrofit_reduction': _round(r['reduction']),
                'control_reduction': _round(c['reduction']),
                'net_reduction': _round(value),
            })
        items.sort(key=lambda x: (x['retrofit'], x['control']))
        mean = total_net / len(pairs)
        return {
            'pairs': items,
            'paired': len(pairs),
            'total_output_gap': _round(total_gap),
            'total_output_gap_exact': _exact(total_gap),
            'total_net_reduction': _round(total_net),
            'mean_net_reduction': _round(mean),
            'mean_net_reduction_exact': _exact(mean),
        }

    lower = describe(lower_pairs)
    upper = describe(upper_pairs)
    lower_sum = sum(r['reduction'] - c['reduction'] for r, c in lower_pairs)
    upper_sum = sum(r['reduction'] - c['reduction'] for r, c in upper_pairs)
    degenerate = lower_sum == upper_sum

    return {
        'summary': {
            '前期报告': earlier['name'],
            '后期报告': later['name'],
            '前期': ep,
            '后期': lp,
            '重算因子版本': earlier['policy_version'],
            '部门数': len(rows),
            '最大配对数': cardinality,
            '最小产量绝对差总和': lower['total_output_gap'],
            '同优均值下界': lower['mean_net_reduction'],
            '同优均值上界': upper['mean_net_reduction'],
            '同优均值下界精确值': lower['mean_net_reduction_exact'],
            '同优均值上界精确值': upper['mean_net_reduction_exact'],
            '均值区间是否退化为单值': degenerate,
        },
        'departments': [
            {
                '部门': r['department'],
                '改造': '是' if r['retrofit'] else '否',
                f'{ep}产量': _round(r['output_earlier']),
                f'{lp}产量': _round(r['output_later']),
                f'{ep}排放kgCO2e': _round(r['emissions_earlier']),
                f'{lp}排放kgCO2e': _round(r['emissions_later']),
                f'{ep}单位产量排放': _round(r['intensity_earlier']),
                f'{lp}单位产量排放': _round(r['intensity_later']),
                '单位产量排放降幅': _round(r['reduction']),
            }
            for r in rows
        ],
        'bounds': {'lower': lower, 'upper': upper},
        'method': (
            '按前期报告冻结因子重算两份月报的部门排放；单位产量排放=部门排放/部门产量。'
            '改造与对照部门一对一，仅允许前期产量绝对差不超过上限的配对；'
            '以精确有理数求解最大配对数，再在其中最小化产量绝对差总和。'
            '对全部同优配对计算每对净减排（改造降幅−对照降幅）的均值，给出精确上下界并各列一组达到该界的配对。'
        ),
        'warnings': warnings,
    }


# ---------------------------------------------------------------- 排队与发布

def _payload_hash(body):
    return _checksum({
        'name': body.name,
        'earlier_run_id': body.earlier_run_id,
        'later_run_id': body.later_run_id,
        'gap_limit': str(_decimal(body.gap_limit).quantize(Decimal(1).scaleb(-SCALE))),
    })


def submit(db, user, body):
    digest = _payload_hash(body)
    existing = db.query(Evaluation).filter_by(request_key=body.request_key).first()
    if existing:
        if existing.payload_hash != digest:
            raise HTTPException(409, '同一申请编号已用于不同评估内容，拒绝重复生效')
        return existing, False
    basis = build_basis(db, body.earlier_run_id, body.later_run_id, body.gap_limit)
    obj = Evaluation(
        name=body.name,
        earlier_run_id=body.earlier_run_id,
        later_run_id=body.later_run_id,
        gap_limit=_decimal(body.gap_limit).quantize(Decimal(1).scaleb(-SCALE)),
        status='queued',
        basis=basis,
        checksum=_checksum(basis),
        request_key=body.request_key,
        payload_hash=digest,
        created=time.time(),
        published=False,
    )
    db.add(obj)
    log(db, user.email, 'evaluation.queue', body.request_key)
    try:
        db.flush()
        redis.Redis.from_url(REDIS_URL).rpush(JOB_QUEUE, obj.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Evaluation).filter_by(request_key=body.request_key).first()
        if existing and existing.payload_hash == digest:
            return existing, False
        raise HTTPException(409, '同一申请编号已用于不同评估内容，拒绝重复生效')
    except Exception:
        db.rollback()
        raise HTTPException(503, '评估队列不可用，请稍后重试')
    return obj, True


def publish(db, user, id):
    obj = db.query(Evaluation).filter_by(id=id).with_for_update().first()
    if not obj:
        raise HTTPException(404, '评估不存在')
    if obj.status != 'completed':
        raise HTTPException(409, '仅可发布已完成的评估')
    if obj.published:
        return obj
    current = build_basis(db, obj.earlier_run_id, obj.later_run_id, _decimal(obj.gap_limit), lock=True)
    if _checksum(current) != obj.checksum:
        raise HTTPException(409, '排队后部门产量、改造标记或报告依据已变更，冻结结论已失效，请重新发起评估')
    obj.published = True
    obj.published_at = time.time()
    log(db, user.email, 'evaluation.publish', f'{obj.id}:{obj.checksum[:12]}')
    db.commit()
    return obj


def process(db, job_id):
    obj = db.query(Evaluation).filter_by(id=job_id).with_for_update().first()
    if not obj or obj.status != 'queued':
        return
    obj.status = 'running'
    db.commit()
    try:
        obj.result = evaluate(obj.basis)
        obj.status = 'completed'
        obj.error = None
    except Exception as exc:  # noqa: BLE001 - 失败信息需要留给业务核对
        obj.status = 'failed'
        obj.error = str(exc)
    db.commit()
