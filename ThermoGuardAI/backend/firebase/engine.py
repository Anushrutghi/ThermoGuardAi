"""FirestoreSession — Firestore-backed session shim for the existing data layer (S6).

The repository/service layer talks to the database exclusively through the
SQLAlchemy Session surface: ``db.get/scalar/scalars/execute/add/delete/flush/
commit`` and ``select(...).where(...)`` statements. When
``STORAGE_BACKEND=firestore``, ``get_db()`` yields a ``FirestoreSession`` that
implements that exact surface over Cloud Firestore, so services and
repositories keep working unchanged and every ownership decision stays
server-side.

Design decisions (documented, never guessed):

* **Collections** are named after the ORM ``__tablename__``.
* **IDs** are numeric and allocated from a ``__counters`` collection via a
  Firestore transaction, preserving the integer-id schema contract.
* **Organization isolation** uses a single *derived* ``organization`` field on
  every record (written server-side at creation time). ``None``/NULL is
  encoded as ``""`` because Firestore cannot filter on field absence with
  ``== None``; the record layer maps ``""`` back to ``None`` for readers.
  NULL-matches-NULL semantics are preserved exactly.
* **Datetime columns** are stored as ISO-8601 strings and deserialized on read
  so ``.isoformat()`` and pydantic coercion keep working.
* **Relationships** are resolved lazily (single = FK lookup, many = inverse FK
  query) so ``inspection.panel`` / ``inspection.faults`` keep working.
* Writes are auto-committed (Firestore is autocommit); ``commit()`` is a flush
  of staged inserts, ``rollback()`` discards only not-yet-persisted inserts.

Unsupported query shapes raise ``UnsupportedQueryError`` loudly instead of
silently returning wrong rows.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, Grouping, Label, Null, UnaryExpression
from sqlalchemy.sql.selectable import Exists, Join, Subquery

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


def _load_models() -> dict[str, type]:
    """Map ORM ``__tablename__`` → model class (lazy to avoid import cycles)."""
    from backend import models as models_pkg

    out: dict[str, type] = {}
    for name in dir(models_pkg):
        obj = getattr(models_pkg, name)
        if isinstance(obj, type) and hasattr(obj, "__tablename__") and hasattr(obj, "__table__"):
            out[obj.__tablename__] = obj
    return out


_TABLE_TO_MODEL: dict[str, type] | None = None


def table_to_model() -> dict[str, type]:
    global _TABLE_TO_MODEL
    if _TABLE_TO_MODEL is None:
        _TABLE_TO_MODEL = _load_models()
    return _TABLE_TO_MODEL


def _datetime_cols(model: type) -> set[str]:
    cols: set[str] = set()
    for col in model.__table__.columns:
        if isinstance(col.type, (sa.DateTime, sa.Date)):
            cols.add(col.key)
    return cols


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _deserialize(model: type, field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in _datetime_cols(model):
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value
    # NULL-matches-NULL org encoding: "" means unowned (None)
    if field == "organization" and value == "":
        return None
    return value


# ---------------------------------------------------------------------------
# Record (attribute-accessible Firestore document with lazy relationships)
# ---------------------------------------------------------------------------
# Relationship map: model tablename → {attr: (kind, target_tablename, fk_field)}
#   kind "single": this record holds fk_field → target doc id
#   kind "many" : target docs hold fk_field == this record id
RELATIONSHIPS: dict[str, dict[str, tuple[str, str, str]]] = {
    "inspections": {
        "panel": ("single", "panels", "panel_id"),
        "user": ("single", "users", "user_id"),
        "device": ("single", "devices", "device_id"),
        "detections": ("many", "detections", "inspection_id"),
        "faults": ("many", "faults", "inspection_id"),
        "incidents": ("many", "incidents", "inspection_id"),
        "alarms": ("many", "alarms", "inspection_id"),
        "maintenance": ("many", "maintenance_records", "inspection_id"),
        "thermal_history": ("many", "thermal_history", "inspection_id"),
    },
    "faults": {"inspection": ("single", "inspections", "inspection_id"), "component": ("single", "components", "component_id")},
    "detections": {"inspection": ("single", "inspections", "inspection_id"), "component": ("single", "components", "component_id")},
    "incidents": {"inspection": ("single", "inspections", "inspection_id")},
    "alarms": {"inspection": ("single", "inspections", "inspection_id")},
    "reports": {"inspection": ("single", "inspections", "inspection_id")},
    "maintenance_records": {
        "inspection": ("single", "inspections", "inspection_id"),
        "incident": ("single", "incidents", "incident_id"),
        "component": ("single", "components", "component_id"),
    },
    "thermal_history": {"inspection": ("single", "inspections", "inspection_id"), "device": ("single", "devices", "device_id")},
    "temperature_history": {"inspection": ("single", "inspections", "inspection_id"), "component": ("single", "components", "component_id")},
    "components": {"panel": ("single", "panels", "panel_id")},
    "devices": {
        "inspections": ("many", "inspections", "device_id"),
        "thermal_history": ("many", "thermal_history", "device_id"),
    },
    "panels": {"components": ("many", "components", "panel_id")},
}


class FirestoreRecord:
    """Attribute-accessible view of one Firestore document.

    Reads resolve lazy relationships; writes persist through to Firestore so
    the existing ``setattr(obj, ...)`` mutation pattern keeps working.
    """

    __slots__ = ("_model", "_collection", "_doc_id", "_fields", "_session", "_dtcols")

    def __init__(self, model: type, collection: str, doc_id: str | None, fields: dict, session: FirestoreSession) -> None:
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_collection", collection)
        object.__setattr__(self, "_doc_id", doc_id)
        object.__setattr__(self, "_fields", dict(fields))
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_dtcols", _datetime_cols(model))

    # -- attribute access ----------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            fields = self._fields
        except AttributeError:  # pragma: no cover — during unpickling/early init
            raise AttributeError(name) from None
        if name == "id":
            if self._doc_id is not None:
                # Domain collections use numeric ids; the ``users`` collection
                # stores Firebase uids (non-numeric strings) — return them as-is
                # so profile reads (e.g. the Team endpoint) keep working.
                try:
                    return int(self._doc_id)
                except (TypeError, ValueError):
                    return self._doc_id
            return fields.get("id")
        if name in fields:
            return _deserialize(self._model, name, fields[name])
        rel = RELATIONSHIPS.get(self._model.__tablename__, {}).get(name)
        if rel is not None:
            kind, target_table, fk = rel
            session = self._session
            target_model = table_to_model()[target_table]
            if kind == "single":
                fk_val = fields.get(fk)
                if fk_val is None:
                    return None
                return session.get(target_model, fk_val)
            return session.list_where(target_model, fk, self.id)
        # Computed ORM property (e.g. Report.file_available/file_name) — evaluate
        # against an ORM instance built from the stored fields.
        prop = getattr(self._model, name, None)
        if isinstance(prop, property):
            try:
                kwargs = {k: _deserialize(self._model, k, v) for k, v in fields.items() if k != "id"}
                if "id" in fields:
                    kwargs["id"] = fields["id"]
                return getattr(self._model(**kwargs), name)
            except Exception:  # noqa: BLE001
                return None
        # ORM-equivalent default: unset columns read as None.
        return None

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        fields = self._fields
        if name == "id":
            fields["id"] = value
            if value is not None:
                object.__setattr__(self, "_doc_id", str(value))
            return
        fields[name] = _serialize(value)
        session = self._session
        if session is not None and self._doc_id is not None:
            session._persist_record(self)  # write-through (Firestore is autocommit)

    def to_dict(self) -> dict:
        """Mirror the ORM model's ``to_dict`` when it defines one (response shapes)."""
        cls = self._model
        if hasattr(cls, "to_dict"):
            try:
                kwargs = {k: _deserialize(cls, k, v) for k, v in self._fields.items() if k != "id"}
                if "id" in self._fields:
                    kwargs["id"] = self._fields["id"]
                return cls(**kwargs).to_dict()
            except Exception:  # noqa: BLE001
                logger.exception("to_dict fallback for %s", cls.__name__)
        return dict(self._fields)

    def __repr__(self) -> str:
        return f"<FirestoreRecord {self._model.__name__} id={self._doc_id}>"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, FirestoreRecord)
            and other._model is self._model
            and other._doc_id == self._doc_id
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnsupportedQueryError(NotImplementedError):
    """Raised when a SQLAlchemy query shape cannot be faithfully mapped."""


