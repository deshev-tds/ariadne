from __future__ import annotations

import ast
import copy
import json
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal

GUIDED_STATE_KEY = "offsec_guided_state"
GUIDED_RUN_COMMAND_BUDGET_DEFAULT = 8
GUIDED_MAX_OBSERVATIONS = 5
GUIDED_MAX_OBSERVATION_SUMMARY_CHARS = 200

OFFSEC_PRIMARY_ACTION_CLASSES = {
    "passive_recon",
    "light_probe",
    "focused_validation",
    "deep_scan",
    "broad_fuzzing",
    "exploitation",
    "remediation",
    "local_system_modification",
}

OFFSEC_EXECUTION_CONTEXTS = {"remote_observer", "local_operator"}
OFFSEC_STEP_RESULT_STATUSES = {
    "complete",
    "blocked",
    "needs_reorder",
    "needs_replan",
}
OFFSEC_OBSERVATION_SOURCE_TYPES = {
    "terminal_result",
    "terminal_artifact",
    "corpus_evidence",
    "docs_evidence",
    "inference",
}
OFFSEC_REMOTE_FORBIDDEN_PRIMARY = {"remediation", "local_system_modification"}
OFFSEC_APPROVAL_TERMS = {
    "continue",
    "next",
    "proceed",
    "go on",
    "go ahead",
    "continue please",
    "next step",
}

OffsecActionClass = Literal[
    "passive_recon",
    "light_probe",
    "focused_validation",
    "deep_scan",
    "broad_fuzzing",
    "exploitation",
    "remediation",
    "local_system_modification",
]
OffsecExecutionContext = Literal["remote_observer", "local_operator"]
OffsecStepResultStatus = Literal[
    "complete",
    "blocked",
    "needs_reorder",
    "needs_replan",
]
OffsecObservationSourceType = Literal[
    "terminal_result",
    "terminal_artifact",
    "corpus_evidence",
    "docs_evidence",
    "inference",
]


class GuidedAcceptanceCriterion(BaseModel):
    id: str = Field(..., description="Stable criterion id within the step.")
    text: str = Field(..., description="Short acceptance criterion text.")


class GuidedPlanStep(BaseModel):
    id: str = Field(..., description="Stable step id, for example step_1.")
    title: str = Field(..., description="Short step title.")
    purpose: str = Field(..., description="What this step is trying to achieve.")
    primary_action_classes: list[OffsecActionClass] = Field(
        ...,
        description="One or two action classes for the step.",
    )
    suggested_tools: list[str] = Field(
        default_factory=list,
        description="Short list of preferred tool names.",
    )
    acceptance_criteria: list[GuidedAcceptanceCriterion] = Field(
        ...,
        description="Two to five acceptance criteria objects.",
    )
    forbidden_action_classes: list[OffsecActionClass] = Field(
        ...,
        description="Non-empty list of action classes that are out of scope for this step.",
    )


class GuidedObservation(BaseModel):
    id: str = Field(..., description="Stable observation id within the step result.")
    summary: str = Field(..., description="Telegraphic observation summary, max 200 chars.")
    source_type: OffsecObservationSourceType = Field(
        ...,
        description="Where the observation came from.",
    )
    source_ref: dict[str, Any] = Field(
        ...,
        description="Structured provenance reference for the observation source.",
    )
    confidence: float = Field(..., description="Confidence score for the observation.")
    implication: str = Field(..., description="Short implication for the plan or target picture.")


class GuidedPlanUpdate(BaseModel):
    type: Literal["reorder", "revise"] = Field(
        ...,
        description="reorder for partial reprioritization, revise for a full plan replacement.",
    )
    active_step_id: str | None = Field(
        default=None,
        description="Active step id after the update.",
    )
    ordered_step_ids: list[str] | None = Field(
        default=None,
        description="Required when type is reorder.",
    )
    phase: str | None = Field(
        default=None,
        description="Optional updated phase when revising the plan.",
    )
    assumptions: list[str] | None = Field(
        default=None,
        description="Optional updated assumptions when revising the plan.",
    )
    steps: list[GuidedPlanStep] | None = Field(
        default=None,
        description="Full replacement step list when type is revise.",
    )

