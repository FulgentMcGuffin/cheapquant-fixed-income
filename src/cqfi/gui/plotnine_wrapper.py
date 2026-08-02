import re

import polars as pl
import pandas as pd
from datetime import date, datetime, time

from plotnine import (
    geom_line, geom_point, geom_bar, geom_col, geom_histogram, geom_density,  
    geom_smooth, geom_boxplot, geom_rug, geom_jitter, geom_violin,geom_tile, 
    facet_grid, facet_wrap,
    scale_y_continuous, scale_y_log10, scale_y_symlog, scale_y_sqrt, scale_y_reverse,
    theme_538, theme_dark, theme_light, theme_matplotlib, theme_minimal,
    theme_classic, theme_bw, theme_gray, theme_seaborn, theme_tufte,
    theme_xkcd, theme_linedraw,
    ggplot, aes, ggtitle, ylab, theme, element_text, element_line,
    coord_flip, after_stat,
)

PLOT9_THEMES_DICT = {
    "538": theme_538,
    "5": theme_538,
    "f": theme_538,
    "five": theme_538,
    "dark": theme_dark,
    "d": theme_dark,
    "light": theme_light,
    "1": theme_light,
    "matplotlib": theme_matplotlib,
    "m": theme_matplotlib,
    "minimal": theme_minimal,
    "min": theme_minimal,
    "classic": theme_classic,
    "c": theme_classic,
    "bw": theme_bw,
    "b": theme_bw,
    "gray": theme_gray,
    "g": theme_gray,
    "seaborn": theme_seaborn,
    "s": theme_seaborn,
    "tufte": theme_tufte,
    "t": theme_tufte,
    "xkcd": theme_xkcd,
    "x": theme_xkcd,
    "linedraw": theme_linedraw,
    "ld": theme_linedraw,
}

PLOT9_GEOMS_DICT = {
    "L": geom_line,
    "P": geom_point,
    "B": geom_bar,
    "S": geom_smooth,
    "R": geom_rug,
    "J": geom_jitter,
    "T": geom_tile,
    "V": geom_violin,
    "H": geom_histogram,
    "D": geom_density,
    "X": geom_boxplot,
    "F": facet_grid,
    "W": facet_wrap,
}

PLOT9_YSCALE_DICT = {
    "linear": scale_y_continuous,
    "reverse": scale_y_reverse,
    "log": scale_y_log10,
    "sqrt": scale_y_sqrt,
    "symlog": scale_y_symlog,
}

PLOT9_POS_LEGEND_DICT = {
    "inside": "inside",
    "left": "left",
    "right": "right",
    "top": "top",
    "bottom": "bottom",
    "none": "none",
    "i": "inside",
    "l": "left",
    "r": "right",
    "t": "top",
    "b": "bottom",
    "n": "none",
}


def _is_matplotlib_date_num(x: float) -> bool:
    """True if *x* looks like a matplotlib day number (not a categorical index)."""
    return 365 <= float(x) <= 900_000


def _matplotlib_num_to_date_str(x: float) -> str | None:
    """Convert a matplotlib day number to YYYY-mm-dd, or None if not a date number."""
    if not _is_matplotlib_date_num(x):
        return None
    try:
        import matplotlib.dates as mdates

        return mdates.num2date(float(x)).strftime("%Y-%m-%d")
    except Exception:
        return None


