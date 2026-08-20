"""Read-only RouteTemplate listing (Phase 4 — release Route selection).

The release flow (GUI_DESIGN §11.4) lets the user confirm ``PLANNED``
with an existing **active** RouteTemplate; this module provides exactly
that minimal read model — the active templates with their ordered
steps — and nothing more. Planned Routes management (create/edit/
archive, GUI_DESIGN §13) is a later phase and deliberately has no API
surface here: templates cannot be created, changed, or archived through
this module.
"""

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import RouteStep, RouteTemplate


class RouteTemplateDetail(NamedTuple):
    """One active RouteTemplate with its ordered steps."""

    template: RouteTemplate
    steps: list[RouteStep]


def list_active_route_templates(session: Session) -> list[RouteTemplateDetail]:
    """The active (non-archived) RouteTemplates, steps in sequence order.

    Ordered by name (then id) for a stable, user-facing selection list.
    Archived templates never appear: a new release can only reference an
    active template (SLICE1_DATA_MODEL §8), while historical PLANNED
    flows keep their own immutable AssignedRoute snapshots regardless.
    """
    templates = (
        session.execute(
            select(RouteTemplate)
            .where(RouteTemplate.archived_at.is_(None))
            .order_by(RouteTemplate.name, RouteTemplate.id)
        )
        .scalars()
        .all()
    )
    if not templates:
        return []
    steps_by_template: dict[int, list[RouteStep]] = {template.id: [] for template in templates}
    steps = (
        session.execute(
            select(RouteStep)
            .where(RouteStep.route_template_id.in_(steps_by_template))
            .order_by(RouteStep.route_template_id, RouteStep.sequence)
        )
        .scalars()
        .all()
    )
    for step in steps:
        steps_by_template[step.route_template_id].append(step)
    return [
        RouteTemplateDetail(template=template, steps=steps_by_template[template.id])
        for template in templates
    ]
