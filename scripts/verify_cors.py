"""Check browser preflights without credentials, and ensure identity stays protected."""

import argparse
import urllib.error
import urllib.request


def verify(api_url, origin):
    paths = ["/api/identity/me", "/api/projects", "/api/projects/preflight-check"]
    paths += [f"/api/{service}/preflight-check" for service in ["research", "data", "artifacts", "activity"]]
    for path in paths:
        method = "POST" if path == "/api/projects" else "GET"
        request = urllib.request.Request(api_url + path, method="OPTIONS", headers={
            "Origin": origin, "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            headers = response.headers
            allowed = {h.strip().lower() for h in headers.get("Access-Control-Allow-Headers", "").split(",")}
            methods = {m.strip() for m in headers.get("Access-Control-Allow-Methods", "").split(",")}
            if headers.get("Access-Control-Allow-Origin") != origin or not {"authorization", "content-type"} <= allowed or method not in methods:
                raise RuntimeError("Preflight did not allow the workspace origin, method and request headers.")
    # Making OPTIONS public must never make the identity endpoint public.
    try:
        with urllib.request.urlopen(api_url + "/api/identity/me", timeout=20):
            raise RuntimeError("Identity endpoint unexpectedly allowed an unauthenticated request.")
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    verify(args.api_url.rstrip("/"), args.origin.rstrip("/"))
    print("Browser preflights pass; authenticated endpoints remain protected.")


if __name__ == "__main__":
    main()
