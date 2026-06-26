import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui_constants import THEMES, Theme, dialog_style, get_theme
from gui_settings import DEFAULT_DOWNLOAD_SETTINGS
from gui_utils import TitleBar
from plot_settings_dialog import GRAPH_SETTINGS_DEFAULTS, PlotSettingsContent


class CollapsibleSection(QWidget):
    """A collapsible section with a header button and content widget."""

    toggled = Signal()  # Emitted when expanded/collapsed

    def __init__(
        self,
        title: str,
        content: QWidget,
        theme: Theme,
        collapsed: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._content = content
        self._theme = theme
        self._title = title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header button
        self._toggle_btn = QPushButton()
        self._toggle_btn.setObjectName("collapsibleToggle")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(not collapsed)
        self._toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self._toggle_btn)

        # Content container
        self._content_container = QWidget()
        self._content_container.setObjectName("collapsibleContent")
        self._content_container.setAttribute(Qt.WA_StyledBackground, True)
        content_layout = QVBoxLayout(self._content_container)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.addWidget(content)
        layout.addWidget(self._content_container)

        self._update_state()

    def _on_toggle(self):
        self._update_state()
        self.toggled.emit()

    def _update_state(self):
        expanded = self._toggle_btn.isChecked()
        self._content_container.setVisible(expanded)
        arrow = "▼" if expanded else "▶"
        self._toggle_btn.setText(f"{arrow} {self._title}")

    def is_expanded(self) -> bool:
        return self._toggle_btn.isChecked()

    def update_theme(self, theme: Theme):
        self._theme = theme


class ChatSettingsDialog(QDialog):
    """Settings dialog for Chat with integrated Plot and Download Settings."""

    theme_changed = Signal(str)

    def __init__(
        self,
        plot_settings: dict | None = None,
        download_settings: dict | None = None,
        parent=None,
        theme: str | Theme | None = None,
    ):
        super().__init__(parent)

        self._theme = get_theme(theme)
        self.plot_settings = dict(plot_settings or GRAPH_SETTINGS_DEFAULTS)
        self.download_settings = dict(download_settings or DEFAULT_DOWNLOAD_SETTINGS)

        self.setStyleSheet(dialog_style(self._theme))
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setWindowTitle("Chat Settings")
        self.setMinimumWidth(360)
        self.setMaximumWidth(480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        shell = QWidget()
        shell.setObjectName("dialogShell")
        shell.setAttribute(Qt.WA_StyledBackground, True)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        shell_layout.addWidget(TitleBar(self, "Settings", shell))

        # Main content area (not scrollable - dialog resizes instead)
        self._content_widget = QWidget()
        self._content_widget.setObjectName("dialogContent")
        self._content_widget.setAttribute(Qt.WA_StyledBackground, True)

        self._layout = QVBoxLayout(self._content_widget)
        self._layout.setContentsMargins(16, 12, 16, 16)
        self._layout.setSpacing(12)

        # Theme selection (always visible)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self._theme_combo = QComboBox()
        theme_names = [t.name for t in THEMES]
        self._theme_combo.addItems(theme_names)
        idx = self._theme_combo.findText(self._theme.name)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self._theme_combo, stretch=1)
        self._layout.addLayout(theme_row)

        # Plot Settings section (collapsed by default)
        self._plot_content = PlotSettingsContent(self.plot_settings, self._theme)
        self._plot_section = CollapsibleSection(
            "Plot Settings",
            self._plot_content,
            self._theme,
            collapsed=True,
        )
        self._plot_section.toggled.connect(self._adjust_dialog_size)
        self._layout.addWidget(self._plot_section)

        # Download Settings section (collapsed by default)
        self._download_content = self._build_download_settings()
        self._download_section = CollapsibleSection(
            "Download Settings",
            self._download_content,
            self._theme,
            collapsed=True,
        )
        self._download_section.toggled.connect(self._adjust_dialog_size)
        self._layout.addWidget(self._download_section)

        # Stretch at bottom to push buttons down when expanded
        self._layout.addStretch()

        shell_layout.addWidget(self._content_widget, stretch=1)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        button_container = QWidget()
        button_container.setObjectName("dialogContent")
        button_container.setAttribute(Qt.WA_StyledBackground, True)
        btn_layout = QHBoxLayout(button_container)
        btn_layout.setContentsMargins(16, 8, 16, 16)
        btn_layout.addStretch()
        btn_layout.addWidget(buttons)
        shell_layout.addWidget(button_container)

        outer.addWidget(shell)

        # Initial size adjustment
        self._adjust_dialog_size()

    def _build_download_settings(self) -> QWidget:
        """Build the download settings content widget."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Format combobox
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        self._format_combo.addItems(["csv", "parquet"])
        saved_format = self.download_settings.get("format", "csv")
        idx = self._format_combo.findText(saved_format)
        if idx >= 0:
            self._format_combo.setCurrentIndex(idx)
        format_row.addWidget(self._format_combo, stretch=1)
        layout.addLayout(format_row)

        # Headers checkbox
        self._headers_cb = QCheckBox("Include headers")
        self._headers_cb.setChecked(self.download_settings.get("headers", True))
        layout.addWidget(self._headers_cb)

        # Directory selection
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Directory:"))

        self._dir_input = QLineEdit()
        current_dir = self.download_settings.get("directory")
        if not current_dir:
            current_dir = str(Path.home())
        self._dir_input.setText(current_dir)
        dir_row.addWidget(self._dir_input, stretch=1)

        browse_btn = QPushButton("…")
        browse_btn.setObjectName("smallBtn")
        browse_btn.clicked.connect(self._browse_directory)
        dir_row.addWidget(browse_btn)

        layout.addLayout(dir_row)
        layout.addStretch()

        return widget

    def _browse_directory(self):
        """Open directory browser dialog."""
        current = self._dir_input.text() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            current,
        )
        if directory:
            self._dir_input.setText(directory)

    def _on_theme_changed(self, theme_name: str):
        self._theme = get_theme(theme_name)
        self.setStyleSheet(dialog_style(self._theme))
        self._plot_content.update_theme(self._theme)
        self._plot_section.update_theme(self._theme)
        self._download_section.update_theme(self._theme)
        self.theme_changed.emit(theme_name)

    def _adjust_dialog_size(self):
        """Adjust dialog size based on expanded/collapsed sections."""
        # Recalculate the size hint for the content
        self._content_widget.adjustSize()
        self.adjustSize()

        # Ensure dialog stays within reasonable bounds
        screen = self.screen().availableGeometry()
        max_height = int(screen.height() * 0.85)
        new_height = min(self.height(), max_height)
        self.setMaximumHeight(max_height)

        # Center on parent if possible
        if self.parent():
            parent_rect = self.parent().geometry()
            x = parent_rect.center().x() - self.width() // 2
            y = max(0, parent_rect.center().y() - new_height // 2)
            self.move(x, y)

    @property
    def ui_theme_name(self) -> str:
        return self._theme.name

    def accept(self):
        """Collect settings from all sections before accepting."""
        # Collect plot settings
        self.plot_settings = self._plot_content.get_settings()

        # Collect download settings
        self.download_settings = {
            "format": self._format_combo.currentText(),
            "headers": self._headers_cb.isChecked(),
            "directory": self._dir_input.text(),
        }

        super().accept()


# if __name__ == "__main__":
#     app = QApplication(sys.argv)
#     dlg = ChatSettingsDialog()
#     dlg.exec()
