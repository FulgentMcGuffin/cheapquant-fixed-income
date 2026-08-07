"""Batch progress windows: one heatmap tab per issuer (bonds) or future code
(bond futures).

Bare-import module (see ``gui/app.py``'s ``_bootstrap_sys_path`` docstring) —
only importable once ``gui/`` is on ``sys.path``. ``cqfi.*`` package imports
work normally throughout, since only sibling ``gui/`` modules need the path
bootstrap.

``HeatmapCanvas``/``_RowHeader``/``_ColumnHeader``/``HeatmapPane`` are
domain-agnostic (row label + trade date + status, nothing bond- or
future-specific) and are shared by both ``BatchAnalyticsWindow`` (bonds) and
``FutureBatchAnalyticsWindow`` (bond futures) below.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, NamedTuple

from PySide6.QtCore import QRect, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QFontMetrics, QPainter, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from gui_constants import Theme, get_theme, window_style
from gui_settings import load_settings
from gui_utils import FramelessResizeHelper, RoundedShell, TitleBar

from cqfi.batch.engine import BatchEngine
from cqfi.batch.future_engine import FutureBatchEngine
from cqfi.batch.future_planner import FutureBatchPlan, FutureSeriesPlan
from cqfi.batch.models import (
    BatchDone,
    BatchStarted,
    CellDone,
    CellsStarted,
    CellStatus,
    FutureCellDone,
    FutureCellsStarted,
    Stopping,
)
from cqfi.batch.planner import BatchPlan, IssuerPlan
from cqfi.config import AppSettings

_CELL_W = 14
_CELL_H = 14
_ROW_HEADER_W = 130
_COL_HEADER_H = 26
_MIN_DATE_LABEL_GAP_PX = 62

_STATUS_HEX: dict[CellStatus, str] = {
    CellStatus.PENDING: "#E8C547",
    CellStatus.IN_PROGRESS: "#111111",
    CellStatus.SUCCESS: "#3FB950",
    CellStatus.FAILED: "#E5534B",
}


class HoverInfo(NamedTuple):
    row_key: str
    trade_date: date
    status: CellStatus
    detail: str | None


_FLOAT_DISPLAY_DECIMALS = 4
_JSON_FONT_MAX_PT = 9
_JSON_FONT_MIN_PT = 4
_JSON_FONT_MIN_PX = 7  # last-resort pixel size when point sizes still overflow


def _round_floats_for_display(value: Any, ndigits: int = _FLOAT_DISPLAY_DECIMALS) -> Any:
    """Return a copy of *value* with floats rounded for display only."""
    if isinstance(value, float):
        if value != value:  # NaN
            return value
        return round(value, ndigits)
    if isinstance(value, dict):
        return {k: _round_floats_for_display(v, ndigits) for k, v in value.items()}
    if isinstance(value, list):
        return [_round_floats_for_display(v, ndigits) for v in value]
    return value


def _compact_row_lines(payload: Any) -> str | None:
    """One compact JSON object per line (good for ``{"rows":[...]}`` / lists)."""
    rows: list[Any] | None = None
    wrapper_key = None
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
        wrapper_key = "rows"
        extra = {k: v for k, v in payload.items() if k != "rows"}
    elif isinstance(payload, list):
        rows = payload
        extra = {}
    else:
        return None
    if not rows:
        return None
    lines = [
        json.dumps(
            _round_floats_for_display(row), separators=(",", ":"), ensure_ascii=False
        )
        for row in rows
    ]
    body = ",\n".join(lines)
    if wrapper_key is None:
        return "[\n" + body + "\n]"
    # Keep non-row keys (e.g. _warning) on a trailing compact line.
    if extra:
        extras = json.dumps(
            _round_floats_for_display(extra), separators=(",", ":"), ensure_ascii=False
        )
        # extras is "{...}"; splice into {"rows":[...], ...}
        extras_inner = extras[1:-1].strip()
        if extras_inner:
            return '{"' + wrapper_key + '":[\n' + body + "\n]," + extras_inner + "}"
    return '{"' + wrapper_key + '":[\n' + body + "\n]}"


def _detail_display_candidates(detail: str | None) -> list[str]:
    """Return JSON renderings from roomiest to densest (display-only copies)."""
    if not detail:
        return [""]
    try:
        payload = _round_floats_for_display(json.loads(detail))
    except (TypeError, json.JSONDecodeError):
        return [detail]

    candidates = [
        json.dumps(payload, indent=2, ensure_ascii=False),
        json.dumps(payload, indent=1, ensure_ascii=False),
    ]
    row_lines = _compact_row_lines(payload)
    if row_lines is not None:
        candidates.append(row_lines)
    candidates.append(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    # De-dupe while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for text in candidates:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


class FittingJsonView(QPlainTextEdit):
    """Read-only JSON view: compact spacing + smaller fonts until text fits (no scroll)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("chatView")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setPlaceholderText(
            "Hover over a completed (green) or failed (red) cell to see details."
        )
        self.document().setDocumentMargin(1)
        self.setStyleSheet("QPlainTextEdit { padding: 0px; }")
        self._candidates: list[str] = []
        self.setFont(QFont("Consolas", _JSON_FONT_MAX_PT))

    def clear_text(self) -> None:
        self._candidates = []
        self.clear()

    def set_detail(self, detail: str | None) -> None:
        """Show *detail*, shrinking whitespace/font until it fits the viewport."""
        self._candidates = _detail_display_candidates(detail)
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._candidates:
            self._fit()

    def _font_candidates(self) -> list[QFont]:
        fonts: list[QFont] = []
        for size in range(_JSON_FONT_MAX_PT, _JSON_FONT_MIN_PT - 1, -1):
            fonts.append(QFont("Consolas", size))
        for px in range(11, _JSON_FONT_MIN_PX - 1, -1):
            font = QFont("Consolas")
            font.setPixelSize(px)
            fonts.append(font)
        return fonts

    def _text_fits(self, text: str, font: QFont, width: int, height: int) -> bool:
        doc = self.document()
        doc.setDefaultFont(font)
        doc.setDocumentMargin(1)
        doc.setTextWidth(width)
        self.setFont(font)
        self.setPlainText(text)
        return doc.size().height() <= height + 0.5

    def _fit(self) -> None:
        """Choose the roomiest formatting + largest font that fits; never scroll."""
        if not self._candidates or self._candidates == [""]:
            self.clear()
            return
        viewport_h = max(self.viewport().height(), 1)
        viewport_w = max(self.viewport().width(), 1)
        fonts = self._font_candidates()
        for text in self._candidates:
            for font in fonts:
                if self._text_fits(text, font, viewport_w, viewport_h):
                    self.setVerticalScrollBarPolicy(
                        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                    )
                    return
        # Last resort: densest text + smallest font (may clip; still no scrollbar —
        # scrollbars are unusable while the panel is hover-driven).
        self.setFont(fonts[-1])
        self.setPlainText(self._candidates[-1])
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


