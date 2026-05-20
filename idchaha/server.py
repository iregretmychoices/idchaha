from __future__ import annotations

import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, parse_qs
from urllib.request import Request, urlopen


HENRIK_API_KEYS = [
    key.strip()
    for key in os.environ.get("HENRIK_API_KEYS", "").split(",")
    if key.strip()
]
HENRIK_BASE = "https://api.henrikdev.xyz/valorant"
PROJECT_ROOT = Path(__file__).resolve().parent
REACT_DIST = PROJECT_ROOT / "react-app" / "dist"
ROOT = REACT_DIST if REACT_DIST.exists() else PROJECT_ROOT
PORT = int(os.environ.get("PORT", "8000"))
KEY_LOCK = Lock()
KEY_INDEX = 0
STATS_FILE = PROJECT_ROOT / "search_stats.json"
STATS_LOCK = Lock()


class ValorantSiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/search-stream":
            self.handle_search_stream(parsed.query)
            return
        if parsed.path == "/api/search":
            self.handle_search(parsed.query)
            return
        if parsed.path == "/api/stats":
            self.send_json({"searches": read_search_count()})
            return
        if ROOT == REACT_DIST and not (ROOT / parsed.path.lstrip("/")).exists():
            self.path = "/index.html"
        super().do_GET()

    def handle_search(self, query: str) -> None:
        params = parse_qs(query)
        name = first(params, "name")
        tag = first(params, "tag")
        region = first(params, "region", "na").lower()

        if not name or not tag:
            self.send_json({"error": "Use the format Player#TAG."}, status=400)
            return

        try:
            search_count = increment_search_count()
            payload = build_lookup(name, tag, region)
            payload["search_count"] = search_count
            self.send_json(payload)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
        except Exception as exc:
            self.send_json({"error": f"Lookup failed: {exc}"}, status=500)

    def handle_search_stream(self, query: str) -> None:
        params = parse_qs(query)
        name = first(params, "name")
        tag = first(params, "tag")
        region = first(params, "region", "na").lower()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def progress(stage: str, message: str, current: int = 0, total: int = 0, extra: dict[str, Any] | None = None) -> None:
            payload = {"stage": stage, "message": message, "current": current, "total": total}
            if extra:
                payload.update(extra)
            self.send_event("progress", payload)

        if not name or not tag:
            self.send_event("error", {"error": "Use the format Player#TAG."})
            return

        try:
            search_count = increment_search_count()
            self.send_event("progress", {
                "stage": "search",
                "message": f"Search #{search_count}",
                "current": 0,
                "total": 0,
                "search_count": search_count,
            })
            payload = build_lookup(name, tag, region, progress)
            payload["search_count"] = search_count
            self.send_event("result", payload)
            self.close_connection = True
        except LookupError as exc:
            self.send_event("error", {"error": str(exc)})
            self.close_connection = True
        except Exception as exc:
            self.send_event("error", {"error": f"Lookup failed: {exc}"})
            self.close_connection = True

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_event(self, event: str, payload: dict[str, Any]) -> None:
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
        self.wfile.flush()


def first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    return values[0].strip() if values else default


def build_lookup(name: str, tag: str, region: str, progress: Any | None = None) -> dict[str, Any]:
    safe_name = quote(name)
    safe_tag = quote(tag)
    emit(progress, "account", "Finding account...")
    account = api_json(f"{HENRIK_BASE}/v2/account/{safe_name}/{safe_tag}").get("data")
    if not account:
        raise LookupError(f"Could not find player {name}#{tag}.")

    puuid = account["puuid"]
    player_name = account.get("name", name)
    player_tag = account.get("tag", tag)
    card_id = account.get("card")
    card_url = f"https://media.valorant-api.com/playercards/{card_id}/smallart.png" if card_id else ""
    player_payload = {
        "riot_id": f"{player_name}#{player_tag}",
        "puuid": puuid,
        "account_level": account.get("account_level"),
        "card_url": card_url,
    }

    emit(progress, "profile", f"Account found: {player_name}#{player_tag}.", extra={"player": player_payload})
    emit(progress, "matches", "Loading stored matches...")
    matches_payload = api_json(
        f"{HENRIK_BASE}/v1/stored-matches/{region}/{quote(player_name)}/{quote(player_tag)}?mode=competitive"
    )
    matches = matches_payload.get("data") or []
    if not matches:
        raise LookupError(f"No stored competitive matches found for {player_name}#{player_tag}.")

    emit(progress, "summary", f"Loaded {len(matches)} competitive matches. Pulling rank history...", 0, len(matches))
    rank_payload = safe_api_json(f"{HENRIK_BASE}/v1/by-puuid/stored-mmr-history/{region}/{puuid}")
    mmr_payload = safe_api_json(f"{HENRIK_BASE}/v1/stored-mmr-history/{region}/{quote(player_name)}/{quote(player_tag)}")
    emit(progress, "summary", "Stats and server patterns are ready. Checking name history...", 0, len(matches))

    return {
        "player": player_payload,
        "summary": {
            "matches_checked": len(matches),
            "region": region.upper(),
        },
        "rank": rank_summary(rank_payload, mmr_payload),
        "stats": stats_summary(matches, puuid),
        "servers": server_summary(matches),
        "keys": {"configured": len(HENRIK_API_KEYS)},
        "names": name_history(matches, puuid, progress),
    }


