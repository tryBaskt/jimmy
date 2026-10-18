import json

from app.lambda_handler import handler


def test_http_api_event_reaches_fastapi_health():
    event = {
        "version": "2.0",
        "routeKey": "ANY /api/{proxy+}",
        "rawPath": "/api/health",
        "rawQueryString": "",
        "headers": {"host": "example.execute-api.us-east-1.amazonaws.com"},
        "requestContext": {
            "stage": "$default",
            "http": {
                "method": "GET",
                "path": "/api/health",
                "sourceIp": "127.0.0.1",
                "protocol": "HTTP/1.1",
            },
        },
        "isBase64Encoded": False,
    }
    response = handler(event, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "status": "ok",
        "service": "jimmy-api",
    }
