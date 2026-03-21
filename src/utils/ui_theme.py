"""Shared command-center theme helpers for root GUI windows."""

from PySide6.QtWidgets import QWidget


def get_command_center_stylesheet() -> str:
    return """
        QWidget {
            background-color: #050608;
            color: #E8EEF0;
            font-family: 'Segoe UI';
            font-size: 13px;
        }
        QMainWindow { background-color: #050608; }

        QLabel#pageTitle {
            color: #16E0D6;
            font-size: 30px;
            font-weight: 800;
        }

        QLabel#sectionTitle {
            color: #16E0D6;
            font-size: 22px;
            font-weight: 700;
        }

        QLabel#cardTitle {
            color: #16E0D6;
            font-size: 14px;
            font-weight: 700;
        }

        QLabel#cardValue {
            color: #E8EEF0;
            font-size: 16px;
            font-weight: 600;
        }

        QPushButton {
            background-color: #1A2A38;
            border: 1px solid #1E3C4A;
            border-radius: 12px;
            color: #E8EEF0;
            padding: 10px 14px;
            font-size: 14px;
            font-weight: 600;
        }
        QPushButton:hover {
            border-color: #16E0D6;
            background-color: #22384A;
        }

        QFrame#card {
            background-color: #1D2B3A;
            border: 1px solid #16E0D6;
            border-radius: 16px;
            padding: 6px;
        }

        QComboBox,
        QLineEdit,
        QTreeWidget,
        QListWidget,
        QTextBrowser,
        QTableWidget {
            background-color: #0D161D;
            border: 1px solid #1E3C4A;
            border-radius: 10px;
            padding: 6px;
            selection-background-color: #16E0D6;
            selection-color: #041116;
        }

        QHeaderView::section {
            background-color: #10212A;
            color: #16E0D6;
            border: none;
            padding: 7px;
            font-weight: 700;
        }

        QStatusBar {
            background-color: #0D161D;
            border-top: 1px solid #1E3C4A;
            color: #A7BBC3;
        }
    """


def apply_command_center_theme(widget: QWidget) -> None:
    widget.setStyleSheet(get_command_center_stylesheet())