def emit(
    progress: Any | None,
    stage: str,
    message: str,
    current: int = 0,
    total: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    if progress:
        progress(stage, message, current, total, extra)


def ensure_api_keys() -> None:
    if not HENRIK_API_KEYS:
        raise LookupError(
            "HENRIK_API_KEYS is not set. Add your Henrik API key(s) to the environment."
        )


def next_api_key() -> str:
    ensure_api_keys()
    global KEY_INDEX
    with KEY_LOCK:
        key = HENRIK_API_KEYS[KEY_INDEX % len(HENRIK_API_KEYS)]
        KEY_INDEX += 1
    return key


def read_search_count() -> int:
    with STATS_LOCK:
        if not STATS_FILE.exists():
            return 0
        try:
            payload = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        return int(payload.get("searches", 0))


def increment_search_count() -> int:
    with STATS_LOCK:
        count = 0
        if STATS_FILE.exists():
            try:
                count = int(json.loads(STATS_FILE.read_text(encoding="utf-8")).get("searches", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                count = 0
        count += 1
        STATS_FILE.write_text(json.dumps({"searches": count}, indent=2), encoding="utf-8")
        return count


def api_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"accept": "application/json", "Authorization": next_api_key()})
    try:
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise LookupError(clean_api_error(message, exc.code)) from exc
    except URLError as exc:
        raise LookupError(f"Could not reach Henrik API: {exc.reason}") from exc

    if not payload.get("data"):
        raise LookupError(payload.get("message") or "The API returned no data.")
    return payload


def clean_api_error(message: str, status_code: int) -> str:
    if message:
        try:
            payload = json.loads(message)
            errors = payload.get("errors") or []
            if errors and errors[0].get("message"):
                return errors[0]["message"]
            if payload.get("message"):
                return payload["message"]
        except json.JSONDecodeError:
            if len(message) < 120 and "{" not in message:
                return message
    return f"API returned HTTP {status_code}."


def safe_api_json(url: str) -> dict[str, Any] | None:
    try:
        return api_json(url)
    except LookupError:
        return None


def name_history(matches: list[dict[str, Any]], puuid: str, progress: Any | None = None) -> list[dict[str, str]]:
    selected = sample_name_matches(matches)
    seen: set[str] = set()
    names: list[dict[str, str]] = []
    total = len(selected)

    if not selected:
        return names

    emit(progress, "names", f"Checking {total} sampled matches with {len(HENRIK_API_KEYS)} API key(s)...", 0, total)
    workers = max(1, min(total, len(HENRIK_API_KEYS) * 3, 12))
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(name_from_match, match, puuid): match for match in selected}
        for future in as_completed(futures):
            completed += 1
            entry = future.result()
            if entry and entry["riot_id"] not in seen:
                seen.add(entry["riot_id"])
                names.append(entry)
                emit(progress, "names", f"Name found: {entry['riot_id']}", completed, total)
            emit(progress, "names", f"Checked {completed}/{total} sampled matches...", completed, total)

    names.sort(key=lambda entry: entry["sort_date"], reverse=True)
    for entry in names:
        entry.pop("sort_date", None)

    return names


def name_from_match(match: dict[str, Any], puuid: str) -> dict[str, str] | None:
    match_id = match.get("meta", {}).get("id")
    if not match_id:
        return None

    match_payload = retry_api_json(f"{HENRIK_BASE}/v2/match/{match_id}")
    players = ((match_payload or {}).get("data", {}).get("players", {}).get("all_players") or [])
    player = next((entry for entry in players if entry.get("puuid") == puuid), None)
    if not player:
        return None

    started_at = match.get("meta", {}).get("started_at") or ""
    return {
        "riot_id": f"{player.get('name', 'Unknown')}#{player.get('tag', 'Unknown')}",
        "date": nice_date(started_at),
        "sort_date": started_at,
    }


