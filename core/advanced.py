"""
Advanced Planner v3 + Diagnostics v1.
"""
import logging
import json
import re
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

from core.tools import TOOLS_BY_NAME
from core.confirmation import manager as confirmation_manager
from core import security_gateway
from core.context_manager import build_context
from core import diagnostics

log = logging.getLogger("secretary.advanced")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PlanState(Enum):
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    STEP_RUNNING = "step_running"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    PENDING = "pending"
    STEP_RUNNING = "step_running"
    EXECUTED = "executed"
    WAITING_CONFIRMATION = "waiting_confirmation"
    FAILED = "failed"


class Step:
    def __init__(self, index: int, tool_name: str, args: Dict[str, Any]):
        self.id = uuid.uuid4().hex
        self.index = index
        self.tool_name = tool_name
        self.args = args
        self.result: Optional[str] = None
        self.status = StepStatus.PENDING
        self.pending_action = None
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "index": self.index,
            "tool": self.tool_name,
            "args": self.args,
            "status": self.status.value,
            "result": self.result,
            "pending_id": getattr(self.pending_action, "id", None),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def __repr__(self):
        return f"Step({self.id[:6]}, {self.tool_name}, {self.status.value})"


class Plan:
    def __init__(self, goal: str):
        self.id = uuid.uuid4().hex
        self.goal = goal
        self.steps: List[Step] = []
        self.state = PlanState.PLANNING
        self.current_index = 0
        self.error_message: Optional[str] = None
        self.results: List[str] = []
        self.created_at = _now_iso()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "state": self.state.value,
            "current_index": self.current_index,
            "created_at": self.created_at,
            "error": self.error_message,
            "steps": [s.to_dict() for s in self.steps],
        }


