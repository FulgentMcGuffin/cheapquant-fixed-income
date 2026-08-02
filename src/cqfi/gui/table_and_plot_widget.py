import numpy as np
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTableWidget, QToolTip, QApplication, QTableWidgetItem, QSizePolicy
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QResizeEvent

from gui_constants import get_plot_bg
from PySide6.QtGui import QKeySequence

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class TableWidget(QTableWidget):
    """Custom table widget with tooltip support and row selection"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.requestor = None

        # Enable mouse tracking for hover tooltips
        self.setMouseTracking(True)

        # Set selection behavior - allow extended selection for copying ranges
        self.setSelectionBehavior(QTableWidget.SelectItems)
        self.setSelectionMode(QTableWidget.ExtendedSelection)

        # Enable sorting
        self.setSortingEnabled(True)

        self.verticalHeader().setDefaultSectionSize(32)
        self.horizontalHeader().setFixedHeight(32)
        self.horizontalHeader().setMouseTracking(True)
        self.horizontalHeader().viewport().setMouseTracking(True)
        self.horizontalHeader().viewport().installEventFilter(self)

    def mouseMoveEvent(self, event):
        """Show tooltip with cell content when hovering over cells"""
        # Get the item at the mouse position (using position() for Qt6 compatibility)
        item = self.itemAt(event.position().toPoint())

        if item is not None:
            # Get the cell content
            cell_text = item.text()
            if cell_text:
                # Show tooltip with the cell content
                QToolTip.showText(event.globalPosition().toPoint(), cell_text, self)
            else:
                QToolTip.hideText()
        else:
            # Hide tooltip if not over an item
            QToolTip.hideText()

        # Call the parent implementation
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        """Handle key press events - Ctrl+C to copy selected range to clipboard"""
        if event.matches(QKeySequence.Copy):
            self.copy_selection_to_clipboard()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Show header tooltip with the hovered column name."""
        if obj is self.horizontalHeader().viewport():
            if event.type() == QEvent.MouseMove:
                col = self.horizontalHeader().logicalIndexAt(event.pos())
                if col >= 0:
                    header_item = self.horizontalHeaderItem(col)
                    header_text = header_item.text() if header_item else ""
                    if header_text:
                        QToolTip.showText(
                            self.horizontalHeader().viewport().mapToGlobal(event.pos()),
                            header_text,
                            self,
                        )
                    else:
                        QToolTip.hideText()
                else:
                    QToolTip.hideText()
                return False
            if event.type() == QEvent.Leave:
                QToolTip.hideText()
                return False
        return super().eventFilter(obj, event)

    def copy_selection_to_clipboard(self):
        """Copy selected cells to clipboard in TSV format (tab-separated values)"""
        selected_ranges = self.selectedRanges()
        if not selected_ranges:
            return

        # Get the bounding rectangle of all selected cells
        min_row = min(r.topRow() for r in selected_ranges)
        max_row = max(r.bottomRow() for r in selected_ranges)
        min_col = min(r.leftColumn() for r in selected_ranges)
        max_col = max(r.rightColumn() for r in selected_ranges)

        # Build a set of selected cells for quick lookup
        selected_cells = set()
        for r in selected_ranges:
            for row in range(r.topRow(), r.bottomRow() + 1):
                for col in range(r.leftColumn(), r.rightColumn() + 1):
                    selected_cells.add((row, col))

        # Build the clipboard text
        rows_text = []
        for row in range(min_row, max_row + 1):
            row_data = []
            for col in range(min_col, max_col + 1):
                if (row, col) in selected_cells:
                    item = self.item(row, col)
                    cell_text = item.text() if item else ""
                else:
                    cell_text = ""
                row_data.append(cell_text)
            rows_text.append("\t".join(row_data))

        clipboard_text = "\n".join(rows_text)

        # Copy to clipboard
        clipboard = QApplication.clipboard()
        clipboard.setText(clipboard_text)


