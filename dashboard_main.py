"""
F1 Command Center Dashboard (PySide6)

Unified GUI launcher for:
- Tom Shaw's F1 Race Replay
- Live race workflow (staging module)
- News explorer with article detail + image
- 2026 race schedule + next race weather on home
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any

import requests
from PySide6.QtCore import QObject, Qt, QThread, Signal, QTimer, QRectF
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.utils.config import Config
from src.utils.ui_theme import apply_command_center_theme


class LiveTrackMapWidget(QWidget):
    """Lightweight live track map from OpenF1 location coordinates."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.rows: list[dict[str, Any]] = []
        self.traces: dict[int, deque[tuple[float, float]]] = {}

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        for row in rows:
            driver_number = row.get("driver_number")
            x = row.get("x")
            y = row.get("y")
            if driver_number is None or x is None or y is None:
                continue

            dnum = int(driver_number)
            if dnum not in self.traces:
                self.traces[dnum] = deque(maxlen=220)
            self.traces[dnum].append((float(x), float(y)))

        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(10, 10, -10, -10)
        painter.setPen(QPen(QColor("#1E3C4A"), 1))
        painter.setBrush(QBrush(QColor("#0D161D")))
        painter.drawRoundedRect(rect, 12, 12)

        points: list[tuple[int, float, float]] = []
        for row in self.rows:
            d = row.get("driver_number")
            x = row.get("x")
            y = row.get("y")
            if d is None or x is None or y is None:
                continue
            points.append((int(d), float(x), float(y)))

        if not points:
            painter.setPen(QColor("#92A3A8"))
            painter.drawText(rect, Qt.AlignCenter, "Waiting for live location stream...")
            painter.end()
            return

        all_trace_points: list[tuple[float, float]] = []
        for trace in self.traces.values():
            all_trace_points.extend(list(trace))

        if all_trace_points:
            xs = [p[0] for p in all_trace_points]
            ys = [p[1] for p in all_trace_points]
        else:
            xs = [p[1] for p in points]
            ys = [p[2] for p in points]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)

        draw_area = rect.adjusted(18, 18, -18, -18)

        def to_canvas(x: float, y: float) -> tuple[float, float]:
            nx = (x - min_x) / span_x
            ny = (y - min_y) / span_y
            cx = draw_area.left() + nx * draw_area.width()
            cy = draw_area.bottom() - ny * draw_area.height()
            return cx, cy

        # Draw recent traces
        for dnum, trace in self.traces.items():
            if len(trace) < 2:
                continue
            color = self._driver_color(dnum)
            painter.setPen(QPen(color, 1.8))
            it = list(trace)
            for i in range(1, len(it)):
                x1, y1 = to_canvas(it[i - 1][0], it[i - 1][1])
                x2, y2 = to_canvas(it[i][0], it[i][1])
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Draw current points
        for dnum, x, y in points:
            cx, cy = to_canvas(x, y)
            color = self._driver_color(dnum)
            painter.setPen(QPen(QColor("#0A1014"), 1))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))

            painter.setPen(QColor("#E8EEF0"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.drawText(int(cx + 7), int(cy - 7), str(dnum))

        painter.setPen(QColor("#16E0D6"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
        painter.drawText(rect.adjusted(8, 6, -8, -6), Qt.AlignTop | Qt.AlignLeft, "Live Track Map")
        painter.end()

    @staticmethod
    def _driver_color(driver_number: int) -> QColor:
        palette = [
            QColor("#16E0D6"), QColor("#FF4D4D"), QColor("#FFD166"), QColor("#7BD389"),
            QColor("#4D96FF"), QColor("#B388EB"), QColor("#F4A261"), QColor("#06D6A0"),
        ]
        return palette[driver_number % len(palette)]


def request_json_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 12,
    retries: int = 2,
    backoff_seconds: float = 1.0,
) -> Any:
    """Small production-safe HTTP GET helper with retries and backoff."""
    headers = {"accept": "application/json"}
    if Config.OPENF1_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {Config.OPENF1_ACCESS_TOKEN}"

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
    raise RuntimeError(f"Request failed after retries for {url}: {last_exc}")


class LiveRaceWorker(QObject):
    """Background worker for resilient OpenF1 live telemetry polling."""

    status_changed = Signal(str)
    telemetry_loaded = Signal(list)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, session_key: int | str):
        super().__init__()
        self.session_key = session_key
        self._running = True
        self._driver_cache: dict[int, dict[str, Any]] = {}
        self._driver_order: list[dict[str, Any]] = []
        self._round_robin_index = 0

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        try:
            self.status_changed.emit(f"Connecting to OpenF1 session {self.session_key}...")

            while self._running:
                try:
                    drivers = self._fetch_drivers()
                    if drivers:
                        self._driver_order = drivers

                    self._poll_positions_recent()
                    self._poll_locations_recent()
                    self._poll_driver_chunks(chunk_size=5)
                    table_rows = self._build_rows()
                    self.telemetry_loaded.emit(table_rows)
                    self.status_changed.emit(
                        f"Live telemetry connected • Session {self.session_key} • Drivers {len(self._driver_order)}"
                    )
                except Exception as exc:
                    self.failed.emit(f"Live polling error: {exc}")
                    self.status_changed.emit("Live polling degraded - retrying...")

                for _ in range(10):  # 10 * 300ms = 3s loop
                    if not self._running:
                        break
                    time.sleep(0.3)
        finally:
            self.finished.emit()

    def _fetch_drivers(self) -> list[dict[str, Any]]:
        payload = request_json_with_retry(
            f"{Config.OPENF1_BASE_URL}/drivers",
            params={"session_key": self.session_key},
            timeout=10,
            retries=2,
        )
        return payload if isinstance(payload, list) else []

    def _poll_driver_chunks(self, chunk_size: int = 5) -> None:
        if not self._driver_order:
            return

        # Always keep a complete baseline row-set (name/team/position) for all drivers.
        for d in self._driver_order:
            driver_number = d.get("driver_number")
            if driver_number is None:
                continue
            driver_id = int(driver_number)
            existing = self._driver_cache.get(driver_id, {})
            self._driver_cache[driver_id] = {
                "driver_number": driver_number,
                "name": d.get("full_name") or d.get("name_acronym") or existing.get("name") or "Unknown",
                "team": d.get("team_name") or existing.get("team") or "Unknown",
                "position": d.get("position") or existing.get("position") or 99,
                "speed": existing.get("speed"),
                "gear": existing.get("gear"),
                "throttle": existing.get("throttle"),
                "brake": existing.get("brake"),
                "x": existing.get("x"),
                "y": existing.get("y"),
                "updated": existing.get("updated") or "-",
            }

        total = len(self._driver_order)
        start = self._round_robin_index
        end = min(start + chunk_size, total)
        current = self._driver_order[start:end]
        if end >= total:
            overflow = (start + chunk_size) - total
            if overflow > 0:
                current += self._driver_order[:overflow]
            self._round_robin_index = overflow
        else:
            self._round_robin_index = end

        for d in current:
            driver_number = d.get("driver_number")
            if driver_number is None:
                continue

            driver_id = int(driver_number)

            try:
                recent_iso = (datetime.utcnow() - timedelta(seconds=45)).isoformat() + "Z"
                car_data = request_json_with_retry(
                    f"{Config.OPENF1_BASE_URL}/car_data",
                    params={
                        "session_key": self.session_key,
                        "driver_number": driver_number,
                        "date>=": recent_iso,
                    },
                    timeout=6,
                    retries=1,
                )

                # Fallback for non-live/historical sessions where recent window may be empty.
                if not car_data:
                    car_data = request_json_with_retry(
                        f"{Config.OPENF1_BASE_URL}/car_data",
                        params={"session_key": self.session_key, "driver_number": driver_number},
                        timeout=6,
                        retries=0,
                    )
                latest = car_data[-1] if isinstance(car_data, list) and car_data else {}

                self._driver_cache[driver_id] = {
                    "driver_number": driver_number,
                    "name": d.get("full_name") or d.get("name_acronym") or "Unknown",
                    "team": d.get("team_name") or "Unknown",
                    "position": self._driver_cache[driver_id].get("position") or d.get("position") or 99,
                    "speed": latest.get("speed"),
                    "gear": latest.get("n_gear") or latest.get("gear"),
                    "throttle": latest.get("throttle"),
                    "brake": latest.get("brake"),
                    "x": self._driver_cache[driver_id].get("x"),
                    "y": self._driver_cache[driver_id].get("y"),
                    "updated": datetime.utcnow().strftime("%H:%M:%S"),
                }
            except Exception:
                # Keep last known data; fail soft for production resilience.
                continue

    def _poll_positions_recent(self) -> None:
        try:
            recent_iso = (datetime.utcnow() - timedelta(seconds=45)).isoformat() + "Z"
            payload = request_json_with_retry(
                f"{Config.OPENF1_BASE_URL}/position",
                params={"session_key": self.session_key, "date>=": recent_iso},
                timeout=6,
                retries=1,
            )
            if not isinstance(payload, list):
                return

            latest_by_driver: dict[int, dict[str, Any]] = {}
            for item in payload:
                dnum = item.get("driver_number")
                if dnum is None:
                    continue
                latest_by_driver[int(dnum)] = item

            for dnum, item in latest_by_driver.items():
                existing = self._driver_cache.get(dnum, {})
                existing["position"] = item.get("position", existing.get("position", 99))
                self._driver_cache[dnum] = existing
        except Exception:
            return

    def _poll_locations_recent(self) -> None:
        try:
            recent_iso = (datetime.utcnow() - timedelta(seconds=45)).isoformat() + "Z"
            payload = request_json_with_retry(
                f"{Config.OPENF1_BASE_URL}/location",
                params={"session_key": self.session_key, "date>=": recent_iso},
                timeout=6,
                retries=1,
            )
            if not isinstance(payload, list):
                return

            latest_by_driver: dict[int, dict[str, Any]] = {}
            for item in payload:
                dnum = item.get("driver_number")
                if dnum is None:
                    continue
                latest_by_driver[int(dnum)] = item

            for dnum, item in latest_by_driver.items():
                existing = self._driver_cache.get(dnum, {})
                existing["x"] = item.get("x", existing.get("x"))
                existing["y"] = item.get("y", existing.get("y"))
                self._driver_cache[dnum] = existing
        except Exception:
            return

    def _build_rows(self) -> list[dict[str, Any]]:
        rows = list(self._driver_cache.values())
        rows.sort(key=lambda x: (x.get("position") is None, x.get("position", 999)))
        return rows


class DashboardDataWorker(QObject):
    """Background worker for schedule/news/weather loading."""

    schedule_loaded = Signal(list, dict)
    news_loaded = Signal(list)
    failed = Signal(str)
    finished = Signal()

    CACHE_PATH = Path(__file__).parent / "data" / "raw" / "dashboard_cache.json"

    def load_all(self) -> None:
        races: list[dict[str, Any]] = []

        try:
            try:
                races = self._fetch_2026_schedule()
            except Exception as exc:
                self.failed.emit(f"Schedule fetch failed: {exc}")

            weather = self._fetch_next_race_weather(races)
            self.schedule_loaded.emit(races, weather)

            # News must never break the dashboard UX.
            articles = self._fetch_news(limit=15)
            self.news_loaded.emit(articles)
        finally:
            self.finished.emit()

    def _get_json_with_retry(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: int = 15,
        retries: int = 2,
        backoff_seconds: float = 1.2,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = requests.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(backoff_seconds * (attempt + 1))

        raise RuntimeError(f"Request failed after retries for {url}: {last_exc}")

    def _write_cache(self, races: list[dict[str, Any]]) -> None:
        try:
            self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.utcnow().isoformat(),
                "races": races,
            }
            self.CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

    def _read_cached_races(self) -> list[dict[str, Any]]:
        try:
            if not self.CACHE_PATH.exists():
                return []
            payload = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            races = payload.get("races", [])
            return races if isinstance(races, list) else []
        except Exception:
            return []

    def _fetch_2026_schedule(self) -> list[dict[str, Any]]:
        url = f"{Config.JOLPICA_2026_BASE_URL.rstrip('/')}/races.json"
        payload = self._get_json_with_retry(url, timeout=20, retries=2)

        races = (
            payload.get("MRData", {}).get("RaceTable", {}).get("Races")
            or payload.get("RaceTable", {}).get("Races")
            or []
        )

        if races:
            self._write_cache(races)
            return races

        cached = self._read_cached_races()
        if cached:
            return cached

        return races

    def _fetch_next_race_weather(self, races: list[dict[str, Any]]) -> dict[str, str]:
        if not races:
            cached = self._read_cached_races()
            if cached:
                races = cached
            else:
                return {
                    "race_name": "No schedule data",
                    "location": "-",
                    "race_date": "-",
                    "summary": "Could not load upcoming races.",
                }

        if not races:
            return {
                "race_name": "No schedule data",
                "location": "-",
                "race_date": "-",
                "summary": "Could not load upcoming races.",
            }

        today = date.today()
        next_race = None

        for race in races:
            race_date = race.get("date")
            if not race_date:
                continue
            try:
                parsed = datetime.strptime(race_date, "%Y-%m-%d").date()
                if parsed >= today:
                    next_race = race
                    break
            except ValueError:
                continue

        if next_race is None:
            next_race = races[-1]

        race_name = next_race.get("raceName", "Unknown Race")
        race_date = next_race.get("date", "Unknown Date")
        location = next_race.get("Circuit", {}).get("Location", {})
        city = location.get("locality", "Unknown City")
        country = location.get("country", "Unknown Country")
        place = f"{city}, {country}"

        lat_raw = location.get("lat")
        lon_raw = location.get("long")

        lat = float(lat_raw) if lat_raw not in (None, "") else None
        lon = float(lon_raw) if lon_raw not in (None, "") else None

        if lat is None or lon is None:
            lat, lon = self._resolve_lat_lon(city, country)

        if lat is None or lon is None:
            return {
                "race_name": race_name,
                "location": place,
                "race_date": race_date,
                "summary": "Weather unavailable (could not resolve race location).",
            }

        weather_summary = self._fetch_weather_summary(lat, lon, race_date)
        return {
            "race_name": race_name,
            "location": place,
            "race_date": race_date,
            "summary": weather_summary,
        }

    def _resolve_lat_lon(self, city: str, country: str) -> tuple[float | None, float | None]:
        geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
        response = requests.get(
            geocode_url,
            params={"name": f"{city}, {country}", "count": 1, "language": "en", "format": "json"},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        if not results:
            return None, None

        first = results[0]
        return float(first.get("latitude")), float(first.get("longitude"))

    def _fetch_weather_summary(self, latitude: float, longitude: float, race_date: str) -> str:
        try:
            target_date = datetime.strptime(race_date, "%Y-%m-%d").date()
        except ValueError:
            return "Weather unavailable (invalid race date format)."

        days_ahead = (target_date - date.today()).days
        if days_ahead > 16:
            return (
                f"Forecast for {race_date} not available yet. "
                "Open-Meteo provides reliable forecast up to 16 days ahead."
            )

        if days_ahead < 0:
            return f"Race date {race_date} is in the past; live forecast unavailable."

        try:
            payload = self._get_json_with_retry(
                Config.OPEN_METEO_BASE_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
                    "start_date": race_date,
                    "end_date": race_date,
                    "timezone": "auto",
                },
                timeout=20,
                retries=2,
            )
        except Exception:
            return (
                f"Weather service timeout for {race_date}. "
                "Using race schedule only for now — try Refresh again."
            )

        daily = payload.get("daily", {})

        t_max = _first_or_unknown(daily.get("temperature_2m_max"))
        t_min = _first_or_unknown(daily.get("temperature_2m_min"))
        rain = _first_or_unknown(daily.get("precipitation_probability_max"))
        wind_max = _first_or_unknown(daily.get("wind_speed_10m_max"))

        return f"Forecast {race_date}: {t_min}°C–{t_max}°C | Rain risk {rain}% | Wind up to {wind_max} km/h"

    def _fetch_news(self, limit: int = 10) -> list[dict[str, Any]]:
        try:
            response = requests.get(
                "https://gnews.io/api/v4/search",
                params={
                    "q": "Formula 1 OR F1",
                    "lang": Config.NEWS_API_LANGUAGE,
                    "max": limit,
                    "sortby": "publishedAt",
                    "token": Config.NEWS_API_KEY,
                },
                timeout=15,
            )

            if response.status_code in {401, 403, 429}:
                return []

            response.raise_for_status()
            payload = response.json()
            return payload.get("articles", [])
        except Exception:
            return []


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("F1 Command Center")
        self.resize(1440, 920)

        self.articles: list[dict[str, Any]] = []
        self.next_race_date: date | None = None
        self.live_worker: LiveRaceWorker | None = None
        self.live_thread: QThread | None = None

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = self._build_sidebar()
        root_layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.home_page = self._build_home_page()
        self.live_page = self._build_live_page()
        self.news_page = self._build_news_page()

        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.live_page)
        self.pages.addWidget(self.news_page)

        root_layout.addWidget(self.pages, 1)

        self.setCentralWidget(root)
        apply_command_center_theme(self)

        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._update_countdown)
        self.countdown_timer.start(1000)

        self._start_load()

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setMinimumWidth(220)
        side.setMaximumWidth(320)

        layout = QVBoxLayout(side)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(10)

        title = QLabel("🏁 F1 COMMAND CENTER")
        title.setWordWrap(True)
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        title.setObjectName("sideTitle")
        layout.addWidget(title)
        layout.addSpacing(8)

        home_btn = QPushButton("Home")
        home_btn.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        layout.addWidget(home_btn)

        replay_btn = QPushButton("Launch F1 Race Replay")
        replay_btn.clicked.connect(self.launch_replay)
        layout.addWidget(replay_btn)

        live_btn = QPushButton("Live Races")
        live_btn.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        layout.addWidget(live_btn)

        news_btn = QPushButton("F1 News")
        news_btn.clicked.connect(lambda: self.pages.setCurrentIndex(2))
        layout.addWidget(news_btn)

        refresh_btn = QPushButton("Refresh Dashboard Data")
        refresh_btn.clicked.connect(self._start_load)
        layout.addWidget(refresh_btn)

        layout.addStretch(1)

        exit_btn = QPushButton("Exit")
        exit_btn.clicked.connect(self.close)
        layout.addWidget(exit_btn)
        return side

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        headline = QLabel("Mission Control Home")
        headline.setObjectName("sectionTitle")
        headline.setFont(QFont("Segoe UI", 20, QFont.Bold))
        layout.addWidget(headline)

        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)

        self.card_next_race = self._info_card("Next Race", "Loading...")
        self.card_weather = self._info_card("Next Race Weather", "Loading...")
        self.card_countdown = self._info_card("Next Race Countdown", "--d --h --m --s")
        self.card_quick_actions = self._info_card(
            "Quick Launch",
            "• Replay: Launches Tom Shaw F1 Replay\n• Live: Opens live race staging page\n• News: Opens article explorer",
        )

        cards.addWidget(self.card_next_race, 0, 0)
        cards.addWidget(self.card_weather, 0, 1)
        cards.addWidget(self.card_countdown, 1, 0, 1, 2)
        cards.addWidget(self.card_quick_actions, 2, 0, 1, 2)

        layout.addLayout(cards)

        schedule_title = QLabel("2026 Race Schedule")
        schedule_title.setObjectName("sectionTitle")
        schedule_title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        layout.addWidget(schedule_title)

        self.schedule_table = QTableWidget(0, 4)
        self.schedule_table.setHorizontalHeaderLabels(["Round", "Race", "Circuit", "Date"])
        self.schedule_table.horizontalHeader().setStretchLastSection(True)
        self.schedule_table.setAlternatingRowColors(True)
        self.schedule_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.schedule_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.schedule_table, 1)

        return page

    def _build_live_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Live Race Monitor")
        title.setObjectName("sectionTitle")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        layout.addWidget(title)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.live_session_input = QLineEdit()
        self.live_session_input.setPlaceholderText("OpenF1 session key (e.g. 9158)")
        self.live_session_input.setMinimumHeight(38)

        self.detect_session_btn = QPushButton("Auto Detect Session")
        self.detect_session_btn.clicked.connect(self._auto_detect_live_session)
        self.detect_session_btn.setMinimumHeight(38)

        self.start_live_btn = QPushButton("Start Live")
        self.start_live_btn.setObjectName("primaryButton")
        self.start_live_btn.clicked.connect(self._start_live_monitor)
        self.start_live_btn.setMinimumHeight(38)

        self.stop_live_btn = QPushButton("Stop Live")
        self.stop_live_btn.clicked.connect(self._stop_live_monitor)
        self.stop_live_btn.setMinimumHeight(38)
        self.stop_live_btn.setEnabled(False)

        controls.addWidget(self.live_session_input, 1)
        controls.addWidget(self.detect_session_btn)
        controls.addWidget(self.start_live_btn)
        controls.addWidget(self.stop_live_btn)
        layout.addLayout(controls)

        self.live_status = QLabel("Live monitor idle")
        self.live_status.setObjectName("mutedText")
        self.live_status.setWordWrap(True)
        layout.addWidget(self.live_status)

        self.live_track_map = LiveTrackMapWidget()
        layout.addWidget(self.live_track_map)

        self.live_table = QTableWidget(0, 8)
        self.live_table.setHorizontalHeaderLabels(
            ["Pos", "Driver #", "Driver", "Team", "Speed", "Gear", "Throttle", "Brake"]
        )
        self.live_table.setAlternatingRowColors(True)
        self.live_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.live_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.live_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.live_table, 1)

        layout.addStretch(1)
        return page

    def _auto_detect_live_session(self) -> None:
        self.live_status.setText("Detecting latest active session...")
        try:
            year = datetime.utcnow().year
            payload = request_json_with_retry(
                f"{Config.OPENF1_BASE_URL}/sessions",
                params={"year": year},
                timeout=12,
                retries=1,
            )
            sessions = payload if isinstance(payload, list) else []
            if not sessions:
                self.live_status.setText("No sessions returned by OpenF1. Enter session key manually.")
                return

            def _dt(value: str | None) -> datetime:
                if not value:
                    return datetime.min
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except Exception:
                    return datetime.min

            now = datetime.utcnow().replace(tzinfo=None)
            preferred = [
                s for s in sessions
                if str(s.get("session_name", "")).lower() in {"qualifying", "race", "sprint", "sprint qualifying"}
            ]
            preferred.sort(key=lambda s: _dt(s.get("date_start")), reverse=True)

            picked = None
            for s in preferred:
                start = _dt(s.get("date_start"))
                end = _dt(s.get("date_end"))
                if start <= now <= (end if end != datetime.min else now):
                    picked = s
                    break

            if picked is None:
                picked = preferred[0] if preferred else sessions[0]

            key = picked.get("session_key")
            if key is None:
                self.live_status.setText("Could not extract session key. Enter key manually.")
                return

            self.live_session_input.setText(str(key))
            name = picked.get("session_name", "Session")
            event = picked.get("country_name") or picked.get("location") or "Unknown"
            self.live_status.setText(f"Detected {name} • {event} • session_key={key}")
        except Exception as exc:
            self.live_session_input.setText("latest")
            self.live_status.setText(
                f"Auto detect failed: {exc}. Falling back to session_key=latest for live mode."
            )

    def _start_live_monitor(self) -> None:
        session_key_text = self.live_session_input.text().strip()
        if not (session_key_text.isdigit() or session_key_text.lower() == "latest"):
            QMessageBox.warning(
                self,
                "Invalid session key",
                "Please enter a numeric OpenF1 session key or 'latest'.",
            )
            return

        session_key: int | str = int(session_key_text) if session_key_text.isdigit() else "latest"
        self._stop_live_monitor(silent=True)

        self.live_thread = QThread(self)
        self.live_worker = LiveRaceWorker(session_key=session_key)
        self.live_worker.moveToThread(self.live_thread)

        self.live_thread.started.connect(self.live_worker.run)
        self.live_worker.status_changed.connect(self._on_live_status)
        self.live_worker.telemetry_loaded.connect(self._on_live_telemetry)
        self.live_worker.failed.connect(self._on_live_error)
        self.live_worker.finished.connect(self.live_thread.quit)
        self.live_worker.finished.connect(self.live_worker.deleteLater)
        self.live_thread.finished.connect(self.live_thread.deleteLater)

        self.live_thread.start()
        self.start_live_btn.setEnabled(False)
        self.stop_live_btn.setEnabled(True)
        self.detect_session_btn.setEnabled(False)
        self.live_status.setText(f"Starting live monitor for session {session_key}...")

    def _stop_live_monitor(self, silent: bool = False) -> None:
        if self.live_worker is not None:
            self.live_worker.stop()

        if self.live_thread is not None and self.live_thread.isRunning():
            self.live_thread.quit()
            self.live_thread.wait(2000)

        self.live_worker = None
        self.live_thread = None

        self.start_live_btn.setEnabled(True)
        self.stop_live_btn.setEnabled(False)
        self.detect_session_btn.setEnabled(True)
        if hasattr(self, "live_track_map") and isinstance(self.live_track_map, LiveTrackMapWidget):
            self.live_track_map.set_rows([])
            self.live_track_map.traces.clear()
        if not silent:
            self.live_status.setText("Live monitor stopped")

    def _on_live_status(self, status: str) -> None:
        self.live_status.setText(status)

    def _on_live_error(self, message: str) -> None:
        self.live_status.setText(message)

    def _on_live_telemetry(self, rows: list[dict[str, Any]]) -> None:
        if hasattr(self, "live_track_map") and isinstance(self.live_track_map, LiveTrackMapWidget):
            self.live_track_map.set_rows(rows)

        self.live_table.setRowCount(0)
        for idx, row in enumerate(rows):
            self.live_table.insertRow(idx)
            self.live_table.setItem(idx, 0, QTableWidgetItem(str(row.get("position", "-"))))
            self.live_table.setItem(idx, 1, QTableWidgetItem(str(row.get("driver_number", "-"))))
            self.live_table.setItem(idx, 2, QTableWidgetItem(str(row.get("name", "Unknown"))))
            self.live_table.setItem(idx, 3, QTableWidgetItem(str(row.get("team", "Unknown"))))

            speed = row.get("speed")
            self.live_table.setItem(idx, 4, QTableWidgetItem(f"{speed} km/h" if speed is not None else "-"))
            self.live_table.setItem(idx, 5, QTableWidgetItem(str(row.get("gear", "-"))))
            throttle = row.get("throttle")
            brake = row.get("brake")
            self.live_table.setItem(idx, 6, QTableWidgetItem(f"{throttle}%" if throttle is not None else "-"))
            self.live_table.setItem(idx, 7, QTableWidgetItem(f"{brake}%" if brake is not None else "-"))

    def _build_news_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left = QVBoxLayout()
        left_title = QLabel("F1 Headlines")
        left_title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        left.addWidget(left_title)

        self.news_list = QListWidget()
        self.news_list.currentRowChanged.connect(self._show_article)
        left.addWidget(self.news_list, 1)

        right = QVBoxLayout()
        self.news_title = QLabel("Select an article")
        self.news_title.setWordWrap(True)
        self.news_title.setFont(QFont("Segoe UI", 14, QFont.Bold))

        self.news_meta = QLabel("")
        self.news_meta.setWordWrap(True)

        self.news_image = QLabel("No image")
        self.news_image.setAlignment(Qt.AlignCenter)
        self.news_image.setMinimumHeight(260)
        self.news_image.setObjectName("imageFrame")

        self.news_body = QTextBrowser()
        self.news_body.setOpenExternalLinks(True)

        right.addWidget(self.news_title)
        right.addWidget(self.news_meta)
        right.addWidget(self.news_image)
        right.addWidget(self.news_body, 1)

        left_widget = QWidget()
        left_widget.setLayout(left)
        right_widget = QWidget()
        right_widget.setLayout(right)

        split.addWidget(left_widget)
        split.addWidget(right_widget)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setSizes([420, 820])

        layout.addWidget(split, 1)
        return page

    def _start_load(self) -> None:
        self.statusBar().showMessage("Loading schedule, weather, and news...")

        self.thread = QThread(self)
        self.worker = DashboardDataWorker()
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.load_all)
        self.worker.schedule_loaded.connect(self._on_schedule_loaded)
        self.worker.news_loaded.connect(self._on_news_loaded)
        self.worker.failed.connect(self._on_load_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    def _on_schedule_loaded(self, races: list, weather: dict) -> None:
        self.schedule_table.setRowCount(0)
        for row, race in enumerate(races):
            self.schedule_table.insertRow(row)
            self.schedule_table.setItem(row, 0, QTableWidgetItem(str(race.get("round", "-"))))
            self.schedule_table.setItem(row, 1, QTableWidgetItem(str(race.get("raceName", "-"))))

            circuit = race.get("Circuit", {})
            circuit_name = circuit.get("circuitName", "-")
            self.schedule_table.setItem(row, 2, QTableWidgetItem(circuit_name))
            self.schedule_table.setItem(row, 3, QTableWidgetItem(str(race.get("date", "-"))))

        next_race = f"{weather.get('race_name', '-')}\n{weather.get('location', '-')}\n{weather.get('race_date', '-')}"
        self._set_card_value(self.card_next_race, next_race)
        self._set_card_value(self.card_weather, weather.get("summary", "Weather unavailable."))

        race_date_text = weather.get("race_date", "")
        self.next_race_date = None
        if race_date_text:
            try:
                self.next_race_date = datetime.strptime(race_date_text, "%Y-%m-%d").date()
            except ValueError:
                self.next_race_date = None

        self._update_countdown()

    def _on_news_loaded(self, articles: list) -> None:
        self.articles = articles
        self.news_list.clear()

        if not self.articles:
            self.news_list.addItem("No news loaded. Check GNEWS API key or API limits.")
            self.news_title.setText("News feed unavailable")
            self.news_meta.setText("Dashboard running with schedule + weather only.")
            self.news_body.setHtml(
                "<p>Could not load GNews articles.</p>"
                "<p>Check your <b>NEWS_API_KEY</b> in <code>.env</code> (GNews token) or wait for rate-limit reset.</p>"
            )
            self.news_image.setPixmap(QPixmap())
            self.news_image.setText("No image available")
            self.statusBar().showMessage("Dashboard data updated (news unavailable)", 5000)
            return

        for article in self.articles:
            title = article.get("title") or "Untitled"
            source = article.get("source", {}).get("name", "Unknown")
            item = QListWidgetItem(f"{title}\n{source}")
            self.news_list.addItem(item)

        if self.articles:
            self.news_list.setCurrentRow(0)

        self.statusBar().showMessage("Dashboard data updated", 5000)

    def _on_load_failed(self, error_message: str) -> None:
        self.statusBar().showMessage("Load failed", 5000)
        QMessageBox.warning(self, "Dashboard Data Error", error_message)

    def _update_countdown(self) -> None:
        if self.next_race_date is None:
            self._set_card_value(self.card_countdown, "Waiting for next race date...")
            return

        now = datetime.now()
        target = datetime.combine(self.next_race_date, datetime.min.time())
        delta = target - now

        if delta.total_seconds() <= 0:
            self._set_card_value(self.card_countdown, "Race day is live or completed.")
            return

        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        self._set_card_value(
            self.card_countdown,
            f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s",
        )

    def _show_article(self, index: int) -> None:
        if index < 0 or index >= len(self.articles):
            return

        article = self.articles[index]
        title = article.get("title", "Untitled")
        source = article.get("source", {}).get("name", "Unknown Source")
        published = article.get("publishedAt", "")
        description = article.get("description") or "No summary provided."
        content = article.get("content") or "No content provided by source."
        url = article.get("url", "")
        image_url = article.get("image") or article.get("urlToImage")

        self.news_title.setText(title)
        self.news_meta.setText(f"{source} • {published}")

        body_html = (
            f"<p><b>Summary:</b> {description}</p>"
            f"<p>{content}</p>"
            f"<p><a href='{url}'>Read full article</a></p>"
        )
        self.news_body.setHtml(body_html)

        pix = self._load_pixmap_from_url(image_url)
        if pix is None:
            self.news_image.setPixmap(QPixmap())
            self.news_image.setText("No image available")
        else:
            self.news_image.setText("")
            self.news_image.setPixmap(pix.scaled(640, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def launch_replay(self) -> None:
        replay_root = Path(__file__).parent / "f1-race-replay-main"
        replay_entry = replay_root / "main.py"

        if not replay_entry.exists():
            QMessageBox.critical(
                self,
                "Replay Not Found",
                f"Could not find replay entry point at:\n{replay_entry}",
            )
            return

        try:
            subprocess.Popen(
                [sys.executable, str(replay_entry)],
                cwd=str(replay_root),
            )
            self.statusBar().showMessage("Launched F1 Race Replay", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Launch Error", f"Failed to launch replay:\n{exc}")

    def closeEvent(self, event) -> None:
        self._stop_live_monitor(silent=True)
        super().closeEvent(event)

    @staticmethod
    def _info_card(title: str, value: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        label_title = QLabel(title)
        label_title.setObjectName("cardTitle")
        label_value = QLabel(value)
        label_value.setWordWrap(True)
        label_value.setObjectName("cardValue")

        layout.addWidget(label_title)
        layout.addWidget(label_value)

        frame._value_label = label_value  # type: ignore[attr-defined]
        return frame

    @staticmethod
    def _set_card_value(frame: QFrame, text: str) -> None:
        label = getattr(frame, "_value_label", None)
        if isinstance(label, QLabel):
            label.setText(text)

    @staticmethod
    def _load_pixmap_from_url(url: str | None) -> QPixmap | None:
        if not url:
            return None

        try:
            response = requests.get(url, timeout=12)
            response.raise_for_status()

            pix = QPixmap()
            ok = pix.loadFromData(response.content)
            return pix if ok else None
        except Exception:
            return None

    @staticmethod
    def _stylesheet() -> str:
        return """
            QWidget {
                background-color: #080808;
                color: #F2F5F7;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            #sidebar {
                background-color: #0F1112;
                border-right: 1px solid #00D2BE;
            }
            #sideTitle {
                color: #00F2FF;
            }
            QPushButton {
                background-color: #131819;
                border: 1px solid #243032;
                border-radius: 8px;
                padding: 10px;
                text-align: left;
            }
            QPushButton:hover {
                border-color: #00D2BE;
            }
            QPushButton:pressed {
                background-color: #0F1415;
            }
            #card {
                background-color: #0F1112;
                border: 1px solid #1F2E31;
                border-radius: 12px;
            }
            #cardTitle {
                color: #00D2BE;
                font-weight: 600;
            }
            #cardValue {
                color: #E7ECEF;
            }
            QTableWidget {
                background-color: #0F1112;
                gridline-color: #1D2A2D;
                border: 1px solid #1F2E31;
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #182022;
                color: #00D2BE;
                border: 0;
                padding: 8px;
                font-weight: 600;
            }
            QListWidget {
                background-color: #0F1112;
                border: 1px solid #1F2E31;
                border-radius: 8px;
            }
            QTextBrowser {
                background-color: #0F1112;
                border: 1px solid #1F2E31;
                border-radius: 8px;
                padding: 8px;
            }
            #imageFrame {
                background-color: #0F1112;
                border: 1px dashed #2B3A3D;
                border-radius: 8px;
                color: #92A3A8;
            }
        """


def _first_or_unknown(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return "?"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("F1 Command Center")

    window = DashboardWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
