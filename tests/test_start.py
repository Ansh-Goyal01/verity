from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

from start import QuietHandler


def test_launcher_serves_only_public_assets(tmp_path):
    (tmp_path / ".env").write_text("private test fixture")
    (tmp_path / "index.html").write_text("Root")
    for name in ("web", "design", "logger", "data", "docs"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "index.html").write_text(name)
    handler = partial(QuietHandler, directory=tmp_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = HTTPConnection(*server.server_address, timeout=3)
        try:
            for path in ("/.env", "/%2eenv", "/web/../.env", "/web/%2e%2e%5c.env",
                         "/docs/index.html", "/data/index.html"):
                conn.request("GET", path)
                response = conn.getresponse()
                assert response.status == 404, path
                assert b"private test fixture" not in response.read()
            for path in ("/", "/web/", "/logger/index.html", "/design/index.html"):
                conn.request("GET", path)
                response = conn.getresponse()
                assert response.status == 200, path
                response.read()
        finally:
            conn.close()
            server.shutdown()
            thread.join()