# ---------------------------------------------------------------------------
# SQLAlchemy expression parsing helpers
# ---------------------------------------------------------------------------


def _unwrap(expr: Any) -> Any:
    while isinstance(expr, (Label, Grouping, UnaryExpression)):
        expr = expr.element
    return expr


def _as_column(expr: Any) -> sa.Column | None:
    expr = _unwrap(expr)
    # group_by/order_by wrap expressions in a ClauseList (never a BooleanClauseList)
    if isinstance(expr, sa.sql.elements.ClauseList) and not isinstance(expr, BooleanClauseList):
        children = list(expr.get_children())
        expr = children[0] if children else expr
    for _ in range(4):
        if hasattr(expr, "__clause_element__"):
            expr = expr.__clause_element__()
        else:
            break
    expr = _unwrap(expr)
    return expr if isinstance(expr, sa.Column) else None


def _column_table_name(col: sa.Column) -> str:
    return col.table.name if col.table is not None else ""


def _value(expr: Any) -> Any:
    """Extract the raw value from a literal/BindParameter (not a column)."""
    expr = _unwrap(expr)
    if isinstance(expr, Null):
        return None
    if isinstance(expr, sa.sql.elements.BindParameter):
        return expr.value
    if isinstance(expr, (list, tuple)):
        return [_value(v) for v in expr]
    return expr