def sample_name_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_matches = sorted(matches, key=lambda match: match.get("meta", {}).get("started_at", ""), reverse=True)
    selected: list[dict[str, Any]] = []
    last_selected: datetime | None = None

    for match in sorted_matches:
        match_date = parse_date(match.get("meta", {}).get("started_at"))
        if not match_date:
            continue
        if last_selected is None or (last_selected - match_date).days >= 29:
            selected.append(match)
            last_selected = match_date

    if sorted_matches and sorted_matches[-1] not in selected:
        selected.append(sorted_matches[-1])
    return sorted(selected, key=lambda match: match.get("meta", {}).get("started_at", ""), reverse=True)


def retry_api_json(url: str, attempts: int = 3) -> dict[str, Any] | None:
    for attempt in range(attempts):
        try:
            return api_json(url)
        except LookupError:
            if attempt < attempts - 1:
                time.sleep(1 + attempt)
    return None


def rank_summary(rank_payload: dict[str, Any] | None, mmr_payload: dict[str, Any] | None) -> dict[str, Any]:
    current = {"display": "Unranked", "change_text": "No recent RR data"}
    latest = (rank_payload or {}).get("data", [None])[0]
    if latest:
        tier = latest.get("tier", {}).get("name", "Unranked")
        rr = latest.get("ranking_in_tier", 0)
        change = latest.get("last_mmr_change", 0)
        current = {"display": f"{tier} {rr}RR", "change_text": f"{change:+d} RR last change"}

    peak = {"display": "No data", "elo": 0}
    history = (mmr_payload or {}).get("data") or []
    if history:
        peak_entry = max(history, key=lambda entry: entry.get("elo", 0))
        tier = peak_entry.get("tier", {}).get("name", "Unknown")
        rr = peak_entry.get("ranking_in_tier", 0)
        peak = {"display": f"{tier} {rr}RR", "elo": peak_entry.get("elo", 0)}

    return {"current": current, "peak": peak}


def stats_summary(matches: list[dict[str, Any]], puuid: str) -> dict[str, Any]:
    totals = Counter()
    agents: Counter[str] = Counter()
    maps: Counter[str] = Counter()

    for match in matches:
        stats = match.get("stats") or {}
        if stats.get("puuid") != puuid:
            continue
        totals["processed"] += 1
        totals["kills"] += stats.get("kills", 0)
        totals["deaths"] += stats.get("deaths", 0)
        totals["assists"] += stats.get("assists", 0)
        totals["score"] += stats.get("score", 0)
        totals["head"] += stats.get("shots", {}).get("head", 0)
        totals["body"] += stats.get("shots", {}).get("body", 0)
        totals["leg"] += stats.get("shots", {}).get("leg", 0)
        agents[stats.get("character", {}).get("name", "Unknown")] += 1
        maps[match.get("meta", {}).get("map", {}).get("name", "Unknown")] += 1

    processed = totals["processed"]
    shots = totals["head"] + totals["body"] + totals["leg"]
    return {
        "kd_ratio": round(totals["kills"] / totals["deaths"], 2) if totals["deaths"] else float(totals["kills"]),
        "kills": totals["kills"],
        "deaths": totals["deaths"],
        "assists": totals["assists"],
        "avg_score": round(totals["score"] / processed) if processed else 0,
        "hs_percent": round((totals["head"] / shots) * 100, 1) if shots else 0,
        "most_agent": counter_label(agents),
        "most_map": counter_label(maps),
    }


def server_summary(matches: list[dict[str, Any]]) -> dict[str, Any]:
    clusters: Counter[str] = Counter(match.get("meta", {}).get("cluster", "Unknown") for match in matches)
    sorted_clusters = clusters.most_common()
    latest = max(matches, key=lambda match: match.get("meta", {}).get("started_at", ""))
    most_name, most_count = sorted_clusters[0] if sorted_clusters else ("Unknown", 0)
    return {
        "last": {
            "name": latest.get("meta", {}).get("cluster", "Unknown"),
            "date": nice_date(latest.get("meta", {}).get("started_at")),
        },
        "most_common": {"name": most_name, "count": most_count},
        "all": [{"name": name, "count": count} for name, count in sorted_clusters],
    }


def counter_label(counter: Counter[str]) -> str:
    if not counter:
        return "None"
    name, count = counter.most_common(1)[0]
    return f"{name} ({count})"


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def nice_date(value: str | None) -> str:
    parsed = parse_date(value)
    if not parsed:
        return ""
    return parsed.astimezone(timezone.utc).strftime("%b %d, %Y")


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, PORT), ValorantSiteHandler)
    print(f"Valorant website running on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
