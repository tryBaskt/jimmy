from paper_replication_api import app


def test_api_contract_documents_authenticated_endpoints():
    contract = app.openapi()
    assert "/api/paper-replication/runs" in contract["paths"]
    assert "security" not in contract["paths"]["/api/paper-replication/health"]["get"]
    assert contract["paths"]["/api/paper-replication/me"]["get"]["security"] == [{"HTTPBearer": []}]
