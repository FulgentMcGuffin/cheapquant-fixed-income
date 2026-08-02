import sys

from PySide6.QtCore import Qt, QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui_constants import Theme, dialog_style, get_theme
from gui_utils import TitleBar

GRAPH_SETTINGS_DEFAULTS = {
    "geoms": "LP",
    "alpha": 1.0,
    "theme_str": "538",
    "y_scale": "linear",
    "pos_legend": "bottom",
    "color": True,
    "size": False,
    "shape": False,
    "group": False,
    "flip_coord": False,
}

PLOT_THEME_CHOICES = {
    "538",
    "dark",
    "light",
    "matplotlib",
    "minimal",
    "classic",
    "bw",
    "dark",
    "light",
    "gray",
    "matplotlib",
    "seaborn",
    "tufte",
    "xkcd",
    "linedraw",
}

PLOT_Y_SCALE_CHOICES = {
    "linear",
    "log",
    "log10",
    "log2",
    "sqrt",
    "reverse",
    "symlog",
}

PLOT_POS_LEGEND_CHOICES = {
    "inside",
    "left",
    "right",
    "top",
    "bottom",
    "none",
}

GEOM_CHOICES = (
    ("line", "L"),
    ("point", "P"),
    ("bar", "B"),
    ("smooth", "S"),
    ("rug", "R"),
    ("jitter", "J"),
    ("tile", "T"),
    ("violin", "V"),
    ("histogram", "H"),
    ("density", "D"),
    ("boxplot", "X"),
    ("facet_grid", "F"),
    ("facet_wrap", "W"),
)


def geoms_string_from_letters(letters: str) -> str:
    """Build a canonical geoms string from selected letters."""
    selected = {ch.upper() for ch in letters}
    return "".join(letter for _label, letter in GEOM_CHOICES if letter in selected)


