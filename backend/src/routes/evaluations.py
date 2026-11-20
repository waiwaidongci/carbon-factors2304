import time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from src.config.database import database
from src.controllers.auth import admin, current, editor
from src.models.evaluation import Department, DepartmentOutput, Evaluation
from src.schemas.requests import EvaluationInput, OutputBatch
from src.services.audit import log
from src.services.evaluation import department_rows, publish, submit
from src.utils.serialize import row

router = APIRouter()


@router.get('/departments')
def departments(db=Depends(database), u=Depends(current)):
    flags = {d.name: bool(d.retrofit) for d in db.query(Department).all()}
    return [{'department': k, 'retrofit': v} for k, v in sorted(flags.items())]


@router.get('/department-outputs')
def outputs(db=Depends(database), u=Depends(current)):
    return department_rows(db)


@router.put('/department-outputs')
def save_outputs(b: OutputBatch, db=Depends(database), u=Depends(editor)):
    if not b.rows:
        raise HTTPException(422, '至少登记一行部门产量')
    seen = set()
    for item in b.rows:
        key = (item.department.strip(), item.period.strip())
        if not key[0] or not key[1]:
            raise HTTPException(422, '部门与月份不能为空')
        if key in seen:
            raise HTTPException(422, f'同一部门同一月份重复登记：{key[0]} {key[1]}')
        seen.add(key)
    now = time.time()
    try:
        for item in b.rows:
            name, period = item.department.strip(), item.period.strip()
            dept = db.query(Department).filter_by(name=name).first()
            if not dept:
                dept = Department(name=name, retrofit=bool(item.retrofit), created=now)
                db.add(dept)
            elif item.retrofit is not None:
                dept.retrofit = item.retrofit
            obj = db.query(DepartmentOutput).filter_by(department=name, period=period).first()
            if not obj:
                obj = DepartmentOutput(department=name, period=period, output=Decimal(str(item.output)), created=now)
                db.add(obj)
            else:
                obj.output = Decimal(item.output)
        log(db, u.email, 'department.output', f'{len(b.rows)}行/期间{len({i.period for i in b.rows})}')
        db.commit()
    except Exception:
        db.rollback()
        raise
    return department_rows(db)


@router.get('/evaluations')
def evaluations(db=Depends(database), u=Depends(current)):
    return [row(x) for x in db.query(Evaluation).order_by(Evaluation.id.desc()).all()]


@router.get('/evaluations/{id}')
def evaluation(id: int, db=Depends(database), u=Depends(current)):
    obj = db.get(Evaluation, id)
    if not obj:
        raise HTTPException(404, '评估不存在')
    return row(obj)


@router.post('/evaluations')
def create(b: EvaluationInput, db=Depends(database), u=Depends(editor)):
    obj, created = submit(db, u, b)
    result = row(obj)
    result['created_now'] = created
    return result


@router.post('/evaluations/{id}/publish')
def publish_result(id: int, db=Depends(database), u=Depends(admin)):
    return row(publish(db, u, id))
