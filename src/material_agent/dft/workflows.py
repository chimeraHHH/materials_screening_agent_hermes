"""Validated, non-executable workflow templates for future real DFT plans."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field, field_validator, model_validator

from .models import StrictModel, TaskType


class WorkflowTaskTemplate(StrictModel):
    task_key: str = Field(min_length=1)
    task_type: TaskType
    depends_on: tuple[str, ...] = ()
    required_output_names: tuple[str, ...] = ()
    validator_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def real_task_guard(self) -> WorkflowTaskTemplate:
        if self.task_type is TaskType.MOCK_TASK:
            raise ValueError("real workflow templates cannot contain MOCK_TASK")
        if self.task_key in self.depends_on:
            raise ValueError("a workflow task cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("workflow dependencies must be unique")
        return self

    @field_validator("required_output_names")
    @classmethod
    def unique_output_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("workflow required outputs must be unique")
        return values


class ClaimDependencyTemplate(StrictModel):
    claim_type: str = Field(min_length=1)
    supporting_task_keys: tuple[str, ...] = Field(min_length=1)
    domain_validator_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_supporting_tasks(self) -> ClaimDependencyTemplate:
        if len(set(self.supporting_task_keys)) != len(self.supporting_task_keys):
            raise ValueError("claim supporting task keys must be unique")
        return self


class WorkflowTemplate(StrictModel):
    template_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    target_class: str = Field(min_length=1)
    tasks: tuple[WorkflowTaskTemplate, ...] = Field(min_length=1)
    claim_dependencies: tuple[ClaimDependencyTemplate, ...] = ()

    @model_validator(mode="after")
    def graph_guard(self) -> WorkflowTemplate:
        keys = tuple(task.task_key for task in self.tasks)
        if len(set(keys)) != len(keys):
            raise ValueError("workflow task keys must be unique")
        key_set = set(keys)
        for task in self.tasks:
            unknown = set(task.depends_on) - key_set
            if unknown:
                raise ValueError("workflow task dependency is not in the template")
        for claim in self.claim_dependencies:
            if set(claim.supporting_task_keys) - key_set:
                raise ValueError("claim dependency references a missing workflow task")
        self.topological_task_keys()
        return self

    def topological_task_keys(self) -> tuple[str, ...]:
        """Return deterministic task order or reject a cyclic template."""

        pending = {task.task_key: set(task.depends_on) for task in self.tasks}
        ordered: list[str] = []
        while pending:
            ready = sorted(key for key, dependencies in pending.items() if not dependencies)
            if not ready:
                raise ValueError("workflow template contains a dependency cycle")
            for key in ready:
                ordered.append(key)
                pending.pop(key)
            for dependencies in pending.values():
                dependencies.difference_update(ready)
        return tuple(ordered)

    def claim_dependency(self, claim_type: str) -> ClaimDependencyTemplate | None:
        matches = tuple(
            dependency
            for dependency in self.claim_dependencies
            if dependency.claim_type == claim_type
        )
        if len(matches) > 1:
            raise ValueError("workflow has duplicate claim dependency rules")
        return matches[0] if matches else None


class WorkflowTemplateRegistry(StrictModel):
    templates: tuple[WorkflowTemplate, ...] = ()

    @model_validator(mode="after")
    def unique_templates(self) -> WorkflowTemplateRegistry:
        ids = tuple(template.template_id for template in self.templates)
        if len(set(ids)) != len(ids):
            raise ValueError("workflow template IDs must be unique")
        return self

    def resolve(self, template_id: str) -> WorkflowTemplate:
        for template in self.templates:
            if template.template_id == template_id:
                return template
        raise KeyError(f"workflow template is not registered: {template_id}")


def unique_task_types(tasks: Iterable[WorkflowTaskTemplate]) -> tuple[TaskType, ...]:
    """Preserve task order while rejecting ambiguous duplicate task types."""

    types = tuple(task.task_type for task in tasks)
    if len(set(types)) != len(types):
        raise ValueError("workflow template task types must be unique for preflight")
    return types
