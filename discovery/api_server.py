"""API server for the blog discovery dashboard.

Reads from DynamoDB and serves registry data as JSON.
Supports bookmarking and adding custom URLs.

Run: uv run python api_server.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from registry_client import (
    get_item as _get_item,
    make_key as _make_key,
    scan_all as _scan_all,
    update_blogs as _update_blogs,
)

PORT = int(os.environ.get("API_PORT", "8080"))


def get_registry() -> dict:
    """Load full registry, keyed by team name."""
    items = _scan_all()
    return {item["team"]: item for item in items if "team" in item}


def toggle_bookmark(team: str, url: str, bookmarked: bool) -> dict:
    """Toggle bookmark status for a URL within a team's blog list."""
    item = _get_item(team)
    if not item:
        return {"error": "Team not found"}

    blogs = item.get("blogs", [])
    found = False
    for blog in blogs:
        if blog.get("url") == url:
            blog["bookmarked"] = bookmarked
            blog["bookmarked_at"] = datetime.now(timezone.utc).isoformat() if bookmarked else ""
            found = True
            break

    if not found:
        return {"error": "URL not found for this team"}

    _update_blogs(team, blogs)
    return {"success": True, "bookmarked": bookmarked}


def add_url(team: str, url: str) -> dict:
    """Add a custom URL to a team immediately. Checks run async in background."""
    item = _get_item(team)

    if not item:
        return {"error": "Team not found"}

    blogs = item.get("blogs", [])

    # Check if URL already exists for this team
    for blog in blogs:
        if blog.get("url") == url:
            blog["bookmarked"] = True
            blog["bookmarked_at"] = datetime.now(timezone.utc).isoformat()
            _update_blogs(team, blogs)
            return {"success": True, "already_exists": True, "message": f"URL already tracked — added to favorites ⭐"}

    # Check if URL exists under a different team
    registry = get_registry()
    for other_team, entry in registry.items():
        if other_team == team:
            continue
        for blog in entry.get("blogs", []):
            if blog.get("url") == url:
                return {
                    "success": False,
                    "conflict": True,
                    "message": f"This URL is already tracked under {other_team}. Add it to {team} as well?",
                    "existing_team": other_team,
                }

    # Save immediately with "pending" status
    new_blog = {
        "url": url,
        "platform": "Manual",
        "accessible": False,
        "status_label": "pending_check",
        "recency_status": "pending",
        "last_post_date": "",
        "rss_url": "",
        "bookmarked": True,
        "bookmarked_at": datetime.now(timezone.utc).isoformat(),
        "source": "manual",
    }
    blogs.append(new_blog)
    _update_blogs(team, blogs)

    # Trigger async check in background thread
    import threading
    threading.Thread(target=_run_checks_async, args=(team, url), daemon=True).start()

    return {"success": True, "already_exists": False, "message": "✅ URL added — accessibility check running in background"}


def _run_checks_async(team: str, url: str):
    """Run access + recency checks and update DynamoDB (background thread)."""
    try:
        from tools.access_check import access_check
        from tools.recency_check import recency_check

        access_result = access_check._tool_func(url=url)
        recency_result = {"recency_status": "unknown", "last_post_date": ""}
        if access_result.get("accessible"):
            recency_result = recency_check._tool_func(url=url)

        item = _get_item(team)
        if not item:
            return

        blogs = item.get("blogs", [])
        for blog in blogs:
            if blog.get("url") == url:
                blog["accessible"] = access_result.get("accessible", False)
                blog["status_label"] = access_result.get("status_label", "unknown")
                blog["recency_status"] = recency_result.get("recency_status", "unknown")
                blog["last_post_date"] = recency_result.get("last_post_date") or ""
                break

        _update_blogs(team, blogs)
    except Exception as e:
        print(f"[ASYNC CHECK] Error for {url}: {e}")


def delete_url(team: str, url: str) -> dict:
    """Delete a URL from a team's blog list."""
    item = _get_item(team)
    if not item:
        return {"error": "Team not found"}

    blogs = item.get("blogs", [])
    original_len = len(blogs)
    blogs = [b for b in blogs if b.get("url") != url]

    if len(blogs) == original_len:
        return {"error": "URL not found for this team"}

    _update_blogs(team, blogs)
    return {"success": True}


def _force_add(team: str, url: str) -> dict:
    """Force-add a URL to team, skipping conflict check."""
    item = _get_item(team)
    if not item:
        return {"error": "Team not found"}

    blogs = item.get("blogs", [])
    new_blog = {
        "url": url,
        "platform": "Manual",
        "accessible": False,
        "status_label": "pending_check",
        "recency_status": "pending",
        "last_post_date": "",
        "rss_url": "",
        "bookmarked": True,
        "bookmarked_at": datetime.now(timezone.utc).isoformat(),
        "source": "manual",
    }
    blogs.append(new_blog)
    _update_blogs(team, blogs)
    import threading
    threading.Thread(target=_run_checks_async, args=(team, url), daemon=True).start()
    return {"success": True, "message": "✅ URL added — accessibility check running in background"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/registry":
            self._json_response(get_registry())
        elif self.path.startswith("/api/team/"):
            team_name = unquote(self.path[len("/api/team/"):])
            entry = get_registry().get(team_name)
            if entry:
                self._json_response(entry)
            else:
                self._json_response({"error": "Team not found"}, 404)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        body = self._read_body()

        if self.path == "/api/bookmark":
            team = body.get("team", "")
            url = body.get("url", "")
            bookmarked = body.get("bookmarked", True)
            result = toggle_bookmark(team, url, bookmarked)
            self._json_response(result, 200 if result.get("success") else 400)

        elif self.path == "/api/add-url":
            team = body.get("team", "")
            url = body.get("url", "")
            force = body.get("force", False)
            if force:
                # Force add even if exists under another team
                result = add_url(team, url)
                # If still conflict on force, just add it directly
                if result.get("conflict"):
                    result = _force_add(team, url)
            else:
                result = add_url(team, url)
            status = 200 if result.get("success") else (409 if result.get("conflict") else 400)
            self._json_response(result, status)

        elif self.path == "/api/delete-url":
            team = body.get("team", "")
            url = body.get("url", "")
            result = delete_url(team, url)
            self._json_response(result, 200 if result.get("success") else 400)

        else:
            self._json_response({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _json_response(self, data, status=200):
        try:
            payload = json.dumps(data, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    print(f"API server running on http://localhost:{PORT}")
    print(f"  GET  /api/registry        — full registry")
    print(f"  GET  /api/team/<name>      — single team")
    print(f"  POST /api/bookmark         — toggle bookmark {{team, url, bookmarked}}")
    print(f"  POST /api/add-url          — add custom URL {{team, url, force?}}")
    ThreadedHTTPServer(("", PORT), Handler).serve_forever()