class _Planned:
    """Parsed statement: base model, joins, filters, ordering, limits."""

    def __init__(self) -> None:
        self.base_model: type | None = None
        self.joins: list[tuple[type, tuple[str, str], tuple[str, str], bool]] = []
        self.where: list[Any] = []
        self.order: list[Any] = []
        self.group_by: list[Any] = []
        self.limit: int | None = None
        self.offset: int | None = None
        self.raw_cols: list[Any] = []
        self.subquery_rows: list[list[dict]] = []


def _flatten_from(expr: Any) -> tuple[Any, list[tuple[Any, Any, Any, bool]]]:
    """Return (base, [(right, onclause, isouter)]) for a from-clause tree."""
    if isinstance(expr, Join):
        base, joins = _flatten_from(expr.left)
        joins.append((expr.right, expr.onclause, expr.isouter))
        return base, joins
    return expr, []


def _model_for_table(table: Any) -> type:
    name = getattr(table, "name", None)
    if name is None:
        raise UnsupportedQueryError(f"Cannot resolve table for {table!r}")
    model = table_to_model().get(name)
    if model is None:
        raise UnsupportedQueryError(f"No model mapped for table {name!r}")
    return model


def _parse_onclause(onclause: Any) -> tuple[tuple[str, str], tuple[str, str]]:
    onclause = _unwrap(onclause)
    if not isinstance(onclause, BinaryExpression):
        raise UnsupportedQueryError(f"Unsupported join condition {onclause!r}")
    left = _as_column(onclause.left)
    right = _as_column(onclause.right)
    if left is None or right is None:
        raise UnsupportedQueryError(f"Unsupported join condition {onclause!r}")
    return (_column_table_name(left), left.key), (_column_table_name(right), right.key)


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------


def _is_function(expr: Any) -> bool:
    return isinstance(_unwrap(expr), sa.sql.functions.Function)


def _function_info(expr: Any) -> tuple[str, list[Any]]:
    fn = _unwrap(expr)
    if isinstance(fn, sa.sql.elements.ClauseList) and not isinstance(fn, BooleanClauseList):
        children = list(fn.get_children())
        fn = children[0] if children else fn
        fn = _unwrap(fn)  # Label → Grouping → Function
    clauses = list(fn.clauses) if hasattr(fn, "clauses") else []
    return str(fn.name).lower(), clauses


def _deep_function(expr: Any) -> tuple[str, list[Any]] | None:
    """Function info only when the expression ultimately IS a SQL function."""
    candidate = _unwrap(expr)
    if isinstance(candidate, sa.sql.elements.ClauseList) and not isinstance(candidate, BooleanClauseList):
        children = list(candidate.get_children())
        candidate = _unwrap(children[0]) if children else candidate
    if isinstance(candidate, sa.sql.functions.Function):
        return _function_info(candidate)
    return None