class PlotWidget(QWidget):
    """Custom plot widget with line plot, tooltips, and title"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.figure = Figure(figsize=(8, 6))
        self.figure.patch.set_facecolor(get_plot_bg())
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor(get_plot_bg())

        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.canvas)
        self.setLayout(layout)
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Initialize hover functionality
        self.lines = []
        self.annotation = None
        self._annotation_ax = None

        # Initialize highlight functionality
        self.highlight_markers = []  # Changed to list to support multiple lines
        self.highlighted_index = None

        # Initialize vertical date slider functionality
        self._x_slider_enabled = False
        self._x_slider_dragging = False
        self._x_slider_values = []
        self._x_slider_index = None
        self._x_slider_line = None
        self._x_slider_handle = None
        self._x_slider_handle_text = None
        self._x_slider_date_callback = None

        # Reference to parent requestor for table selection
        self.requestor = None

        # Track whether current figure is from an external source (e.g. plotnine)
        self._external_figure = False
        self._flip_coord = False

        # Track whether a plot has been rendered
        self._has_plot = False
        self._axis_tick_cache: dict[int, dict[str, dict]] = {}
        self._last_responsive_size: tuple[int, int] | None = None
        self._x_numeric_to_display: dict[float, str] = {}
        self._axis_x_lookups: dict[int, dict[float, str]] = {}

        # Connect mouse events for tooltip and click
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_motion)
        self.canvas.mpl_connect('button_press_event', self.on_mouse_click)
        self.canvas.mpl_connect('button_release_event', self.on_mouse_release)
        # Re-apply tight_layout on resize so edges stay visible
        self.canvas.mpl_connect('resize_event', self._on_resize)

        self.canvas.hide()

    @property
    def has_plot(self) -> bool:
        return self._has_plot

    def clear_plot(self):
        """Hide the plot area until data is rendered."""
        self._has_plot = False
        self._external_figure = False
        self._flip_coord = False
        self._axis_tick_cache = {}
        self._last_responsive_size = None
        self._x_numeric_to_display = {}
        self._axis_x_lookups = {}
        self.lines = []
        self.highlight_markers = []
        self.highlighted_index = None
        self.annotation = None
        self._annotation_ax = None
        self.canvas.hide()

    def _main_axes(self):
        if self.figure is None:
            return []
        return [ax for ax in self.figure.get_axes() if self._is_main_axes(ax)]

    def _facet_axis_label(self, ax, ax_idx: int, series_names: list[str]) -> str:
        title = (ax.get_title() or "").strip()
        if title:
            for prefix in ("Series:", "series:"):
                if title.lower().startswith(prefix.lower()):
                    return title[len(prefix):].strip()
            return title
        sorted_names = sorted(series_names) if len(series_names) > 1 else list(series_names)
        if ax_idx < len(sorted_names):
            return sorted_names[ax_idx]
        return f"series_{ax_idx}"

    def _build_color_to_label(self) -> dict:
        from matplotlib.lines import Line2D
        from matplotlib.collections import PathCollection
        from matplotlib.colors import to_rgba

        def _color_key(color):
            return tuple(round(v, 4) for v in to_rgba(color))

        color_to_label = {}
        all_legends = []
        if self.ax is not None:
            ax_legend = self.ax.get_legend()
            if ax_legend is not None:
                all_legends.append(ax_legend)
        if hasattr(self.figure, "legends"):
            all_legends.extend(self.figure.legends)

        for leg in all_legends:
            handles = getattr(leg, "legend_handles", getattr(leg, "legendHandles", []))
            for handle, text in zip(handles, leg.get_texts()):
                try:
                    if isinstance(handle, Line2D):
                        key = _color_key(handle.get_color())
                    elif isinstance(handle, PathCollection):
                        fc = handle.get_facecolors()
                        key = _color_key(fc[0]) if len(fc) > 0 else None
                    else:
                        key = None
                    if key is not None:
                        color_to_label[key] = text.get_text()
                except Exception:
                    pass
        return color_to_label

    def _register_axis_x_values(self, ax, x_data) -> None:
        """Map x coordinates within a facet panel to display labels."""
        import matplotlib.dates as mdates
        from datetime import date as _date, datetime as _datetime

        from plotnine_wrapper import format_display_date, normalize_x_value

        lookup = self._axis_x_lookups.setdefault(id(ax), {})
        for idx, x in enumerate(x_data):
            normalized = normalize_x_value(x)
            if isinstance(normalized, (_datetime, _date)):
                label = format_display_date(normalized)
            else:
                label = format_display_date(x)

            if not label:
                label = str(x)

            lookup[float(idx)] = label
            try:
                if isinstance(normalized, (_datetime, _date)):
                    x_num = float(mdates.date2num(normalized))
                elif isinstance(x, (_datetime, _date)):
                    x_num = float(mdates.date2num(x))
                elif hasattr(x, "to_pydatetime"):
                    x_num = float(mdates.date2num(x.to_pydatetime()))
                else:
                    x_num = float(x)
                lookup[x_num] = label
                lookup[round(x_num, 6)] = label
            except Exception:
                pass

    def _extract_tooltip_lines(self, series_names: list[str] | None) -> None:
        """Extract line/point artists from every facet panel for tooltips."""
        from matplotlib.lines import Line2D
        from matplotlib.collections import PathCollection
        from matplotlib.colors import to_rgba

        self.lines = []
        self._axis_x_lookups = {}
        main_axes = self._main_axes()
        if not main_axes:
            return

        names = series_names or []
        color_to_label = self._build_color_to_label()
        sorted_names = sorted(names) if len(names) > 1 else list(names)
        n = len(sorted_names)
        use_facet_labels = len(main_axes) > 1

        def _color_key(color):
            return tuple(round(v, 4) for v in to_rgba(color))

        def _append_line(artist, xd, yd, lbl, ax):
            self._register_axis_x_values(ax, xd)
            if use_facet_labels:
                lbl = self._facet_axis_label(ax, main_axes.index(ax), names)
            self.lines.append((artist, xd, yd, lbl, ax))

        for ax_idx, ax in enumerate(main_axes):
            facet_label = self._facet_axis_label(ax, ax_idx, names)
            ax_lines = []

            if color_to_label and not use_facet_labels:
                seen = set()
                for artist in ax.get_children():
                    lbl = xd = yd = None
                    if isinstance(artist, Line2D):
                        xd = list(artist.get_xdata())
                        yd = list(artist.get_ydata())
                        if len(xd) <= 1:
                            continue
                        try:
                            lbl = color_to_label.get(_color_key(artist.get_color()))
                        except Exception:
                            pass
                    elif isinstance(artist, PathCollection):
                        offsets = artist.get_offsets()
                        if len(offsets) == 0:
                            continue
                        xd = list(offsets[:, 0])
                        yd = list(offsets[:, 1])
                        try:
                            fc = artist.get_facecolors()
                            if len(fc) > 0:
                                lbl = color_to_label.get(_color_key(fc[0]))
                        except Exception:
                            pass
                    if lbl and lbl not in seen:
                        seen.add(lbl)
                        ax_lines.append((artist, xd, yd, lbl, ax))

            if not ax_lines:
                line_artists = []
                point_artists = []
                for artist in ax.get_children():
                    if isinstance(artist, Line2D):
                        xd = list(artist.get_xdata())
                        yd = list(artist.get_ydata())
                        if len(xd) > 2:
                            line_artists.append((artist, xd, yd))
                    elif isinstance(artist, PathCollection):
                        offsets = artist.get_offsets()
                        if len(offsets) > 0:
                            xd = list(offsets[:, 0])
                            yd = list(offsets[:, 1])
                            point_artists.append((artist, xd, yd))

                for i, (artist, xd, yd) in enumerate(line_artists[:n] if n else line_artists):
                    lbl = facet_label if use_facet_labels else (
                        sorted_names[i] if i < n else (artist.get_label() or f"series_{i}")
                    )
                    ax_lines.append((artist, xd, yd, lbl, ax))
                for i, (artist, xd, yd) in enumerate(point_artists[:n] if n else point_artists):
                    lbl = facet_label if use_facet_labels else (
                        sorted_names[i] if i < n else (artist.get_label() or f"series_{i}")
                    )
                    ax_lines.append((artist, xd, yd, lbl, ax))

            for artist, xd, yd, lbl, line_ax in ax_lines:
                _append_line(artist, xd, yd, lbl, line_ax)

    def _hide_tooltip(self) -> None:
        if self.annotation is not None:
            self.annotation.set_visible(False)
            self.canvas.draw_idle()

    def _show_tooltip(self, ax, x_num: float, y_val: float, x_orig, label: str) -> None:
        point_px = ax.transData.transform([x_num, y_val])
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        cx, cy = (xlim[0] + xlim[1]) / 2, (ylim[0] + ylim[1]) / 2
        center_px = ax.transData.transform([cx, cy])
        dx = center_px[0] - point_px[0]
        dy = center_px[1] - point_px[1]
        length = np.sqrt(dx ** 2 + dy ** 2)
        arrow_len = 40.0
        if length > 0:
            offset = [dx / length * arrow_len, dy / length * arrow_len]
        else:
            offset = [arrow_len, arrow_len]

        ha = "left" if offset[0] >= 0 else "right"
        va = "bottom" if offset[1] >= 0 else "top"

        if self.annotation is not None and self._annotation_ax is not ax:
            self.annotation.remove()
            self.annotation = None
            self._annotation_ax = None

        if self.annotation is None:
            self.annotation = ax.annotate(
                "",
                xy=(x_num, y_val),
                xytext=offset,
                textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.4", fc="#ffffcc", ec="#999999", alpha=0.95),
                arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0"),
                fontsize=6,
                ha=ha,
                va=va,
            )
            self._annotation_ax = ax
        else:
            self.annotation.xyann = offset
            self.annotation.set_ha(ha)
            self.annotation.set_va(va)
            self.annotation.xy = (x_num, y_val)

        x_str = self._format_x_as_date(x_orig if x_orig is not None else x_num, ax=ax)
        y_str = f"{y_val:.4f}"
        self.annotation.set_text(f"{label}\n{x_str} | {y_str}")
        self.annotation.set_visible(True)
        self.canvas.draw_idle()

    def _layout_params_for_span(self, span_px: int) -> dict:
        """Map available axis span (px) to tick density, font sizes, and label angle."""
        span_px = max(int(span_px), 80)
        max_ticks = max(3, min(int(span_px / 65), 24))

        if span_px < 220:
            rotation, ha = 90, "center"
            tick_fs, label_fs = 6, 8
        elif span_px < 320:
            rotation, ha = 60, "right"
            tick_fs, label_fs = 7, 9
        elif span_px < 450:
            rotation, ha = 45, "right"
            tick_fs, label_fs = 8, 10
        elif span_px < 600:
            rotation, ha = 30, "right"
            tick_fs, label_fs = 9, 11
        else:
            rotation, ha = 0, "center"
            tick_fs, label_fs = 10, 12

        return {
            "max_ticks": max_ticks,
            "rotation": rotation,
            "ha": ha,
            "tick_fs": tick_fs,
            "label_fs": label_fs,
        }

    def _is_main_axes(self, ax) -> bool:
        pos = ax.get_position()
        return pos.width > 0.12 and pos.height > 0.12

    def _capture_axis_tick_cache(self) -> None:
        """Snapshot plotnine tick positions/formatters for responsive re-sampling."""
        self._axis_tick_cache = {}
        if self.figure is None:
            return

        self.canvas.draw()
        for ax in self.figure.get_axes():
            if not self._is_main_axes(ax):
                continue
            self._axis_tick_cache[id(ax)] = {
                "x": {
                    "ticks": list(ax.get_xticks()),
                    "labels": [label.get_text() for label in ax.get_xticklabels()],
                    "formatter": ax.xaxis.get_major_formatter(),
                },
                "y": {
                    "ticks": list(ax.get_yticks()),
                    "labels": [label.get_text() for label in ax.get_yticklabels()],
                    "formatter": ax.yaxis.get_major_formatter(),
                },
            }

    def _apply_axis_ticks_from_cache(self, ax, axis_name: str, max_ticks: int) -> None:
        """Subsample or restore ticks from the original plotnine snapshot."""
        if max_ticks < 1:
            return

        cached = self._axis_tick_cache.get(id(ax), {}).get(axis_name)
        if not cached:
            return

        ticks = cached["ticks"]
        labels = cached["labels"]
        formatter = cached.get("formatter")
        if not ticks:
            return

        if len(labels) < len(ticks):
            labels = labels + [""] * (len(ticks) - len(labels))

        if len(ticks) <= max_ticks:
            selected_ticks = ticks
            selected_labels = labels
        else:
            indices = np.unique(
                np.round(np.linspace(0, len(ticks) - 1, max_ticks)).astype(int)
            )
            selected_ticks = [ticks[i] for i in indices]
            selected_labels = [labels[i] for i in indices]

        axis = ax.xaxis if axis_name == "x" else ax.yaxis
        set_ticks = ax.set_xticks if axis_name == "x" else ax.set_yticks
        set_ticklabels = ax.set_xticklabels if axis_name == "x" else ax.set_yticklabels

        set_ticks(selected_ticks)
        if formatter is not None:
            axis.set_major_formatter(formatter)
        elif any(selected_labels):
            set_ticklabels(selected_labels)

    def _tick_pad_for_rotation(self, rotation: float) -> float:
        if rotation >= 90:
            return 8.0
        if rotation >= 60:
            return 6.0
        if rotation >= 45:
            return 5.0
        if rotation >= 30:
            return 4.0
        return 2.0

    def _style_axis_tick_labels(self, ax, axis_name: str, params: dict, *, vertical: bool = False) -> None:
        tick_labels = ax.get_xticklabels() if axis_name == "x" else ax.get_yticklabels()
        if not tick_labels:
            return
        rotation = 0 if vertical else params["rotation"]
        ha = "right" if vertical else params["ha"]
        for label in tick_labels:
            label.set_visible(True)
            label.set_rotation(rotation)
            label.set_ha(ha)
            label.set_fontsize(params["tick_fs"])

    def _apply_responsive_axes(self, width_px: int | None = None, height_px: int | None = None) -> None:
        """Re-tune axis ticks and label sizes for the current canvas dimensions."""
        if self.figure is None or not self._has_plot:
            return

        width_px = width_px if width_px is not None else self.canvas.width()
        height_px = height_px if height_px is not None else self.canvas.height()
        if width_px <= 0 or height_px <= 0:
            return

        if not self._axis_tick_cache:
            self._capture_axis_tick_cache()

        main_axes = [ax for ax in self.figure.get_axes() if self._is_main_axes(ax)]
        if not main_axes:
            return

        x_params = self._layout_params_for_span(width_px)
        y_params = self._layout_params_for_span(height_px)

        for ax in main_axes:
            self._apply_axis_ticks_from_cache(ax, "x", x_params["max_ticks"])
            if self._flip_coord:
                self._apply_axis_ticks_from_cache(ax, "y", y_params["max_ticks"])
            ax.xaxis.set_visible(True)
            ax.spines["bottom"].set_visible(True)

        self.canvas.draw()

        for ax in main_axes:
            self._style_axis_tick_labels(ax, "x", x_params)
            if self._flip_coord:
                self._style_axis_tick_labels(ax, "y", y_params, vertical=True)
            else:
                for label in ax.get_yticklabels():
                    label.set_fontsize(x_params["tick_fs"])

            ax.xaxis.label.set_fontsize(x_params["label_fs"])
            ax.yaxis.label.set_fontsize(x_params["label_fs"])
            ax.title.set_fontsize(x_params["label_fs"] + 1)
            ax.tick_params(axis="x", pad=self._tick_pad_for_rotation(x_params["rotation"]))

        self._last_responsive_size = (width_px, height_px)
        self.canvas.draw_idle()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not self._has_plot:
            return
        size = (self.canvas.width(), self.canvas.height())
        if size == self._last_responsive_size:
            return
        self._apply_responsive_axes(size[0], size[1])

    def _on_resize(self, event):
        if not self._has_plot:
            return
        width_px = int(event.width) if event is not None else self.canvas.width()
        height_px = int(event.height) if event is not None else self.canvas.height()
        if (width_px, height_px) == self._last_responsive_size:
            return
        self._apply_responsive_axes(width_px, height_px)

    def display_figure(self, fig, series_names=None, flip_coord=False, x_values=None):
        """Replace the current canvas with an external matplotlib Figure (e.g. from plotnine).

        Args:
            fig: A matplotlib Figure instance.
            series_names: optional list of column/series names for tooltip labels.
            flip_coord: True when the plot uses coord_flip (data-x on the y-axis).
            x_values: original x-column values for tooltip date labels.
        """
        self._external_figure = True
        self._flip_coord = bool(flip_coord)
        if x_values:
            from plotnine_wrapper import build_x_numeric_lookup

            self._x_numeric_to_display = build_x_numeric_lookup(x_values)
        else:
            self._x_numeric_to_display = {}

        # Tear down old canvas
        self.layout().removeWidget(self.canvas)
        self.canvas.setParent(None)
        self.canvas.close()

        # Adopt the new figure
        self.figure = fig
        self.canvas = FigureCanvas(self.figure)
        self.layout().addWidget(self.canvas)

        # Re-wire events on the new canvas
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_motion)
        self.canvas.mpl_connect('button_press_event', self.on_mouse_click)
        self.canvas.mpl_connect('button_release_event', self.on_mouse_release)
        self.canvas.mpl_connect('resize_event', self._on_resize)

        # Grab the first axes (plotnine always creates one)
        axes = self.figure.get_axes()
        self.ax = axes[0] if axes else None

        # Reset interactive state
        self.highlight_markers = []
        self.highlighted_index = None
        self.annotation = None
        self._annotation_ax = None

        self._extract_tooltip_lines(series_names if series_names else [])

        self._has_plot = True
        self._last_responsive_size = None
        self.canvas.show()
        QApplication.processEvents()
        self._capture_axis_tick_cache()
        self._apply_responsive_axes()

    def _format_x_as_date(self, x_value, ax=None):
        """Format numeric/datetime x value as YYYY-mm-dd."""
        import matplotlib.dates as mdates
        from datetime import date as _date, datetime as _datetime

        from plotnine_wrapper import (
            _matplotlib_num_to_date_str,
            format_display_date,
            lookup_x_display,
            normalize_x_value,
        )

        try:
            normalized = normalize_x_value(x_value)
            if isinstance(normalized, (_datetime, _date)):
                return format_display_date(normalized)
            if hasattr(x_value, "to_pydatetime"):
                return format_display_date(x_value.to_pydatetime())
            if hasattr(x_value, "dtype") and str(getattr(x_value, "dtype", "")).startswith("datetime64"):
                import pandas as pd
                return format_display_date(pd.Timestamp(x_value).to_pydatetime())

            x_num = float(x_value)

            mapped = lookup_x_display(self._x_numeric_to_display, x_num)
            if mapped is not None:
                return mapped

            if ax is not None:
                axis_lookup = self._axis_x_lookups.get(id(ax), {})
                mapped = lookup_x_display(axis_lookup, x_num)
                if mapped is not None:
                    return mapped

            as_date = _matplotlib_num_to_date_str(x_num)
            if as_date is not None:
                return as_date

            return format_display_date(x_value)
        except Exception:
            return str(x_value)

    def _collect_unique_x_values(self):
        """Collect sorted unique x values from all visible series."""
        import matplotlib.dates as mdates
        from datetime import date as _date, datetime as _datetime

        values = []
        for _line, x_data, _y_data, _label, _ax in self.lines:
            for x in x_data:
                try:
                    if isinstance(x, (_date, _datetime)):
                        values.append(float(mdates.date2num(x)))
                    else:
                        values.append(float(x))
                except Exception:
                    continue
        self._x_slider_values = sorted(set(values))

    def _remove_x_slider_artists(self):
        """Remove slider line/handle artists if present."""
        if self._x_slider_line is not None:
            try:
                self._x_slider_line.remove()
            except Exception:
                pass
            self._x_slider_line = None
        if self._x_slider_handle is not None:
            try:
                self._x_slider_handle.remove()
            except Exception:
                pass
            self._x_slider_handle = None
        if self._x_slider_handle_text is not None:
            try:
                self._x_slider_handle_text.remove()
            except Exception:
                pass
            self._x_slider_handle_text = None

    def _draw_x_slider(self):
        """Draw slider line and center handle at current slider index."""
        if not self._x_slider_values or self._x_slider_index is None or self.ax is None:
            return
        x_val = self._x_slider_values[self._x_slider_index]
        y_min, y_max = self.ax.get_ylim()
        y_mid = (y_min + y_max) / 2.0

        self._remove_x_slider_artists()
        self._x_slider_line = self.ax.axvline(x=x_val, color='black', linewidth=1.8, alpha=0.85, zorder=9)
        self._x_slider_handle, = self.ax.plot(
            x_val,
            y_mid,
            marker='o',
            color='black',
            markersize=22,
            markerfacecolor='white',
            markeredgewidth=1.5,
            zorder=10,
        )
        self._x_slider_handle_text = self.ax.text(
            x_val, y_mid, '◁▷',
            ha='center', va='center',
            fontsize=7, fontweight='bold', color='black',
            zorder=11,
        )
        self._x_slider_line.set_linestyle(':')
        self.canvas.draw_idle()

    def _notify_slider_date(self):
        """Send current slider date text to callback."""
        if self._x_slider_date_callback is None or not self._x_slider_values or self._x_slider_index is None:
            return
        x_val = self._x_slider_values[self._x_slider_index]
        self._x_slider_date_callback(self._format_x_as_date(x_val))

    def set_x_slider_enabled(self, enabled, date_callback=None):
        """Enable/disable vertical date slider on the graph."""
        self._x_slider_enabled = bool(enabled)
        self._x_slider_date_callback = date_callback
        self._x_slider_dragging = False

        if not self._x_slider_enabled:
            self._remove_x_slider_artists()
            if self._x_slider_date_callback is not None:
                self._x_slider_date_callback("")
            self.canvas.draw_idle()
            return

        self._collect_unique_x_values()
        if not self._x_slider_values:
            self._x_slider_enabled = False
            if self._x_slider_date_callback is not None:
                self._x_slider_date_callback("")
            self.canvas.draw_idle()
            return

        # Start at the far right (latest date / highest x value)
        self._x_slider_index = len(self._x_slider_values) - 1
        self._draw_x_slider()
        self._notify_slider_date()

    def _set_slider_by_event_x(self, event_xdata):
        """Snap slider to the nearest available x value."""
        if not self._x_slider_values:
            return
        try:
            target = float(event_xdata)
        except Exception:
            return
        nearest_idx = min(range(len(self._x_slider_values)), key=lambda i: abs(self._x_slider_values[i] - target))
        if nearest_idx != self._x_slider_index:
            self._x_slider_index = nearest_idx
            self._draw_x_slider()
            self._notify_slider_date()

    def _is_near_slider(self, event):
        """Return True if mouse event is near the slider line/handle."""
        if not self._x_slider_enabled or self._x_slider_line is None or event.inaxes != self.ax:
            return False
        try:
            x_val = self._x_slider_values[self._x_slider_index]
            x_px = self.ax.transData.transform((x_val, 0))[0]
            return abs(event.x - x_px) <= 8
        except Exception:
            return False

    def on_mouse_motion(self, event):
        """Show tooltip when mouse hovers over data points"""
        if self._x_slider_dragging and event.inaxes is not None and event.xdata is not None:
            if event.inaxes == self.ax:
                self._set_slider_by_event_x(event.xdata)
            return

        if event.inaxes is None or not self.lines:
            self._hide_tooltip()
            return

        hover_ax = event.inaxes
        from datetime import date as _date, datetime as _datetime
        import matplotlib.dates as mdates

        min_dist = float("inf")
        closest_point = None
        closest_ax = None

        def _to_x_num(x):
            if isinstance(x, (_date, _datetime)):
                return float(mdates.date2num(x))
            if hasattr(x, "to_pydatetime"):
                return float(mdates.date2num(x.to_pydatetime()))
            if hasattr(x, "dtype") and str(getattr(x, "dtype", "")).startswith("datetime64"):
                import pandas as pd
                return float(mdates.date2num(pd.Timestamp(x).to_pydatetime()))
            return float(x)

        for _artist, x_data, y_data, label, line_ax in self.lines:
            if line_ax is not hover_ax or len(x_data) == 0:
                continue
            try:
                for x, y in zip(x_data, y_data):
                    if y is None or (isinstance(y, float) and np.isnan(y)):
                        continue
                    x_num = _to_x_num(x)
                    point = line_ax.transData.transform([x_num, float(y)])
                    dist = np.sqrt((point[0] - event.x) ** 2 + (point[1] - event.y) ** 2)
                    if dist < min_dist and dist < 15:
                        min_dist = dist
                        closest_point = (x_num, float(y), x, label)
                        closest_ax = line_ax
            except Exception:
                pass

        if closest_point and closest_ax is not None:
            x_num, y_val, x_orig, label = closest_point
            self._show_tooltip(closest_ax, x_num, y_val, x_orig, label)
        else:
            self._hide_tooltip()

    def on_mouse_click(self, event):
        """Handle mouse click on plot - select corresponding table row"""
        if event.inaxes is None or not self.lines or self.requestor is None:
            return

        # Only handle left clicks
        if event.button != 1:
            return

        if self._x_slider_enabled and self._is_near_slider(event):
            self._x_slider_dragging = True
            if event.xdata is not None:
                self._set_slider_by_event_x(event.xdata)
            return

        hover_ax = event.inaxes
        min_dist = float("inf")
        closest_index = None

        for _line, x_data, y_data, _label, line_ax in self.lines:
            if line_ax is not hover_ax or len(x_data) == 0:
                continue
            try:
                from datetime import date
                import matplotlib.dates as mdates

                for i, (x, y) in enumerate(zip(x_data, y_data)):
                    if isinstance(x, date):
                        x_numeric = mdates.date2num(x)
                    else:
                        x_numeric = x

                    point = line_ax.transData.transform([x_numeric, y])
                    cursor = (event.x, event.y)
                    dist = np.sqrt((point[0] - cursor[0]) ** 2 + (point[1] - cursor[1]) ** 2)

                    if dist < min_dist and dist < 15:
                        min_dist = dist
                        closest_index = i
            except Exception:
                pass

        # If we found a close point, select the corresponding table row
        if closest_index is not None:
            # Get the item at the closest index to find the visual row
            # We need to find which visual row corresponds to the original data index
            for visual_row in range(self.requestor.table.rowCount()):
                item = self.requestor.table.item(visual_row, 0)
                if item is not None:
                    original_index = item.data(Qt.UserRole)
                    if original_index == closest_index:
                        # Select this row in the table
                        self.requestor.table.selectRow(visual_row)
                        break

    def on_mouse_release(self, event):
        """Stop slider dragging when mouse button is released."""
        if event.button == 1 and self._x_slider_dragging:
            self._x_slider_dragging = False

    def highlight_point(self, index):
        """Highlight a specific data point by index in red on all lines"""
        if not self.lines or index < 0:
            return

        # Remove previous highlight markers if they exist
        for marker in self.highlight_markers:
            marker.remove()
        self.highlight_markers = []

        # Highlight the point at the given index on all lines
        for line, x_data, y_data, _label, line_ax in self.lines:
            # Check if index is within bounds
            if index < len(x_data) and index < len(y_data):
                x_point = x_data[index]
                y_point = y_data[index]

                marker, = line_ax.plot(
                    x_point, y_point,
                    marker='o',
                    color='red',
                    markersize=12,
                    markeredgewidth=2,
                    markerfacecolor='none',
                    zorder=10,
                )
                self.highlight_markers.append(marker)

        self.highlighted_index = index
        self.canvas.draw()

    def highlight_date(self, target_date_str):
        """Highlight only points whose x-value date matches target_date_str."""
        if not self.lines or not target_date_str:
            self.clear_highlight()
            return

        import matplotlib.dates as mdates
        from datetime import date as _date, datetime as _datetime

        # Remove previous highlight markers
        for marker in self.highlight_markers:
            marker.remove()
        self.highlight_markers = []

        target = str(target_date_str).strip()

        for _line, x_data, y_data, _label, line_ax in self.lines:
            for x_point, y_point in zip(x_data, y_data):
                if y_point is None or (isinstance(y_point, float) and np.isnan(y_point)):
                    continue
                try:
                    if isinstance(x_point, _datetime):
                        x_str = x_point.strftime("%Y-%m-%d")
                    elif isinstance(x_point, _date):
                        x_str = x_point.strftime("%Y-%m-%d")
                    else:
                        x_str = mdates.num2date(float(x_point)).strftime("%Y-%m-%d")
                except Exception:
                    x_str = str(x_point).strip()

                if x_str == target:
                    marker, = line_ax.plot(
                        x_point, y_point,
                        marker='o',
                        color='red',
                        markersize=12,
                        markeredgewidth=2,
                        markerfacecolor='none',
                        zorder=10,
                    )
                    self.highlight_markers.append(marker)

        self.highlighted_index = None
        self.canvas.draw()

    def clear_highlight(self):
        """Clear the highlighted points"""
        for marker in self.highlight_markers:
            marker.remove()
        self.highlight_markers = []
        self.highlighted_index = None
        self.canvas.draw()
