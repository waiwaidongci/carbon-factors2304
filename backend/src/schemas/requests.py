from pydantic import BaseModel, Field


class Login(BaseModel): email:str; password:str
class DataInput(BaseModel): data:dict
class RunInput(BaseModel): name:str=Field(min_length=2,max_length=120); policy_id:int; period:str='全部'
class ReviewInput(BaseModel): note:str=Field(min_length=4,max_length=2000)
class ScheduleInput(BaseModel): name:str=Field(min_length=2,max_length=120); run_id:int; interval_seconds:int=Field(ge=5,le=86400)


class OutputInput(BaseModel):
    department: str = Field(min_length=1, max_length=100)
    period: str = Field(min_length=1, max_length=40)
    output: float = Field(gt=0)
    retrofit: bool | None = None


class OutputBatch(BaseModel):
    rows: list[OutputInput]


class EvaluationInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    earlier_run_id: int
    later_run_id: int
    gap_limit: float = Field(ge=0)
    request_key: str = Field(min_length=8, max_length=160)
