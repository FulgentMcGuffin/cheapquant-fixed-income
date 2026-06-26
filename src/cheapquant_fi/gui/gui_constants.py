import sys
from dataclasses import dataclass

# ── Theme definition ──────────────────────────────────────────────────────────

@dataclass
class Theme:
    name: str
    bg: str               # window background
    surface: str          # card / elevated surface
    border: str           # card border / subtle divider
    text: str             # primary text
    text_muted: str       # subtitle / secondary text
    accents: list         # [(primary, hover), ...] — cycles across categories
    total_ok: str         # total-label colour when ≈ 100 %
    total_warn: str       # colour when slightly off
    total_err: str        # colour when far off
    slider_groove: str    # unfilled track colour
    slider_handle: str    # handle fill colour


THEMES: list[Theme] = [
    # 1 ── Fluent Dark  (default — matches Windows 11 dark shell)
    Theme(
        name="Fluent Dark",
        bg="#1C1C1C", surface="#272727", border="#383838",
        text="#F0F0F0", text_muted="#888888",
        accents=[
            ("#0078D4", "#60BDFF"),
            ("#107C10", "#4EC94E"),
            ("#C239B3", "#E97FD7"),
            ("#D83B01", "#FF8C42"),
            ("#FFB900", "#FFD85C"),
        ],
        total_ok="#6CCB5F", total_warn="#FFB900", total_err="#E05C5C",
        slider_groove="#3C3C3C", slider_handle="#F0F0F0",
    ),
    # 2 ── Fluent Light  (Windows 11 light shell)
    Theme(
        name="Fluent Light",
        bg="#F3F3F3", surface="#FFFFFF", border="#DCDCDC",
        text="#1A1A1A", text_muted="#767676",
        accents=[
            ("#0078D4", "#005EA2"),
            ("#107C10", "#0B5A08"),
            ("#881798", "#6B0F8A"),
            ("#D83B01", "#A4262C"),
            ("#986F0B", "#7A5800"),
        ],
        total_ok="#107C10", total_warn="#C17D00", total_err="#C42B1C",
        slider_groove="#CCCCCC", slider_handle="#1A1A1A",
    ),
    # 3 ── Nord
    Theme(
        name="Nord",
        bg="#2E3440", surface="#3B4252", border="#434C5E",
        text="#ECEFF4", text_muted="#8994A3",
        accents=[
            ("#5E81AC", "#81A1C1"),
            ("#A3BE8C", "#B5CC9E"),
            ("#B48EAD", "#C7A7C5"),
            ("#D08770", "#DFA080"),
            ("#EBCB8B", "#F0D8A0"),
        ],
        total_ok="#A3BE8C", total_warn="#EBCB8B", total_err="#BF616A",
        slider_groove="#4C566A", slider_handle="#ECEFF4",
    ),
    # 4 ── Dracula
    Theme(
        name="Dracula",
        bg="#282A36", surface="#44475A", border="#6272A4",
        text="#F8F8F2", text_muted="#6272A4",
        accents=[
            ("#FF79C6", "#FFB3E0"),
            ("#50FA7B", "#8AFCA2"),
            ("#BD93F9", "#D4B3FB"),
            ("#FFB86C", "#FFD0A0"),
            ("#F1FA8C", "#F6FCB4"),
        ],
        total_ok="#50FA7B", total_warn="#F1FA8C", total_err="#FF5555",
        slider_groove="#3A3C4E", slider_handle="#F8F8F2",
    ),
    # 5 ── Solarized Dark
    Theme(
        name="Solarized Dark",
        bg="#002B36", surface="#073642", border="#0D4A57",
        text="#93A1A1", text_muted="#657B83",
        accents=[
            ("#268BD2", "#4EA9EB"),
            ("#859900", "#A7BE00"),
            ("#D33682", "#E262A2"),
            ("#CB4B16", "#E06B36"),
            ("#B58900", "#D4A500"),
        ],
        total_ok="#859900", total_warn="#B58900", total_err="#DC322F",
        slider_groove="#0D4A57", slider_handle="#93A1A1",
    ),
    # 6 ── Monokai
    Theme(
        name="Monokai",
        bg="#1E1F1C", surface="#272822", border="#3E3D32",
        text="#F8F8F2", text_muted="#75715E",
        accents=[
            ("#66D9E8", "#96E7F2"),
            ("#A6E22E", "#C4EE6E"),
            ("#AE81FF", "#C9ADFF"),
            ("#FD971F", "#FEBC6F"),
            ("#E6DB74", "#EDE5A0"),
        ],
        total_ok="#A6E22E", total_warn="#E6DB74", total_err="#F92672",
        slider_groove="#49483E", slider_handle="#F8F8F2",
    ),
    # 7 ── High Contrast  (accessibility)
    Theme(
        name="High Contrast",
        bg="#000000", surface="#0D0D0D", border="#FFFFFF",
        text="#FFFFFF", text_muted="#CCCCCC",
        accents=[
            ("#FFFF00", "#FFFF99"),
            ("#00FF00", "#99FF99"),
            ("#00FFFF", "#99FFFF"),
            ("#FF6600", "#FFA366"),
            ("#FF00FF", "#FF99FF"),
        ],
        total_ok="#00FF00", total_warn="#FFFF00", total_err="#FF0000",
        slider_groove="#333333", slider_handle="#FFFFFF",
    ),
    # 8 ── Catppuccin Mocha
    Theme(
        name="Catppuccin Mocha",
        bg="#1E1E2E", surface="#2A2A3E", border="#313244",
        text="#CDD6F4", text_muted="#6C7086",
        accents=[
            ("#89B4FA", "#B4CFFC"),
            ("#A6E3A1", "#C3EEC0"),
            ("#CBA6F7", "#DEC3FA"),
            ("#FAB387", "#FCC9AA"),
            ("#F9E2AF", "#FBF0CD"),
        ],
        total_ok="#A6E3A1", total_warn="#F9E2AF", total_err="#F38BA8",
        slider_groove="#313244", slider_handle="#CDD6F4",
    ),
    # 9 ── Blossom Pink
    Theme(
        name="Blossom Pink",
        bg="#FFF5F8", surface="#FFFFFF", border="#F5C6D6",
        text="#4A3040", text_muted="#9E7A8C",
        accents=[
            ("#F472B6", "#FBCFE8"),
            ("#FB7185", "#FECDD3"),
            ("#C084FC", "#E9D5FF"),
            ("#FBBF24", "#FDE68A"),
            ("#60A5FA", "#BFDBFE"),
        ],
        total_ok="#86EFAC", total_warn="#FDE047", total_err="#FB7185",
        slider_groove="#F9D4E4", slider_handle="#4A3040",
    ),
    # 10 ── Arctic Light
    Theme(
        name="Arctic Light",
        bg="#F4F7FA", surface="#FFFFFF", border="#D5DDE8",
        text="#1A1A1A", text_muted="#6B7280",
        accents=[
            ("#0078D4", "#005EA2"),
            ("#0E8A8A", "#0B6E6E"),
            ("#5B5BD6", "#4646B8"),
            ("#881798", "#6B0F8A"),
            ("#986F0B", "#7A5800"),
        ],
        total_ok="#107C10", total_warn="#C17D00", total_err="#C42B1C",
        slider_groove="#D5DDE8", slider_handle="#1A1A1A",
    ),
    # 11 ── Lavender Mist
    Theme(
        name="Lavender Mist",
        bg="#F5F3FF", surface="#FFFFFF", border="#DDD6FE",
        text="#3B2F5A", text_muted="#8B80A8",
        accents=[
            ("#A78BFA", "#DDD6FE"),
            ("#C084FC", "#E9D5FF"),
            ("#818CF8", "#C7D2FE"),
            ("#F472B6", "#FBCFE8"),
            ("#67E8F9", "#CFFAFE"),
        ],
        total_ok="#86EFAC", total_warn="#FDE68A", total_err="#F472B6",
        slider_groove="#DDD6FE", slider_handle="#3B2F5A",
    ),
    # 12 ── Pearl Light
    Theme(
        name="Pearl Light",
        bg="#F7F7F7", surface="#FFFFFF", border="#E0E0E0",
        text="#1A1A1A", text_muted="#757575",
        accents=[
            ("#0078D4", "#005EA2"),
            ("#107C10", "#0B5A08"),
            ("#881798", "#6B0F8A"),
            ("#CA5010", "#A4262C"),
            ("#797673", "#605E5C"),
        ],
        total_ok="#107C10", total_warn="#986F0B", total_err="#C42B1C",
        slider_groove="#E0E0E0", slider_handle="#1A1A1A",
    ),
    # 13 ── Sky Day
    Theme(
        name="Sky Day",
        bg="#F0F9FF", surface="#FFFFFF", border="#BAE6FD",
        text="#1E3A5F", text_muted="#6B8CAE",
        accents=[
            ("#38BDF8", "#BAE6FD"),
            ("#60A5FA", "#BFDBFE"),
            ("#22D3EE", "#A5F3FC"),
            ("#818CF8", "#C7D2FE"),
            ("#4ADE80", "#BBF7D0"),
        ],
        total_ok="#4ADE80", total_warn="#FACC15", total_err="#FB7185",
        slider_groove="#BAE6FD", slider_handle="#1E3A5F",
    ),
    # 14 ── Canvas Light
    Theme(
        name="Canvas Light",
        bg="#FAFAF8", surface="#FFFFFF", border="#E8E6E1",
        text="#1C1C1A", text_muted="#787774",
        accents=[
            ("#0078D4", "#005EA2"),
            ("#498205", "#3A6804"),
            ("#8764B8", "#6B4F93"),
            ("#D83B01", "#A4262C"),
            ("#8E562E", "#704522"),
        ],
        total_ok="#107C10", total_warn="#986F0B", total_err="#C42B1C",
        slider_groove="#E8E6E1", slider_handle="#1C1C1A",
    ),
]


