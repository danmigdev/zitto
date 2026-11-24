"""A picture in memory that numpy fills, and the widget that shows it. How depends on the system."""

import sys

if sys.platform == "win32":
    from .surface_windows import Surface, keep_content_on_resize
else:
    from .surface_tk import Surface, keep_content_on_resize

__all__ = ["Surface", "keep_content_on_resize"]
