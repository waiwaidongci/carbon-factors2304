# 初始结构

SQLAlchemy启动创建以下业务表：activity_periods, activities, emission_factors, carbon_runs, delivery_schedules, deliveries、users、audits；改造评估新增 departments、department_outputs、carbon_evaluations（2026-11 独立新增表，不改动既有表结构）。Source专属JSON字段由对应validation严格校验；Policy为不可覆盖的版本记录；Run保存source/policy冻结快照；Period仅用于已有期间选择器，不计入核心复杂度。已有数据库升级需先备份，后续字段演进应以独立迁移执行。
