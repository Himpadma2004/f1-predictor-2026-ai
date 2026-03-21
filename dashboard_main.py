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
from datetime import datetime, date
from pathlib import Path
from typing import Any

import requests
from PySide6.QtCore import QObject, Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
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

        title = QLabel("Live Races (Streaming Staging)")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        layout.addWidget(title)

        body = QLabel(
            "This module is your launch pad for live match workflows.\n"
            "Use Replay from the sidebar for the current Tom Shaw desktop replay.\n"
            "You can expand this page next with OpenF1 live driver/session selectors."
        )
        body.setWordWrap(True)
        layout.addWidget(body)

        launch_replay_from_live = QPushButton("Launch Replay Now")
        launch_replay_from_live.clicked.connect(self.launch_replay)
        layout.addWidget(launch_replay_from_live)

        layout.addStretch(1)
        return page

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