# ═══════════════════════════════════════════════════════════════════════════
# Domain-agnostic heatmap: row label x trade date x status
# ═══════════════════════════════════════════════════════════════════════════


class HeatmapCanvas(QWidget):
    """Paints a row x trade-date grid; one cell per applicable/inapplicable combination."""

    cell_hovered = Signal(object)  # HoverInfo | None

    def __init__(
        self,
        row_labels: Sequence[str],
        col_dates: Sequence[date],
        applicable: Mapping[date, Sequence[str]],
        theme: Theme,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._rows = tuple(row_labels)
        self._dates = tuple(col_dates)
        self._row_index = {key: i for i, key in enumerate(self._rows)}
        self._col_index = {d: i for i, d in enumerate(self._dates)}
        self._status: dict[tuple[int, int], CellStatus] = {}
        self._detail: dict[tuple[int, int], str] = {}
        self._theme = theme
        self._na_color = QColor(theme.border)
        self._bg_color = QColor(theme.bg)
        self._status_colors = {status: QColor(hex_) for status, hex_ in _STATUS_HEX.items()}

        self.setMouseTracking(True)
        self.setFixedSize(max(self.n_cols(), 1) * _CELL_W, max(self.n_rows(), 1) * _CELL_H)

        for trade_date, row_keys in applicable.items():
            col = self._col_index.get(trade_date)
            if col is None:
                continue
            for row_key in row_keys:
                row = self._row_index.get(row_key)
                if row is not None:
                    self._status[(row, col)] = CellStatus.PENDING

    def n_rows(self) -> int:
        return len(self._rows)

    def n_cols(self) -> int:
        return len(self._dates)

    def row_label(self, row: int) -> str:
        return self._rows[row]

    def date_at(self, col: int) -> date:
        return self._dates[col]

    def status_at(self, row: int, col: int) -> CellStatus:
        return self._status.get((row, col), CellStatus.NOT_APPLICABLE)

    def detail_at(self, row: int, col: int) -> str | None:
        return self._detail.get((row, col))

    def set_status(
        self, row_key: str, trade_date: date, status: CellStatus, detail: str | None = None
    ) -> None:
        row = self._row_index.get(row_key)
        col = self._col_index.get(trade_date)
        if row is None or col is None:
            return
        self._status[(row, col)] = status
        if detail is not None:
            self._detail[(row, col)] = detail
        self.update(QRect(col * _CELL_W, row * _CELL_H, _CELL_W, _CELL_H))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = event.rect()
        painter.fillRect(rect, self._bg_color)

        col_start = max(0, rect.left() // _CELL_W)
        col_end = min(self.n_cols(), rect.right() // _CELL_W + 1)
        row_start = max(0, rect.top() // _CELL_H)
        row_end = min(self.n_rows(), rect.bottom() // _CELL_H + 1)

        for row in range(row_start, row_end):
            for col in range(col_start, col_end):
                status = self._status.get((row, col), CellStatus.NOT_APPLICABLE)
                color = self._status_colors.get(status, self._na_color)
                painter.fillRect(
                    col * _CELL_W + 1, row * _CELL_H + 1, _CELL_W - 1, _CELL_H - 1, color
                )

    def _cell_at(self, x: int, y: int) -> tuple[int, int] | None:
        row, col = y // _CELL_H, x // _CELL_W
        if 0 <= row < self.n_rows() and 0 <= col < self.n_cols():
            return row, col
        return None

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        hit = self._cell_at(int(pos.x()), int(pos.y()))
        if hit is None:
            self.cell_hovered.emit(None)
            QToolTip.hideText()
            return
        row, col = hit
        status = self.status_at(row, col)
        info = HoverInfo(
            row_key=self.row_label(row),
            trade_date=self.date_at(col),
            status=status,
            detail=self.detail_at(row, col),
        )
        self.cell_hovered.emit(info)
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{info.row_key}\n{info.trade_date.isoformat()}\n{status.value}",
            self,
        )

    def leaveEvent(self, event) -> None:
        self.cell_hovered.emit(None)
        QToolTip.hideText()
        super().leaveEvent(event)


class _RowHeader(QWidget):
    """Fixed-width sidebar of row labels; vertical offset synced to the scroll area."""

    def __init__(self, canvas: HeatmapCanvas, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self._canvas = canvas
        self._theme = theme
        self._offset = 0
        self.setFixedWidth(_ROW_HEADER_W)

    def set_offset(self, value: int) -> None:
        self._offset = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._theme.bg))
        painter.setPen(QColor(self._theme.text_muted))
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        row_start = max(0, self._offset // _CELL_H)
        row_end = min(self._canvas.n_rows(), (self._offset + self.height()) // _CELL_H + 1)
        for row in range(row_start, row_end):
            y = row * _CELL_H - self._offset
            text = metrics.elidedText(
                self._canvas.row_label(row), Qt.TextElideMode.ElideRight, _ROW_HEADER_W - 8
            )
            painter.drawText(4, y + _CELL_H - 3, text)


class _ColumnHeader(QWidget):
    """Fixed-height strip of sparse date labels; horizontal offset synced to the scroll area."""

    def __init__(self, canvas: HeatmapCanvas, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self._canvas = canvas
        self._theme = theme
        self._offset = 0
        self.setFixedHeight(_COL_HEADER_H)

    def set_offset(self, value: int) -> None:
        self._offset = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._theme.bg))
        painter.setPen(QColor(self._theme.text_muted))
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)

        n = self._canvas.n_cols()
        if n == 0:
            return
        step = max(1, _MIN_DATE_LABEL_GAP_PX // _CELL_W)
        col_start = max(0, self._offset // _CELL_W)
        col_end = min(n, (self._offset + self.width()) // _CELL_W + 1)
        first_labeled = (col_start // step) * step
        for col in range(first_labeled, col_end, step):
            if col < col_start - step:
                continue
            x = col * _CELL_W - self._offset
            painter.drawLine(x, self.height() - 4, x, self.height())
            painter.drawText(x + 2, self.height() - 8, self._canvas.date_at(col).isoformat())


class HeatmapPane(QWidget):
    """One tab: frozen-header scrollable heatmap."""

    cell_hovered = Signal(object)  # HoverInfo | None

    def __init__(
        self,
        row_labels: Sequence[str],
        col_dates: Sequence[date],
        applicable: Mapping[date, Sequence[str]],
        theme: Theme,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.canvas = HeatmapCanvas(row_labels, col_dates, applicable, theme)
        self.canvas.cell_hovered.connect(self.cell_hovered.emit)
        self._row_header = _RowHeader(self.canvas, theme)
        self._col_header = _ColumnHeader(self.canvas, theme)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self.canvas)
        self._scroll.setWidgetResizable(False)
        self._scroll.horizontalScrollBar().valueChanged.connect(self._col_header.set_offset)
        self._scroll.verticalScrollBar().valueChanged.connect(self._row_header.set_offset)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)
        corner = QWidget()
        corner.setFixedSize(_ROW_HEADER_W, _COL_HEADER_H)
        top_row.addWidget(corner)
        top_row.addWidget(self._col_header, stretch=1)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(0)
        bottom_row.addWidget(self._row_header)
        bottom_row.addWidget(self._scroll, stretch=1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top_row)
        layout.addLayout(bottom_row, stretch=1)

    def set_status(
        self, row_key: str, trade_date: date, status: CellStatus, detail: str | None = None
    ) -> None:
        self.canvas.set_status(row_key, trade_date, status, detail)


def _build_legend(theme: Theme, na_label: str) -> QWidget:
    legend = QWidget()
    row = QHBoxLayout(legend)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(18)
    entries = [
        ("Queued", _STATUS_HEX[CellStatus.PENDING]),
        ("In progress", _STATUS_HEX[CellStatus.IN_PROGRESS]),
        ("Success", _STATUS_HEX[CellStatus.SUCCESS]),
        ("Failed", _STATUS_HEX[CellStatus.FAILED]),
        (na_label, theme.border),
    ]
    for label, color in entries:
        swatch = QLabel()
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(f"background-color: {color}; border-radius: 2px;")
        row.addWidget(swatch)
        text = QLabel(label)
        text.setObjectName("statusLabel")
        row.addWidget(text)
    row.addStretch(1)
    return legend


class _BaseBatchWindow(QMainWindow):
    """Shared chrome for the batch progress windows: header, tabs, progress, stop, info panel.

    Subclasses provide the window title, tab panes, legend "not applicable"
    label, and the worker thread; everything else (frameless shell, stop
    confirmation, hover-to-detail wiring) lives here.
    """

    _WINDOW_TITLE = "cqfi — batch analytics"
    _NOT_APPLICABLE_LABEL = "Not applicable that day"

    def __init__(
        self,
        *,
        total_cells: int,
        args_summary: str,
        panes: dict[str, HeatmapPane],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._panes = panes
        self._thread: QThread | None = None
        self._finished = False
        self._stopping = False

        gui_settings = load_settings()
        self._theme = get_theme(gui_settings.get("ui_theme"))

        self.setWindowTitle(self._WINDOW_TITLE)
        self.resize(1200, 820)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QWidget()
        outer.setObjectName("outerRoot")
        outer.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(outer)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(16, 16, 16, 16)

        shell = RoundedShell()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(
            TitleBar(self, self._WINDOW_TITLE, shell, subtitle="Batch analytics progress")
        )

        content = QWidget()
        content.setObjectName("dialogContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)

        args_label = QLabel(args_summary)
        args_label.setWordWrap(True)
        args_font = args_label.font()
        args_font.setPointSize(11)
        args_font.setBold(True)
        args_label.setFont(args_font)
        layout.addWidget(args_label)

        self._tabs = QTabWidget()
        for tab_label, pane in panes.items():
            pane.cell_hovered.connect(self._on_cell_hovered)
            self._tabs.addTab(pane, tab_label)
        layout.addWidget(self._tabs, stretch=1)

        layout.addWidget(_build_legend(self._theme, self._NOT_APPLICABLE_LABEL))

        progress_row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, max(total_cells, 1))
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m  (%p%)")
        progress_row.addWidget(self._progress, stretch=1)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("actionBtn")
        self._stop_btn.setMinimumWidth(96)
        self._stop_btn.clicked.connect(self._confirm_and_stop)
        progress_row.addWidget(self._stop_btn)
        layout.addLayout(progress_row)

        info_box = QWidget()
        info_box.setFixedHeight(220)
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)
        self._info_header = QLabel(
            "Hover over a completed (green) or failed (red) cell to see details."
        )
        self._info_header.setWordWrap(True)
        header_font = self._info_header.font()
        header_font.setPointSize(9)
        self._info_header.setFont(header_font)
        info_layout.addWidget(self._info_header)
        self._info_panel = FittingJsonView()
        info_layout.addWidget(self._info_panel, stretch=1)
        layout.addWidget(info_box)

        shell_layout.addWidget(content)
        outer_layout.addWidget(shell)

        self.setStyleSheet(window_style(self._theme))

        self._resize_helper = FramelessResizeHelper(self)
        self._resize_helper.install()

    def eventFilter(self, watched, event):
        if self._resize_helper.handle_event_filter(watched, event):
            return True
        return super().eventFilter(watched, event)

    def _make_thread(self) -> QThread:
        """Return the (not-yet-started) worker thread. Subclasses implement."""
        raise NotImplementedError

    def start(self) -> None:
        """Kick off the background engine thread. Call once, after ``show()``."""
        self._thread = self._make_thread()
        self._thread.event_received.connect(self._on_event)
        self._thread.start()

    def _on_event(self, event) -> None:
        raise NotImplementedError

    def _apply_progress_event(self, event) -> None:
        """Shared handling for the events every engine emits directly (not per-cell)."""
        if isinstance(event, BatchStarted):
            self._progress.setRange(0, max(event.total_cells, 1))
            self._progress.setValue(0)
        elif isinstance(event, BatchDone):
            self._finished = True
            self._stop_btn.setEnabled(False)
            self._progress.setFormat(
                ("Stopped: " if event.cancelled else "Done: ") + "%v / %m"
            )
        elif isinstance(event, Stopping):
            # In-flight workers are being torn down; surface the tail so
            # "Stopping…" doesn't look frozen while futures settle.
            if event.remaining:
                self._stop_btn.setText(f"Stopping… ({event.remaining}/{event.total} left)")
            else:
                self._stop_btn.setText("Stopping…")

    def _on_cell_hovered(self, info: HoverInfo | None) -> None:
        # Keep the last SUCCESS/FAILED detail when the pointer leaves the cell —
        # clearing on leave made the panel unreadable (and any scrollbar
        # unusable, since moving toward it left the cell).
        if info is None or info.status not in (CellStatus.SUCCESS, CellStatus.FAILED):
            return
        self._info_header.setText(
            f"{info.row_key}  |  {info.trade_date.isoformat()}  |  {info.status.value}"
        )
        self._info_panel.set_detail(info.detail)

    def _confirm_and_stop(self) -> None:
        if self._finished:
            self.close()
            return
        if self._stopping:
            return
        answer = QMessageBox.question(
            self,
            "Stop batch run?",
            "This cancels queued work and terminates worker processes so CPU "
            "load drops immediately. Results already written stay in "
            "bond_analytics_db; in-progress calculations are abandoned.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._stopping = True
        self._stop_btn.setEnabled(False)
        self._stop_btn.setText("Stopping…")
        if self._thread is not None:
            self._thread.finished.connect(self._on_thread_finished)
            self._thread.stop_event.set()

    def _on_thread_finished(self) -> None:
        if self._stopping:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None and self._thread.isRunning() and not self._finished:
            event.ignore()
            self._confirm_and_stop()
            return
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(5000)
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════
# Bonds
# ═══════════════════════════════════════════════════════════════════════════


def _issuer_applicable(issuer_plan: IssuerPlan) -> dict[date, tuple[str, ...]]:
    return {item.trade_date: tuple(str(b) for b in item.bonds) for item in issuer_plan.work_items}


class BatchWorkerThread(QThread):
    """Runs :class:`BatchEngine` off the GUI thread; forwards events as a Qt signal."""

    event_received = Signal(object)

    def __init__(
        self,
        plan: BatchPlan,
        settings: AppSettings,
        workers: int,
        curve_label: str,
        also_cache: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._plan = plan
        self._settings = settings
        self._workers = workers
        self._curve_label = curve_label
        self._also_cache = also_cache
        self.stop_event = threading.Event()

    def run(self) -> None:
        BatchEngine().run(
            self._plan,
            self._settings,
            workers=self._workers,
            curve_label=self._curve_label,
            on_event=self.event_received.emit,
            stop_event=self.stop_event,
            also_cache=self._also_cache,
        )


class BatchAnalyticsWindow(_BaseBatchWindow):
    """Themed progress window: one heatmap tab per issuer, live-updated."""

    _WINDOW_TITLE = "cqfi — batch bond analytics"
    _NOT_APPLICABLE_LABEL = "Not active that day"

    def __init__(
        self,
        plan: BatchPlan,
        settings: AppSettings,
        *,
        workers: int,
        curve_label: str,
        args_summary: str,
        also_cache: bool = False,
        parent: QWidget | None = None,
    ):
        self._plan = plan
        self._settings = settings
        self._workers = workers
        self._curve_label = curve_label
        self._also_cache = also_cache

        gui_settings = load_settings()
        theme = get_theme(gui_settings.get("ui_theme"))
        panes = {
            issuer_plan.issuer: HeatmapPane(
                [str(b) for b in issuer_plan.bonds],
                issuer_plan.trade_dates,
                _issuer_applicable(issuer_plan),
                theme,
            )
            for issuer_plan in plan.issuer_plans
        }
        super().__init__(
            total_cells=plan.total_cells, args_summary=args_summary, panes=panes, parent=parent
        )

    def _make_thread(self) -> BatchWorkerThread:
        return BatchWorkerThread(
            self._plan, self._settings, self._workers, self._curve_label, self._also_cache
        )

    def _on_event(self, event) -> None:
        if isinstance(event, CellsStarted):
            pane = self._panes.get(event.issuer)
            if pane is not None:
                for bond_key in event.bond_keys:
                    pane.set_status(bond_key, event.trade_date, CellStatus.IN_PROGRESS)
        elif isinstance(event, CellDone):
            pane = self._panes.get(event.issuer)
            if pane is not None:
                pane.set_status(event.bond_key, event.trade_date, event.status, event.detail)
            self._progress.setValue(event.completed)
        else:
            self._apply_progress_event(event)


# ═══════════════════════════════════════════════════════════════════════════
# Bond futures
# ═══════════════════════════════════════════════════════════════════════════


def _future_applicable(series_plan: FutureSeriesPlan) -> dict[date, tuple[str, ...]]:
    return {
        item.trade_date: tuple(str(basket.bond_future) for basket in item.baskets)
        for item in series_plan.work_items
    }


class FutureBatchWorkerThread(QThread):
    """Runs :class:`FutureBatchEngine` off the GUI thread; forwards events as a Qt signal."""

    event_received = Signal(object)

    def __init__(
        self,
        plan: FutureBatchPlan,
        settings: AppSettings,
        workers: int,
        curve_label: str,
        also_cache: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._plan = plan
        self._settings = settings
        self._workers = workers
        self._curve_label = curve_label
        self._also_cache = also_cache
        self.stop_event = threading.Event()

    def run(self) -> None:
        FutureBatchEngine().run(
            self._plan,
            self._settings,
            workers=self._workers,
            curve_label=self._curve_label,
            on_event=self.event_received.emit,
            stop_event=self.stop_event,
            also_cache=self._also_cache,
        )


class FutureBatchAnalyticsWindow(_BaseBatchWindow):
    """Themed progress window: one heatmap tab per future code, live-updated.

    Rows are dated contracts (e.g. ``FGBMH0``, one per delivery month x
    year); a cell is a whole delivery basket, priced as one unit.
    """

    _WINDOW_TITLE = "cqfi — batch bond-future analytics"
    _NOT_APPLICABLE_LABEL = "Already delivered that day"

    def __init__(
        self,
        plan: FutureBatchPlan,
        settings: AppSettings,
        *,
        workers: int,
        curve_label: str,
        args_summary: str,
        also_cache: bool = False,
        parent: QWidget | None = None,
    ):
        self._plan = plan
        self._settings = settings
        self._workers = workers
        self._curve_label = curve_label
        self._also_cache = also_cache

        gui_settings = load_settings()
        theme = get_theme(gui_settings.get("ui_theme"))
        panes = {
            series_plan.future_code: HeatmapPane(
                [str(f) for f in series_plan.contracts],
                series_plan.trade_dates,
                _future_applicable(series_plan),
                theme,
            )
            for series_plan in plan.series_plans
        }
        super().__init__(
            total_cells=plan.total_cells, args_summary=args_summary, panes=panes, parent=parent
        )

    def _make_thread(self) -> FutureBatchWorkerThread:
        return FutureBatchWorkerThread(
            self._plan, self._settings, self._workers, self._curve_label, self._also_cache
        )

    def _on_event(self, event) -> None:
        # FutureCellsStarted/FutureCellDone are grouped by (issuer, trade_date),
        # not by future code — a single event can span several tabs (e.g.
        # FGBX and FGBM, which share the DEU issuer). Each pane's canvas
        # silently ignores contract labels it doesn't contain as a row, so
        # broadcasting to every tab is both correct and simple.
        if isinstance(event, FutureCellsStarted):
            for contract_label in event.contracts:
                for pane in self._panes.values():
                    pane.set_status(contract_label, event.trade_date, CellStatus.IN_PROGRESS)
        elif isinstance(event, FutureCellDone):
            for pane in self._panes.values():
                pane.set_status(event.contract, event.trade_date, event.status, event.detail)
            self._progress.setValue(event.completed)
        else:
            self._apply_progress_event(event)