OFFSEC_OPERATIONAL_VERBS = (
    "assess",
    "test",
    "scan",
    "recon",
    "enumerate",
    "investigate",
    "validate",
    "probe",
    "map",
    "proceed",
)
OFFSEC_REFERENCE_VERBS = (
    "what is",
    "explain",
    "give me examples",
    "summarize",
    "compare",
    "what are",
    "tell me about",
)

URL_RE = re.compile(r"https?://", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HOST_RE = re.compile(r"\b[a-z0-9.-]+\.[a-z]{2,}\b", re.IGNORECASE)
CHAINING_RE = re.compile(r"(;|&&|\|\||\n)")
BACKGROUND_RE = re.compile(r"(?:^|[^>])&\s*$")


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _copy_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    return copy.deepcopy(state) if isinstance(state, dict) else None


def _normalize_mapping_candidate(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return value.model_dump()
        except Exception:
            return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return json.loads(stripped)
            except Exception:
                try:
                    return ast.literal_eval(stripped)
                except Exception:
                    return value
    return value


def build_guided_run_id() -> str:
    return f"offsec-guided-{uuid.uuid4().hex[:12]}"


def detect_offsec_operational_turn(text: str) -> bool:
    normalized = _normalize_lower(text)
    if not normalized:
        return False
    if any(term in normalized for term in OFFSEC_REFERENCE_VERBS):
        return False
    has_operational_verb = any(term in normalized for term in OFFSEC_OPERATIONAL_VERBS)
    has_target_anchor = bool(URL_RE.search(normalized) or IP_RE.search(normalized) or HOST_RE.search(normalized))
    return has_operational_verb or has_target_anchor


def default_execution_context_for_text(text: str) -> str | None:
    normalized = _normalize_lower(text)
    if not normalized:
        return None
    if URL_RE.search(normalized) or IP_RE.search(normalized):
        return "remote_observer"
    if HOST_RE.search(normalized):
        if "localhost" in normalized or "127.0.0.1" in normalized:
            return None
        return "remote_observer"
    return None


def is_approval_turn(text: str) -> bool:
    normalized = _normalize_lower(text)
    return normalized in OFFSEC_APPROVAL_TERMS


def should_block_command_payload(command: Any) -> bool:
    if not isinstance(command, str):
        return False
    if CHAINING_RE.search(command):
        return True
    return bool(BACKGROUND_RE.search(command))


def resolve_guided_state_from_messages(messages: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for message in reversed(messages or []):
        if message.get("role") != "assistant":
            continue
        state = message.get(GUIDED_STATE_KEY)
        if isinstance(state, dict):
            return _copy_state(state)
    return None


def apply_continue_signal_to_state(
    state: dict[str, Any] | None, latest_user_text: str
) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    if not state.get("waiting_for_confirmation"):
        return _copy_state(state)
    if not is_approval_turn(latest_user_text):
        return _copy_state(state)

    recommended_next = _normalize_text(state.get("recommended_next_step_id"))
    if not recommended_next:
        return _copy_state(state)

    next_state = _copy_state(state) or {}
    next_state["active_step_id"] = recommended_next
    next_state["recommended_next_step_id"] = ""
    next_state["waiting_for_confirmation"] = False
    next_state["current_step_run_command_count"] = 0
    next_state["remaining_step_run_command_budget"] = int(
        next_state.get("step_run_command_budget", GUIDED_RUN_COMMAND_BUDGET_DEFAULT)
    )
    return next_state


def validate_execution_context(
    execution_context: Any, *, objective_text: str = ""
) -> tuple[str | None, str | None]:
    normalized = _normalize_lower(execution_context)
    if normalized in OFFSEC_EXECUTION_CONTEXTS:
        return normalized, None
    guessed = default_execution_context_for_text(objective_text)
    if guessed:
        return guessed, None
    return None, (
        "Execution context is required for guided Offsec terminal work. "
        "Use 'remote_observer' for remote target assessment or 'local_operator' when the terminal host is the operational target."
    )


def _validate_primary_action_classes(classes: Any, execution_context: str) -> tuple[list[str] | None, str | None]:
    if not isinstance(classes, list):
        return None, "primary_action_classes must be a list."
    normalized = [_normalize_lower(item) for item in classes if _normalize_text(item)]
    if not (1 <= len(normalized) <= 2):
        return None, "primary_action_classes must contain 1 to 2 items."
    if len(set(normalized)) != len(normalized):
        return None, "primary_action_classes must not contain duplicates."
    invalid = [item for item in normalized if item not in OFFSEC_PRIMARY_ACTION_CLASSES]
    if invalid:
        return None, f"Invalid primary_action_classes: {', '.join(invalid)}"
    if execution_context == "remote_observer":
        blocked = [item for item in normalized if item in OFFSEC_REMOTE_FORBIDDEN_PRIMARY]
        if blocked:
            return None, (
                "remote_observer steps cannot use primary_action_classes: "
                + ", ".join(blocked)
            )
    return normalized, None


def _validate_forbidden_action_classes(classes: Any) -> tuple[list[str] | None, str | None]:
    if not isinstance(classes, list):
        return None, "forbidden_action_classes must be a list."
    normalized = [_normalize_lower(item) for item in classes if _normalize_text(item)]
    if not normalized:
        return None, "forbidden_action_classes must not be empty."
    if len(set(normalized)) != len(normalized):
        return None, "forbidden_action_classes must not contain duplicates."
    invalid = [item for item in normalized if item not in OFFSEC_PRIMARY_ACTION_CLASSES]
    if invalid:
        return None, f"Invalid forbidden_action_classes: {', '.join(invalid)}"
    return normalized, None


def _validate_acceptance_criteria(criteria: Any) -> tuple[list[dict[str, str]] | None, str | None]:
    if not isinstance(criteria, list):
        return None, "acceptance_criteria must be a list."
    if not (2 <= len(criteria) <= 5):
        return None, "acceptance_criteria must contain 2 to 5 items."
    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in criteria:
        item = _normalize_mapping_candidate(item)
        if not isinstance(item, dict):
            return None, "Each acceptance criterion must be an object."
        criterion_id = _normalize_text(item.get("id"))
        text = _normalize_text(item.get("text"))
        if not criterion_id or not text:
            return None, "Each acceptance criterion requires id and text."
        if criterion_id in seen_ids:
            return None, "Duplicate acceptance criterion ids are not allowed within a step."
        seen_ids.add(criterion_id)
        normalized.append({"id": criterion_id, "text": text})
    return normalized, None


def _normalize_suggested_tools(tools: Any) -> tuple[list[str] | None, str | None]:
    if tools is None:
        return [], None
    if not isinstance(tools, list):
        return None, "suggested_tools must be a list."
    normalized = [_normalize_text(item) for item in tools if _normalize_text(item)]
    return normalized, None


def validate_plan_steps(
    steps: Any, *, execution_context: str
) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(steps, list):
        return None, "steps must be a list."
    if not (2 <= len(steps) <= 6):
        return None, "steps must contain 2 to 6 items."

    normalized_steps: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for step in steps:
        step = _normalize_mapping_candidate(step)
        if not isinstance(step, dict):
            return None, "Each step must be an object."
        step_id = _normalize_text(step.get("id") or step.get("step_id"))
        purpose = _normalize_text(step.get("purpose") or step.get("description"))
        title = _normalize_text(step.get("title") or step.get("name") or purpose or step_id)
        purpose = purpose or title
        if not step_id or not title or not purpose:
            return None, "Each step requires id, title, and purpose."
        if step_id in seen_ids:
            return None, "Duplicate step ids are not allowed."
        seen_ids.add(step_id)

        primary_action_classes, error = _validate_primary_action_classes(
            step.get("primary_action_classes"),
            execution_context,
        )
        if error:
            return None, f"{step_id}: {error}"

        forbidden_action_classes, error = _validate_forbidden_action_classes(
            step.get("forbidden_action_classes")
        )
        if error:
            return None, f"{step_id}: {error}"

        if set(primary_action_classes or []).intersection(forbidden_action_classes or []):
            return None, f"{step_id}: a step cannot forbid one of its primary_action_classes."

        suggested_tools, error = _normalize_suggested_tools(step.get("suggested_tools"))
        if error:
            return None, f"{step_id}: {error}"

        acceptance_criteria, error = _validate_acceptance_criteria(
            step.get("acceptance_criteria")
        )
        if error:
            return None, f"{step_id}: {error}"

        normalized_steps.append(
            {
                "id": step_id,
                "title": title,
                "purpose": purpose,
                "primary_action_classes": primary_action_classes,
                "suggested_tools": suggested_tools,
                "acceptance_criteria": acceptance_criteria,
                "forbidden_action_classes": forbidden_action_classes,
            }
        )

    return normalized_steps, None


def build_guided_plan_state(
    *,
    objective: Any,
    phase: Any,
    execution_context: Any,
    bound_terminal_id: Any,
    assumptions: Any,
    active_step_id: Any,
    steps: Any,
    corpus_book_ids: Any = None,
    corpus_note: Any = "",
    prior_state: dict[str, Any] | None = None,
    budget: int = GUIDED_RUN_COMMAND_BUDGET_DEFAULT,
) -> tuple[dict[str, Any] | None, str | None]:
    objective_text = _normalize_text(objective)
    if not objective_text:
        return None, "objective is required."

    phase_text = _normalize_text(phase)
    if not phase_text:
        return None, "phase is required."

    execution_context_value, context_error = validate_execution_context(
        execution_context,
        objective_text=objective_text,
    )
    if context_error:
        return None, context_error

    terminal_id = _normalize_text(bound_terminal_id)
    if not terminal_id:
        return None, "bound_terminal_id is required for guided Offsec terminal work."

    normalized_steps, error = validate_plan_steps(
        steps,
        execution_context=execution_context_value,
    )
    if error:
        return None, error

    active_step = _normalize_text(active_step_id)
    if active_step not in {step["id"] for step in normalized_steps}:
        return None, "active_step_id must point to a valid step."

    normalized_assumptions = []
    if assumptions is None:
        normalized_assumptions = []
    elif isinstance(assumptions, list):
        normalized_assumptions = [
            _normalize_text(item) for item in assumptions if _normalize_text(item)
        ]
    else:
        return None, "assumptions must be a list."

    normalized_corpus_book_ids = []
    if corpus_book_ids is None:
        normalized_corpus_book_ids = []
    elif isinstance(corpus_book_ids, list):
        normalized_corpus_book_ids = [
            _normalize_text(item) for item in corpus_book_ids if _normalize_text(item)
        ]
    else:
        return None, "corpus_book_ids must be a list."

    step_budget = max(1, int(budget or GUIDED_RUN_COMMAND_BUDGET_DEFAULT))
    existing = _copy_state(prior_state) or {}
    return (
        {
            "guided_run_id": existing.get("guided_run_id") or build_guided_run_id(),
            "objective": objective_text,
            "phase": phase_text,
            "execution_context": execution_context_value,
            "bound_terminal_id": terminal_id,
            "assumptions": normalized_assumptions,
            "steps": normalized_steps,
            "active_step_id": active_step,
            "completed_step_ids": [],
            "recommended_next_step_id": "",
            "latest_observations": [],
            "waiting_for_confirmation": False,
            "current_step_run_command_count": 0,
            "step_run_command_budget": step_budget,
            "remaining_step_run_command_budget": step_budget,
            "corpus_book_ids": normalized_corpus_book_ids,
            "corpus_note": _normalize_text(corpus_note),
        },
        None,
    )


def _criterion_ids_for_state_step(step: dict[str, Any]) -> set[str]:
    return {
        _normalize_text(item.get("id"))
        for item in step.get("acceptance_criteria") or []
        if isinstance(item, dict) and _normalize_text(item.get("id"))
    }


def _validate_observations(observations: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(observations, list):
        return None, "observations must be a list."
    if not (1 <= len(observations) <= GUIDED_MAX_OBSERVATIONS):
        return None, f"observations must contain 1 to {GUIDED_MAX_OBSERVATIONS} items."

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in observations:
        item = _normalize_mapping_candidate(item)
        if not isinstance(item, dict):
            return None, "Each observation must be an object."
        obs_id = _normalize_text(item.get("id"))
        summary = _normalize_text(item.get("summary"))
        source_type = _normalize_lower(item.get("source_type"))
        source_ref = item.get("source_ref")
        implication = _normalize_text(item.get("implication"))
        confidence = item.get("confidence")

        if not obs_id or not summary or not source_type or not implication:
            return None, "Each observation requires id, summary, source_type, and implication."
        if len(summary) > GUIDED_MAX_OBSERVATION_SUMMARY_CHARS:
            return None, (
                f"Observation summaries must be at most {GUIDED_MAX_OBSERVATION_SUMMARY_CHARS} characters."
            )
        if obs_id in seen_ids:
            return None, "Observation ids must be unique."
        if source_type not in OFFSEC_OBSERVATION_SOURCE_TYPES:
            return None, f"Invalid observation source_type: {source_type}"
        if not isinstance(source_ref, dict) or not source_ref:
            return None, "Each observation requires a non-empty source_ref object."
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            return None, "Each observation requires numeric confidence."

        seen_ids.add(obs_id)
        normalized.append(
            {
                "id": obs_id,
                "summary": summary,
                "source_type": source_type,
                "source_ref": copy.deepcopy(source_ref),
                "confidence": confidence_value,
                "implication": implication,
            }
        )

    return normalized, None


def _normalize_plan_update(plan_update: Any) -> tuple[dict[str, Any] | None, str | None]:
    plan_update = _normalize_mapping_candidate(plan_update)
    if plan_update in (None, "", {}):
        return {"type": "none"}, None
    if not isinstance(plan_update, dict):
        return None, "plan_update must be an object."

    update_type = _normalize_lower(plan_update.get("type"))
    if update_type not in {"reorder", "revise"}:
        return None, "plan_update.type must be 'reorder' or 'revise'."

    normalized: dict[str, Any] = {"type": update_type}
    if update_type == "reorder":
        ordered_step_ids = plan_update.get("ordered_step_ids")
        if not isinstance(ordered_step_ids, list):
            return None, "reorder requires ordered_step_ids."
        normalized["ordered_step_ids"] = [
            _normalize_text(item) for item in ordered_step_ids if _normalize_text(item)
        ]
        normalized["active_step_id"] = _normalize_text(plan_update.get("active_step_id"))
        if not normalized["ordered_step_ids"] or not normalized["active_step_id"]:
            return None, "reorder requires ordered_step_ids and active_step_id."
        return normalized, None

    normalized["steps"] = plan_update.get("steps")
    normalized["active_step_id"] = _normalize_text(plan_update.get("active_step_id"))
    normalized["phase"] = _normalize_text(plan_update.get("phase"))
    assumptions = plan_update.get("assumptions")
    if assumptions is not None and not isinstance(assumptions, list):
        return None, "revise assumptions must be a list when provided."
    normalized["assumptions"] = [
        _normalize_text(item) for item in assumptions or [] if _normalize_text(item)
    ]
    if normalized["steps"] is None or not normalized["active_step_id"]:
        return None, "revise requires steps and active_step_id."
    return normalized, None


def apply_guided_step_result(
    *,
    state: dict[str, Any],
    step_id: Any,
    status: Any,
    observations: Any,
    criteria_met_ids: Any,
    criteria_unmet_ids: Any,
    recommended_next_step_id: Any = "",
    plan_update: Any = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(state, dict):
        return None, "No active guided state is available."

    step_id_text = _normalize_text(step_id)
    active_step_id = _normalize_text(state.get("active_step_id"))
    if not step_id_text or step_id_text != active_step_id:
        return None, "step_id must match the active_step_id."

    status_value = _normalize_lower(status)
    if status_value not in OFFSEC_STEP_RESULT_STATUSES:
        return None, f"Invalid status: {status_value}"

    current_step = next(
        (step for step in state.get("steps") or [] if step.get("id") == step_id_text),
        None,
    )
    if current_step is None:
        return None, "Active step is missing from guided state."

    normalized_observations, error = _validate_observations(observations)
    if error:
        return None, error

    if not isinstance(criteria_met_ids, list) or not isinstance(criteria_unmet_ids, list):
        return None, "criteria_met_ids and criteria_unmet_ids must be lists."
    normalized_met = [_normalize_text(item) for item in criteria_met_ids if _normalize_text(item)]
    normalized_unmet = [
        _normalize_text(item) for item in criteria_unmet_ids if _normalize_text(item)
    ]
    if not normalized_met and not normalized_unmet:
        return None, "criteria_met_ids and criteria_unmet_ids cannot both be empty."

    allowed_ids = _criterion_ids_for_state_step(current_step)
    if any(item not in allowed_ids for item in [*normalized_met, *normalized_unmet]):
        return None, "Unknown criterion ids were provided for the active step."
    if set(normalized_met).intersection(normalized_unmet):
        return None, "A criterion cannot be both met and unmet."

    normalized_next = _normalize_text(recommended_next_step_id)
    normalized_update, error = _normalize_plan_update(plan_update)
    if error:
        return None, error

    next_state = _copy_state(state) or {}
    next_state["latest_observations"] = normalized_observations
    next_state["current_step_run_command_count"] = int(
        next_state.get("current_step_run_command_count") or 0
    )

    step_ids = [step.get("id") for step in next_state.get("steps") or [] if step.get("id")]
    if normalized_update and normalized_update.get("type") == "reorder":
        ordered_step_ids = normalized_update["ordered_step_ids"]
        if set(ordered_step_ids) != set(step_ids):
            return None, "reorder must include the exact current step ids."
        step_index = {step["id"]: step for step in next_state.get("steps") or []}
        next_state["steps"] = [copy.deepcopy(step_index[step_id]) for step_id in ordered_step_ids]
        step_ids = ordered_step_ids
        next_state["active_step_id"] = normalized_update["active_step_id"]
        if next_state["active_step_id"] not in step_ids:
            return None, "reorder active_step_id must be a valid step id."
    elif normalized_update and normalized_update.get("type") == "revise":
        revised_steps, error = validate_plan_steps(
            normalized_update.get("steps"),
            execution_context=_normalize_lower(next_state.get("execution_context")),
        )
        if error:
            return None, error
        revised_active = _normalize_text(normalized_update.get("active_step_id"))
        revised_ids = {step["id"] for step in revised_steps}
        if revised_active not in revised_ids:
            return None, "revise active_step_id must point to a valid step."
        next_state["steps"] = revised_steps
        next_state["active_step_id"] = revised_active
        if normalized_update.get("phase"):
            next_state["phase"] = normalized_update["phase"]
        if normalized_update.get("assumptions") is not None:
            next_state["assumptions"] = normalized_update["assumptions"]
        step_ids = [step["id"] for step in revised_steps]

    if not normalized_next and normalized_update and normalized_update.get("type") in {"reorder", "revise"}:
        normalized_next = _normalize_text(normalized_update.get("active_step_id"))

    if normalized_next and normalized_next not in step_ids:
        return None, "recommended_next_step_id must point to a valid step."

    completed = list(next_state.get("completed_step_ids") or [])
    if status_value == "complete" and step_id_text not in completed:
        completed.append(step_id_text)
    next_state["completed_step_ids"] = completed

    next_state["recommended_next_step_id"] = normalized_next
    next_state["waiting_for_confirmation"] = True
    next_state["current_step_run_command_count"] = int(
        next_state.get("current_step_run_command_count") or 0
    )
    next_state["remaining_step_run_command_budget"] = max(
        0,
        int(next_state.get("step_run_command_budget", GUIDED_RUN_COMMAND_BUDGET_DEFAULT))
        - int(next_state.get("current_step_run_command_count") or 0),
    )
    return next_state, None


def current_step_for_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    active_id = _normalize_text(state.get("active_step_id"))
    return next((step for step in state.get("steps") or [] if step.get("id") == active_id), None)


def increment_run_command_budget(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    next_state = _copy_state(state) or {}
    used = int(next_state.get("current_step_run_command_count") or 0) + 1
    budget = int(next_state.get("step_run_command_budget") or GUIDED_RUN_COMMAND_BUDGET_DEFAULT)
    next_state["current_step_run_command_count"] = used
    next_state["remaining_step_run_command_budget"] = max(0, budget - used)
    return next_state


def budget_remaining_for_state(state: dict[str, Any] | None) -> int:
    if not isinstance(state, dict):
        return 0
    budget = int(state.get("step_run_command_budget") or GUIDED_RUN_COMMAND_BUDGET_DEFAULT)
    used = int(state.get("current_step_run_command_count") or 0)
    return max(0, budget - used)
