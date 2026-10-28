from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import urllib.error

import pytest
from scripts.verify_cors import verify


@pytest.mark.parametrize('preflight_status', [204, 401])
def test_browser_probe_catches_authorized_preflight_regression(preflight_status):
    requests = []
    origin = 'https://workspace.example'

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            requests.append((self.path, self.headers.get('Authorization')))
            self.send_response(preflight_status)
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Headers', 'authorization,content-type')
            self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,OPTIONS')
            self.end_headers()

        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        if preflight_status == 401:
            with pytest.raises(urllib.error.HTTPError) as failure:
                verify(url, origin)
            assert failure.value.code == 401
        else:
            verify(url, origin)
            assert len(requests) == 7
        assert all(token is None for _, token in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
