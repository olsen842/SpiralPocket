import math


def build_spiral_points(
    x_start, y_start,
    total_width, total_height,
    desired_width, desired_height,
    cut_step, tool_diameter=0.0
):
    """Compute the (x, y) tool-center waypoints of the contour spiral.

    (x_start, y_start) is the lower-left corner of the finished part,
    which measures desired_width x desired_height. The stock extends
    ceil(extra/2) beyond the part on the left/bottom and floor(extra/2)
    on the right/top. The tool plunges just outside the stock corner
    (where there is no material), then spirals inward by `cut_step` per
    lap. The final lap runs with the tool center one tool radius
    outside the part boundary, so the part comes out at the desired
    size and the tool never cuts into it.
    """
    if cut_step <= 0:
        raise ValueError("cut_step must be positive")
    if tool_diameter < 0:
        raise ValueError("tool_diameter cannot be negative")
    if total_width < desired_width or total_height < desired_height:
        raise ValueError("desired size cannot exceed total stock size")

    r = tool_diameter / 2.0
    extra_x = total_width - desired_width
    extra_y = total_height - desired_height

    # Final tool-center rectangle: one tool radius outside the part,
    # so the tool edge lands exactly on the part boundary.
    final_left = x_start - r
    final_right = x_start + desired_width + r
    final_bottom = y_start - r
    final_top = y_start + desired_height + r

    # Outermost tool-center rectangle: tool tangent to the stock edges.
    outer_left = final_left - math.ceil(extra_x / 2)
    outer_right = final_right + math.floor(extra_x / 2)
    outer_bottom = final_bottom - math.ceil(extra_y / 2)
    outer_top = final_top + math.floor(extra_y / 2)

    # First lap steps in from the outer rectangle: on the outer
    # rectangle itself the tool only grazes the stock without cutting.
    cur_left = min(outer_left + cut_step, final_left)
    cur_right = max(outer_right - cut_step, final_right)
    cur_bottom = min(outer_bottom + cut_step, final_bottom)
    cur_top = max(outer_top - cut_step, final_top)

    # Plunge point (outside the stock), then lead in to the first lap.
    points = [(outer_left, outer_bottom), (cur_left, cur_bottom)]

    while True:
        points.append((cur_right, cur_bottom))   # bottom edge, going right
        points.append((cur_right, cur_top))      # right edge, going up
        if (cur_left == final_left and cur_right == final_right and
                cur_bottom == final_bottom and cur_top == final_top):
            points.append((cur_left, cur_top))   # close the final lap
            points.append((cur_left, cur_bottom))
            break
        next_left = min(cur_left + cut_step, final_left)
        next_bottom = min(cur_bottom + cut_step, final_bottom)
        points.append((next_left, cur_top))      # top edge, stepping in
        points.append((next_left, next_bottom))  # left edge, stepping in
        cur_left, cur_bottom = next_left, next_bottom
        cur_right = max(cur_right - cut_step, final_right)
        cur_top = max(cur_top - cut_step, final_top)

    # Drop consecutive duplicate points (avoids zero-length moves)
    deduped = [points[0]]
    for p in points[1:]:
        if p != deduped[-1]:
            deduped.append(p)
    return deduped