def normalize_x_value(value):
    """Convert assorted date-like values to a Python datetime/date when possible."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value

    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value

    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:
            pass

    try:
        if hasattr(value, "dtype") and str(getattr(value, "dtype", "")).startswith("datetime64"):
            return pd.Timestamp(value).to_pydatetime()
    except Exception:
        pass

    type_str = str(type(value))
    if "polars" in type_str.lower() and "date" in type_str.lower():
        try:
            return pd.Timestamp(str(value)).to_pydatetime()
        except Exception:
            pass

    if isinstance(value, str):
        parsed = parse_flexible_datetime(value)
        if parsed is not None:
            return parsed

    return value


_YEAR_MONTH_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})\s*$")
_TIME_ONLY_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")


def parse_flexible_datetime(value) -> datetime | None:
    """Parse assorted date/time strings into a datetime.

    Handles standard formats plus partial values such as:
    - ``2010-12`` -> 2010-12-15
    - ``08:30``   -> today at 08:30
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    text = str(value).strip()
    if not text:
        return None

    year_month = _YEAR_MONTH_RE.match(text)
    if year_month:
        year, month = int(year_month.group(1)), int(year_month.group(2))
        if 1 <= month <= 12:
            return datetime(year, month, 15)

    time_only = _TIME_ONLY_RE.match(text)
    if time_only:
        hour = int(time_only.group(1))
        minute = int(time_only.group(2))
        second = int(time_only.group(3) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            return datetime.combine(date.today(), time(hour, minute, second))

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        return parsed.to_pydatetime()

    return None


def format_display_date(value) -> str:
    """Format a date/datetime-like value for table labels and plot tooltips."""
    if value is None:
        return ""

    normalized = normalize_x_value(value)
    if isinstance(normalized, datetime):
        if normalized.hour or normalized.minute or normalized.second:
            return normalized.strftime("%Y-%m-%d %H:%M")
        return normalized.strftime("%Y-%m-%d")
    if isinstance(normalized, date):
        return normalized.strftime("%Y-%m-%d")
    if isinstance(normalized, pd.Timestamp):
        dt = normalized.to_pydatetime()
        if dt.hour or dt.minute or dt.second:
            return dt.strftime("%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        as_date = _matplotlib_num_to_date_str(float(value))
        if as_date is not None:
            return as_date

    if isinstance(value, str):
        parsed = parse_flexible_datetime(value)
        if parsed is not None:
            return format_display_date(parsed)
        return value

    return str(value)


def format_table_cell(value) -> str:
    """Format a dataframe cell for display in the table."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    
    # First check for common datetime types
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return format_display_date(value)
    
    # Handle Polars date/datetime by converting to string and parsing
    value_type_str = str(type(value))
    if "polars" in value_type_str.lower() and "date" in value_type_str.lower():
        try:
            # Convert Polars date type to datetime via pandas
            dt = pd.Timestamp(str(value))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    
    # Handle numeric types
    if isinstance(value, (int, float)):
        # Small integers that look like dates (day of year or epoch)
        if isinstance(value, int) and 1 <= value <= 366:
            # Likely day of year or similar, not a date
            return str(value)
        if isinstance(value, float):
            return f"{value:.4g}"
        return str(value)
    
    # Handle string types - try to parse as date
    if isinstance(value, str):
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%Y-%m-%d")
        return value
    
    # Fallback: try to convert to string and check if it's date-like
    value_str = str(value)
    if len(value_str) < 50:  # Not too long
        try:
            parsed = pd.to_datetime(value_str, errors="coerce")
            if pd.notna(parsed):
                return parsed.strftime("%Y-%m-%d")
        except Exception:
            pass
    
    return value_str


def first_column_date_parse_rate(df: pl.DataFrame) -> float:
    """Fraction of non-empty first-column values that parse as dates."""
    if df.is_empty():
        return 0.0

    first_col = df.columns[0]
    dtype = df.schema.get(first_col)
    if dtype in (pl.Date, pl.Datetime):
        return 1.0
    if dtype not in (pl.Utf8, pl.String):
        return 0.0

    values = df[first_col].to_list()
    non_empty = [value for value in values if value is not None and str(value).strip()]
    if not non_empty:
        return 0.0

    parsed = sum(1 for value in non_empty if parse_flexible_datetime(value) is not None)
    return parsed / len(non_empty)


def coerce_date_columns(df: pl.DataFrame, *, threshold: float = 0.8) -> pl.DataFrame:
    """Parse the first column as dates when most values are date-like strings.

    If fewer than *threshold* of non-empty values parse successfully the first
    column is left unchanged (typically as ``Utf8``) so the table keeps the
    original strings and the plot can use a categorical x-axis.
    """
    if df.is_empty():
        return df

    first_col = df.columns[0]
    dtype = df.schema.get(first_col)
    if dtype in (pl.Date, pl.Datetime):
        return df
    if dtype not in (pl.Utf8, pl.String):
        return df
    if first_column_date_parse_rate(df) < threshold:
        return df

    return df.with_columns(
        pl.col(first_col)
        .map_elements(parse_flexible_datetime, return_dtype=pl.Datetime("us"))
        .alias(first_col)
    )


def build_x_numeric_lookup(x_values: list) -> dict[float, str]:
    """Map matplotlib x coordinates (or categorical indices) to display labels."""
    import matplotlib.dates as mdates

    lookup: dict[float, str] = {}
    for idx, value in enumerate(x_values):
        normalized = normalize_x_value(value)
        label = format_display_date(normalized if normalized is not value else value)
        if not label and value is not None:
            label = str(value).strip()
        if not label:
            continue

        # plotnine uses 1-based factor positions; also keep 0-based as fallback.
        keys: list[float] = [float(idx + 1)]
        if float(idx) not in lookup:
            keys.append(float(idx))

        if isinstance(normalized, datetime):
            keys.append(float(mdates.date2num(normalized)))
        elif isinstance(normalized, date):
            keys.append(float(mdates.date2num(normalized)))
        elif isinstance(normalized, pd.Timestamp):
            keys.append(float(mdates.date2num(normalized.to_pydatetime())))

        for key in keys:
            lookup[key] = label
            lookup[round(key, 6)] = label

    return lookup


def lookup_x_display(lookup: dict[float, str], x_num: float, tolerance: float = 0.51) -> str | None:
    """Find a display label for a numeric x coordinate."""
    if x_num in lookup:
        return lookup[x_num]

    rounded = round(x_num, 6)
    if rounded in lookup:
        return lookup[rounded]

    best_label = None
    best_dist = tolerance
    for key, label in lookup.items():
        dist = abs(key - x_num)
        if dist < best_dist:
            best_dist = dist
            best_label = label
    return best_label


def _coerce_datetime_x(plot_df: pd.DataFrame, x: str) -> pd.DataFrame:
    """Parse date-like string/object x columns so geom_line can connect points."""
    if x not in plot_df.columns:
        return plot_df

    series = plot_df[x]
    if pd.api.types.is_datetime64_any_dtype(series):
        return plot_df

    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return plot_df

    parsed = series.map(parse_flexible_datetime)
    if parsed.notna().mean() < 0.9:
        return plot_df

    out = plot_df.copy()
    out[x] = pd.to_datetime(parsed)
    return out


def _ensure_line_grouping(
    aes_dict_kwargs: dict,
    geoms: str,
    has_multiple_series: bool,
    is_histogram: bool,
    series_name: str,
) -> None:
    """geom_line requires a stable group aesthetic to connect observations."""
    if is_histogram or "L" not in geoms:
        return

    if has_multiple_series:
        aes_dict_kwargs.setdefault("group", series_name)
    else:
        aes_dict_kwargs.setdefault("group", 1)


def _resolve_facet_var(
    plot_df: pd.DataFrame,
    x: str,
    has_multiple_series: bool,
    is_histogram: bool,
    series_name: str = "Series",
) -> str | None:
    """Pick a discrete column to facet on, if any."""
    if is_histogram:
        return None

    if has_multiple_series and series_name in plot_df.columns:
        if plot_df[series_name].nunique() > 1:
            return series_name

    skip = {x, series_name, "Value", "_color_key"}
    for col in plot_df.columns:
        if col in skip:
            continue
        nunique = plot_df[col].nunique()
        if nunique < 2 or nunique > 12:
            continue
        if pd.api.types.is_numeric_dtype(plot_df[col]):
            if pd.api.types.is_integer_dtype(plot_df[col]):
                return col
            continue
        return col

    return None


def create_ggplot_from_df(
    df: pd.DataFrame | pl.DataFrame,
    x: str,
    y_cols: list[str],
    formula: str,
    title: str,
    has_multiple_series: bool,
    geoms: str,
    color: bool,
    alpha: float,
    size: bool,
    shape: bool,
    group: bool,
    theme_str: str,
    flip_coord: bool,
    y_scale: str,
    pos_legend: str,
    main_series_col: str = None,
):
    """Generate a ggplot from a DataFrame with flexible styling options."""

    if isinstance(df, pl.DataFrame):
        df = df.to_pandas()
   
    if isinstance(alpha, type):
        alpha = None
    if isinstance(color, type):
        color = False
    if isinstance(size, type):
        size = False
    if isinstance(shape, type):
        shape = False
    if isinstance(group, type):
        group = False
    if isinstance(flip_coord, type):
        flip_coord = False
    if isinstance(y_scale, type):
        y_scale = None
    if isinstance(pos_legend, type):
        pos_legend = None
    if isinstance(theme_str, type):
        theme_str = "538"
    if isinstance(geoms, type):
        geoms = "LP"

    geoms = geoms.replace(" ", "").replace("_", "").strip().upper()
    is_histogram = ("D" in geoms) or ("H" in geoms)

    SERIES_NAME = "Series"
    VALUE_NAME = "Value"
    COLOR_KEY = "_color_key"

    plot_df = df.copy()
    if not is_histogram:
        if not has_multiple_series:
            aes_dict_kwargs = {"x": x, "y": y_cols[0]}
            if color is True:
                # Map color to a discrete label, not the continuous y column.
                # Continuous color breaks geom_line (points still render).
                plot_df[COLOR_KEY] = y_cols[0]
                aes_dict_kwargs["color"] = COLOR_KEY
                aes_dict_kwargs["fill"] = COLOR_KEY
                aes_dict_kwargs["group"] = COLOR_KEY
            if size is True:
                aes_dict_kwargs["size"] = y_cols[0]
        else:
            aes_dict_kwargs = {"x": x, "y": VALUE_NAME}
            if color is True:
                aes_dict_kwargs["color"] = SERIES_NAME
                aes_dict_kwargs["fill"] = SERIES_NAME
            if size is True:
                aes_dict_kwargs["size"] = VALUE_NAME
            if shape is True:
                aes_dict_kwargs["shape"] = SERIES_NAME
            if group is True:
                aes_dict_kwargs["group"] = SERIES_NAME
    else:
        geoms = "".join(char for char in geoms if char in "DHRXFWB")
        aes_dict_kwargs = (
            {"x": y_cols[0]}
            if not has_multiple_series
            else {"x": VALUE_NAME}
        )
        if color is True:
            aes_dict_kwargs["color"] = y_cols[0] if not has_multiple_series else SERIES_NAME
            aes_dict_kwargs["fill"] = aes_dict_kwargs["color"]

    if has_multiple_series and not is_histogram:
        if SERIES_NAME not in plot_df.columns:
            plot_df = df.melt(
                id_vars=[x],
                value_vars=y_cols,
                var_name=SERIES_NAME,
                value_name=VALUE_NAME,
            )

    plot_geoms = "".join(ch for ch in geoms if ch not in "FW")
    if not plot_geoms:
        plot_geoms = "LP"

    _ensure_line_grouping(
        aes_dict_kwargs, plot_geoms, has_multiple_series, is_histogram, SERIES_NAME
    )

    if "L" in plot_geoms and not is_histogram:
        plot_df = _coerce_datetime_x(plot_df, x)
        if x in plot_df.columns:
            plot_df = plot_df.sort_values(x)

    p = ggplot(plot_df, aes(**aes_dict_kwargs))

    alpha_val = 1.0 if alpha is None else alpha
    add_facet_wrap = "W" in geoms
    add_facet_grid = "F" in geoms

    for char in plot_geoms:
        geom_fn = PLOT9_GEOMS_DICT.get(char)
        if geom_fn is None:
            continue
        if char in ("H", "D"):
            p = p + geom_fn(alpha=alpha_val)
        elif char == "B":
            bar_geom = geom_bar if is_histogram else geom_col
            p = p + bar_geom(alpha=alpha_val)
        else:
            p = p + geom_fn(alpha=alpha_val)

    theme_key = (theme_str or "538").lower()
    theme_fn = PLOT9_THEMES_DICT.get(theme_key, theme_538)
    p = p + theme_fn()

    y_scale_key = (y_scale or "linear").lower()
    if y_scale_key == "log10":
        y_scale_key = "log"
    scale_fn = PLOT9_YSCALE_DICT.get(y_scale_key, scale_y_continuous)
    p = p + scale_fn()

    pos_key = (pos_legend or "bottom").lower()
    legend_pos = PLOT9_POS_LEGEND_DICT.get(pos_key, "bottom")
    if legend_pos != "none":
        p = p + theme(legend_position=legend_pos)

    if flip_coord:
        p = p + coord_flip()

    facet_var = _resolve_facet_var(plot_df, x, has_multiple_series, is_histogram, SERIES_NAME)
    if facet_var:
        if add_facet_wrap:
            p = p + facet_wrap(facet_var)
        elif add_facet_grid:
            p = p + facet_grid(cols=facet_var)

    if title:
        p = p + ggtitle(title)

    if main_series_col and not has_multiple_series and main_series_col in y_cols:
        p = p + ylab(main_series_col)
        

    return p


def process_df_for_ggplot(
    df: pd.DataFrame,
    x_col: str,
) -> tuple[pd.DataFrame, list[str], bool]:
    """Prepare a pandas DataFrame for plotnine rendering."""
    if x_col not in df.columns and len(df.columns) > 0:
        x_col = df.columns[0]

    numeric_cols = [
        c
        for c in df.columns
        if c != x_col and pd.api.types.is_numeric_dtype(df[c])
    ]
    y_cols = numeric_cols or [c for c in df.columns if c != x_col][:1]
    has_multiple_series = len(y_cols) > 1

    plot_df = df.copy()
    if has_multiple_series:
        plot_df = plot_df.melt(
            id_vars=[x_col],
            value_vars=y_cols,
            var_name="Series",
            value_name="Value",
        )

    return plot_df, y_cols, has_multiple_series


def infer_plot_columns(df: pl.DataFrame) -> tuple[str, list[str]]:
    """Pick x/y columns from a generic query result.

    The first column is always the x-axis. Remaining numeric columns — or, if
    none exist, the next few non-x columns — supply the y series.
    """
    if df.is_empty():
        return "index", []

    cols = df.columns
    x_col = cols[0]

    numeric_types = {
        pl.Float64,
        pl.Float32,
        pl.Int64,
        pl.Int32,
        pl.UInt64,
        pl.UInt32,
    }
    y_cols = [c for c in cols if c != x_col and df[c].dtype in numeric_types]
    if not y_cols:
        y_cols = [c for c in cols if c != x_col][:3]
    return x_col, y_cols