class AdvancedPlanner:
    MAX_STEPS = 10
    MAX_ITERATIONS = 30

    def __init__(self, llm, memory=None, personality=None):
        self.llm = llm
        self.memory = memory
        self.personality = personality
        self.active_plan: Optional[Plan] = None
        self._cancel_requested = False

    def create_plan(self, goal: str, context=None) -> Plan:
        plan = Plan(goal)
        self.active_plan = plan
        plan.state = PlanState.PLANNING
        self._cancel_requested = False

        if context is None:
            try:
                context = build_context(
                    request=goal, memory=self.memory, personality=self.personality
                )
            except Exception as e:
                log.warning(f"Не удалось построить контекст: {e}")
                context = None

        context_block = ""
        if context is not None:
            lines = list(context.core_memory) + list(context.relevant_memory)
            if lines:
                context_block += "ИЗВЕСТНО О ПОЛЬЗОВАТЕЛЕ:\n" + "\n".join(lines) + "\n"
            if context.active_task_summary:
                context_block += f"АКТИВНАЯ ЗАДАЧА: {context.active_task_summary}\n"
            if context.confirmation_hint:
                context_block += f"СОСТОЯНИЕ: {context.confirmation_hint}\n"

        prompt = f"""Ты — планировщик задач. Пользователь просит: {goal}

{context_block}
Разбей задачу на последовательные шаги. Каждый шаг — вызов одного инструмента.

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
{chr(10).join(f"- {name}" for name in sorted(TOOLS_BY_NAME.keys()))}

ФОРМАТ ОТВЕТА (строго JSON):
{{"steps": [{{"tool": "имя", "args": {{"параметр": "значение"}}}}]}}

ПРАВИЛА:
1. Используй ТОЛЬКО инструменты из списка выше
2. Максимум {self.MAX_STEPS} шагов
3. Каждый шаг — ОДИН инструмент
4. Верни ТОЛЬКО JSON
"""

        try:
            response = self.llm.ask(
                self.llm.smart, prompt, max_tool_hops=0, include_history=False
            )

            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if not json_match:
                raise ValueError("LLM не вернул JSON")

            plan_data = json.loads(json_match.group())
            steps_data = plan_data.get("steps", [])

            if not steps_data:
                raise ValueError("План пустой")
            if len(steps_data) > self.MAX_STEPS:
                raise ValueError(f"Слишком много шагов: {len(steps_data)}")

            steps = []
            for i, step_data in enumerate(steps_data):
                tool_name = step_data.get("tool")
                if tool_name not in TOOLS_BY_NAME:
                    raise ValueError(f"Неизвестный инструмент: {tool_name}")
                steps.append(Step(i, tool_name, step_data.get("args", {})))

            plan.steps = steps
            plan.state = PlanState.READY
            diagnostics.log_event("advanced", event="plan_created",
                                  plan=plan.id[:6], steps=len(steps))
            log.info(f"План {plan.id[:6]} создан: {len(steps)} шагов")
            return plan

        except Exception as e:
            log.error(f"Ошибка создания плана: {e}")
            plan.state = PlanState.FAILED
            plan.error_message = str(e)
            diagnostics.log_event("advanced", event="plan_failed", error=str(e)[:120])
            return plan

    def run_plan(self) -> str:
        plan = self.active_plan
        if plan is None:
            return "Нет активного плана."
        if plan.state not in (PlanState.READY, PlanState.RUNNING):
            return f"План не готов к запуску (state={plan.state.value})."

        plan.state = PlanState.RUNNING
        iterations = 0

        while plan.current_index < len(plan.steps):
            iterations += 1
            if iterations > self.MAX_ITERATIONS:
                plan.state = PlanState.FAILED
                plan.error_message = "Превышен лимит итераций"
                return "Ошибка: превышен лимит итераций."

            if self._cancel_requested:
                self._cancel_requested = False
                plan.state = PlanState.CANCELLED
                plan.error_message = "План отменён пользователем"
                diagnostics.log_event("advanced", event="plan_state", state="cancelled")
                return "План отменён."

            step = plan.steps[plan.current_index]

            if step.status == StepStatus.EXECUTED:
                plan.current_index += 1
                continue

            if step.status != StepStatus.PENDING:
                plan.state = PlanState.FAILED
                plan.error_message = (
                    f"Шаг {step.id[:6]} в недопустимом состоянии {step.status.value}"
                )
                return f"Ошибка: шаг {step.index} нельзя запустить повторно."

            step.status = StepStatus.STEP_RUNNING
            step.started_at = _now_iso()
            plan.state = PlanState.STEP_RUNNING
            diagnostics.log_event("advanced", event="step_start",
                                  step=step.index, tool=step.tool_name)

            pending_before = confirmation_manager.get_active()

            try:
                result = security_gateway.execute(
                    name=step.tool_name,
                    fn=TOOLS_BY_NAME.get(step.tool_name),
                    args=step.args,
                )
                if result == security_gateway.DENY_MESSAGE:
                    raise ValueError("шаг запрещён политикой безопасности")
                step.result = result
                log.info(f"Шаг {step.index} ({step.id[:6]}) выполнен: {step.tool_name}")
            except Exception as e:
                log.error(f"Ошибка шага {step.index}: {e}")
                step.status = StepStatus.FAILED
                step.completed_at = _now_iso()
                step.result = f"Ошибка: {e}"
                plan.state = PlanState.FAILED
                plan.error_message = f"Шаг {step.index} упал: {e}"
                diagnostics.log_event("advanced", event="step_failed",
                                      step=step.index, error=str(e)[:120])
                return f"Ошибка на шаге {step.index}: {e}"

            pending_after = confirmation_manager.get_active()

            if (pending_after is not None
                    and pending_after is not pending_before
                    and pending_after.status == "pending"):
                step.status = StepStatus.WAITING_CONFIRMATION
                step.pending_action = pending_after
                plan.state = PlanState.WAITING_FOR_CONFIRMATION
                diagnostics.log_event("advanced", event="plan_waiting", step=step.index)
                log.info(f"План {plan.id[:6]} ждёт подтверждения шага {step.index}")
                return (
                    f"Ожидает подтверждения: {pending_after.summary}. "
                    f"Скажи «да» для продолжения или «нет» для отмены."
                )

            step.status = StepStatus.EXECUTED
            step.completed_at = _now_iso()
            plan.results.append(result)
            plan.current_index += 1
            diagnostics.log_event("advanced", event="step_done",
                                  step=step.index, tool=step.tool_name)

        if self._cancel_requested:
            self._cancel_requested = False
            plan.state = PlanState.CANCELLED
            plan.error_message = "План отменён пользователем"
            diagnostics.log_event("advanced", event="plan_state", state="cancelled")
            return "План отменён."

        plan.state = PlanState.COMPLETED
        diagnostics.log_event("advanced", event="plan_state", state="completed")
        log.info(f"План {plan.id[:6]} завершён")
        return "План завершён успешно."

    def resume_if_waiting(self) -> str:
        plan = self.active_plan
        if plan is None or plan.state != PlanState.WAITING_FOR_CONFIRMATION:
            return ""

        step = plan.steps[plan.current_index]
        if step.pending_action is None:
            return ""

        status = step.pending_action.status
        log.info(f"Возобновление плана {plan.id[:6]}: pending status={status}")
        diagnostics.log_event("advanced", event="resume", status=status)

        if status == "executed":
            step.status = StepStatus.EXECUTED
            step.completed_at = _now_iso()
            plan.results.append(step.result or "подтверждено и выполнено")
            plan.current_index += 1
            step.pending_action = None
            plan.state = PlanState.READY
            return self.run_plan()

        if status == "rejected":
            step.status = StepStatus.FAILED
            step.completed_at = _now_iso()
            plan.state = PlanState.CANCELLED
            plan.error_message = f"Шаг {step.index} отклонён пользователем"
            diagnostics.log_event("advanced", event="plan_state", state="cancelled")
            return "План отменён."

        if status in ("expired", "aborted", "discarded"):
            step.status = StepStatus.FAILED
            step.completed_at = _now_iso()
            plan.state = PlanState.FAILED
            plan.error_message = f"Шаг {step.index}: подтверждение не получено вовремя"
            diagnostics.log_event("advanced", event="plan_state", state="failed")
            return "План остановлен: подтверждение не получено вовремя."

        return ""

    def cancel_plan(self) -> str:
        plan = self.active_plan
        if plan is None:
            return "Нет активного плана."
        if plan.state in (PlanState.COMPLETED, PlanState.FAILED, PlanState.CANCELLED):
            return f"План уже завершён ({plan.state.value})."

        if plan.state == PlanState.WAITING_FOR_CONFIRMATION:
            step = plan.steps[plan.current_index]
            if step.pending_action is not None:
                confirmation_manager.reject(step.pending_action.id)
            step.status = StepStatus.FAILED
            step.completed_at = _now_iso()
            plan.state = PlanState.CANCELLED
            plan.error_message = "План отменён пользователем"
            diagnostics.log_event("advanced", event="plan_state", state="cancelled")
            return "План отменён."

        if plan.state == PlanState.STEP_RUNNING:
            self._cancel_requested = True
            return ("Отмена запрошена: текущий шаг завершится штатно, "
                    "следующие шаги не запустятся.")

        plan.state = PlanState.CANCELLED
        plan.error_message = "План отменён пользователем"
        diagnostics.log_event("advanced", event="plan_state", state="cancelled")
        return "План отменён."

    def get_status(self) -> dict:
        if self.active_plan is None:
            return {"plan": None}
        return {"plan": self.active_plan.to_dict()}


_planner: Optional[AdvancedPlanner] = None


def get_planner() -> Optional[AdvancedPlanner]:
    return _planner


def init_planner(llm, memory=None, personality=None):
    global _planner
    _planner = AdvancedPlanner(llm, memory=memory, personality=personality)


def advanced_task(task: str) -> str:
    """Решить сложную многошаговую задачу (все шаги через Security Gateway)."""
    planner = get_planner()
    if planner is None:
        return "Планировщик не инициализирован."

    plan = planner.create_plan(task)
    if plan.state == PlanState.FAILED:
        return f"Не смогла составить план: {plan.error_message}"

    return planner.run_plan()


def cancel_advanced_task() -> str:
    """Отменить активный многошаговый план."""
    planner = get_planner()
    if planner is None:
        return "Планировщик не инициализирован."
    return planner.cancel_plan() 
