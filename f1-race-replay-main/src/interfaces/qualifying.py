import arcade
import threading
import time
import numpy as np
from typing import Any
from src.ui_components import (
    build_track_from_example_lap,
    LapTimeLeaderboardComponent,
    QualifyingSegmentSelectorComponent,
    RaceControlsComponent,
    draw_finish_line,
    LegendComponent,
    ControlsPopupComponent,
    QualifyingLapTimeComponent,
)
from src.f1_data import get_driver_quali_telemetry
from src.f1_data import FPS
from src.lib.time import format_time

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
SCREEN_TITLE = "F1 Qualifying Telemetry"

H_ROW = 38
HEADER_H = 56
LEFT_MARGIN = 40
RIGHT_MARGIN = 40
TOP_MARGIN = 40
BOTTOM_MARGIN = 40

class QualifyingReplay(arcade.Window):
    def __init__(self, session, data, circuit_rotation=0, left_ui_margin=340, right_ui_margin=0, title="Qualifying Results"):
        super().__init__(width=SCREEN_WIDTH, height=SCREEN_HEIGHT, title=title, resizable=True)
        self.maximize()
        
        self.session = session
        self.data = data
        self.leaderboard = LapTimeLeaderboardComponent(
            x=LEFT_MARGIN,
        )
        self.race_controls_comp = RaceControlsComponent(
            center_x= self.width // 2 + 100,
            center_y= 40
        )
        self.qualifying_lap_time_comp = QualifyingLapTimeComponent()
        self.leaderboard.set_entries(self.data.get("results", []))
        self.drs_zones = []
        self.drs_zones_xy = []
        self.toggle_drs_zones = True
        self.n_frames = 0
        self.min_speed = 0.0
        self.max_speed = 0.0

        self.th_min = 0
        self.th_max = 100

        self.br_min = 0
        self.br_max = 100

        self.g_min = 0
        self.g_max = 8

        # cached arrays for fast indexing/interpolation when telemetry loaded
        self._times = None   # numpy array of frame times
        self._xs = None      # numpy array of telemetry x
        self._ys = None      # numpy array of telemetry y
        self._speeds = None  # optional cached speeds

        # Playback / animation state for the chart
        self.play_time = 0.0          # current play time (seconds)
        self.play_start_t = 0.0       # first-frame timestamp (seconds)
        self.frame_index = 0          # current frame index (int)
        self.paused = True            # start paused by default
        self.playback_speed = 1.0     # 1.0 = realtime
        self.loading_telemetry = False

        # Rotation (degrees) to apply to the whole circuit around its centre
        self.circuit_rotation = circuit_rotation
        self._rot_rad = float(np.deg2rad(self.circuit_rotation)) if self.circuit_rotation else 0.0
        self._cos_rot = float(np.cos(self._rot_rad))
        self._sin_rot = float(np.sin(self._rot_rad))
        self.left_ui_margin = left_ui_margin
        self.right_ui_margin = right_ui_margin

        self.chart_active = False
        self.show_comparison_telemetry = True
        self.selected_drivers = []
        self.corner_metric_modes = ["minimum", "entry", "apex", "exit"]
        self.corner_metric_mode_index = 0

        self.loaded_driver_code = None
        self.loaded_driver_segment = None

        # Legend + controls popup (same behavior as race replay)
        self.legend_comp = LegendComponent(x=max(12, self.left_ui_margin - 320))
        self.controls_popup_comp = ControlsPopupComponent(lines=[
            ("SPACE", "Pause/Resume"),
            ("← / →", "Jump back/forward"),
            ("↑ / ↓", "Speed +/-"),
            ("1-4", "Set speed: 0.5x / 1x / 2x / 4x"),
            ("SHIFT+Click", "Select multiple drivers"),
            ("R", "Restart"),
            ("D", "Toggle DRS Zones"),
            ("C", "Toggle Comparison Telemetry"),
            ("T", "Toggle Corner Metric"),
            ("H", "Toggle Help Popup"),
            ("Drag ⋮⋮", "Move panels freely on screen"),
            ("Y", "Reset all panel positions"),
            ("ESC", "Close Window"),
        ])
        self.controls_popup_comp.set_size(340, 280)
        self.controls_popup_comp.set_font_sizes(header_font_size=16, body_font_size=13)

        # Build the track layout from an example lap

        example_lap = None
        for res in self.data['results']:
            if res['Q3'] is not None:
                example_lap = self.session.laps.pick_drivers(res['code']).pick_fastest()
                break
            elif res['Q2'] is not None:
                example_lap = self.session.laps.pick_drivers(res['code']).pick_fastest()
                break
            elif res['Q1'] is not None:
                example_lap = self.session.laps.pick_drivers(res['code']).pick_fastest()
                break

        self.world_scale = 1.0
        self.tx = 0
        self.ty = 0

        (self.plot_x_ref, self.plot_y_ref,
         self.x_inner, self.y_inner,
         self.x_outer, self.y_outer,
         self.x_min, self.x_max,
         self.y_min, self.y_max, self.drs_zones_xy) = build_track_from_example_lap(example_lap.get_telemetry())
         
        ref_points = self._interpolate_points(self.plot_x_ref, self.plot_y_ref, interp_points=4000)
        self._ref_xs = np.array([p[0] for p in ref_points])
        self._ref_ys = np.array([p[1] for p in ref_points])

        # cumulative distances along the reference polyline (metres)
        diffs = np.sqrt(np.diff(self._ref_xs)**2 + np.diff(self._ref_ys)**2)
        self._ref_seg_len = diffs
        self._ref_cumdist = np.concatenate(([0.0], np.cumsum(diffs)))
        self._ref_total_length = float(self._ref_cumdist[-1]) if len(self._ref_cumdist) > 0 else 0.0
        self.corner_markers = self._detect_track_corners()

        # Pre-calculate interpolated world points ONCE (optimization)
        self.world_inner_points = self._interpolate_points(self.x_inner, self.y_inner)
        self.world_outer_points = self._interpolate_points(self.x_outer, self.y_outer)

        # These will hold the actual screen coordinates to draw
        self.screen_inner_points = [self.world_to_screen(x, y) for x, y in self.world_inner_points]
        self.screen_outer_points = [self.world_to_screen(x, y) for x, y in self.world_outer_points]

        # Qualifying segment selector modal
        self.selected_driver = None
        self.qualifying_segment_selector_modal = QualifyingSegmentSelectorComponent()

        arcade.set_background_color(arcade.color.BLACK)

        self.update_scaling(self.width, self.height)

        self.is_rewinding = False
        self.is_forwarding = False
        self.was_paused_before_hold = False

        # Draggable Panel System
        # Track positions of movable panels (offset from default, in pixels)
        self.panel_positions = {
            "overlay_charts": {"offset_x": 0, "offset_y": 0},  # Multi-driver overlay charts
            "heatmap": {"offset_x": 0, "offset_y": 0}           # Corner split heatmap
        }
        self.dragging_panel = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_start_panel_x = 0
        self.drag_start_panel_y = 0
        self.load_panel_positions()

    def update_scaling(self, screen_w, screen_h):
        """
        Recalculates the scale and translation to fit the track 
        perfectly within the new screen dimensions while maintaining aspect ratio.
        """
        padding = 0.05
        # If a rotation is applied, we must compute the rotated bounds
        world_cx = (self.x_min + self.x_max) / 2
        world_cy = (self.y_min + self.y_max) / 2

        def _rotate_about_center(x, y):
            # Translate to centre, rotate, translate back
            tx = x - world_cx
            ty = y - world_cy
            rx = tx * self._cos_rot - ty * self._sin_rot
            ry = tx * self._sin_rot + ty * self._cos_rot
            return rx + world_cx, ry + world_cy

        # Build rotated extents from inner/outer world points
        rotated_points = []
        for x, y in self.world_inner_points:
            rotated_points.append(_rotate_about_center(x, y))
        for x, y in self.world_outer_points:
            rotated_points.append(_rotate_about_center(x, y))

        xs = [p[0] for p in rotated_points]
        ys = [p[1] for p in rotated_points]
        world_x_min = min(xs) if xs else self.x_min
        world_x_max = max(xs) if xs else self.x_max
        world_y_min = min(ys) if ys else self.y_min
        world_y_max = max(ys) if ys else self.y_max

        world_w = max(1.0, world_x_max - world_x_min)
        world_h = max(1.0, world_y_max - world_y_min)
        
        # Reserve left/right UI margins before applying padding so the track
        # never overlaps side UI elements (leaderboard, telemetry, legends).
        inner_w = max(1.0, screen_w - self.left_ui_margin - self.right_ui_margin)
        usable_w = inner_w * (1 - 2 * padding)
        usable_h = screen_h * (1 - 2 * padding)

        # Calculate scale to fit whichever dimension is the limiting factor
        scale_x = usable_w / world_w
        scale_y = usable_h / world_h
        self.world_scale = min(scale_x, scale_y)

        # Center the world in the screen (rotation done about original centre)
        # world_cx/world_cy are unchanged by rotation about centre
        # Center within the available inner area (left_ui_margin .. screen_w - right_ui_margin)
        screen_cx = self.left_ui_margin + inner_w / 2
        screen_cy = screen_h / 2

        self.tx = screen_cx - self.world_scale * world_cx
        self.ty = screen_cy - self.world_scale * world_cy

        # Update the polyline screen coordinates based on new scale
        self.screen_inner_points = [self.world_to_screen(x, y) for x, y in self.world_inner_points]
        self.screen_outer_points = [self.world_to_screen(x, y) for x, y in self.world_outer_points]

    def on_draw(self):
        self.clear()

        # Draw simple line chart if telemetry is loaded
        if self.chart_active and self.loaded_telemetry:
            frames = self.loaded_telemetry.get("frames") if isinstance(self.loaded_telemetry, dict) else None
            if frames:
                fastest_driver = self.data.get("results", [])[0] if isinstance(self.data.get("results", []), list) and len(self.data.get("results", [])) > 0 else None
                comparison_data = self.data.get("telemetry", {}).get(fastest_driver.get("code")) if fastest_driver and self.show_comparison_telemetry else None

                active_series = self._build_active_driver_series(fastest_driver)
                if not active_series:
                    return

                # right-hand area (to the right of leaderboard)
                area_left = self.leaderboard.x + getattr(self.leaderboard, "width", 240) + 40
                area_right = self.width - RIGHT_MARGIN
                area_top = self.height - TOP_MARGIN
                area_bottom = BOTTOM_MARGIN
                area_w = max(10, area_right - area_left)
                area_h = max(10, area_top - area_bottom)

                # Split vertically: top half = chart, bottom half = circuit map
                top_half_h = int(area_h * 0.5)
                chart_top = area_top
                chart_bottom = area_top - top_half_h
                chart_left = area_left
                chart_right = area_right
                chart_w = max(10, chart_right - chart_left)
                chart_h = max(10, chart_top - chart_bottom)

                # Divide chart area into 3 sub-areas:
                # - Top 50% of the chart area: Speed
                # - Next 25%: Gears
                # - Bottom 25%: Brake + Throttle

                M = 30 # margin between charts
                VP = 5 # vertical padding between charts
                total_margin = 2 * M
                effective_h = max(0, chart_h - total_margin)

                speed_h = int(effective_h * 0.5)
                gear_h = int(effective_h * 0.25)
                ctrl_h = effective_h - speed_h - gear_h

                speed_top = chart_top
                speed_bottom = speed_top - speed_h
                gear_top = speed_bottom - M
                gear_bottom = gear_top - gear_h
                ctrl_top = gear_bottom - M
                ctrl_bottom = ctrl_top - ctrl_h

                map_top = ctrl_bottom - 8
                map_bottom = area_bottom
                map_left = area_left
                map_right = area_right
                map_w = max(10, map_right - map_left)
                map_h = max(10, map_top - map_bottom)

                # Backgrounds for the charts

                speed_bg = arcade.XYWH(chart_left + chart_w * 0.5, speed_bottom + speed_h * 0.5, chart_w, speed_h)
                gear_bg = arcade.XYWH(chart_left + chart_w * 0.5, gear_bottom + gear_h * 0.5, chart_w, gear_h)
                ctrl_bg = arcade.XYWH(chart_left + chart_w * 0.5, ctrl_bottom + ctrl_h * 0.5, chart_w, ctrl_h)

                arcade.draw_rect_filled(speed_bg, (40, 40, 40, 230))
                arcade.draw_rect_filled(gear_bg, (40, 40, 40, 230))
                arcade.draw_rect_filled(ctrl_bg, (40, 40, 40, 230))

                # Add Subtitles to the charts

                arcade.Text("Speed (km/h)", chart_left + 10, speed_top + 10, arcade.color.ANTI_FLASH_WHITE, 14).draw()
                arcade.Text("Gear", chart_left + 10, gear_top + 10, arcade.color.ANTI_FLASH_WHITE, 14).draw()
                arcade.Text("Throttle / Brake (%)", chart_left + 10, ctrl_top + 10, arcade.color.ANTI_FLASH_WHITE, 14).draw()

                # DRS key at right of the speed subtitle (green square + label)
                key_size = 12
                key_padding_right = 110
                # Align vertically with the subtitle (use same y offset, center the square)
                key_y = speed_top + 10 + (key_size * 0.5)
                square_x = chart_right - key_padding_right - (key_size / 2)

                drs_key_rect = arcade.XYWH(square_x, key_y, key_size, key_size)
                arcade.draw_rect_filled(drs_key_rect, arcade.color.GREEN)
                arcade.Text(
                    "DRS active",
                    square_x + (key_size * 0.5) + 6,
                    key_y,
                    arcade.color.ANTI_FLASH_WHITE,
                    12,
                    anchor_y="center"
                ).draw()

                # compute global ranges from all frames (use distance for x-axis) - Should be max of 1.0 rel_dist, but just in case

                all_dists = [ self._pick_telemetry_value(f.get("telemetry", {}), "rel_dist") for f in frames ]
                
                # filter out None
                all_dists = [d for d in all_dists if d is not None]
                if not all_dists:
                    return

                full_d_min, full_d_max = min(all_dists), max(all_dists)
                full_s_min, full_s_max = self.min_speed, self.max_speed

                # avoid zero-range
                if full_d_max == full_d_min:
                    full_d_max = full_d_min + 1.0
                if full_s_max == full_s_min:
                    full_s_max = full_s_min + 1.0

                # Prepare arrays for drawing up to current frame index (animate)
                self.frame_index = max(0, min(self.frame_index, len(frames) - 1))
                # The speed chart background will have sections of it shaded green to indicate where DRS was active

                # find the drs zones for this lap that the driver has already passed.
                # If they have partially passed a zone, shade up to their current distance only.

                drs_zones_to_show = []

                current_frame = frames[self.frame_index]
                current_tel = current_frame.get("telemetry", {}) if isinstance(current_frame.get("telemetry", {}), dict) else {}
                current_dist = self._pick_telemetry_value(current_tel, "dist")
                
                for dz in self.drs_zones:
                    zone_start = dz.get("zone_start")
                    zone_end = dz.get("zone_end")
                    if zone_start is None or zone_end is None:
                        continue
                    if current_dist >= zone_start:
                        # driver has passed at least the start of this zone
                        shade_end = min(zone_end, current_dist)
                        drs_zones_to_show.append({
                            "zone_start": zone_start,
                            "zone_end": shade_end
                        })

                for dz in drs_zones_to_show:
                    # Convert to float to handle string values
                    try:
                        zone_start = float(dz['zone_start'])
                        shade_end = float(dz['zone_end'])
                    except (ValueError, TypeError):
                        continue  # Skip invalid zones
                    
                    # Get the full distance range from all frames
                    all_abs_dists = [self._pick_telemetry_value(f.get("telemetry", {}), "dist") for f in frames]
                    all_abs_dists = [d for d in all_abs_dists if d is not None]
                    if not all_abs_dists:
                        continue
                    
                    full_abs_d_min, full_abs_d_max = min(all_abs_dists), max(all_abs_dists)
                    if full_abs_d_max == full_abs_d_min:
                        continue
                    
                    # map to screen coords using absolute distances
                    nx1 = (zone_start - full_abs_d_min) / (full_abs_d_max - full_abs_d_min)
                    nx2 = (shade_end - full_abs_d_min) / (full_abs_d_max - full_abs_d_min)
                    x1pix = chart_left + nx1 * chart_w
                    x2pix = chart_left + nx2 * chart_w
                    drs_rect = arcade.XYWH((x1pix + x2pix) * 0.5, speed_bottom + speed_h * 0.5, x2pix - x1pix, speed_h)
                    arcade.draw_rect_filled(drs_rect, (0, 100, 0, 100)) # semi-transparent green

                # Draw multi-driver overlays (speed/gear/throttle/brake) in shared charts.
                self._draw_multi_driver_overlay_charts(
                    active_series=active_series,
                    chart_left=chart_left,
                    chart_w=chart_w,
                    speed_bottom=speed_bottom,
                    speed_h=speed_h,
                    gear_bottom=gear_bottom,
                    gear_h=gear_h,
                    ctrl_bottom=ctrl_bottom,
                    ctrl_h=ctrl_h,
                    vp=VP,
                    full_d_min=full_d_min,
                    full_d_max=full_d_max,
                    full_s_min=full_s_min,
                    full_s_max=full_s_max,
                    chart_right=chart_right,
                    speed_top=speed_top,
                )
                
                # Draw qualifying lap time component at top of map area
                self.qualifying_lap_time_comp.x = map_left
                self.qualifying_lap_time_comp.y = map_top
                self.qualifying_lap_time_comp.fastest_driver = fastest_driver
                self.qualifying_lap_time_comp.fastest_driver_sector_times = comparison_data.get("Q3").get("sector_times", {}) if comparison_data and self.show_comparison_telemetry and fastest_driver and ((fastest_driver.get("code") != self.loaded_driver_code) or (fastest_driver.get("code") == self.loaded_driver_code and self.loaded_driver_segment != "Q3")) else None
                self.qualifying_lap_time_comp.draw(self)

                side_panel_x = max(map_left + 280, map_right - 450)
                self._draw_multi_driver_comparison_panel(side_panel_x, map_top - 5)
                self._draw_corner_split_panel(side_panel_x, map_top - 210)

                y_offset = map_top - 48
                arcade.Text(f"Playback Speed: {self.playback_speed:.1f}x", map_left + 10, y_offset - 130, arcade.color.ANTI_FLASH_WHITE, 14).draw()

                # Legends
                legend_x = chart_right - 100
                legend_y = ctrl_top - int(ctrl_h * 0.2)

                # Draw circuit map in bottom half (fit inner/outer polylines into map area)
                if getattr(self, "x_min", None) is not None and getattr(self, "x_max", None) is not None:
                    world_x_min = float(self.x_min)
                    world_x_max = float(self.x_max)
                    world_y_min = float(self.y_min)
                    world_y_max = float(self.y_max)

                    world_w = max(1.0, world_x_max - world_x_min)
                    world_h = max(1.0, world_y_max - world_y_min)

                    pad = 0.06
                    usable_w = map_w * (1 - 2 * pad)
                    usable_h = map_h * (1 - 2 * pad)

                    scale_x = usable_w / world_w
                    scale_y = usable_h / world_h
                    world_scale = min(scale_x, scale_y)

                    world_cx = (world_x_min + world_x_max) / 2
                    world_cy = (world_y_min + world_y_max) / 2

                    screen_cx = map_left + map_w / 2
                    screen_cy = map_bottom + map_h / 2

                    tx = screen_cx - world_scale * world_cx
                    ty = screen_cy - world_scale * world_cy

                    def world_to_map(x, y):
                        sx = world_scale * x + tx
                        sy = world_scale * y + ty
                        return sx, sy

                    # Use the interpolated world points if available, fallback to raw arrays
                    inner_world = getattr(self, "world_inner_points", None) or list(zip(self.x_inner, self.y_inner))
                    outer_world = getattr(self, "world_outer_points", None) or list(zip(self.x_outer, self.y_outer))

                    self.inner_pts = [world_to_map(x, y) for x, y in inner_world if x is not None and y is not None]
                    self.outer_pts = [world_to_map(x, y) for x, y in outer_world if x is not None and y is not None]
                    try:
                        if len(self.inner_pts) > 1:
                            arcade.draw_line_strip(self.inner_pts, arcade.color.GRAY, 2)
                        if len(self.outer_pts) > 1:
                            arcade.draw_line_strip(self.outer_pts, arcade.color.GRAY, 2)
                        draw_finish_line(self, 'Q')
                    except Exception as e:
                        print("Circuit draw error:", e)

                    # Draw additional compared drivers on track first so primary stays on top.
                    for comp in active_series[1:]:
                        comp_frames = comp.get("frames", [])
                        if not comp_frames:
                            continue
                        c_idx = min(self.frame_index, len(comp_frames) - 1)
                        comp_frame = comp_frames[c_idx]
                        comp_tel = comp_frame.get("telemetry", {}) if isinstance(comp_frame.get("telemetry", {}), dict) else {}
                        c_px = comp_tel.get("x")
                        c_py = comp_tel.get("y")
                        if c_px is None or c_py is None:
                            continue
                        c_sx, c_sy = world_to_map(c_px, c_py)
                        comp_color = tuple(comp.get("color", arcade.color.YELLOW))
                        arcade.draw_circle_filled(c_sx, c_sy, 5, comp_color)
                        arcade.Text(comp.get("code", ""), c_sx + 8, c_sy + 3, arcade.color.WHITE, 10).draw()

                    # Draw DRS zones on track map as green highlights
                    if self.drs_zones_xy and self.toggle_drs_zones:
                        drs_color = (0, 255, 0)
                        original_length = len(self.x_inner)
                        # Interpolated world points length
                        interpolated_length = len(inner_world)
                        
                        for dz in self.drs_zones_xy:
                            orig_start_idx = dz["start"]["index"]
                            orig_end_idx = dz["end"]["index"]

                            if orig_start_idx is None or orig_end_idx is None:
                                continue
                            try:
                                # Map original indices to interpolated array indices
                                interp_start_idx = int((orig_start_idx / original_length) * interpolated_length)
                                interp_end_idx = int((orig_end_idx / original_length) * interpolated_length)
                                
                                # Clamp to valid range
                                interp_start_idx = max(0, min(interp_start_idx, interpolated_length - 1))
                                interp_end_idx = max(0, min(interp_end_idx, interpolated_length - 1))
                                
                                if interp_start_idx < interp_end_idx:
                                    # Extract segments for this DRS zone using mapped indices
                                    outer_zone = [world_to_map(x, y) for x, y in outer_world[interp_start_idx:interp_end_idx+1] 
                                                  if x is not None and y is not None]
                                    if len(outer_zone) > 1:
                                        arcade.draw_line_strip(outer_zone, drs_color, 3)

                            except Exception as e:
                                print(f"DRS zone draw error: {e}")

                    # Draw current driver's position marker (sync with frame_index)
                    current_frame = frames[self.frame_index]
                    tel = current_frame.get("telemetry", {}) if isinstance(current_frame.get("telemetry", {}), dict) else {}
                    px = tel.get("x")
                    py = tel.get("y")
                    sx, sy = world_to_map(px, py)
                    # driver colour lookup (fallback to white)
                    drv_color = (255, 255, 255)
                    if getattr(self, "loaded_driver_code", None):
                        for r in self.data.get("results", []):
                            if r.get("code") == self.loaded_driver_code and r.get("color"):
                                drv_color = tuple(r.get("color"))
                                break
                    arcade.draw_circle_filled(sx, sy, 6, drv_color)

                    # Overlay current gear near the position marker on the track
                    cur_gear = tel.get("gear") or tel.get("nGear") or tel.get("Gear")
                    if cur_gear is None:
                        cur_gear = None
                    arcade.Text(self.loaded_driver_code or "", sx + 10, sy + 4, arcade.color.WHITE, 12).draw()
                    if cur_gear is not None:
                        arcade.Text(f"G:{int(cur_gear)}", sx + 10, sy - 10, arcade.color.LIGHT_GRAY, 12).draw()

        else:
            # Add "click a driver to view their qualifying lap" text in the center of the chart area

            info_text = "Click a driver on the left to load their qualifying lap telemetry."
            arcade.Text(
                info_text,
                self.width / 2, self.height / 2,
                arcade.color.LIGHT_GRAY, 18,
                anchor_x="center", anchor_y="center"
            ).draw()

        self.leaderboard.draw(self)
        self.qualifying_segment_selector_modal.draw(self)

        # Controls Legend - Bottom Left (keeps small offset from left UI edge)
        self.legend_comp.x = max(12, self.left_ui_margin - 320) if hasattr(self, "left_ui_margin") else 20
        self.legend_comp.draw(self)
        
        # Show race controls only when telemetry is loaded (driver + session selected)
        if self.chart_active and self.loaded_telemetry and self.frame_index < self.n_frames:
            self.race_controls_comp.draw(self)
        # Controls popup (Help)
        self.controls_popup_comp.draw(self)

    def on_mouse_motion(self, x: int, y: int, dx: int, dy: int):
        """Pass mouse motion events to UI components and handle panel dragging."""
        # Update panel position if dragging
        if self.dragging_panel:
            delta_x = x - self.drag_start_x
            delta_y = y - self.drag_start_y
            
            new_offset_x = self.drag_start_panel_x + delta_x
            new_offset_y = self.drag_start_panel_y + delta_y
            
            self.panel_positions[self.dragging_panel]["offset_x"] = new_offset_x
            self.panel_positions[self.dragging_panel]["offset_y"] = new_offset_y
            return

        self.race_controls_comp.on_mouse_motion(self, x, y, dx, dy)
    
    def on_resize(self, width: int, height: int):
        """Handle the window being resized."""
        super().on_resize(width, height)
        self.update_scaling(width, height)
        self.race_controls_comp.on_resize(self)

    def _interpolate_points(self, xs, ys, interp_points=2000):
        t_old = np.linspace(0, 1, len(xs))
        t_new = np.linspace(0, 1, interp_points)
        xs_i = np.interp(t_new, t_old, xs)
        ys_i = np.interp(t_new, t_old, ys)
        return list(zip(xs_i, ys_i))

    def load_panel_positions(self):
        """Load panel positions from config file."""
        import json
        import os
        config_path = "panel_positions.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    saved_positions = json.load(f)
                    if isinstance(saved_positions, dict):
                        self.panel_positions.update(saved_positions)
                        print(f"✓ Loaded panel positions from {config_path}")
            except Exception as e:
                print(f"Failed to load panel positions: {e}")

    def save_panel_positions(self):
        """Save current panel positions to config file."""
        import json
        config_path = "panel_positions.json"
        try:
            with open(config_path, 'w') as f:
                json.dump(self.panel_positions, f, indent=2)
                print(f"✓ Saved panel positions to {config_path}")
        except Exception as e:
            print(f"Failed to save panel positions: {e}")

    def world_to_screen(self, x, y):
        # Rotate around the track centre (if rotation is set), then scale+translate
        world_cx = (self.x_min + self.x_max) / 2
        world_cy = (self.y_min + self.y_max) / 2

        if self._rot_rad:
            tx = x - world_cx
            ty = y - world_cy
            rx = tx * self._cos_rot - ty * self._sin_rot
            ry = tx * self._sin_rot + ty * self._cos_rot
            x, y = rx + world_cx, ry + world_cy

        sx = self.world_scale * x + self.tx
        sy = self.world_scale * y + self.ty
        return sx, sy

    def _pick_telemetry_value(self, tel: dict, *keys):
        """Return the first value for keys that exists in tel and is not None.
        Preserves falsy-but-valid values like 0.0."""
        if not isinstance(tel, dict):
            return None
        for k in keys:
            if k in tel and tel[k] is not None:
                return tel[k]
        return None

    def _driver_color(self, code: str) -> tuple[int, int, int]:
        for r in self.data.get("results", []):
            if r.get("code") == code and r.get("color"):
                c = tuple(r.get("color"))
                return (int(c[0]), int(c[1]), int(c[2]))
        return (220, 220, 220)

    def _resolve_segment_frames(self, code: str, preferred_segment: str) -> list[dict[str, Any]]:
        telemetry_store = self.data.get("telemetry") if isinstance(self.data, dict) else None
        if not isinstance(telemetry_store, dict):
            return []
        block = telemetry_store.get(code, {}) if isinstance(telemetry_store.get(code, {}), dict) else {}
        seg = block.get(preferred_segment) if isinstance(block, dict) else None
        if isinstance(seg, dict) and seg.get("frames"):
            return seg.get("frames") or []
        for fallback in ("Q3", "Q2", "Q1"):
            cand = block.get(fallback) if isinstance(block, dict) else None
            if isinstance(cand, dict) and cand.get("frames"):
                return cand.get("frames") or []
        return []

    def _build_active_driver_series(self, fastest_driver: dict[str, Any] | None) -> list[dict[str, Any]]:
        preferred = self.loaded_driver_segment or "Q3"
        active_codes: list[str] = []
        if self.loaded_driver_code:
            active_codes.append(self.loaded_driver_code)
        for code in self.selected_drivers:
            if code and code not in active_codes:
                active_codes.append(code)
        # Optional auto comparison with fastest driver if only one selected.
        if self.show_comparison_telemetry and len(active_codes) < 2 and fastest_driver:
            fast_code = fastest_driver.get("code")
            if fast_code and fast_code not in active_codes:
                active_codes.append(fast_code)

        series: list[dict[str, Any]] = []
        for code in active_codes[:6]:
            s_frames = self._resolve_segment_frames(code, preferred)
            if not s_frames:
                continue
            series.append({
                "code": code,
                "frames": s_frames,
                "color": self._driver_color(code),
            })
        return series

    @staticmethod
    def _darken_color(color: tuple[int, int, int], amount: int = 90) -> tuple[int, int, int]:
        return (
            max(0, color[0] - amount),
            max(0, color[1] - amount),
            max(0, color[2] - amount),
        )

    def _draw_multi_driver_overlay_charts(
        self,
        *,
        active_series: list[dict[str, Any]],
        chart_left: float,
        chart_w: float,
        speed_bottom: float,
        speed_h: float,
        gear_bottom: float,
        gear_h: float,
        ctrl_bottom: float,
        ctrl_h: float,
        vp: float,
        full_d_min: float,
        full_d_max: float,
        full_s_min: float,
        full_s_max: float,
        chart_right: float,
        speed_top: float,
    ) -> None:
        """Draw overlays for selected drivers in speed/gear/throttle-brake charts."""
        def to_x(d: float) -> float:
            nx = (d - full_d_min) / (full_d_max - full_d_min)
            return chart_left + nx * chart_w

        legend_entries: list[tuple[str, tuple[int, int, int], float, int, float, float]] = []

        for i, s in enumerate(active_series):
            code = s["code"]
            color = tuple(s["color"])
            frames = s["frames"]
            idx = min(self.frame_index, len(frames) - 1)

            pos = []
            speeds = []
            throttle = []
            brake = []
            gears = []

            for f in frames[:idx + 1]:
                tel = f.get("telemetry", {}) if isinstance(f, dict) else {}
                d = self._pick_telemetry_value(tel, "rel_dist")
                spd = self._pick_telemetry_value(tel, "speed")
                if d is None or spd is None:
                    continue
                th = self._pick_telemetry_value(tel, "throttle")
                br = self._pick_telemetry_value(tel, "brake")
                gr = self._pick_telemetry_value(tel, "gear", "nGear", "Gear")

                pos.append(float(d))
                speeds.append(float(spd))
                throttle.append(float(th) if th is not None else None)
                if isinstance(br, (bool, int)):
                    brake.append(1.0 if br else 0.0)
                else:
                    brake.append(float(br) if br is not None else None)
                gears.append(int(gr) if gr is not None else None)

            if not pos:
                continue

            # Speed
            speed_pts = []
            for d, spd in zip(pos, speeds):
                xpix = to_x(d)
                ny = (spd - full_s_min) / (full_s_max - full_s_min)
                ypix = speed_bottom + vp + ny * (speed_h - 2 * vp)
                speed_pts.append((xpix, ypix))
            if len(speed_pts) > 1:
                arcade.draw_line_strip(speed_pts, color, 2)
                end_x, end_y = speed_pts[-1]
                y_off = (i - (len(active_series) - 1) / 2.0) * 12
                arcade.Text(
                    f"{code} {speeds[-1]:.0f}",
                    min(chart_right - 72, end_x + 8),
                    end_y + y_off,
                    color,
                    9,
                    bold=True,
                ).draw()

            # Gear
            gear_pts = []
            for d, g in zip(pos, gears):
                if g is None:
                    continue
                xpix = to_x(d)
                gy = (g - self.g_min) / (self.g_max - self.g_min)
                ypix = gear_bottom + vp + gy * (gear_h - 2 * vp)
                gear_pts.append((xpix, ypix))
            if len(gear_pts) > 1:
                arcade.draw_line_strip(gear_pts, color, 2)
                g_end_x, g_end_y = gear_pts[-1]
                y_off = (i - (len(active_series) - 1) / 2.0) * 10
                last_gear = gears[-1] if gears and gears[-1] is not None else 0
                arcade.Text(
                    f"{code} G{int(last_gear)}",
                    min(chart_right - 90, g_end_x + 8),
                    g_end_y + y_off,
                    color,
                    9,
                    bold=True,
                ).draw()

            # Throttle / Brake
            th_pts = []
            br_pts = []
            for d, th, br in zip(pos, throttle, brake):
                xpix = to_x(d)
                if th is not None:
                    ny = (th - self.th_min) / (self.th_max - self.th_min)
                    th_pts.append((xpix, ctrl_bottom + vp + ny * (ctrl_h - 2 * vp)))
                if br is not None:
                    ny = (br - self.br_min) / (self.br_max - self.br_min)
                    br_pts.append((xpix, ctrl_bottom + vp + ny * (ctrl_h - 2 * vp)))
            if len(th_pts) > 1:
                arcade.draw_line_strip(th_pts, color, 2)
            if len(br_pts) > 1:
                arcade.draw_line_strip(br_pts, self._darken_color(color), 2)

            if th_pts:
                t_end_x, t_end_y = th_pts[-1]
                y_off = (i - (len(active_series) - 1) / 2.0) * 10
                t_val = throttle[-1] if throttle and throttle[-1] is not None else 0.0
                b_val = brake[-1] if brake and brake[-1] is not None else 0.0
                arcade.Text(
                    f"{code} T{t_val:.0f}/B{b_val:.0f}",
                    min(chart_right - 140, t_end_x + 8),
                    t_end_y + y_off,
                    color,
                    9,
                ).draw()

            latest_tel = frames[idx].get("telemetry", {}) if isinstance(frames[idx], dict) else {}
            legend_entries.append(
                (
                    code,
                    color,
                    float(self._pick_telemetry_value(latest_tel, "speed") or 0.0),
                    int(self._pick_telemetry_value(latest_tel, "gear", "nGear", "Gear") or 0),
                    float(self._pick_telemetry_value(latest_tel, "throttle") or 0.0),
                    float(self._pick_telemetry_value(latest_tel, "brake") or 0.0),
                )
            )

        # Dedicated legend block so chart lines and labels do not overlap.
        if legend_entries:
            lg_w = 360
            lg_h = 22 + 18 * len(legend_entries)
            lg_left = chart_right - lg_w - 8
            lg_top = speed_top - 2
            lg_rect = arcade.XYWH(lg_left + lg_w / 2, lg_top - lg_h / 2, lg_w, lg_h)
            arcade.draw_rect_filled(lg_rect, (15, 15, 15, 215))
            arcade.draw_rect_outline(lg_rect, arcade.color.GRAY, 1)
            arcade.Text("Multi-driver overlay", lg_left + 8, lg_top - 14, arcade.color.ANTI_FLASH_WHITE, 10, bold=True).draw()
            y = lg_top - 30
            for code, color, spd, gear, th, br in legend_entries:
                arcade.draw_line(lg_left + 8, y + 6, lg_left + 24, y + 6, color, 3)
                arcade.draw_line(lg_left + 28, y + 6, lg_left + 42, y + 6, self._darken_color(color), 3)
                arcade.Text(
                    f"{code}  S:{spd:>3.0f}  G:{gear}  TH:{th:>3.0f}%  BR:{br:>3.0f}%",
                    lg_left + 48,
                    y,
                    arcade.color.WHITE,
                    10,
                ).draw()
                y -= 18

    def _draw_multi_driver_comparison_panel(self, left: float, top: float) -> None:
        """Draw side-by-side telemetry comparison for 2+ selected drivers."""
        telemetry_store = self.data.get("telemetry") if isinstance(self.data, dict) else None
        if not isinstance(telemetry_store, dict):
            return

        compare_codes: list[str] = []
        if self.loaded_driver_code:
            compare_codes.append(self.loaded_driver_code)

        for code in getattr(self, "selected_drivers", []) or []:
            if code and code not in compare_codes:
                compare_codes.append(code)

        if len(compare_codes) < 2:
            return

        segment = self.loaded_driver_segment or "Q3"
        rows: list[dict[str, Any]] = []
        for code in compare_codes[:6]:
            driver_block = telemetry_store.get(code, {}) if isinstance(telemetry_store.get(code, {}), dict) else {}
            seg_block = driver_block.get(segment) if isinstance(driver_block, dict) else None

            if not isinstance(seg_block, dict) or not seg_block.get("frames"):
                for fallback in ("Q3", "Q2", "Q1"):
                    cand = driver_block.get(fallback) if isinstance(driver_block, dict) else None
                    if isinstance(cand, dict) and cand.get("frames"):
                        seg_block = cand
                        break

            if not isinstance(seg_block, dict):
                continue

            frames = seg_block.get("frames") or []
            if not frames:
                continue

            idx = min(self.frame_index, len(frames) - 1)
            tel = frames[idx].get("telemetry", {}) if isinstance(frames[idx], dict) else {}
            speed = self._pick_telemetry_value(tel, "speed")
            gear = self._pick_telemetry_value(tel, "gear", "nGear", "Gear")
            throttle = self._pick_telemetry_value(tel, "throttle")
            brake = self._pick_telemetry_value(tel, "brake")
            rel_dist = self._pick_telemetry_value(tel, "rel_dist")

            color = arcade.color.WHITE
            for r in self.data.get("results", []):
                if r.get("code") == code and r.get("color"):
                    color = tuple(r.get("color"))
                    break

            rows.append({
                "code": code,
                "speed": float(speed) if speed is not None else 0.0,
                "gear": int(gear) if gear is not None else 0,
                "throttle": float(throttle) if throttle is not None else 0.0,
                "brake": float(brake) if brake is not None else 0.0,
                "rel_dist": float(rel_dist) if rel_dist is not None else 0.0,
                "color": color,
            })

        if len(rows) < 2:
            return

        rows.sort(key=lambda x: x["speed"], reverse=True)
        fastest = rows[0]["speed"] if rows else 0.0

        panel_w = 430
        row_h = 24
        panel_h = 36 + row_h * (len(rows) + 1)
        cx = left + panel_w / 2
        cy = top - panel_h / 2
        panel_rect = arcade.XYWH(cx, cy, panel_w, panel_h)
        arcade.draw_rect_filled(panel_rect, (10, 10, 10, 220))
        arcade.draw_rect_outline(panel_rect, arcade.color.GRAY, 1)

        arcade.Text("Multi-Driver Turn Compare (Shift+Click leaderboard)", left + 10, top - 10, arcade.color.ANTI_FLASH_WHITE, 12, bold=True).draw()
        arcade.Text("DRV   SPEED   ΔFAST   GEAR   THR   BRK   DIST%", left + 10, top - 32, arcade.color.LIGHT_GRAY, 10).draw()

        y = top - 54
        for row in rows:
            delta = row["speed"] - fastest
            delta_text = f"{delta:+.1f}"
            line = (
                f"{row['code']:>3}   "
                f"{row['speed']:>5.0f}   "
                f"{delta_text:>6}   "
                f"{row['gear']:>4}   "
                f"{row['throttle']:>3.0f}%   "
                f"{row['brake']:>3.0f}%   "
                f"{row['rel_dist']*100:>5.1f}"
            )
            arcade.Text(line, left + 10, y, row["color"], 10).draw()
            y -= row_h

    def _detect_track_corners(self) -> list[dict[str, float | int]]:
        """Detect turn apexes from reference line curvature and return corner markers."""
        if len(self._ref_xs) < 30:
            return []

        step = 6
        min_sep = 80
        curvatures: list[float] = [0.0] * len(self._ref_xs)

        for i in range(step, len(self._ref_xs) - step):
            x0, y0 = self._ref_xs[i - step], self._ref_ys[i - step]
            x1, y1 = self._ref_xs[i], self._ref_ys[i]
            x2, y2 = self._ref_xs[i + step], self._ref_ys[i + step]

            v1 = np.array([x1 - x0, y1 - y0], dtype=float)
            v2 = np.array([x2 - x1, y2 - y1], dtype=float)
            n1 = np.linalg.norm(v1)
            n2 = np.linalg.norm(v2)
            if n1 <= 1e-6 or n2 <= 1e-6:
                continue
            cosang = float(np.dot(v1, v2) / (n1 * n2))
            cosang = max(-1.0, min(1.0, cosang))
            curvatures[i] = abs(np.arccos(cosang))

        curv_arr = np.array(curvatures)
        non_zero = curv_arr[curv_arr > 0]
        if non_zero.size == 0:
            return []
        threshold = float(np.percentile(non_zero, 82))

        candidate_idx = [i for i, c in enumerate(curvatures) if c >= threshold]
        candidate_idx.sort(key=lambda i: curvatures[i], reverse=True)

        selected: list[int] = []
        for idx in candidate_idx:
            if all(min(abs(idx - s), len(self._ref_xs) - abs(idx - s)) >= min_sep for s in selected):
                selected.append(idx)

        selected.sort()
        corners: list[dict[str, float | int]] = []
        for n, idx in enumerate(selected, start=1):
            apex_rel = float(self._ref_cumdist[idx] / self._ref_total_length) if self._ref_total_length > 0 else 0.0
            corners.append({"turn": n, "idx": idx, "apex_rel": apex_rel})
        return corners

    @staticmethod
    def _rel_in_window(rel: float, center: float, half: float) -> bool:
        """Check circular [0..1] window membership around a center point."""
        low = center - half
        high = center + half
        if low < 0:
            return rel >= (1.0 + low) or rel <= high
        if high > 1:
            return rel >= low or rel <= (high - 1.0)
        return low <= rel <= high

    def _corner_metric_from_frames(self, frames: list[dict[str, Any]], apex_rel: float) -> float | None:
        """Compute selected corner metric around an apex location."""
        mode = self.corner_metric_modes[self.corner_metric_mode_index]
        speeds: list[tuple[float, float]] = []  # (rel_dist, speed)
        for fr in frames:
            tel = fr.get("telemetry", {}) if isinstance(fr, dict) else {}
            rel = self._pick_telemetry_value(tel, "rel_dist")
            spd = self._pick_telemetry_value(tel, "speed")
            if rel is None or spd is None:
                continue
            rel_f = float(rel)
            if rel_f > 1.0:
                rel_f = rel_f / 100.0 if rel_f <= 100.0 else rel_f % 1.0
            speeds.append((rel_f, float(spd)))

        if not speeds:
            return None

        def _circular_delta(a: float, b: float) -> float:
            d = abs(a - b)
            return min(d, 1.0 - d)

        if mode == "minimum":
            vals = [s for r, s in speeds if self._rel_in_window(r, apex_rel, 0.018)]
            return min(vals) if vals else None

        if mode == "apex":
            best = min(speeds, key=lambda rs: _circular_delta(rs[0], apex_rel))
            return best[1]

        if mode == "entry":
            center = (apex_rel - 0.025) % 1.0
            vals = [s for r, s in speeds if self._rel_in_window(r, center, 0.010)]
            return float(np.mean(vals)) if vals else None

        # exit
        center = (apex_rel + 0.025) % 1.0
        vals = [s for r, s in speeds if self._rel_in_window(r, center, 0.010)]
        return float(np.mean(vals)) if vals else None

    def _draw_corner_split_panel(self, left: float, top: float) -> None:
        """Draw true per-corner split view for selected qualifying drivers."""
        if not self.corner_markers:
            return

        telemetry_store = self.data.get("telemetry") if isinstance(self.data, dict) else None
        if not isinstance(telemetry_store, dict):
            return

        compare_codes: list[str] = []
        if self.loaded_driver_code:
            compare_codes.append(self.loaded_driver_code)
        for code in getattr(self, "selected_drivers", []) or []:
            if code and code not in compare_codes:
                compare_codes.append(code)

        if len(compare_codes) < 2:
            return

        # Apply stored offset
        offset = self.panel_positions.get("heatmap", {})
        left += offset.get("offset_x", 0)
        top += offset.get("offset_y", 0)

        segment = self.loaded_driver_segment or "Q3"
        driver_frames: dict[str, list[dict[str, Any]]] = {}
        driver_color: dict[str, tuple[int, int, int]] = {}

        for code in compare_codes[:5]:
            block = telemetry_store.get(code, {}) if isinstance(telemetry_store.get(code, {}), dict) else {}
            seg = block.get(segment) if isinstance(block, dict) else None
            if not isinstance(seg, dict) or not seg.get("frames"):
                for fallback in ("Q3", "Q2", "Q1"):
                    cand = block.get(fallback) if isinstance(block, dict) else None
                    if isinstance(cand, dict) and cand.get("frames"):
                        seg = cand
                        break
            if not isinstance(seg, dict):
                continue

            frames = seg.get("frames") or []
            if not frames:
                continue
            driver_frames[code] = frames

            clr = arcade.color.WHITE
            for r in self.data.get("results", []):
                if r.get("code") == code and r.get("color"):
                    clr = tuple(r.get("color"))
                    break
            driver_color[code] = clr

        if len(driver_frames) < 2:
            return

        shown_corners = self.corner_markers[:10]
        codes = list(driver_frames.keys())
        panel_w = 430
        row_h = 20
        header_h = 56
        panel_h = header_h + row_h * len(shown_corners)
        cx = left + panel_w / 2
        cy = top - panel_h / 2

        # Store panel bounds for mouse hit detection
        self.heatmap_panel_bounds = {"left": left, "top": top, "width": panel_w, "height": panel_h}

        panel_rect = arcade.XYWH(cx, cy, panel_w, panel_h)
        arcade.draw_rect_filled(panel_rect, (10, 10, 10, 220))
        arcade.draw_rect_outline(panel_rect, arcade.color.GRAY, 1)

        # Draw draggable handle on header
        handle_rect = arcade.XYWH(left + 5, top - 20, 20, 15)
        arcade.draw_rect_filled(handle_rect, (80, 80, 80, 200))
        arcade.draw_rect_outline(handle_rect, (120, 120, 120), 1)
        arcade.Text("⋮⋮", left + 8, top - 12, arcade.color.LIGHT_GRAY, 8).draw()

        mode = self.corner_metric_modes[self.corner_metric_mode_index].upper()
        arcade.Text(f"Per-Corner Split Heatmap ({mode} speed)", left + 10, top - 10, arcade.color.ANTI_FLASH_WHITE, 12, bold=True).draw()
        arcade.Text("Press T to switch metric", left + 10, top - 28, arcade.color.LIGHT_GRAY, 10).draw()

        heat_left = left + 72
        heat_w = panel_w - 80
        col_w = heat_w / max(1, len(codes))

        for idx, code in enumerate(codes):
            cxh = heat_left + idx * col_w + col_w / 2
            arcade.Text(code, cxh, top - 44, driver_color.get(code, arcade.color.WHITE), 9, anchor_x="center").draw()

        y = top - 64
        for corner in shown_corners:
            turn = int(corner["turn"])
            apex_rel = float(corner["apex_rel"])
            vals: dict[str, float] = {}
            for code, frames in driver_frames.items():
                v = self._corner_metric_from_frames(frames, apex_rel)
                if v is not None:
                    vals[code] = v

            arcade.Text(f"T{turn:>2}", left + 10, y, arcade.color.ANTI_FLASH_WHITE, 10).draw()

            baseline_code = self.loaded_driver_code if self.loaded_driver_code in vals else (codes[0] if codes and codes[0] in vals else None)
            baseline = vals.get(baseline_code) if baseline_code else None
            best = max(vals.values()) if vals else None

            for idx, code in enumerate(codes):
                cxh = heat_left + idx * col_w + col_w / 2
                val = vals.get(code)
                if val is None:
                    arcade.draw_rect_filled(arcade.XYWH(cxh, y + 2, col_w - 4, row_h - 6), (50, 50, 50, 180))
                    continue

                if baseline is not None:
                    delta = val - baseline
                elif best is not None:
                    delta = val - best
                else:
                    delta = 0.0

                norm = max(-1.0, min(1.0, delta / 12.0))
                if norm >= 0:
                    color = (int(40 + 160 * norm), int(80 + 160 * norm), 50, 220)
                else:
                    n = abs(norm)
                    color = (int(80 + 170 * n), int(40 + 70 * (1 - n)), int(40 + 50 * (1 - n)), 220)

                arcade.draw_rect_filled(arcade.XYWH(cxh, y + 2, col_w - 4, row_h - 6), color)
                arcade.Text(f"{val:.0f}", cxh, y + 1, arcade.color.WHITE, 9, anchor_x="center").draw()

            y -= row_h

    def on_mouse_press(self, x: float, y: float, button: int, modifiers: int):
        # Check if clicking on a draggable panel handle
        if hasattr(self, 'heatmap_panel_bounds') and self.heatmap_panel_bounds:
            bounds = self.heatmap_panel_bounds
            if (bounds["left"] + 5 <= x <= bounds["left"] + 25 and
                bounds["top"] - 20 <= y <= bounds["top"]):
                self.dragging_panel = "heatmap"
                self.drag_start_x = x
                self.drag_start_y = y
                self.drag_start_panel_x = self.panel_positions["heatmap"]["offset_x"]
                self.drag_start_panel_y = self.panel_positions["heatmap"]["offset_y"]
                return

        # If the segment-selector modal is visible (a driver selected), give it first chance
        # to handle the click (so its close button can work). If it handled the click,
        # stop further processing so the leaderboard doesn't re-select the driver.
        if getattr(self, "selected_driver", None):
            try:
                handled = self.qualifying_segment_selector_modal.on_mouse_press(self, x, y, button, modifiers)
                if handled:
                    return
            except Exception as e:
                print("Segment selector click error:", e)

        if self.controls_popup_comp.on_mouse_press(self, x, y, button, modifiers):
            return
        if self.legend_comp.on_mouse_press(self, x, y, button, modifiers):
            return

        # Fallback: let the leaderboard handle the click (select drivers)
        self.leaderboard.on_mouse_press(self, x, y, button, modifiers)
        
        # Only allow race controls interaction if lap is not complete
        if not self.is_lap_complete():
            self.race_controls_comp.on_mouse_press(self, x, y, button, modifiers)

    def is_lap_complete(self):
        """Check if the current lap has finished playing."""
        return self.chart_active and self.n_frames > 0 and self.frame_index >= self.n_frames - 1

    def on_key_press(self, symbol: int, modifiers: int):
        # Allow ESC to close window at any time
        if symbol == arcade.key.ESCAPE:
            arcade.close_window()
            return
        # Allow restart (R), comparison toggle (C), and DRS toggle (D) even when lap is complete
        if symbol == arcade.key.R:
            self.frame_index = 0
            self.play_time = self.play_start_t
            self.playback_speed = 1.0
            self.paused = True
            self.race_controls_comp.flash_button('rewind')
            return
        elif symbol == arcade.key.C:
            # Toggle the ability to see the comparison driver's telemetry
            self.show_comparison_telemetry = not self.show_comparison_telemetry
            return
        elif symbol == arcade.key.D:
            # Toggle DRS zones on track map
            self.toggle_drs_zones = not self.toggle_drs_zones
            return
        elif symbol == arcade.key.T:
            self.corner_metric_mode_index = (self.corner_metric_mode_index + 1) % len(self.corner_metric_modes)
            return
        elif symbol == arcade.key.Y:
            # Reset all panel positions to default
            self.panel_positions = {
                "overlay_charts": {"offset_x": 0, "offset_y": 0},
                "heatmap": {"offset_x": 0, "offset_y": 0}
            }
            self.save_panel_positions()
            print("✓ Panel positions reset to default")
            return
        elif symbol == arcade.key.H:
            # Toggle Controls popup with 'H' key — show anchored to bottom-left with 20px margin
            margin_x = 20
            margin_y = 20
            left_pos = float(margin_x)
            top_pos = float(margin_y + self.controls_popup_comp.height)
            if self.controls_popup_comp.visible:
                self.controls_popup_comp.hide()
            else:
                self.controls_popup_comp.show_over(left_pos, top_pos)
            return
        
        # Disable other controls when lap is complete
        if self.is_lap_complete():
            return
        
        if symbol == arcade.key.SPACE:
            self.paused = not self.paused
            self.race_controls_comp.flash_button('play_pause')
        elif symbol == arcade.key.RIGHT:
            self.was_paused_before_hold = self.paused
            self.is_forwarding = True
            self.paused = True
        elif symbol == arcade.key.LEFT:
            self.was_paused_before_hold = self.paused
            self.is_rewinding = True
            self.paused = True
        elif symbol == arcade.key.UP:
            if self.playback_speed < 1024.0:
                self.playback_speed *= 2.0
                self.race_controls_comp.flash_button('speed_increase')
        elif symbol == arcade.key.DOWN:
            self.playback_speed = max(0.1, self.playback_speed / 2.0)
            self.race_controls_comp.flash_button('speed_decrease')
        elif symbol == arcade.key.KEY_1:
            self.playback_speed = 0.5
            self.race_controls_comp.flash_button('speed_decrease')
        elif symbol == arcade.key.KEY_2:
            self.playback_speed = 1.0
            self.race_controls_comp.flash_button('speed_decrease')
        elif symbol == arcade.key.KEY_3:
            self.playback_speed = 2.0
            self.race_controls_comp.flash_button('speed_increase')
        elif symbol == arcade.key.KEY_4:
            self.playback_speed = 4.0
            self.race_controls_comp.flash_button('speed_increase')

    def load_driver_telemetry(self, driver_code: str, segment_name: str):

        # If already loading, ignore
        if self.loading_telemetry:
            return

        self.qualifying_lap_time_comp.reset()

        # Try to find telemetry already provided in the window's data object
        telemetry_store = self.data.get("telemetry") if isinstance(self.data, dict) else None
        if telemetry_store:
            driver_block = telemetry_store.get(driver_code) if isinstance(telemetry_store, dict) else None
            if driver_block:
                seg = driver_block.get(segment_name)
                if seg and isinstance(seg, dict) and seg.get("frames"):
                    # Use local telemetry immediately (no background fetch required)
                    self.loaded_telemetry = seg
                    self.loaded_driver_code = driver_code
                    self.loaded_driver_segment = segment_name
                    self.selected_drivers = [driver_code]
                    self.chart_active = True
                    # cache arrays for fast access and search
                    frames = seg.get("frames", [])
                    drs_zones = seg.get("drs_zones", [])
                    times = [float(f.get("t")) for f in frames if f.get("t") is not None]
                    xs = [ (f.get("telemetry") or {}).get("x") for f in frames ]
                    ys = [ (f.get("telemetry") or {}).get("y") for f in frames ]
                    speeds = [ (f.get("telemetry") or {}).get("speed") for f in frames ]
                    # convert to numpy arrays (keep None if any; searchsorted expects numeric times)
                    self._times = np.array(times) if times else None
                    self._xs = np.array(xs) if xs else None
                    self._ys = np.array(ys) if ys else None
                    self._speeds = np.array([float(s) for s in speeds if s is not None]) if speeds else None
                    # populate top-level frames/n_frames and min/max speeds for chart scaling
                    self.frames = frames
                    self.drs_zones = drs_zones
                    self.n_frames = len(frames)
                    if self._speeds is not None and self._speeds.size > 0:
                        self.min_speed = float(np.min(self._speeds))
                        self.max_speed = float(np.max(self._speeds))
                    else:
                        self.min_speed = 0.0
                        self.max_speed = 0.0
                     # initialize playback state based on frames' timestamps
                    frames = seg.get("frames", [])
                    if frames:
                        start_t = frames[0].get("t", 0.0)
                        self.play_start_t = float(start_t)
                        self.play_time = float(start_t)
                        self.frame_index = 0
                        self.paused = False
                        self.playback_speed = 1.0
                    self.loading_telemetry = False
                    self.loading_message = ""
                    return

        # Otherwise proceed with background loading as before
        self.loading_telemetry = True
        self.loading_message = f"Loading telemetry {driver_code} {segment_name}..."
        self.loaded_telemetry = None
        self.chart_active = False

        threading.Thread(
            target=self._bg_load_telemetry,
            args=(driver_code, segment_name),
            daemon=True
        ).start()

    def _bg_load_telemetry(self, driver_code: str, segment_name: str):
        """Background loader that fetches telemetry if not present locally."""
        try:
            telemetry = None
            # First double-check local store in background thread (race-safe)
            telemetry_store = self.data.get("telemetry") if isinstance(self.data, dict) else None
            if telemetry_store:
                driver_block = telemetry_store.get(driver_code) if isinstance(telemetry_store, dict) else None
                if driver_block:
                    seg = driver_block.get(segment_name)
                    if seg and isinstance(seg, dict) and seg.get("frames"):
                        telemetry = seg

            # If not found locally, attempt to fetch via API if a session is available
            if telemetry is None and getattr(self, "session", None) is not None:
                telemetry = get_driver_quali_telemetry(self.session, driver_code, segment_name)
            elif telemetry is None:
                # demo fallback: sleep briefly and leave telemetry None
                time.sleep(1.0)
                telemetry = None

            if telemetry is None:
                self.loaded_telemetry = None
                self.chart_active = False
            else:
                self.loaded_telemetry = telemetry
                self.loaded_driver_code = driver_code
                self.loaded_driver_segment = segment_name
                self.selected_drivers = [driver_code]
                self.chart_active = True
                # cache arrays for fast indexing/interpolation
                frames = telemetry.get("frames", [])
                times = [float(f.get("t")) for f in frames if f.get("t") is not None]
                xs = [ (f.get("telemetry") or {}).get("x") for f in frames ]
                ys = [ (f.get("telemetry") or {}).get("y") for f in frames ]
                speeds = [ (f.get("telemetry") or {}).get("speed") for f in frames ]
                self._times = np.array(times) if times else None
                self._xs = np.array(xs) if xs else None
                self._ys = np.array(ys) if ys else None
                self._speeds = np.array([float(s) for s in speeds if s is not None]) if speeds else None
                self.frames = frames
                self.n_frames = len(frames)
                if self._speeds is not None and self._speeds.size > 0:
                    self.min_speed = float(np.min(self._speeds))
                    self.max_speed = float(np.max(self._speeds))
                else:
                    self.min_speed = 0.0
                    self.max_speed = 0.0
                # initialize playback state for the newly loaded telemetry
                frames = telemetry.get("frames", [])
                if frames:
                    start_t = frames[0].get("t", 0.0)
                    self.play_start_t = float(start_t)
                    self.play_time = float(start_t)
                    self.frame_index = 0
                    self.paused = False
                    self.playback_speed = 1.0
        except Exception as e:
            print("Telemetry load failed:", e)
            self.loaded_telemetry = None
            self.chart_active = False
        finally:
            self.loading_telemetry = False
            self.loading_message = ""

    def on_update(self, delta_time: float):
        if not self.chart_active or self.loaded_telemetry is None:
            return
        self.race_controls_comp.on_update(delta_time)
        self.qualifying_lap_time_comp.on_update(delta_time)

        # Block for continuous seeking
        seek_speed = 3.0 * max(1.0, self.playback_speed)  # scales with current playback speed

        if self.is_rewinding:
            self.play_time -= delta_time * seek_speed
            self.race_controls_comp.flash_button('rewind')
        elif self.is_forwarding:
            self.play_time += delta_time * seek_speed
            self.race_controls_comp.flash_button('forward')
        else:
            # Normal playback path (no seeking)
            if self.paused:
                return
            # advance play_time by delta_time scaled by playback_speed
            self.play_time += delta_time * self.playback_speed

        # compute integer frame index from cached times (fast, robust)
        if self._times is not None and len(self._times) > 0:
            # clamp play_time into available range
            clamped = min(max(self.play_time, float(self._times[0])), float(self._times[-1]))
            idx = int(np.searchsorted(self._times, clamped, side="right") - 1)
            self.frame_index = max(0, min(idx, len(self._times) - 1))

            # Auto-pause when lap completes to prevent errors
            if self.frame_index >= self.n_frames - 1:
                self.paused = True
        else:
            # fallback: step frame index at FPS if no timestamps available
            self.frame_index = int(min(self.n_frames - 1, self.frame_index + int(round(delta_time * FPS * self.playback_speed))))

            # Auto-pause when lap completes to prevent errors
            if self.frame_index >= self.n_frames - 1:
                self.paused = True

    def on_key_release(self, symbol: int, modifiers: int):
        if symbol == arcade.key.RIGHT:
            self.is_forwarding = False
            self.paused = self.was_paused_before_hold
        elif symbol == arcade.key.LEFT:
            self.is_rewinding = False
            self.paused = self.was_paused_before_hold

    def on_mouse_release(self, x: float, y: float, button: int, modifiers: int):
        if self.is_forwarding or self.is_rewinding:
            self.is_forwarding = False
            self.is_rewinding = False
            self.paused = self.was_paused_before_hold
        
        # End panel dragging
        if self.dragging_panel:
            self.save_panel_positions()
            self.dragging_panel = None

def run_qualifying_replay(session, data, title="Qualifying Results", ready_file=None):
    window = QualifyingReplay(session=session, data=data, title=title)
    # Signal readiness to parent process (if requested) after window created
    if ready_file:
        try:
            with open(ready_file, 'w') as f:
                f.write('ready')
        except Exception:
            pass
    arcade.run()
