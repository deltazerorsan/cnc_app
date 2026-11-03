"""Compara dos G-code de corte (por ejemplo uno de referencia y el nuestro).

Alinea los archivos (prueba giros de 0/90/180/270 grados y ajusta la posicion) y mide
cuanto se parecen sus trayectorias: diferencia de la huella de la fresa, desviacion
maxima entre recorridos y sentido de giro de cada contorno.

Uso:  python comparar_nc.py a.nc b.nc [--tool 4]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union

from verificar_nc import analyze


def _rot(points, deg, cx, cy):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [((x - cx) * c - (y - cy) * s + cx, (x - cx) * s + (y - cy) * c + cy) for x, y in points]


def _bounds(contours):
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    return min(xs), min(ys), max(xs), max(ys)


def _signed_area(pts):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))


def _footprint(contours, radius):
    return unary_union([LineString(c).buffer(radius, quad_segs=8) for c in contours])


def _transform(contours, deg, ref_bounds):
    """Gira alrededor del centro y traslada para que el minimo coincida con ref_bounds."""
    b = _bounds(contours)
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    rotated = [_rot(c, deg, cx, cy) for c in contours]
    nb = _bounds(rotated)
    dx, dy = ref_bounds[0] - nb[0], ref_bounds[1] - nb[1]
    return [[(x + dx, y + dy) for x, y in c] for c in rotated], (dx, dy)


def compare(text_a: str, text_b: str, tool_diameter: float = 4.0) -> dict:
    ra, rb = analyze(text_a), analyze(text_b)
    A = [c for c in ra.contours if len(c) >= 3]
    B = [c for c in rb.contours if len(c) >= 3]
    radius = tool_diameter / 2
    ref = _bounds(A)
    fa = _footprint(A, radius)

    best = None
    for deg in (0, 90, 180, 270):
        tb, shift = _transform(B, deg, ref)
        fb = _footprint(tb, radius)
        diff = fa.symmetric_difference(fb).area
        if best is None or diff < best["diff_area"]:
            best = dict(rotation=deg, shift=shift, diff_area=diff, contours_b=tb, footprint_b=fb)

    tb = best["contours_b"]
    # desviacion maxima entre recorridos (en ambos sentidos)
    lines_a = MultiLineString([c for c in A])
    lines_b = MultiLineString([c for c in tb])
    pa = shapely.points(np.array([p for c in A for p in c]))
    pb = shapely.points(np.array([p for c in tb for p in c]))
    dev_a = shapely.distance(pa, lines_b)
    dev_b = shapely.distance(pb, lines_a)

    # sentido de giro: cada contorno de A con el B de centroide mas cercano
    cents_b = [Polygon(c).centroid for c in tb]
    same = opposite = 0
    for c in A:
        cen = Polygon(c).centroid
        j = min(range(len(tb)), key=lambda k: cen.distance(cents_b[k]))
        if (_signed_area(c) > 0) == (_signed_area(tb[j]) > 0):
            same += 1
        else:
            opposite += 1

    return dict(
        a=ra, b=rb, rotation=best["rotation"], shift=best["shift"],
        footprint_a=fa.area, diff_area=best["diff_area"],
        max_dev=float(max(dev_a.max(), dev_b.max())), mean_dev=float((dev_a.mean() + dev_b.mean()) / 2),
        same_direction=same, opposite_direction=opposite, contours=(len(A), len(B)),
    )


def format_comparison(c: dict, name_a: str, name_b: str) -> str:
    a, b = c["a"], c["b"]
    rows = [
        ("lineas", a.lines, b.lines),
        ("contornos", c["contours"][0], c["contours"][1]),
        ("segmentos de corte", len(a.cut_segments), len(b.cut_segments)),
        ("recorrido de corte (mm)", f"{a.cut_length:.0f}", f"{b.cut_length:.0f}"),
        ("tiempo estimado (min)", f"{a.minutes:.1f}", f"{b.minutes:.1f}"),
        ("avance corte / bajada", f"{sorted(a.cut_feeds)}/{sorted(a.plunge_feeds)}", f"{sorted(b.cut_feeds)}/{sorted(b.plunge_feeds)}"),
        ("a avance en el aire (s)", f"{a.air_seconds:.0f}", f"{b.air_seconds:.0f}"),
        ("niveles Z de corte", sorted(a.cut_z_levels), sorted(b.cut_z_levels)),
        ("husillo", " ".join(cmd for _, cmd, _ in a.spindle), " ".join(cmd for _, cmd, _ in b.spindle)),
        ("pausas M0", len(a.pauses), len(b.pauses)),
    ]
    w = max(len(r[0]) for r in rows)
    out = [f"{'':{w}}  {name_a:>24}  {name_b:>24}"]
    out += [f"{r[0]:{w}}  {str(r[1]):>24}  {str(r[2]):>24}" for r in rows]
    out.append("")
    out.append(f"Mejor alineacion: {name_b} girado {c['rotation']} grados y trasladado ({c['shift'][0]:.2f}, {c['shift'][1]:.2f}) mm")
    pct = 100 * c["diff_area"] / c["footprint_a"]
    out.append(f"Diferencia de huella de la fresa: {c['diff_area']:.1f} mm2 ({pct:.3f}% de {c['footprint_a']:.0f} mm2)")
    out.append(f"Desviacion entre recorridos: media {c['mean_dev']:.4f} mm, maxima {c['max_dev']:.4f} mm")
    out.append(f"Sentido de giro por contorno: iguales {c['same_direction']}, contrarios {c['opposite_direction']}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compara dos G-code de corte.")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--tool", type=float, default=4.0, help="diametro de la fresa (mm), por defecto 4")
    args = ap.parse_args(argv)
    ta = Path(args.a).read_text(encoding="ascii", errors="replace")
    tb = Path(args.b).read_text(encoding="ascii", errors="replace")
    print(format_comparison(compare(ta, tb, args.tool), Path(args.a).name, Path(args.b).name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
