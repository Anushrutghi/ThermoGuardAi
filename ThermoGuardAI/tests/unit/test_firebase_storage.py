"""Firebase Storage report-file tests (S6) — offline via the fake bucket."""
from __future__ import annotations

import pytest

from backend.core.exceptions import NotFoundError
from tests.fakes.fake_firestore import FakeStorageBucket


@pytest.fixture
def bucket(monkeypatch) -> FakeStorageBucket:
    fake = FakeStorageBucket()
    monkeypatch.setattr("backend.firebase.client.get_storage_bucket", lambda: fake)
    return fake


def test_upload_exists_download_roundtrip(bucket) -> None:  # noqa: ANN001
    from backend.firebase.storage import download_report_pdf, report_file_exists, upload_report_pdf

    remote = upload_report_pdf("TG-000001-ABC123", b"%PDF-1.4 fake")
    assert remote == "reports/TG-000001-ABC123.pdf"
    assert report_file_exists(remote) is True
    assert report_file_exists("reports/missing.pdf") is False
    assert download_report_pdf(remote) == b"%PDF-1.4 fake"


def test_download_missing_raises_not_found(bucket) -> None:  # noqa: ANN001
    from backend.firebase.storage import download_report_pdf

    with pytest.raises(NotFoundError):
        download_report_pdf("reports/nope.pdf")


def test_report_file_exists_never_raises(bucket, monkeypatch) -> None:  # noqa: ANN001
    from backend.firebase.storage import report_file_exists

    def _boom(*args, **kwargs):  # noqa: ANN002
        raise RuntimeError("bucket down")

    monkeypatch.setattr(bucket, "blob", _boom)
    assert report_file_exists("reports/x.pdf") is False


def test_delete_removes_blob(bucket) -> None:  # noqa: ANN001
    from backend.firebase.storage import delete_report_pdf, report_file_exists, upload_report_pdf

    remote = upload_report_pdf("TG-000002-ABC124", b"%PDF-1.4")
    assert report_file_exists(remote) is True
    delete_report_pdf(remote)
    assert report_file_exists(remote) is False


def test_download_after_delete_raises_not_found(bucket) -> None:  # noqa: ANN001
    from backend.firebase.storage import delete_report_pdf, download_report_pdf, upload_report_pdf

    remote = upload_report_pdf("TG-000003-ABC125", b"%PDF-1.4")
    delete_report_pdf(remote)
    with pytest.raises(NotFoundError):
        download_report_pdf(remote)


def test_delete_missing_blob_is_best_effort(bucket) -> None:  # noqa: ANN001
    from backend.firebase.storage import delete_report_pdf

    # Deleting a blob that does not exist must not raise.
    delete_report_pdf("reports/never-existed.pdf")
