"""注册全部 ORM 模型，供 Base.metadata.create_all 建表。"""

from backend.app.models.corpus import (
    CorpusRecord,
    CorpusTelemetry,
    EquipPriceTco,
    EvalQA,
    FaultCase,
    ProjOperation,
)
from backend.app.models.customer import Contract, Customer
from backend.app.models.device import Device, EquipmentModel, SparePart
from backend.app.models.dispatch import DispatchLog, DispatchOrder, DispatchPlan
from backend.app.models.knowledge import KnowledgeEntry, SolutionDocument
from backend.app.models.maintenance import FaultCode, MaintenancePlan, Warning, WorkOrder, WorkOrderEvent
from backend.app.models.project import Project, ProjectCost, Task, TaskProgress
from backend.app.models.system import ChatMessage, ChatSession, DailyCost, LLMCallLog, User

__all__ = [
    "ChatMessage",
    "ChatSession",
    "Contract",
    "CorpusRecord",
    "CorpusTelemetry",
    "Customer",
    "DailyCost",
    "Device",
    "DispatchLog",
    "DispatchOrder",
    "DispatchPlan",
    "EquipPriceTco",
    "EquipmentModel",
    "EvalQA",
    "FaultCase",
    "FaultCode",
    "KnowledgeEntry",
    "LLMCallLog",
    "MaintenancePlan",
    "ProjOperation",
    "Project",
    "ProjectCost",
    "SolutionDocument",
    "SparePart",
    "Task",
    "TaskProgress",
    "User",
    "Warning",
    "WorkOrder",
    "WorkOrderEvent",
]
