"""FirestoreSession engine tests against the in-memory FakeFirestore (S6).

These tests exercise the exact SQLAlchemy statement shapes the existing
repositories/services issue, proving the Firestore shim maps them faithfully
(org isolation, relationships, aggregates, joins, EXISTS, subquery counts).
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, or_, select

from backend.firebase.engine import FirestoreSession, UnsupportedQueryError
from backend.models.device import Device
from backend.models.fault import Fault
from backend.models.inspection import Inspection
from backend.models.thermal_history import ThermalHistory
from backend.models.user import User
from tests.fakes.fake_firestore import FakeFirestore

pytestmark = pytest.mark.usefixtures("_fake_firestore_patch")


@pytest.fixture
def _fake_firestore_patch(monkeypatch):
    fake = FakeFirestore()
    monkeypatch.setattr("backend.firebase.client.get_firestore", lambda: fake)
    return fake


@pytest.fixture
def session():
    return FirestoreSession(FakeFirestore())


# ---------------------------------------------------------------------------
# CRUD + id allocation
# ---------------------------------------------------------------------------


def test_create_get_and_numeric_id_allocation(session) -> None:  # noqa: ANN001
    dev = Device(name="Switch Panel 1", organization="ThermoGuard", derived_status="ACTIVE")
    session.add(dev)
    session.commit()
    assert dev.id == 1
    assert session.get(Device, 1) is not None
    assert session.get(Device, 1).name == "Switch Panel 1"

    dev2 = Device(name="Second", organization="")
    session.add(dev2)
    session.commit()
    assert dev2.id == 2  # counter increments
    assert session.get(Device, 1).id == 1


def test_update_mutation_persists(session) -> None:  # noqa: ANN001
    dev = Device(name="A", organization="X", derived_status="ACTIVE")
    session.add(dev)
    session.commit()
    dev.derived_status = "CRITICAL"  # ORM mutation + commit (the repo pattern)
    session.commit()
    got = session.get(Device, dev.id)
    assert got.derived_status == "CRITICAL"


def test_delete(session) -> None:  # noqa: ANN001
    dev = Device(name="A", organization="X", derived_status="ACTIVE")
    session.add(dev)
    session.commit()
    session.delete(dev)
    session.commit()
    assert session.get(Device, dev.id) is None


def test_unsupported_expression_raises_loudly(session) -> None:  # noqa: ANN001
    session.add(Device(name="A", organization="X", derived_status="ACTIVE"))
    session.commit()
    from sqlalchemy import func as f

    with pytest.raises(UnsupportedQueryError):
        list(session.scalars(select(Device).where(f.lower(Device.name) == "a")).all())


# ---------------------------------------------------------------------------
# Organization isolation (NULL matches NULL → "" encoding)
# ---------------------------------------------------------------------------


def test_org_scope_null_matches_null(session) -> None:  # noqa: ANN001
    session.add(Device(name="Unowned", organization="", derived_status="ACTIVE"))
    session.add(Device(name="Mine", organization="ThermoGuard", derived_status="ACTIVE"))
    session.add(Device(name="Theirs", organization="OtherCo", derived_status="ACTIVE"))
    session.commit()

    unowned = list(session.scalars(select(Device).where(Device.organization.is_(None))).all())
    assert [d.name for d in unowned] == ["Unowned"]
    mine = list(session.scalars(select(Device).where(Device.organization == "ThermoGuard")).all())
    assert [d.name for d in mine] == ["Mine"]
    # records expose "" as None for the organization field
    assert unowned[0].organization is None


def test_org_scope_or_condition(session) -> None:  # noqa: ANN001
    session.add(Device(name="Unowned", organization="", derived_status="ACTIVE"))
    session.add(Device(name="Mine", organization="ThermoGuard", derived_status="ACTIVE"))
    session.add(Device(name="Theirs", organization="OtherCo", derived_status="ACTIVE"))
    session.commit()
    rows = list(
        session.scalars(select(Device).where(or_(Device.organization.is_(None), Device.organization == "ThermoGuard"))).all()
    )
    assert {d.name for d in rows} == {"Unowned", "Mine"}


# ---------------------------------------------------------------------------
# Filters, ordering, limits
# ---------------------------------------------------------------------------


def test_filters_order_limit_offset(session) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    for i in range(5):
        session.add(
            Inspection(
                device_id=7,
                status="completed" if i % 2 == 0 else "aborted",
                started_at=now - timedelta(hours=i),
                inspection_code=f"TG-{i}",
                mode="switch_first",
                camera_source="webcam",
                software_version="1.0",
                thermal_source="simulator",
                thermal_simulated=True,
            )
        )
    session.commit()
    rows = list(
        session.scalars(
            select(Inspection)
            .where(Inspection.device_id == 7, Inspection.status == "completed")
            .order_by(Inspection.started_at.desc())
            .limit(2)
        ).all()
    )
    assert [r.status for r in rows] == ["completed", "completed"]
    assert rows[0].inspection_code == "TG-0"


def test_in_and_ilike_filters(session) -> None:  # noqa: ANN001
    session.add(Device(name="Panel One", organization="", derived_status="ACTIVE"))
    session.add(Device(name="Panel Two", organization="", derived_status="ACTIVE"))
    session.add(Device(name="Board", organization="", derived_status="ACTIVE"))
    session.commit()
    ids = [d.id for d in session.scalars(select(Device)).all()]
    in_rows = list(session.scalars(select(Device).where(Device.id.in_(ids[:2]))).all())
    assert len(in_rows) == 2
    like_rows = list(session.scalars(select(Device).where(Device.name.ilike("%panel%"))).all())
    assert {d.name for d in like_rows} == {"Panel One", "Panel Two"}


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def test_single_and_many_relationships(session) -> None:  # noqa: ANN001
    user = User(username="inspector", email="i@x.io", role="technician", organization="ThermoGuard", is_active=True)
    dev = Device(name="D1", organization="ThermoGuard", derived_status="ACTIVE")
    session.add_all([user, dev])
    session.commit()
    insp = Inspection(
        user_id=user.id,
        device_id=dev.id,
        status="running",
        inspection_code="TG-1",
        mode="switch_first",
        camera_source="webcam",
        software_version="1.0",
        thermal_source="simulator",
        thermal_simulated=True,
    )
    session.add(insp)
    session.commit()
    got = session.get(Inspection, insp.id)
    assert got.user.username == "inspector"
    assert got.device.name == "D1"
    # many-side: faults under the inspection
    session.add(Fault(inspection_id=insp.id, fault_type="thermal_anomaly", severity="high", confidence=0.9, component_label="wall_switch"))
    session.commit()
    assert len(got.faults) == 1
    assert got.faults[0].fault_type == "thermal_anomaly"


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def test_count_max_min_avg(session) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    for i, temp in enumerate([30.0, 40.0, 50.0]):
        session.add(
            ThermalHistory(
                inspection_id=i + 1,
                device_id=9,
                timestamp=now - timedelta(days=i),
                max_temp=temp,
                min_temp=temp - 10,
                avg_temp=temp - 5,
                simulated=False,
                rapid_increase=False,
            )
        )
    session.commit()
    assert session.scalar(select(func.count()).select_from(ThermalHistory).where(ThermalHistory.device_id == 9)) == 3
    assert session.scalar(select(func.max(ThermalHistory.max_temp)).where(ThermalHistory.device_id == 9)) == 50.0
    assert session.scalar(select(func.min(ThermalHistory.max_temp)).where(ThermalHistory.device_id == 9)) == 30.0
    assert session.scalar(select(func.avg(ThermalHistory.max_temp)).where(ThermalHistory.device_id == 9)) == 40.0
    assert session.scalar(select(func.count()).select_from(ThermalHistory).where(ThermalHistory.simulated.is_(True))) == 0


def test_group_by_and_date_aggregate(session) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    for i, temp in enumerate([30.0, 40.0, 50.0]):
        session.add(
            ThermalHistory(
                inspection_id=i + 1,
                device_id=9,
                timestamp=now - timedelta(days=i),
                max_temp=temp,
                min_temp=temp - 10,
                avg_temp=temp - 5,
                simulated=False,
                rapid_increase=False,
            )
        )
    session.commit()
    rows = session.execute(
        select(func.date(ThermalHistory.timestamp), func.count()).group_by(func.date(ThermalHistory.timestamp))
    ).all()
    assert len(rows) == 3
    assert all(isinstance(day, str) and count == 1 for day, count in rows)


def test_subquery_count(session) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    for i in range(4):
        session.add(
            ThermalHistory(
                inspection_id=i + 1,
                device_id=5,
                timestamp=datetime.now(UTC),
                max_temp=float(i),
                simulated=False,
                rapid_increase=False,
            )
        )
    session.commit()
    base = select(ThermalHistory).where(ThermalHistory.device_id == 5)
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    assert total == 4


# ---------------------------------------------------------------------------
# Joins + EXISTS
# ---------------------------------------------------------------------------


def test_join_select_two_entities(session) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    dev = Device(name="D1", organization="ThermoGuard", derived_status="ACTIVE")
    session.add(dev)
    session.commit()
    insp = Inspection(
        user_id=None,
        device_id=dev.id,
        status="completed",
        inspection_code="TG-1",
        mode="switch_first",
        camera_source="webcam",
        software_version="1.0",
        thermal_source="mlx90640",
        thermal_simulated=False,
        started_at=datetime.now(UTC),
    )
    session.add(insp)
    session.commit()
    session.add(Fault(inspection_id=insp.id, fault_type="thermal_anomaly", severity="high", confidence=0.9, component_label="wall_switch"))
    session.commit()
    rows = session.execute(
        select(Fault, Inspection)
        .join(Inspection, Fault.inspection_id == Inspection.id)
        .where(Inspection.device_id == dev.id, Inspection.status == "completed")
        .order_by(Inspection.started_at.asc(), Fault.id.asc())
    ).all()
    assert len(rows) == 1
    fault, inspection = rows[0]
    assert fault.fault_type == "thermal_anomaly"
    assert inspection.inspection_code == "TG-1"


def test_exists_has_relationship_filter(session) -> None:  # noqa: ANN001
    dev = Device(name="Mine", organization="ThermoGuard", derived_status="ACTIVE")
    session.add(dev)
    session.commit()
    insp = Inspection(
        user_id=None,
        device_id=dev.id,
        status="completed",
        inspection_code="TG-1",
        mode="switch_first",
        camera_source="webcam",
        software_version="1.0",
        thermal_source="simulator",
        thermal_simulated=True,
    )
    session.add(insp)
    session.commit()
    rows = list(session.scalars(select(Inspection).where(Inspection.device.has(Device.organization == "ThermoGuard"))).all())
    assert len(rows) == 1
    rows_foreign = list(session.scalars(select(Inspection).where(Inspection.device.has(Device.organization == "OtherCo"))).all())
    assert rows_foreign == []
    # device-less inspection must NOT match an org-scoped has()
    insp2 = Inspection(
        user_id=None,
        device_id=None,
        status="completed",
        inspection_code="TG-2",
        mode="switch_first",
        camera_source="webcam",
        software_version="1.0",
        thermal_source="simulator",
        thermal_simulated=True,
    )
    session.add(insp2)
    session.commit()
    rows2 = list(session.scalars(select(Inspection).where(Inspection.device.has(Device.organization == "ThermoGuard"))).all())
    assert len(rows2) == 1


def test_to_dict_mirrors_orm(session) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    session.add(
        ThermalHistory(
            inspection_id=1,
            device_id=2,
            timestamp=datetime.now(UTC),
            max_temp=40.0,
            simulated=False,
            rapid_increase=False,
            thermal_source="mlx90640",
            emissivity=0.95,
        )
    )
    session.commit()
    row = session.scalars(select(ThermalHistory)).all()[0]
    d = row.to_dict()
    assert d["max_temp"] == 40.0
    assert d["sensor"] == "mlx90640"
    assert d["emissivity"] == 0.95
    assert d["simulated"] is False
    assert d["timestamp"] is not None


# ---------------------------------------------------------------------------
# Repository layer through the shim
# ---------------------------------------------------------------------------


def test_repository_crud_through_session(session) -> None:  # noqa: ANN001
    from backend.repositories.device_repo import DeviceRepository

    repo = DeviceRepository(session)
    dev = repo.create(name="R1", organization="ThermoGuard", derived_status="ACTIVE")
    session.commit()
    assert dev.id == 1
    assert repo.get_scoped(dev.id, organization="ThermoGuard") is not None
    assert repo.get_scoped(dev.id, organization="OtherCo") is None
    assert len(repo.list_all(organization="ThermoGuard")) == 1
    assert len(repo.list_all(organization="OtherCo")) == 0
