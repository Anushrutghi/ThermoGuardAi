"""In-memory fakes for the Firebase Admin SDK surface ThermoGuard uses.

Implements just enough of the Firestore + Storage SDK contract for the
integration code paths: collection().document().get/set/update/delete,
where() queries with get()/stream(), transactions, and bucket blobs. All tests
using these fakes are deterministic and never touch the network.
"""
from __future__ import annotations

from typing import Any


class FakeSnapshot:
    def __init__(self, doc: FakeDocument | None) -> None:
        self._doc = doc

    @property
    def id(self) -> str | None:
        return self._doc.id if self._doc is not None else None

    @property
    def exists(self) -> bool:
        return self._doc is not None and self._doc._data is not None

    def to_dict(self) -> dict | None:
        return dict(self._doc._data) if self._doc is not None and self._doc._data is not None else None


def _nested_get(data: dict, path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class FakeDocument:
    def __init__(self, client: FakeFirestore, collection_name: str, doc_id: str) -> None:
        self._client = client
        self._collection = collection_name
        self.id = doc_id
        self._data: dict | None = None

    def _raw(self) -> dict | None:
        return self._client._store.get((self._collection, self.id))

    def get(self) -> FakeSnapshot:
        self._data = self._client._store.get((self._collection, self.id))
        return FakeSnapshot(self)

    def set(self, data: dict, merge: bool = False) -> None:
        existing = self._client._store.get((self._collection, self.id))
        if merge and existing is not None:
            merged = dict(existing)
            merged.update(data)
            self._client._store[(self._collection, self.id)] = merged
        else:
            self._client._store[(self._collection, self.id)] = dict(data)
        self._data = self._client._store[(self._collection, self.id)]

    def update(self, data: dict) -> None:
        existing = self._client._store.get((self._collection, self.id))
        if existing is None:
            raise ValueError(f"Document {self.id} does not exist")
        existing.update(data)
        self._data = existing

    def delete(self) -> None:
        self._client._store.pop((self._collection, self.id), None)
        self._data = None

    def __repr__(self) -> str:
        return f"<FakeDocument {self._collection}/{self.id}>"


class FakeQuery:
    def __init__(
        self,
        client: FakeFirestore,
        collection_name: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order: list[tuple[str, str]] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> None:
        self._client = client
        self._collection = collection_name
        self._filters = list(filters or [])
        self._order = list(order or [])
        self._limit = limit
        self._offset = offset

    def where(self, field: str, op: str, value: Any) -> FakeQuery:
        return FakeQuery(self._client, self._collection, self._filters + [(field, op, value)], self._order, self._limit, self._offset)

    def order_by(self, field: str, direction: str = "ASCENDING") -> FakeQuery:
        return FakeQuery(self._client, self._collection, self._filters, self._order + [(field, direction)], self._limit, self._offset)

    def limit(self, n: int) -> FakeQuery:
        return FakeQuery(self._client, self._collection, self._filters, self._order, n, self._offset)

    def offset(self, n: int) -> FakeQuery:
        return FakeQuery(self._client, self._collection, self._filters, self._order, self._limit, n)

    def _matches(self, data: dict) -> bool:
        for field, op, value in self._filters:
            got = _nested_get(data, field)
            if op == "==":
                if got != value:
                    return False
            elif op == "in":
                if got not in (value or []):
                    return False
            elif op == "<=":
                if not (got is not None and got <= value):
                    return False
            elif op == ">=":
                if not (got is not None and got >= value):
                    return False
            elif op == "<":
                if not (got is not None and got < value):
                    return False
            elif op == ">":
                if not (got is not None and got > value):
                    return False
            elif op == "array_contains":
                if got is None or value not in got:
                    return False
        return True

    def get(self) -> list[FakeSnapshot]:
        docs: list[FakeDocument] = []
        for (collection, doc_id), data in self._client._store.items():
            if collection != self._collection:
                continue
            if not self._matches(data):
                continue
            doc = FakeDocument(self._client, self._collection, doc_id)
            doc._data = data
            docs.append(doc)
        for field, direction in reversed(self._order):
            docs.sort(key=lambda d, f=field: _nested_get(d._data or {}, f), reverse=direction.lower().startswith("desc"))
        if self._offset:
            docs = docs[self._offset:]
        if self._limit is not None:
            docs = docs[: self._limit]
        return [FakeSnapshot(d) for d in docs]

    def stream(self):
        return iter(self.get())


class FakeCollection:
    def __init__(self, client: FakeFirestore, name: str) -> None:
        self._client = client
        self.name = name

    def document(self, doc_id: str | None = None) -> FakeDocument:
        if doc_id is None:
            import uuid

            doc_id = str(uuid.uuid4())
        return FakeDocument(self._client, self.name, doc_id)

    def where(self, field: str, op: str, value: Any) -> FakeQuery:
        return FakeQuery(self._client, self.name, [(field, op, value)])

    def order_by(self, field: str, direction: str = "ASCENDING") -> FakeQuery:
        return FakeQuery(self._client, self.name, order=[(field, direction)])

    def limit(self, n: int) -> FakeQuery:
        return FakeQuery(self._client, self.name, limit=n)

    def offset(self, n: int) -> FakeQuery:
        return FakeQuery(self._client, self.name, offset=n)

    def get(self) -> list[FakeSnapshot]:
        return FakeQuery(self._client, self.name).get()

    def stream(self):
        return iter(FakeQuery(self._client, self.name).get())


class FakeTransaction:
    def __init__(self, client: FakeFirestore) -> None:
        self._client = client
        self._writes: list[tuple[str, FakeDocument, dict, bool]] = []

    def get(self, ref: FakeDocument) -> FakeSnapshot:
        ref._data = self._client._store.get((ref._collection, ref.id))
        return FakeSnapshot(ref)

    def set(self, ref: FakeDocument, data: dict, merge: bool = False) -> None:
        self._writes.append(("set", ref, data, merge))

    def update(self, ref: FakeDocument, data: dict) -> None:
        self._writes.append(("update", ref, data, False))

    def delete(self, ref: FakeDocument) -> None:
        self._writes.append(("delete", ref, {}, False))

    def commit(self) -> None:
        for action, ref, data, merge in self._writes:
            if action == "set":
                ref.set(data, merge=merge)
            elif action == "update":
                ref.update(data)
            elif action == "delete":
                ref.delete()


class FakeFirestore:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict] = {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self, name)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def run_transaction(self, fn) -> Any:  # noqa: ANN001
        txn = FakeTransaction(self)
        result = fn(txn)
        txn.commit()
        return result

    # -- test helpers ---------------------------------------------------------
    def seed(self, collection: str, doc_id: str, data: dict) -> None:
        self._store[(collection, doc_id)] = dict(data)

    def get_doc(self, collection: str, doc_id: str) -> dict | None:
        return dict(self._store[(collection, doc_id)]) if (collection, doc_id) in self._store else None


class FakeBlob:
    def __init__(self, name: str, bucket: FakeStorageBucket) -> None:
        self.name = name
        self._bucket = bucket
        self._data: bytes | None = None
        self.content_type: str | None = None

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._data = data
        self.content_type = content_type
        self._bucket._blobs[self.name] = self

    def download_as_bytes(self) -> bytes:
        if self._data is None:
            raise RuntimeError("Blob has no content")
        return self._data

    def exists(self) -> bool:
        return self._data is not None

    def delete(self) -> None:
        self._data = None
        self._bucket._blobs.pop(self.name, None)


class FakeStorageBucket:
    def __init__(self) -> None:
        self._blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        return self._blobs.get(name, FakeBlob(name, self))
