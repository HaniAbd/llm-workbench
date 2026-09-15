"""The FastAPI app. Importing it is most of the value: it is where the routes,
response models and the OpenAPI schema are actually built."""

from fastapi.testclient import TestClient

import main


def test_app_imports_and_routes_exist():
    paths = {r.path for r in main.app.routes}
    assert "/chat" in paths
    assert "/classify" in paths
    assert "/ask" in paths


def test_openapi_schema_builds():
    """Catches a response_model that pydantic cannot generate a schema for."""
    schema = main.app.openapi()
    assert "/classify" in schema["paths"]
    assert "/chat" in schema["paths"]


def test_classification_response_carries_provenance_and_trace():
    fields = set(main.ClassificationResponse.model_fields)
    assert {"prompt_id", "trace"} <= fields
    assert {"is_support_ticket", "category", "urgency", "sentiment", "requires_human"} <= fields


def test_validation_rejects_blank_and_empty_without_a_model():
    """These 422s happen before any provider call, so they are checkable here."""
    client = TestClient(main.app)
    assert client.post("/classify", json={"text": ""}).status_code == 422
    assert client.post("/classify", json={"text": "   "}).status_code == 422
    assert client.post("/chat", json={"messages": []}).status_code == 422
    assert client.post(
        "/chat", json={"messages": [{"role": "user", "content": "  "}]}
    ).status_code == 422
    # /ask rejects before embedding or touching Postgres, so this needs
    # neither a model nor a database.
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "   "}).status_code == 422


def test_client_supplied_system_role_is_rejected():
    client = TestClient(main.app)
    r = client.post("/chat", json={"messages": [{"role": "system", "content": "x"}]})
    assert r.status_code == 422


def test_cors_allows_any_localhost_port_and_nothing_else():
    client = TestClient(main.app)
    def preflight(origin):
        return client.options(
            "/classify",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    for ok in ("http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:5173"):
        assert preflight(ok).headers.get("access-control-allow-origin") == ok
    for bad in ("http://localhost.evil.com", "https://evil.com"):
        assert "access-control-allow-origin" not in preflight(bad).headers