class FirestoreSession:
    """SQLAlchemy-Session-compatible shim over Cloud Firestore."""

    def __init__(self, db: Any | None = None) -> None:
        if db is None:
            from backend.firebase.client import get_firestore

            db = get_firestore()
        self._firestore = db
        self._pending: list[tuple[Any, FirestoreRecord]] = []  # (orm_obj, record) staged inserts
        self._orm_records: list[tuple[Any, FirestoreRecord]] = []  # (orm_obj, record) tracked for re-sync
        self._deletes: list[FirestoreRecord] = []
        self._cache: dict[tuple[str, str], FirestoreRecord] = {}
        self._closed = False

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._closed = True
        self._cache.clear()

    def flush(self) -> None:
        pending, self._pending = self._pending, []
        for obj, record in pending:
            if record._doc_id is None:
                new_id = self._allocate_id(record._collection)
                record._fields["id"] = new_id
                object.__setattr__(record, "_doc_id", str(new_id))
                if obj is not None and obj is not record:
                    try:
                        obj.id = new_id
                    except Exception:  # noqa: BLE001
                        pass
                self._cache[(record._model.__name__, record._doc_id)] = record
            self._persist_record(record)
        # Re-sync every tracked ORM instance so the existing
        # ``setattr(obj, ...) + commit()`` mutation flow persists (e.g.
        # ``inspection.frames_processed += 1``, ``repo.update(report, file_path=...)``).
        for obj, record in self._orm_records:
            if record._doc_id is not None:
                self._sync_from_orm(obj, record)
        for record in self._deletes:
            self._delete_record(record)
        self._deletes = []

    def _sync_from_orm(self, obj: Any, record: FirestoreRecord) -> None:
        """Re-extract fields from the ORM instance into its Firestore record."""
        fields: dict[str, Any] = {}
        for col in obj.__class__.__table__.columns:
            if hasattr(obj, col.key):
                fields[col.key] = _serialize(getattr(obj, col.key))
        object.__setattr__(record, "_fields", fields)
        self._persist_record(record)

    def commit(self) -> None:
        self.flush()

    def rollback(self) -> None:
        self._pending = []
        self._deletes = []

    # -- writes --------------------------------------------------------------
    def add(self, obj: Any) -> None:
        if isinstance(obj, FirestoreRecord):
            self._pending.append((obj, obj))
            return
        model = obj.__class__
        collection = model.__tablename__
        fields: dict[str, Any] = {}
        for col in model.__table__.columns:
            if hasattr(obj, col.key):
                fields[col.key] = _serialize(getattr(obj, col.key))
        record = FirestoreRecord(model, collection, None, fields, self)
        self._pending.append((obj, record))
        self._orm_records.append((obj, record))

    def add_all(self, objs: Iterable[Any]) -> None:
        for obj in objs:
            self.add(obj)

    def delete(self, obj: Any) -> None:
        if isinstance(obj, FirestoreRecord):
            if obj._doc_id is not None:
                self._deletes.append(obj)
        else:
            record = self._to_record(obj)
            if record is not None and record._doc_id is not None:
                self._deletes.append(record)

    def _to_record(self, obj: Any) -> FirestoreRecord | None:
        for orm, record in list(self._pending) + list(self._orm_records):
            if orm is obj:
                return record
        return None

    def _persist_record(self, record: FirestoreRecord) -> None:
        doc = self._firestore.collection(record._collection).document(record._doc_id)
        doc.set(dict(record._fields))

    def _delete_record(self, record: FirestoreRecord) -> None:
        self._firestore.collection(record._collection).document(record._doc_id).delete()
        self._cache.pop((record._model.__name__, record._doc_id), None)

    def _allocate_id(self, collection: str) -> int:
        """Allocate the next integer id for a collection (transactional)."""
        counter_ref = self._firestore.collection("__counters").document(collection)
        try:
            result: list[int] = []

            def _run(txn: Any) -> int:
                snap = txn.get(counter_ref)
                next_id = int((snap.to_dict() or {}).get("next", 1)) if snap.exists else 1
                txn.set(counter_ref, {"next": next_id + 1})
                return next_id

            result.append(self._firestore.run_transaction(_run))
            return result[0]
        except Exception:  # noqa: BLE001 — fall back to read-modify-write
            snap = counter_ref.get()
            next_id = int((snap.to_dict() or {}).get("next", 1)) if snap.exists else 1
            counter_ref.set({"next": next_id + 1})
            return next_id

    # -- reads ---------------------------------------------------------------
    def get(self, model: type, id_value: Any) -> FirestoreRecord | None:
        doc_id = str(id_value)
        key = (model.__name__, doc_id)
        if key in self._cache:
            return self._cache[key]
        snap = self._firestore.collection(model.__tablename__).document(doc_id).get()
        if snap is None or not snap.exists:
            return None
        record = FirestoreRecord(model, model.__tablename__, doc_id, snap.to_dict(), self)
        self._cache[key] = record
        return record

    def list_where(self, model: type, field: str, value: Any) -> list[FirestoreRecord]:
        """All records of a model where field == value (relationship helper)."""
        query = self._firestore.collection(model.__tablename__).where(field, "==", _serialize(value))
        out: list[FirestoreRecord] = []
        for snap in query.get():
            out.append(FirestoreRecord(model, model.__tablename__, snap.id, snap.to_dict(), self))
        return out

    # -- legacy Query API (db.query(Model).filter(...)) ----------------------
    def query(self, model: type) -> _Query:
        return _Query(self, model)

    # -- statement execution -------------------------------------------------
    def scalar(self, stmt: Any) -> Any:
        result = self.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return row[0] if isinstance(row, tuple) else row

    def scalars(self, stmt: Any) -> ScalarResult:
        return self.execute(stmt).scalars()

    def execute(self, stmt: Any) -> Result:
        plan = self._plan(stmt)
        rows = self._materialize(plan)
        return Result(rows)

    # -----------------------------------------------------------------------
    def _plan(self, stmt: Any) -> _Planned:
        plan = _Planned()
        froms = list(stmt.get_final_froms())
        if not froms:
            raise UnsupportedQueryError(f"Unsupported statement {stmt!r}")
        first = froms[0]
        # subquery-backed counts (e.g. select(count).select_from(base.subquery()))
        if isinstance(first, Subquery):
            plan.subquery_rows = self._materialize_subquery(first)
            plan.base_model = None
            plan.raw_cols = list(getattr(stmt, "_raw_columns", ()) or [])
            return plan
        base, joins = _flatten_from(first)
        plan.base_model = _model_for_table(base)
        for right, onclause, isouter in joins:
            right_model = _model_for_table(right)
            left_side, right_side = _parse_onclause(onclause)
            plan.joins.append((right_model, left_side, right_side, isouter))
        plan.where = list(getattr(stmt, "_where_criteria", ()) or ())
        plan.order = list(getattr(stmt, "_order_by_clauses", ()) or ())
        plan.group_by = list(getattr(stmt, "_group_by_clauses", ()) or ())
        plan.limit = getattr(stmt, "_limit", None)
        plan.offset = getattr(stmt, "_offset", None)
        plan.raw_cols = list(getattr(stmt, "_raw_columns", ()) or ())
        return plan

    def _materialize_subquery(self, subquery: Subquery) -> list[Any]:
        inner = getattr(subquery, "original", None)
        if inner is None:
            raise UnsupportedQueryError("Cannot materialize subquery without .original")
        return self._materialize(self._plan(inner))

    def _materialize(self, plan: _Planned) -> list[Any]:
        # ----- subquery-backed count -------------------------------------------
        if plan.base_model is None:
            if len(plan.raw_cols) == 1 and _is_function(plan.raw_cols[0]):
                name, _ = _function_info(plan.raw_cols[0])
                if name == "count":
                    return [(len(plan.subquery_rows),)]
            raise UnsupportedQueryError("Unsupported subquery statement")
        rows: list[Any] = []
        is_aggregate = any(_is_function(c) for c in plan.raw_cols)
        if not is_aggregate:
            rows = self._fetch_records(plan)
        else:
            rows = self._fetch_records(plan)  # raw records, then aggregate below

        # ----- aggregate mode ------------------------------------------------
        if is_aggregate:
            return self._aggregate(plan, rows)

        # ----- plain rows ------------------------------------------------------
        if plan.group_by:
            return self._aggregate(plan, rows)

        out: list[tuple[Any, ...]] = []
        for record in rows:
            ctx, _complete = self._ctx_for(plan, record)
            out.append(tuple(self._select_values(plan, record, ctx)))
        return out

    # -----------------------------------------------------------------------
    def _fetch_records(self, plan: _Planned) -> list[FirestoreRecord]:
        """Stream base-collection docs, pushing equality filters to Firestore."""
        base_model = plan.base_model
        assert base_model is not None
        push_filters: list[tuple[str, str, Any]] = []
        python_filters: list[Any] = list(plan.where)
        for cond in plan.where:
            parsed = self._direct_equality(base_model, cond)
            if parsed is not None:
                field, value = parsed
                push_filters.append((field, "==", value))
                python_filters = [c for c in python_filters if c is not cond]
        query = self._firestore.collection(base_model.__tablename__)
        for field, op, value in push_filters:
            query = query.where(field, op, _serialize(value))
        records: list[FirestoreRecord] = []
        for snap in query.get():
            record = FirestoreRecord(base_model, base_model.__tablename__, snap.id, snap.to_dict(), self)
            ctx, complete = self._ctx_for(plan, record)
            if not complete:
                continue
            if all(self._eval_cond(c, ctx) for c in python_filters):
                records.append(record)
        if plan.order:
            records = self._order_records(records, plan)
        return self._apply_paging(records, plan)

    def _apply_paging(self, records: list[FirestoreRecord], plan: _Planned) -> list[FirestoreRecord]:
        if plan.offset:
            records = records[plan.offset :]
        if plan.limit is not None:
            records = records[: plan.limit]
        return records

    def _direct_equality(self, model: type, cond: Any) -> tuple[str, Any] | None:
        """Extract a base-model equality filter: (field, value) or None."""
        cond = _unwrap(cond)
        if not isinstance(cond, BinaryExpression):
            return None
        op = getattr(cond.operator, "__name__", str(getattr(cond.operator, "name", cond.operator)))
        if op not in ("eq", "is_", "eq_op"):
            return None
        col = _as_column(cond.left)
        if col is None or _column_table_name(col) != model.__tablename__:
            return None
        value = _value(cond.right)
        if op == "is_":
            if value is not None:
                return None
            value = None
        if col.key == "organization" and value is None:
            value = ""  # NULL-matches-NULL encoding
        return col.key, value

    def _order_records(self, records: list[FirestoreRecord], plan: _Planned) -> list[FirestoreRecord]:
        # Stable multi-key sort: apply least-significant key first. The asc/desc
        # modifier must be read BEFORE unwrapping the UnaryExpression wrapper.
        base_model = plan.base_model
        assert base_model is not None
        for clause in reversed(plan.order):
            desc = isinstance(clause, UnaryExpression) and "desc" in getattr(getattr(clause, "modifier", None), "__name__", "")
            col = _as_column(clause)
            if col is None:
                raise UnsupportedQueryError(f"Unsupported order clause {clause!r}")
            if _column_table_name(col) == base_model.__tablename__:
                records.sort(key=lambda r, c=col: (r._fields.get(c.key) is None, r._fields.get(c.key)), reverse=desc)
            else:
                # ordering on a joined/related table (e.g. Inspection.started_at
                # when listing Faults) → resolve the related record per row
                def _key(r: FirestoreRecord, c: sa.Column = col) -> tuple:
                    ctx, _complete = self._ctx_for(plan, r)
                    rec = ctx.get(_column_table_name(c))
                    if rec is None:
                        return (True, None)
                    value = rec._fields.get(c.key)
                    return (value is None, value)

                records.sort(key=_key, reverse=desc)
        return records

    def _ctx_for(self, plan: _Planned, record: FirestoreRecord) -> tuple[dict[str, FirestoreRecord], bool]:
        """(ctx, complete) — complete=False when an INNER join has no match.

        Matches SQL inner-join semantics: a row whose joined record is missing
        (e.g. Report → Inspection → Device where device_id is NULL) must be
        dropped, not kept with a NULL joined value.
        """
        base_model = plan.base_model
        assert base_model is not None
        ctx = {base_model.__tablename__: record}
        complete = True
        for right_model, left_side, right_side, isouter in plan.joins:
            left_table, left_col = left_side
            right_table, right_col = right_side
            target = None
            # LEFT record is either the base table or a previously-joined table
            # already present in ctx (chained joins: Report → Inspection → Device).
            left_rec = ctx.get(left_table)
            if left_rec is not None:
                fk_val = left_rec.id if left_col == "id" else left_rec._fields.get(left_col)
                if fk_val is None:
                    target = None
                elif right_col == "id":
                    target = self.get(right_model, fk_val)
                else:
                    matches = self.list_where(right_model, right_col, fk_val)
                    target = matches[0] if matches else None
            elif right_table == base_model.__tablename__:
                fk_val = record._fields.get(right_col)
                if fk_val is None:
                    fk_val = record.id
                if left_col == "id":
                    target = self.get(right_model, fk_val)
                else:
                    matches = self.list_where(right_model, left_col, fk_val)
                    target = matches[0] if matches else None
            if target is not None:
                ctx[right_model.__tablename__] = target
            elif not isouter:
                complete = False
        return ctx, complete

    # -----------------------------------------------------------------------
    def _eval_cond(self, cond: Any, ctx: dict[str, FirestoreRecord]) -> bool:
        if isinstance(cond, Exists):
            return self._eval_exists(cond, ctx)
        if isinstance(cond, (Label, Grouping)):
            inner = cond.element
            if isinstance(inner, Exists):
                return self._eval_exists(inner, ctx)
            return self._eval_cond(inner, ctx)
        cond = _unwrap(cond)  # note: Exists is a UnaryExpression — checked BEFORE unwrapping
        if isinstance(cond, BooleanClauseList):
            op = str(getattr(cond.operator, "name", getattr(cond.operator, "__name__", "")))
            children = list(cond.get_children())
            if not children:
                return True
            if "or" in op:
                return any(self._eval_cond(c, ctx) for c in children)
            return all(self._eval_cond(c, ctx) for c in children)
        if isinstance(cond, BinaryExpression):
            op = str(getattr(cond.operator, "name", getattr(cond.operator, "__name__", "")))
            col = _as_column(cond.left)
            if col is None:
                raise UnsupportedQueryError(f"Unsupported left-hand expression in {cond!r}")
            # the right side may be a correlated column (e.g. Device.id ==
            # Inspection.device_id inside an EXISTS) — resolve it via ctx first
            right_col = _as_column(cond.right)
            value = self._ctx_value(right_col, ctx) if right_col is not None else _value(cond.right)
            got = self._ctx_value(col, ctx)
            if "is" in op:
                return got == value if value is not None else got is None
            if col.key == "organization" and value is None:
                value = ""  # NULL-matches-NULL encoding
            if "ilike" in op or "like" in op or "contains" in op or "startswith" in op:
                pattern = str(value or "")
                needle = pattern.strip("%").lower()
                return needle in str(got or "").lower()
            if "eq" in op:
                return got == value
            if "ne" in op:
                return got != value
            if "ge" in op:
                return got >= value
            if "gt" in op:
                return got > value
            if "le" in op:
                return got <= value
            if "lt" in op:
                return got < value
            if "in" in op:
                values = _value(cond.right)
                if not isinstance(values, (list, tuple)):
                    values = [values]
                return got in (values or [])
            raise UnsupportedQueryError(f"Unsupported binary operator {op!r} in {cond!r}")
        if isinstance(cond, sa.sql.expression.InElement if hasattr(sa.sql.expression, "InElement") else type(None)):  # pragma: no cover
            col = _as_column(cond.expressions[0]) if cond.expressions else None
            values = list(cond.right) if cond.right else []
            got = self._ctx_value(col, ctx)
            return got in values
        raise UnsupportedQueryError(f"Unsupported where clause {cond!r}")

    def _eval_exists(self, cond: Exists, ctx: dict[str, FirestoreRecord]) -> bool:
        # Exists is a UnaryExpression: the inner query lives in ``.element`` as a
        # ScalarSelect; unwrap to the underlying Select via ``.original``. The
        # inner select is CORRELATED (it references the outer table), so the
        # outer records stay in scope while evaluating the inner condition.
        inner = getattr(cond, "element", None)
        if inner is None:
            inner = cond.select()
        inner = getattr(inner, "original", inner)
        target_model = None
        for from_ in inner.get_final_froms():
            try:
                target_model = _model_for_table(from_)
                break
            except UnsupportedQueryError:
                continue
        if target_model is None:
            raise UnsupportedQueryError(f"Unsupported EXISTS target in {cond!r}")
        # relationship link from the outer (ctx) model to the target
        candidates: list[FirestoreRecord] = []
        for base_record in ctx.values():
            rels = RELATIONSHIPS.get(base_record._model.__tablename__, {})
            for _rel_name, (kind, target_table, fk) in rels.items():
                if table_to_model().get(target_table) is target_model:
                    if kind == "many":
                        candidates = self.list_where(target_model, fk, base_record.id)
                    else:
                        fk_val = base_record._fields.get(fk)
                        if fk_val is not None:
                            target = self.get(target_model, fk_val)
                            candidates = [target] if target is not None else []
                    break
            if candidates:
                break
        inner_where = list(getattr(inner, "_where_criteria", ()) or ())
        for cand in candidates:
            inner_ctx = dict(ctx)  # correlated: outer tables stay visible
            inner_ctx[target_model.__tablename__] = cand
            if all(self._eval_cond(c, inner_ctx) for c in inner_where):
                return True
        return False

    def _ctx_value(self, col: sa.Column | None, ctx: dict[str, FirestoreRecord]) -> Any:
        if col is None:
            return None
        table = _column_table_name(col)
        record = ctx.get(table)
        if record is None:
            return None
        return _deserialize(record._model, col.key, record._fields.get(col.key))

    # -----------------------------------------------------------------------
    def _select_values(self, plan: _Planned, record: FirestoreRecord, ctx: dict[str, FirestoreRecord]) -> list[Any]:
        base_model = plan.base_model
        assert base_model is not None
        values: list[Any] = []
        for raw in plan.raw_cols:
            raw = _unwrap(raw)
            table = None
            if isinstance(raw, type) and hasattr(raw, "__tablename__"):
                table = raw.__tablename__
            elif isinstance(raw, sa.TableClause) and not isinstance(raw, sa.Column):
                table = raw.name
            if table is not None:
                if table == base_model.__tablename__:
                    values.append(record)
                else:
                    values.append(ctx.get(table))
                continue
            col = _as_column(raw)
            if col is not None:
                # the column may belong to the base OR a joined table — resolve
                # the owning record via ctx (chained joins: Report → Inspection → Device)
                values.append(self._ctx_value(col, ctx))
                continue
            raise UnsupportedQueryError(f"Unsupported select column {raw!r}")
        return values

    def _aggregate(self, plan: _Planned, records: list[FirestoreRecord]) -> list[tuple[Any, ...]]:
        base_model = plan.base_model
        assert base_model is not None
        pairs = []
        for record in records:
            ctx, complete = self._ctx_for(plan, record)
            if complete:
                pairs.append((record, ctx))
        records = [p[0] for p in pairs]
        ctxs = [p[1] for p in pairs]

        def group_key(record: FirestoreRecord, ctx: dict[str, FirestoreRecord]) -> tuple:
            key = []
            for clause in plan.group_by:
                col = _as_column(clause)
                if col is None:
                    fn, args = _function_info(clause)
                    if fn == "date":
                        val = self._ctx_value(_as_column(args[0]), ctx)
                        key.append((val or "").isoformat() if hasattr(val, "isoformat") else str(val or "")[:10])
                        continue
                    raise UnsupportedQueryError(f"Unsupported group_by {clause!r}")
                key.append(self._ctx_value(col, ctx))
            return tuple(key)

        groups: dict[tuple, list[tuple[FirestoreRecord, dict]]] = {}
        for record, ctx in zip(records, ctxs, strict=False):
            groups.setdefault(group_key(record, ctx), []).append((record, ctx))

        out: list[tuple[Any, ...]] = []
        if plan.group_by:
            for key, group in groups.items():
                row = list(key)
                row.extend(self._aggregate_values(plan, group))
                out.append(tuple(row))
        else:
            out.append(tuple(self._aggregate_values(plan, list(zip(records, ctxs, strict=False)))))
        # order aggregate rows (e.g. by date function result)
        if plan.order and len(out) > 1:
            col = _as_column(plan.order[0])
            if col is None:
                fn, args = _function_info(plan.order[0])
                idx = self._aggregate_index(plan, plan.order[0])
                if idx is not None:
                    out.sort(key=lambda r: (r[idx] is None, r[idx]))
        return out

    def _in_group_by(self, plan: _Planned, raw: Any) -> bool:
        """True when a raw select column is also a GROUP BY key."""
        for clause in plan.group_by:
            if _unwrap(raw) is _unwrap(clause):
                return True
            raw_fn = _deep_function(raw)
            clause_fn = _deep_function(clause)
            if raw_fn is not None and raw_fn == clause_fn:
                return True
        return False

    def _aggregate_index(self, plan: _Planned, clause: Any) -> int | None:
        for i, raw in enumerate(plan.raw_cols):
            if _unwrap(raw) is _unwrap(clause) or (
                isinstance(_unwrap(raw), sa.sql.functions.Function) and _function_info(raw) == _function_info(clause)
            ):
                return i
        return None

    def _aggregate_values(self, plan: _Planned, group: list[tuple[FirestoreRecord, dict]]) -> list[Any]:
        values: list[Any] = []
        for raw in plan.raw_cols:
            raw = _unwrap(raw)
            if self._in_group_by(plan, raw):
                continue  # already part of the group key tuple
            col = _as_column(raw)
            if col is not None:
                # plain column in a grouped select → group key value
                vals = [self._ctx_value(col, ctx) for _, ctx in group]
                values.append(vals[0] if vals else None)
                continue
            if _is_function(raw):
                fn, args = _function_info(raw)
                arg_col = _as_column(args[0]) if args else None
                vals = [self._ctx_value(arg_col, ctx) for _, ctx in group if self._ctx_value(arg_col, ctx) is not None] if arg_col is not None else list(group)
                if fn == "count":
                    values.append(len(vals))
                elif fn == "max":
                    values.append(max(vals) if vals else None)
                elif fn == "min":
                    values.append(min(vals) if vals else None)
                elif fn == "avg":
                    values.append(float(sum(vals) / len(vals)) if vals else None)
                elif fn == "date":
                    v = vals[0] if vals else None
                    values.append(v.isoformat() if hasattr(v, "isoformat") else str(v or "")[:10])
                else:
                    raise UnsupportedQueryError(f"Unsupported aggregate func({fn})")
                continue
            raise UnsupportedQueryError(f"Unsupported select column {raw!r}")
        return values

    def __enter__(self) -> FirestoreSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class _Query:
    """Minimal legacy Query shim: db.query(Model).filter(...).all()."""

    def __init__(self, session: FirestoreSession, model: type) -> None:
        self._session = session
        self._model = model
        self._criteria: list[Any] = []
        self._order: list[Any] = []
        self._limit: int | None = None
        self._offset: int = 0

    def filter(self, *criteria: Any) -> _Query:
        self._criteria.extend(criteria)
        return self

    def filter_by(self, **kwargs: Any) -> _Query:
        for key, value in kwargs.items():
            self._criteria.append(getattr(self._model, key) == value)
        return self

    def order_by(self, *clauses: Any) -> _Query:
        self._order.extend(clauses)
        return self

    def limit(self, n: int) -> _Query:
        self._limit = n
        return self

    def offset(self, n: int) -> _Query:
        self._offset = n
        return self

    def all(self) -> list[FirestoreRecord]:
        stmt = select(self._model)
        if self._criteria:
            stmt = stmt.where(*self._criteria)
        if self._order:
            stmt = stmt.order_by(*self._order)
        if self._limit is not None:
            stmt = stmt.limit(self._limit)
        if self._offset:
            stmt = stmt.offset(self._offset)
        return list(self._session.scalars(stmt).all())

    def first(self) -> FirestoreRecord | None:
        rows = self.limit(1).all()
        return rows[0] if rows else None

    def one(self) -> FirestoreRecord:
        rows = self.all()
        if len(rows) != 1:
            raise ValueError(f"Expected one row, got {len(rows)}")
        return rows[0]

    def count(self) -> int:

        stmt = select(func.count()).select_from(self._model)
        if self._criteria:
            stmt = stmt.where(*self._criteria)
        return int(self._session.scalar(stmt) or 0)

    def __iter__(self):
        return iter(self.all())


class ScalarResult:
    """Mirror of sqlalchemy ScalarResult."""

    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values

    def first(self) -> Any:
        return self._values[0] if self._values else None

    def one(self) -> Any:
        if len(self._values) != 1:
            raise ValueError(f"Expected one row, got {len(self._values)}")
        return self._values[0]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)


class Result:
    """Mirror of sqlalchemy Result."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        if len(self._rows) != 1:
            raise ValueError(f"Expected one row, got {len(self._rows)}")
        return self._rows[0]

    def scalar(self) -> Any:
        row = self.first()
        if row is None:
            return None
        return row[0] if isinstance(row, tuple) else row

    def scalars(self) -> ScalarResult:
        return ScalarResult([r[0] if isinstance(r, tuple) else r for r in self._rows])

    def rowcount(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._rows)
