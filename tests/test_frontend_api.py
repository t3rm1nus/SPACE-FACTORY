"""Tests del panel de control de tareas (frontend API y task_queue)."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from core.auth import generate_token
from core.database import get_db, init_db
from core.task_queue import (
    all_tasks,
    cancel_task,
    enqueue_task,
    get_task,
    requeue_task,
)
from frontend.frontend_api import create_app


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", tmp.name)
    init_db()
    yield tmp.name
    try:
        os.remove(tmp.name)
    except OSError:
        pass


def _create_task(status="pending", **overrides):
    kwargs = {
        "capability": "count_words",
        "payload": {"text": "hola"},
        "max_attempts": 1,
    }
    kwargs.update(overrides)
    task_id = enqueue_task(**kwargs)
    if status != "pending":
        with get_db() as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )
            conn.commit()
    return task_id


# ============================================
# Task queue
# ============================================

def test_get_tasks_returns_list():
    _create_task()
    tasks = all_tasks()
    assert isinstance(tasks, list)
    assert len(tasks) == 1
    assert tasks[0]["capability"] == "count_words"


def test_enqueue_task_returns_id():
    task_id = enqueue_task("summarize_text", {"text": "hola mundo"})
    assert isinstance(task_id, int)
    task = get_task(task_id)
    assert task["capability"] == "summarize_text"
    assert task["status"] == "pending"


def test_cancel_task_changes_status():
    task_id = _create_task()
    cancel_task(task_id)
    task = get_task(task_id)
    assert task["status"] == "cancelled"
    assert task["error"] == "Cancelled by operator"


def test_cancel_task_only_non_terminal():
    task_id = _create_task(status="done")
    cancel_task(task_id)
    task = get_task(task_id)
    assert task["status"] == "done"


def test_retry_task_resets_to_pending():
    task_id = _create_task(status="error")
    requeue_task(task_id)
    task = get_task(task_id)
    assert task["status"] == "pending"
    assert task["error"] is None


def test_retry_task_also_works_for_cancelled():
    task_id = _create_task(status="cancelled")
    requeue_task(task_id)
    task = get_task(task_id)
    assert task["status"] == "pending"


# ============================================
# Frontend API endpoints
# ============================================

@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_api_tasks_empty(client):
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_api_enqueue(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({"capability": "count_words", "payload": {"text": "test"}}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "pending"
    assert "task_id" in data


def test_api_enqueue_missing_capability(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({"payload": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_api_enqueue_create_book_plan_minimum_payload(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({"capability": "create_book_plan", "payload": {"idea": "test"}}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "pending"
    assert "task_id" in data


def test_api_enqueue_create_book_plan_complete_payload(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({
            "capability": "create_book_plan",
            "payload": {
                "idea": "test",
                "target_chapters": 6,
                "language": "es",
                "target_audience": "General",
                "desired_length": "3000 palabras",
                "style": "Divulgativo",
                "subject_constraints": ""
            }
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "pending"


def test_api_enqueue_create_book_plan_missing_idea(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({"capability": "create_book_plan", "payload": {"text": "test"}}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "Payload invalido" in data["error"]


def test_api_enqueue_create_book_plan_empty_idea(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({"capability": "create_book_plan", "payload": {"idea": ""}}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "Payload invalido" in data["error"]


def test_api_enqueue_build_book_docx_with_book_object(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({
            "capability": "build_book_docx",
            "payload": {"book": {"id": 1, "title": "Mi libro"}, "language": "es"}
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "pending"
    assert "task_id" in data


def test_api_enqueue_build_book_docx_missing_book(client):
    resp = client.post(
        "/api/enqueue",
        data=json.dumps({"capability": "build_book_docx", "payload": {"language": "es"}}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "Payload invalido" in data["error"]


def test_api_approve_without_jwt(client):
    task_id = _create_task(status="pending_approval")
    resp = client.post(f"/api/approve/{task_id}")
    assert resp.status_code == 401


def test_api_approve_with_jwt(client):
    token = generate_token()
    task_id = _create_task(status="pending_approval")
    resp = client.post(f"/api/approve/{task_id}", headers=_auth_header(token))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "pending"


def test_api_reject_without_jwt(client):
    task_id = _create_task(status="pending_approval")
    resp = client.post(f"/api/reject/{task_id}")
    assert resp.status_code == 401


def test_api_reject_with_jwt(client):
    token = generate_token()
    task_id = _create_task(status="pending_approval")
    resp = client.post(f"/api/reject/{task_id}", headers=_auth_header(token))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "error"


def test_api_cancel_pending(client):
    task_id = _create_task(status="pending")
    resp = client.post(f"/api/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "cancelled"


def test_api_cancel_done_fails(client):
    task_id = _create_task(status="done")
    resp = client.post(f"/api/tasks/{task_id}/cancel")
    assert resp.status_code == 400


def test_api_retry_error(client):
    task_id = _create_task(status="error")
    resp = client.post(f"/api/tasks/{task_id}/retry")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "pending"


def test_api_retry_pending_fails(client):
    task_id = _create_task(status="pending")
    resp = client.post(f"/api/tasks/{task_id}/retry")
    assert resp.status_code == 400