DEFAULT_THEME_NAME = "Fluent Dark"

_LEGACY_THEME_NAMES = {
    "Mint Fresh": "Arctic Light",
    "Peach Cream": "Pearl Light",
    "Lemon Chiffon": "Canvas Light",
}


def get_theme(name: str | Theme | None = None) -> Theme:
    if isinstance(name, Theme):
        return name
    lookup = name or DEFAULT_THEME_NAME
    lookup = _LEGACY_THEME_NAMES.get(lookup, lookup)
    for theme in THEMES:
        if theme.name == lookup:
            return theme
    return THEMES[0]


def get_plot_bg(name: str | Theme | None = None) -> str:
    return get_theme(name).bg


# ── Stylesheet builders ───────────────────────────────────────────────────────

def _base_style(t: Theme) -> str:
    return f"""
* {{
    font-family: -apple-system, "Segoe UI", "Helvetica Neue", sans-serif;
}}
QWidget {{
    background-color: {t.bg};
    color: {t.text};
}}
QLabel {{
    background: transparent;
}}
QFrame#card {{
    background-color: {t.surface};
    border-radius: 12px;
    border: none;
    padding: 12px;
}}
QLabel#header {{
    font-size: 24px;
    font-weight: 700;
    color: {t.text};
    background: transparent;
    letter-spacing: -0.5px;
}}
QLabel#subtitle {{
    font-size: 13px;
    font-weight: 400;
    color: {t.text_muted};
    background: transparent;
}}
QLabel#catLabel {{
    font-size: 14px;
    font-weight: 600;
    color: {t.text};
    background: transparent;
}}
QLabel#valLabel {{
    font-size: 14px;
    font-weight: 700;
    background: transparent;
    min-width: 52px;
    color: {t.text};
}}
QLabel#totalLabel {{
    font-size: 15px;
    font-weight: 700;
    background-color: {t.surface};
    border-radius: 12px;
    padding: 14px 20px;
    border: none;
}}
QLabel#statusLabel {{
    font-size: 12px;
    font-weight: 500;
    color: {t.text_muted};
    background: transparent;
}}
QPushButton#gearBtn {{
    background-color: {t.surface};
    color: {t.text};
    border: none;
    border-radius: 8px;
    font-size: 18px;
    padding: 10px 10px;
    font-weight: 500;
}}
QPushButton#gearBtn:hover {{
    background-color: {t.border};
}}
QPushButton#gearBtn:pressed {{
    background-color: {t.bg};
}}
QMenu {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 6px;
    margin: 0px;
}}
QMenu::item {{
    padding: 8px 24px;
    border-radius: 6px;
    margin: 3px 4px;
    font-weight: 500;
}}
QMenu::item:selected {{
    background-color: {t.border};
}}
QMenu::item:pressed {{
    background-color: {t.border};
}}
"""


