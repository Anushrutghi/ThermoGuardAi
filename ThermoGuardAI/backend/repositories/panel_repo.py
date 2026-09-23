"""Repository for the Panel model."""
from __future__ import annotations

from sqlalchemy import select

from backend.models.component import Component
from backend.models.panel import Panel
from backend.repositories.base import BaseRepository


class PanelRepository(BaseRepository[Panel]):
    model = Panel

    def get_by_code(self, code: str) -> Panel | None:
        return self.db.scalar(select(Panel).where(Panel.code == code))

    def list_scoped(self, scope_condition, limit: int = 200) -> list[Panel]:  # noqa: ANN001
        """List panels matching an org scope condition (S4)."""
        stmt = select(Panel).where(scope_condition).order_by(Panel.name.asc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_with_components(self, panel_id: int) -> Panel | None:
        stmt = select(Panel).where(Panel.id == panel_id)
        panel = self.db.scalar(stmt)
        if panel:
            # eagerly load components
            self.db.execute(select(Component).where(Component.panel_id == panel_id))
        return panel
