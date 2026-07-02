import math
import re

WORD_RE = re.compile(r"([A-Za-z])\s*(-?[0-9]*\.?[0-9]+)")
HEADER_TOKEN_RE = re.compile(r"^(\w+)=(\S+)$")


def parse_header(text):
    """Parse the `(key=value ...)` comment header written by
    create_contour_gcode. Returns a dict of {param_name: str_value},
    or an empty dict if the file has no such header.
    """
    params = {}
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith("(") and line.endswith(")")):
            continue
        for token in line[1:-1].split():
            m = HEADER_TOKEN_RE.match(token)
            if m:
                params[m.group(1)] = m.group(2)
    return params


def parse_toolpath(text):
    """Parse G-code into a flat list of motion segments for visualization.

    Each segment is a dict with: start (x, y, z), end (x, y, z),
    rapid (bool, True for G0), and feed (float or None).
    Supports G0/G1/G2/G3 (arcs via I/J or R, XY plane), G90/G91, and
    detects units from G20/G21. Unrecognized words are ignored.
    Returns (segments, units) where units is "mm", "inch" or None.
    """
    segments = []
    x = y = z = 0.0
    absolute = True
    motion = None
    feed = None
    units = None

    for raw_line in text.splitlines():
        line = re.sub(r"\([^)]*\)", "", raw_line)  # strip (...) comments
        line = line.split(";", 1)[0].strip()       # strip ; comments
        if not line:
            continue

        codes = {}
        for letter, value in WORD_RE.findall(line):
            codes.setdefault(letter.upper(), []).append(float(value))

        for g in codes.get("G", []):
            gi = int(g)
            if gi in (0, 1, 2, 3):
                motion = gi
            elif gi == 90:
                absolute = True
            elif gi == 91:
                absolute = False
            elif gi == 20:
                units = "inch"
            elif gi == 21:
                units = "mm"

        if "F" in codes:
            feed = codes["F"][-1]

        if motion is None:
            continue

        target_x, target_y, target_z = x, y, z
        if "X" in codes:
            target_x = codes["X"][-1] if absolute else x + codes["X"][-1]
        if "Y" in codes:
            target_y = codes["Y"][-1] if absolute else y + codes["Y"][-1]
        if "Z" in codes:
            target_z = codes["Z"][-1] if absolute else z + codes["Z"][-1]

        if motion in (0, 1):
            if (target_x, target_y, target_z) != (x, y, z):
                segments.append({
                    "start": (x, y, z),
                    "end": (target_x, target_y, target_z),
                    "rapid": motion == 0,
                    "feed": feed,
                })
            x, y, z = target_x, target_y, target_z
        else:  # G2/G3 arc in the XY plane
            i = codes.get("I", [0.0])[-1]
            j = codes.get("J", [0.0])[-1]
            r = codes.get("R", [None])[-1] if "R" in codes else None
            arc_points = _interpolate_arc(x, y, target_x, target_y, i, j, r,
                                            clockwise=(motion == 2))
            n_arc = len(arc_points) - 1
            for idx, ((ax, ay), (bx, by)) in enumerate(zip(arc_points[:-1], arc_points[1:])):
                sz = z + (target_z - z) * idx / n_arc
                ez = z + (target_z - z) * (idx + 1) / n_arc
                segments.append({
                    "start": (ax, ay, sz),
                    "end": (bx, by, ez),
                    "rapid": False,
                    "feed": feed,
                })
            x, y, z = target_x, target_y, target_z

    return segments, units


def _interpolate_arc(x0, y0, x1, y1, i, j, r, clockwise,
                      max_segment_angle=math.radians(5)):
    if r is not None:
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)
        if chord == 0 or abs(r) < chord / 2:
            return [(x0, y0), (x1, y1)]
        h = math.sqrt(max(r * r - (chord / 2) ** 2, 0.0))
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        ux, uy = -dy / chord, dx / chord
        sign = 1 if (r > 0) == clockwise else -1
        cx, cy = mx + sign * h * ux, my + sign * h * uy
    else:
        cx, cy = x0 + i, y0 + j

    radius = math.hypot(x0 - cx, y0 - cy)
    start_ang = math.atan2(y0 - cy, x0 - cx)
    end_ang = math.atan2(y1 - cy, x1 - cx)

    if clockwise:
        while end_ang >= start_ang:
            end_ang -= 2 * math.pi
    else:
        while end_ang <= start_ang:
            end_ang += 2 * math.pi

    span = end_ang - start_ang
    steps = max(1, int(abs(span) / max_segment_angle))

    return [
        (cx + radius * math.cos(start_ang + span * k / steps),
         cy + radius * math.sin(start_ang + span * k / steps))
        for k in range(steps + 1)
    ]