def _slider_style(primary: str, hover: str, groove: str, handle: str) -> str:
    return f"""
QSlider::groove:horizontal {{
    height: 4px;
    background: {groove};
    border-radius: 2px;
    margin: 0px;
}}
QSlider::sub-page:horizontal {{
    background: {primary};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {handle};
    border: 2px solid {primary};
    width: 16px;
    height: 16px;
    margin: -6px 0px;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: {hover};
    border-color: {hover};
}}
QSlider::handle:horizontal:pressed {{
    background: {primary};
    border-color: {primary};
}}
"""


def dialog_style(t: Theme) -> str:
    primary, primary_hover = t.accents[0]
    return _base_style(t) + f"""
QDialog {{
    background: transparent;
}}
QWidget#dialogShell {{
    background-color: {t.bg};
    border: 1px solid {t.border};
    border-radius: 14px;
}}
QWidget#titleBar {{
    background-color: {t.surface};
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
    border-bottom: 1px solid {t.border};
}}
QFrame#titleAccent {{
    background-color: {primary};
    border-radius: 2px;
    border: none;
}}
QLabel#titleSubtitle {{
    font-size: 11px;
    font-weight: 400;
    color: {t.text_muted};
    background: transparent;
    padding: 0px;
    margin: 0px;
}}
QPushButton#closeBtn:hover {{
    background-color: #E81123;
    color: #FFFFFF;
}}
QFrame#panelFrame {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QPushButton#collapsibleToggle {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 8px 12px;
    font-weight: 600;
    font-size: 12px;
    text-align: left;
}}
QPushButton#collapsibleToggle:hover {{
    background-color: {t.border};
}}
QPushButton#collapsibleToggle:pressed {{
    background-color: {t.bg};
}}
QWidget#collapsibleContent {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 6px;
}}
QLabel#sectionLabel {{
    font-size: 11px;
    font-weight: 700;
    color: {t.text_muted};
    background: transparent;
    letter-spacing: 0.8px;
    padding-bottom: 2px;
}}
QLabel#statusPill {{
    font-size: 11px;
    font-weight: 600;
    color: {t.text_muted};
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 14px;
    padding: 5px 12px;
}}
QLabel#statusPill[busy="true"] {{
    color: {primary};
    border-color: {primary};
    background-color: {t.bg};
}}
QWidget#dialogContent {{
    background: transparent;
}}
QLabel#titleLabel {{
    font-size: 16px;
    font-weight: 700;
    color: {t.text};
    background: transparent;
    letter-spacing: -0.3px;
}}
QPushButton#closeBtn {{
    background: transparent;
    color: {t.text_muted};
    border: none;
    border-radius: 8px;
    font-size: 18px;
    padding: 4px;
    min-height: 34px;
    min-width: 34px;
    font-weight: 600;
}}
QPushButton#closeBtn:pressed {{
    background-color: {t.bg};
}}
QLineEdit, QDoubleSpinBox, QSpinBox {{
    background-color: {t.surface};
    color: {t.text};
    border: 2px solid {t.border};
    border-radius: 8px;
    padding: 8px 12px;
    min-height: 32px;
    font-size: 13px;
    font-weight: 500;
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border: 2px solid {primary};
    background-color: {t.surface};
}}
QComboBox {{
    background-color: {t.surface};
    color: {t.text};
    border: 2px solid {t.border};
    border-radius: 8px;
    padding: 8px 12px;
    min-height: 32px;
    font-size: 13px;
    font-weight: 500;
}}
QComboBox:focus {{
    border: 2px solid {primary};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    background: transparent;
    image: url(none);
}}
QComboBox QAbstractItemView {{
    background-color: {t.surface};
    color: {t.text};
    border: 2px solid {t.border};
    border-radius: 8px;
    selection-background-color: {primary};
    selection-color: #FFFFFF;
}}
QCheckBox {{
    spacing: 10px;
    font-size: 13px;
    color: {t.text};
    background: transparent;
    font-weight: 500;
}}
QCheckBox::indicator {{
    width: 20px;
    height: 20px;
    border: 2px solid {t.border};
    border-radius: 6px;
    background: {t.surface};
}}
QCheckBox::indicator:hover {{
    border-color: {primary};
    background-color: {t.surface};
}}
QCheckBox::indicator:checked {{
    background: {primary};
    border-color: {primary};
}}
QPushButton {{
    background-color: {t.surface};
    color: {t.text};
    border: 2px solid {t.border};
    border-radius: 8px;
    padding: 8px 16px;
    min-height: 32px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {t.border};
    border-color: {t.border};
}}
QPushButton:pressed {{
    background-color: {t.bg};
}}
QPushButton:default {{
    background-color: {primary};
    color: #FFFFFF;
    border-color: {primary};
    font-weight: 700;
}}
QPushButton:default:hover {{
    background-color: {primary_hover};
    border-color: {primary_hover};
}}
QScrollBar:vertical {{
    background-color: {t.bg};
    width: 12px;
    margin: 0px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {t.border};
    border-radius: 6px;
    min-height: 40px;
    margin: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {primary};
}}
QScrollBar:horizontal {{
    background-color: {t.bg};
    height: 12px;
    margin: 0px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {t.border};
    border-radius: 6px;
    min-width: 40px;
    margin: 3px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {primary};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    border: none;
    background: none;
}}
"""


