import math
import time

from kivy.graphics import Color, Ellipse, Line, Rectangle
from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.widget import Widget


class HeadingWidget(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.heading_deg = None
        self.target_deg = None
        self.heading_source = None
        self.heading_detail = ""
        self._text_label = Label(
            text="",
            font_size="12sp",
            color=(1, 1, 1, 1),
            halign="center",
            valign="top",
        )
        self.add_widget(self._text_label)
        self.bind(pos=self._redraw, size=self._redraw)

    def set_heading(self, deg, source=None, detail="", target_deg=None):
        self.heading_deg = deg
        self.target_deg = target_deg
        self.heading_source = source
        self.heading_detail = detail or ""
        self._redraw()

    def _redraw(self, *args):
        self.canvas.clear()
        cx = self.x + self.width / 2
        cy = self.y + self.height / 2
        r = min(self.width, self.height) / 2 - dp(4)
        with self.canvas:
            Color(0.18, 0.18, 0.18, 1)
            Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))
            Color(0.55, 0.55, 0.55, 1)
            Line(circle=(cx, cy, r), width=1.5)
            tick_outer = r
            tick_inner = r - dp(6)
            for deg, label in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
                rad = math.radians(deg)
                tx1 = cx + math.sin(rad) * tick_inner
                ty1 = cy + math.cos(rad) * tick_inner
                tx2 = cx + math.sin(rad) * tick_outer
                ty2 = cy + math.cos(rad) * tick_outer
                Color(1, 1, 1, 0.7)
                Line(points=[tx1, ty1, tx2, ty2], width=1.5)

            # Target stop marker on dial (direction to selected B point).
            if self.target_deg is not None:
                target_rad = math.radians(self.target_deg % 360.0)
                mark_outer = r - dp(1)
                mark_inner = r - dp(12)
                mx1 = cx + math.sin(target_rad) * mark_inner
                my1 = cy + math.cos(target_rad) * mark_inner
                mx2 = cx + math.sin(target_rad) * mark_outer
                my2 = cy + math.cos(target_rad) * mark_outer
                Color(0.1, 0.85, 0.25, 1)
                Line(points=[mx1, my1, mx2, my2], width=dp(3))
                dot_r = dp(3.2)
                Color(0.1, 0.95, 0.35, 1)
                Ellipse(pos=(mx2 - dot_r, my2 - dot_r), size=(dot_r * 2, dot_r * 2))

            if self.heading_deg is not None:
                arrow_r = r - dp(10)
                head_rad = math.radians(self.heading_deg)
                ax2 = cx + math.sin(head_rad) * arrow_r
                ay2 = cy + math.cos(head_rad) * arrow_r
                ax_tail = cx - math.sin(head_rad) * dp(8)
                ay_tail = cy - math.cos(head_rad) * dp(8)
                Color(0.95, 0.4, 0.0, 1)
                Line(points=[ax_tail, ay_tail, ax2, ay2], width=dp(2.5))
                head_len = dp(8)
                left_rad = head_rad + math.radians(150)
                right_rad = head_rad - math.radians(150)
                Color(0.95, 0.4, 0.0, 1)
                Line(
                    points=[ax2, ay2, ax2 + math.sin(left_rad) * head_len, ay2 + math.cos(left_rad) * head_len],
                    width=dp(2.5),
                )
                Line(
                    points=[ax2, ay2, ax2 + math.sin(right_rad) * head_len, ay2 + math.cos(right_rad) * head_len],
                    width=dp(2.5),
                )

        label_h = dp(38)
        self._text_label.pos = (self.x, self.y)
        self._text_label.size = (self.width, label_h)
        self._text_label.text_size = (self.width, label_h)
        if self.heading_deg is not None:
            cardinal = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][
                int(((self.heading_deg % 360.0) + 22.5) // 45.0) % 8
            ]
            source = f" [{self.heading_source}]" if self.heading_source else ""
            if self.heading_detail:
                self._text_label.text = f"{self.heading_deg:.1f}°  {cardinal}{source}\n{self.heading_detail}"
            else:
                self._text_label.text = f"{self.heading_deg:.1f}°  {cardinal}{source}"
        else:
            if self.heading_detail:
                self._text_label.text = f"Heading: ---\n{self.heading_detail}"
            else:
                self._text_label.text = "Heading: ---"


class MapContainer(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.points = []
        self.selected = None
        self.rover_pos = None
        self.rover_heading_deg = None
        self.nav_heading_deg = None
        self.nav_target_in_zone = False
        self.bind(pos=self._redraw, size=self._redraw)

    def set_points(self, points):
        self.points = points
        self.selected = None
        self._redraw()

    def select_index(self, idx):
        if idx is None or idx < 0 or idx >= len(self.points):
            self.selected = None
        else:
            self.selected = idx
        self._redraw()

    def _latlon_to_xy(self, lat, lon, w, h, padding=10):
        lats = [p["lat"] for p in self.points]
        lons = [p["lon"] for p in self.points]
        if self.rover_pos is not None:
            rover_x, rover_y = self.rover_pos
            lons.append(rover_x)
            lats.append(rover_y)

        if not lats or not lons:
            return (w / 2, h / 2)

        max_abs_x = max(max(abs(v) for v in lons), 1e-6)
        max_abs_y = max(max(abs(v) for v in lats), 1e-6)
        radius = max(max_abs_x, max_abs_y)

        usable = max(1.0, min(w, h) - 2 * padding)
        scale = usable / (2.0 * radius)

        cx = w / 2.0
        cy = h / 2.0
        x = cx + lon * scale
        y = cy + lat * scale
        return (x, y)

    def _redraw(self, *args):
        self.canvas.clear()
        with self.canvas:
            Color(0.95, 0.95, 0.95)
            Rectangle(pos=self.pos, size=self.size)

            w, h = self.size
            for i, pt in enumerate(self.points):
                lat, lon = pt["lat"], pt["lon"]
                x_rel, y_rel = self._latlon_to_xy(lat, lon, w, h)
                x = self.x + x_rel
                y = self.y + y_rel
                if self.selected == i:
                    Color(1, 0, 0)
                    r = dp(10)
                    Ellipse(pos=(x - r / 2, y - r / 2), size=(r, r))
                    Color(0, 0, 0)
                    Line(circle=(x, y, r / 2), width=1)
                    # Pulse ring only when rover is inside target tolerance.
                    if self.nav_target_in_zone:
                        t = time.monotonic()
                        pulse = (math.sin(t * 4.0) + 1.0) * 0.5  # 0..1
                        ring_r = dp(9) + pulse * dp(7)
                        ring_alpha = 0.25 + (1.0 - pulse) * 0.45
                        Color(1.0, 0.25, 0.25, ring_alpha)
                        Line(circle=(x, y, ring_r), width=dp(1.8))
                else:
                    Color(0, 0.4, 0.8)
                    r = dp(6)
                    Ellipse(pos=(x - r / 2, y - r / 2), size=(r, r))

            if self.rover_pos:
                x_rel, y_rel = self._latlon_to_xy(self.rover_pos[1], self.rover_pos[0], w, h)
                x = self.x + x_rel
                y = self.y + y_rel
                Color(0, 1, 0)
                r = dp(12)
                Ellipse(pos=(x - r / 2, y - r / 2), size=(r, r))
                Color(0, 0, 0)
                Line(circle=(x, y, r / 2), width=2)

                if self.rover_heading_deg is not None:
                    heading_rad = math.radians(self.rover_heading_deg)
                    arrow_len = dp(26)
                    x2 = x + math.sin(heading_rad) * arrow_len
                    y2 = y + math.cos(heading_rad) * arrow_len
                    Color(0.95, 0.4, 0.0)
                    Line(points=[x, y, x2, y2], width=2)
                    head_len = dp(7)
                    left = heading_rad + math.radians(150)
                    right = heading_rad - math.radians(150)
                    x3 = x2 + math.sin(left) * head_len
                    y3 = y2 + math.cos(left) * head_len
                    x4 = x2 + math.sin(right) * head_len
                    y4 = y2 + math.cos(right) * head_len
                    Line(points=[x2, y2, x3, y3], width=2)
                    Line(points=[x2, y2, x4, y4], width=2)

                if self.nav_heading_deg is not None:
                    nav_rad = math.radians(self.nav_heading_deg)
                    nav_len = dp(34)
                    nx2 = x + math.sin(nav_rad) * nav_len
                    ny2 = y + math.cos(nav_rad) * nav_len
                    Color(0.1, 0.85, 0.25)
                    Line(points=[x, y, nx2, ny2], width=2.6)
                    nav_head_len = dp(8)
                    nav_left = nav_rad + math.radians(150)
                    nav_right = nav_rad - math.radians(150)
                    nx3 = nx2 + math.sin(nav_left) * nav_head_len
                    ny3 = ny2 + math.cos(nav_left) * nav_head_len
                    nx4 = nx2 + math.sin(nav_right) * nav_head_len
                    ny4 = ny2 + math.cos(nav_right) * nav_head_len
                    Line(points=[nx2, ny2, nx3, ny3], width=2.6)
                    Line(points=[nx2, ny2, nx4, ny4], width=2.6)