class GeomsSelectionDialog(QDialog):
    """Small dialog for selecting plot geoms via checkboxes."""

    def __init__(self, geoms: str, parent=None, theme: str | Theme | None = None):
        super().__init__(parent)

        self._theme = get_theme(theme)
        self.setStyleSheet(dialog_style(self._theme))
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setWindowTitle("Select Geoms")
        self.setMinimumWidth(240)
        self.selected_geoms = geoms_string_from_letters(geoms)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        shell = QWidget()
        shell.setObjectName("dialogShell")
        shell.setAttribute(Qt.WA_StyledBackground, True)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        shell_layout.addWidget(TitleBar(self, "Select Geoms", shell))

        content = QWidget()
        content.setObjectName("dialogContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)

        selected = {ch.upper() for ch in geoms}
        self._geom_checkboxes: dict[str, QCheckBox] = {}
        for label, letter in GEOM_CHOICES:
            cb = QCheckBox(label)
            cb.setChecked(letter in selected)
            layout.addWidget(cb)
            self._geom_checkboxes[letter] = cb

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        shell_layout.addWidget(content)
        outer.addWidget(shell)

    def _on_accept(self) -> None:
        letters = [
            letter
            for _label, letter in GEOM_CHOICES
            if self._geom_checkboxes[letter].isChecked()
        ]
        self.selected_geoms = "".join(letters)
        self.accept()


class PlotSettingsContent(QWidget):
    """Content widget for plot settings, can be embedded in other dialogs."""

    def __init__(
        self,
        current_settings: dict,
        theme: str | Theme | None = None,
        parent=None,
    ):
        super().__init__(parent)

        self._theme = get_theme(theme)
        self._current_settings = dict(current_settings or GRAPH_SETTINGS_DEFAULTS)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # geoms (hidden validated store + Select button)
        self.geoms_input = QLineEdit(
            self._current_settings.get("geoms", GRAPH_SETTINGS_DEFAULTS["geoms"])
        )
        self.geoms_input.setValidator(
            QRegularExpressionValidator(QRegularExpression("[A-Za-z]*"))
        )
        self.geoms_input.hide()

        geoms_row = QHBoxLayout()
        self._geoms_label = QLabel(self.geoms_input.text())
        self._geoms_label.setObjectName("geomsValueLabel")
        geoms_row.addWidget(self._geoms_label, stretch=1)
        geoms_select_btn = QPushButton("Select")
        geoms_select_btn.clicked.connect(self._open_geoms_selector)
        geoms_row.addWidget(geoms_select_btn)
        form.addRow("Geoms:", geoms_row)

        # alpha
        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setRange(0.0, 1.0)
        self.alpha_spin.setSingleStep(0.05)
        self.alpha_spin.setDecimals(2)
        self.alpha_spin.setValue(
            self._current_settings.get("alpha", GRAPH_SETTINGS_DEFAULTS["alpha"])
        )
        form.addRow("Alpha:", self.alpha_spin)

        # boolean checkboxes
        self._checkboxes = {}
        for key in ("color", "size", "shape", "group", "flip_coord"):
            cb = QCheckBox()
            cb.setChecked(
                bool(self._current_settings.get(key, GRAPH_SETTINGS_DEFAULTS[key]))
            )
            label = key.replace("_", " ").title()
            form.addRow(f"{label}:", cb)
            self._checkboxes[key] = cb

        # theme_str
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(PLOT_THEME_CHOICES)
        saved_theme = self._current_settings.get(
            "theme_str", GRAPH_SETTINGS_DEFAULTS["theme_str"]
        )
        idx = self.theme_combo.findText(saved_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        form.addRow("Theme:", self.theme_combo)

        # y_scale
        self.y_scale_combo = QComboBox()
        self.y_scale_combo.addItems(PLOT_Y_SCALE_CHOICES)
        saved_yscale = self._current_settings.get(
            "y_scale", GRAPH_SETTINGS_DEFAULTS["y_scale"]
        )
        idx = self.y_scale_combo.findText(saved_yscale)
        if idx >= 0:
            self.y_scale_combo.setCurrentIndex(idx)
        form.addRow("Y Scale:", self.y_scale_combo)

        # pos_legend
        self.pos_legend_combo = QComboBox()
        self.pos_legend_combo.addItems(PLOT_POS_LEGEND_CHOICES)
        saved_pos = self._current_settings.get(
            "pos_legend", GRAPH_SETTINGS_DEFAULTS["pos_legend"]
        )
        idx = self.pos_legend_combo.findText(saved_pos)
        if idx >= 0:
            self.pos_legend_combo.setCurrentIndex(idx)
        form.addRow("Legend Position:", self.pos_legend_combo)

        layout.addLayout(form)
        layout.addStretch()

    def _update_geoms_display(self, geoms: str) -> None:
        self.geoms_input.setText(geoms)
        self._geoms_label.setText(geoms)

    def _open_geoms_selector(self) -> None:
        dlg = GeomsSelectionDialog(
            self.geoms_input.text(),
            parent=self.window(),
            theme=self._theme,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._update_geoms_display(dlg.selected_geoms)

    def get_settings(self) -> dict:
        """Return the current settings as a dictionary."""
        settings = {
            "geoms": self.geoms_input.text(),
            "alpha": self.alpha_spin.value(),
            "theme_str": self.theme_combo.currentText(),
            "y_scale": self.y_scale_combo.currentText(),
            "pos_legend": self.pos_legend_combo.currentText(),
        }
        for key, cb in self._checkboxes.items():
            settings[key] = cb.isChecked()
        return settings

    def update_theme(self, theme: str | Theme):
        """Update the theme for this content widget."""
        self._theme = get_theme(theme)


class PlotSettingsDialog(QDialog):
    """Dialog for configuring graph rendering options."""

    def __init__(self, current_settings: dict, parent=None, theme: str | Theme | None = None):
        super().__init__(parent)

        self._theme = get_theme(theme)
        self.setStyleSheet(dialog_style(self._theme))
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setWindowTitle("Plot Settings")
        self.setMinimumWidth(285)
        self.result_settings = dict(current_settings)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        shell = QWidget()
        shell.setObjectName("dialogShell")
        shell.setAttribute(Qt.WA_StyledBackground, True)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        shell_layout.addWidget(TitleBar(self, "Plot Settings", shell))

        content = QWidget()
        content.setObjectName("dialogContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)

        self._plot_content = PlotSettingsContent(current_settings, self._theme)
        layout.addWidget(self._plot_content)

        # OK / Cancel
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        shell_layout.addWidget(content)
        outer.addWidget(shell)

    def _on_accept(self):
        self.result_settings = self._plot_content.get_settings()
        self.accept()


# if __name__ == "__main__":
#     app = QApplication(sys.argv)
#     dialog = PlotSettingsDialog(GRAPH_SETTINGS_DEFAULTS)
#     dialog.exec()
#     print(dialog.result_settings)