def window_style(t: Theme) -> str:
    primary, primary_hover = t.accents[0]
    return dialog_style(t) + f"""
QMainWindow {{
    background: transparent;
}}
QWidget#windowContent {{
    background: transparent;
}}
QWidget#outerRoot {{
    background: transparent;
}}
QSplitter {{
    background: transparent;
    border: none;
}}
QWidget#chatPanel, QFrame#chatPanel, QFrame#panelFrame {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QWidget#chatInner {{
    background: transparent;
}}
QTextBrowser, QTextBrowser#chatView {{
    background-color: {t.bg};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 14px;
    font-size: 13px;
    line-height: 1.65;
    font-weight: 400;
    selection-background-color: {primary};
}}
QLineEdit#promptInput {{
    background-color: {t.bg};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 10px 14px;
    min-height: 40px;
    font-size: 13px;
    font-weight: 400;
    selection-background-color: {primary};
}}
QLineEdit#promptInput:focus {{
    border: 2px solid {primary};
    padding: 9px 13px;
}}
QPushButton#sendBtn {{
    background-color: {primary};
    color: #FFFFFF;
    border: none;
    border-radius: 10px;
    padding: 10px 28px;
    font-weight: 700;
    font-size: 13px;
    min-height: 40px;
    min-width: 88px;
}}
QPushButton#sendBtn:hover {{
    background-color: {primary_hover};
}}
QPushButton#sendBtn:pressed {{
    background-color: {primary};
}}
QPushButton#sendBtn:disabled {{
    background-color: {t.border};
    color: {t.text_muted};
}}
QPushButton#titleSettingsBtn {{
    background-color: {t.bg};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 14px;
    min-height: 32px;
}}
QPushButton#titleSettingsBtn:hover {{
    background-color: {t.border};
    border-color: {t.border};
}}
QSplitter#mainSplitter::handle, QSplitter#dataSplitter::handle {{
    background-color: transparent;
    margin: 6px 0px;
    height: 6px;
}}
QSplitter#dataSplitter::handle {{
    margin: 0px 6px;
    width: 6px;
    height: auto;
}}
QSplitter#mainSplitter::handle:hover, QSplitter#dataSplitter::handle:hover {{
    background-color: {primary};
    border-radius: 3px;
    opacity: 0.6;
}}
QWidget#dataPlot {{
    background-color: {t.bg};
    border: none;
    border-radius: 10px;
}}
QTableWidget, QTableWidget#dataTable {{
    background-color: {t.bg};
    color: {t.text};
    border: none;
    border-radius: 10px;
    gridline-color: {t.border};
    font-size: 12px;
    font-weight: 400;
    outline: none;
}}
QTableWidget::item {{
    padding: 8px;
    border-bottom: 1px solid {t.border};
    height: 32px;
}}
QTableWidget::item:selected {{
    background-color: {primary};
    color: #FFFFFF;
}}
QTableWidget::item:alternate {{
    background-color: transparent;
}}
QHeaderView::section {{
    background-color: {t.bg};
    color: {t.text};
    padding: 8px 6px;
    border: none;
    border-bottom: 1px solid {t.border};
    font-weight: 700;
    font-size: 12px;
    height: 32px;
}}
QHeaderView::section:hover {{
    background-color: {t.border};
}}
QHeaderView {{
    background-color: {t.bg};
    border: none;
}}
QPushButton#actionBtn {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 600;
    font-size: 12px;
    min-height: 28px;
}}
QPushButton#actionBtn:hover {{
    background-color: {t.border};
    border-color: {t.border};
}}
QPushButton#actionBtn:pressed {{
    background-color: {t.bg};
}}
QPushButton#actionBtn:disabled {{
    background-color: {t.bg};
    color: {t.text_muted};
    border-color: {t.border};
}}
QPushButton#smallBtn {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 4px;
    padding: 4px 10px;
    font-weight: 500;
    font-size: 11px;
    min-height: 24px;
}}
QPushButton#smallBtn:hover {{
    background-color: {t.border};
}}
QPushButton#smallBtn:pressed {{
    background-color: {t.bg};
}}
QTextBrowser {{
    font-size: 13px;
    line-height: 1.6;
}}
"""