def create_contour_gcode(
    x_start, y_start,
    total_width, total_height,
    desired_width, desired_height,
    cut_step, feed, rpm,
    tool_diameter=0.0,   # Tool diameter for radius compensation
    cut_depth=20.0,      # Total depth to mill down to
    pass_depth=None,     # Max Z depth per pass; None = single pass (=cut_depth)
    safe_z=5.0,          # Height to retract to before rapid moves
    plunge_feed=100.0,   # Feed rate for Z plunges
    spindle_dwell=0.0,   # Seconds to pause after spindle start (0 = none)
    units="mm"           # "mm" (G21) or "inch" (G20) -- all dimensions are
                          # assumed to already be in this unit
):
    """Generate the full G-code program for the contour spiral.

    See `build_spiral_points` for the path geometry. `pass_depth` (if
    set) repeats the spiral at increasing depths until `cut_depth` is
    reached. The program starts with a comment header listing every
    parameter used, for traceability.
    """
    if cut_depth <= 0:
        raise ValueError("cut_depth must be positive")
    if pass_depth is not None and pass_depth <= 0:
        raise ValueError("pass_depth must be positive")
    if feed <= 0 or plunge_feed <= 0:
        raise ValueError("feed and plunge_feed must be positive")
    if spindle_dwell < 0:
        raise ValueError("spindle_dwell cannot be negative")
    if units not in ("mm", "inch"):
        raise ValueError('units must be "mm" or "inch"')

    points = build_spiral_points(
        x_start, y_start,
        total_width, total_height,
        desired_width, desired_height,
        cut_step, tool_diameter,
    )

    gcode = []

    def add(cmd):
        gcode.append(cmd)

    decimals = 3 if units == "mm" else 4

    def fmt(v):
        return str(int(v)) if v == int(v) else f"{v:.{decimals}f}"

    # Build the list of Z depths to step down through
    step = pass_depth if pass_depth is not None else cut_depth
    depths = []
    depth = 0.0
    while depth < cut_depth:
        depth = min(depth + step, cut_depth)
        depths.append(depth)

    # Comment header for traceability (what this file was generated with)
    add("(Generated by cnc-gcode-generator)")
    add(f"(x_start={fmt(x_start)} y_start={fmt(y_start)} units={units})")
    add(f"(total_width={fmt(total_width)} total_height={fmt(total_height)})")
    add(f"(desired_width={fmt(desired_width)} desired_height={fmt(desired_height)})")
    add(f"(cut_step={fmt(cut_step)} tool_diameter={fmt(tool_diameter)})")
    add(f"(cut_depth={fmt(cut_depth)} pass_depth={'-' if pass_depth is None else fmt(pass_depth)})")
    add(f"(feed={fmt(feed)} plunge_feed={fmt(plunge_feed)} rpm={fmt(rpm)})")
    add(f"(safe_z={fmt(safe_z)} spindle_dwell={fmt(spindle_dwell)})")

    # Generate G-code
    add("G21" if units == "mm" else "G20")
    # Safe-start block: XY plane, cutter comp off, tool length offset off,
    # G54 work offsets, cancel canned cycles, absolute mode, feed per minute
    add("G17 G40 G49 G54 G80 G90 G94")

    # The plunge point is just outside the stock corner (no material there)
    plunge_x, plunge_y = points[0]
    add(f"G00 Z{fmt(safe_z)}")  # clear before traveling to start position
    add(f"G00 X{fmt(plunge_x)} Y{fmt(plunge_y)}")
    add(f"M03 S{fmt(rpm)}")
    if spindle_dwell > 0:
        add(f"G04 P{fmt(spindle_dwell)}")  # let spindle reach speed

    for di, depth in enumerate(depths):
        if di > 0:
            add(f"G00 Z{fmt(safe_z)}")
            add(f"G00 X{fmt(plunge_x)} Y{fmt(plunge_y)}")
        add(f"G01 Z-{fmt(depth)} F{fmt(plunge_feed)}")
        for i, (x, y) in enumerate(points[1:]):
            if i == 0:
                add(f"G01 X{fmt(x)} Y{fmt(y)} F{fmt(feed)}")
            else:
                add(f"G01 X{fmt(x)} Y{fmt(y)}")

    # End program
    add("M05")
    add(f"G00 Z{fmt(safe_z)}")  # retract before traveling home
    add(f"G00 X{fmt(plunge_x)} Y{fmt(plunge_y)}")
    add("M30")

    return "\n".join(gcode)

if __name__ == "__main__":
    # Example: 205x205 stock down to 200x200, 0.5mm stepover, 10mm tool,
    # 6mm total depth cut in 2mm passes
    gcode = create_contour_gcode(
        x_start=0, y_start=0,
        total_width=205,
        total_height=205,
        desired_width=200,
        desired_height=200,
        cut_step=0.5,
        feed=100,
        rpm=1000,
        tool_diameter=10.0,
        cut_depth=6.0,
        pass_depth=2.0,
    )
    print(gcode)