from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QEvent
from PySide6.QtGui import QCursor, QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FramelessResizeHelper:
    """Edge/corner resize support for frameless top-level windows."""

    MARGIN = 8
    MIN_WIDTH = 640
    MIN_HEIGHT = 480

    def __init__(self, window: QWidget):
        self._window = window
        self._resize_edge: str | None = None
        self._resize_start_global: QPoint | None = None
        self._resize_start_geom: QRect | None = None
        window.setMouseTracking(True)
        window.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)

    def install(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._window)

    def hit_test(self, pos: QPoint) -> str | None:
        x, y = pos.x(), pos.y()
        w, h = self._window.width(), self._window.height()
        m = self.MARGIN
        on_left = x <= m
        on_right = x >= w - m
        on_top = y <= m
        on_bottom = y >= h - m

        if on_bottom and on_right:
            return "bottom-right"
        if on_bottom and on_left:
            return "bottom-left"
        if on_top and on_right:
            return "top-right"
        if on_top and on_left:
            return "top-left"
        if on_left:
            return "left"
        if on_right:
            return "right"
        if on_top:
            return "top"
        if on_bottom:
            return "bottom"
        return None

    def update_cursor(self, edge: str | None) -> None:
        cursors = {
            "left": Qt.CursorShape.SizeHorCursor,
            "right": Qt.CursorShape.SizeHorCursor,
            "top": Qt.CursorShape.SizeVerCursor,
            "bottom": Qt.CursorShape.SizeVerCursor,
            "top-left": Qt.CursorShape.SizeFDiagCursor,
            "bottom-right": Qt.CursorShape.SizeFDiagCursor,
            "top-right": Qt.CursorShape.SizeBDiagCursor,
            "bottom-left": Qt.CursorShape.SizeBDiagCursor,
        }
        if edge is None:
            self._window.unsetCursor()
        else:
            self._window.setCursor(QCursor(cursors[edge]))

    def begin_resize(self, edge: str, global_pos: QPoint) -> None:
        self._resize_edge = edge
        self._resize_start_global = global_pos
        self._resize_start_geom = self._window.geometry()

    def end_resize(self) -> None:
        self._resize_edge = None
        self._resize_start_global = None
        self._resize_start_geom = None

    def apply_resize(self, global_pos: QPoint) -> None:
        if (
            self._resize_edge is None
            or self._resize_start_global is None
            or self._resize_start_geom is None
        ):
            return

        delta = global_pos - self._resize_start_global
        geom = QRect(self._resize_start_geom)
        min_w, min_h = self.MIN_WIDTH, self.MIN_HEIGHT
        edge = self._resize_edge

        if "left" in edge:
            geom.setLeft(min(geom.left() + delta.x(), geom.right() - min_w))
        if "right" in edge:
            geom.setRight(max(geom.right() + delta.x(), geom.left() + min_w))
        if "top" in edge:
            geom.setTop(min(geom.top() + delta.y(), geom.bottom() - min_h))
        if "bottom" in edge:
            geom.setBottom(max(geom.bottom() + delta.y(), geom.top() + min_h))

        self._window.setGeometry(geom)

    def handle_event_filter(self, watched, event) -> bool:
        if event.type() not in (
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            return False

        if not self._window.isVisible():
            return False

        from PySide6.QtWidgets import QApplication

        modal = QApplication.activeModalWidget()
        if modal is not None and modal is not self._window:
            return False

        global_pos = event.globalPosition().toPoint()
        local_pos = self._window.mapFromGlobal(global_pos)
        if not self._window.rect().contains(local_pos):
            if self._resize_edge is None:
                return False

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            edge = self.hit_test(local_pos)
            if edge is None:
                return False
            self.begin_resize(edge, global_pos)
            return True

        if event.type() == QEvent.Type.MouseMove:
            if self._resize_edge is not None:
                self.apply_resize(global_pos)
                return True
            self.update_cursor(self.hit_test(local_pos))
            return False

        if event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton or self._resize_edge is None:
                return False
            self.end_resize()
            self.update_cursor(self.hit_test(local_pos))
            return True

        return False


class RoundedShell(QWidget):
    """Frameless window shell with clipped rounded corners and drop shadow."""

    def __init__(self, parent=None, radius: int = 12):
        super().__init__(parent)
        self.setObjectName("dialogShell")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._radius = radius

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(shadow)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class PanelFrame(QFrame):
    """Elevated content panel with optional section header."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("panelFrame")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 14)
        self._layout.setSpacing(10)

        self._title_label: QLabel | None = None
        if title:
            self._title_label = QLabel(title)
            self._title_label.setObjectName("sectionLabel")
            self._layout.addWidget(self._title_label)

    def set_content(self, widget: QWidget) -> None:
        self._layout.addWidget(widget, stretch=1)


class TitleBar(QWidget):
    """Draggable custom title bar for frameless windows."""

    def __init__(
        self,
        window: QWidget,
        title: str,
        parent=None,
        trailing: QWidget | None = None,
        subtitle: str = "",
    ):
        super().__init__(parent)
        self._window = window
        self._drag_pos = None
        self.setObjectName("titleBar")
        self.setFixedHeight(52)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(12)

        accent = QFrame()
        accent.setObjectName("titleAccent")
        accent.setFixedSize(4, 22)
        layout.addWidget(accent)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(0)

        label = QLabel(title)
        label.setObjectName("titleLabel")
        title_col.addWidget(label)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("titleSubtitle")
            title_col.addWidget(sub)

        title_widget = QWidget()
        title_widget.setLayout(title_col)
        layout.addWidget(title_widget)
        layout.addStretch()

        if trailing is not None:
            layout.addWidget(trailing)

        close_btn = QPushButton("\u2715")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(34, 34)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(window.close)
        layout.addWidget(close_btn)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
