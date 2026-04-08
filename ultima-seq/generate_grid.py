import argparse
import math
import xml.etree.ElementTree as ET
from typing import List, Tuple


def parse_stage_locations(path: str) -> Tuple[List[Tuple[float, float]], List[List[float]], List[str]]:
    tree = ET.parse(path)
    root = tree.getroot()
    coords: List[Tuple[float, float]] = []
    z_values: List[List[float]] = []
    rotations: List[str] = []
    for elem in root.findall("StageLocation"):
        x = float(elem.get("x"))
        y = float(elem.get("y"))
        z_attr = elem.get("z", "0")
        parts = [p.strip() for p in z_attr.split(",") if p.strip()]
        z_list = [float(p) for p in parts] if parts else [0.0]
        coords.append((x, y))
        z_values.append(z_list)
        rotations.append(elem.get("rotation", "0"))
    return coords, z_values, rotations


def inverse_distance_interpolate(
    x: float,
    y: float,
    sample_coords: List[Tuple[float, float]],
    sample_z: List[List[float]],
    power: float = 2.0,
    k: int = 4,
) -> List[float]:
    tol = 1e-6
    distances = []
    for (sx, sy), z_vals in zip(sample_coords, sample_z):
        dx = x - sx
        dy = y - sy
        d = math.hypot(dx, dy)
        if d < tol:
            return z_vals
        distances.append((d, z_vals))
    distances.sort(key=lambda t: t[0])
    used = distances[: min(k, len(distances))]
    if not used:
        return [0.0]
    weights = [1.0 / (d ** power) for d, _ in used]
    total_w = sum(weights)
    if total_w == 0.0:
        return used[0][1]
    num_components = len(used[0][1])
    result: List[float] = []
    for comp in range(num_components):
        num = 0.0
        for w, (_, z_vals) in zip(weights, used):
            if comp < len(z_vals):
                num += w * z_vals[comp]
        result.append(num / total_w)
    return result


def generate_grid(
    input_path: str,
    output_path: str,
    pixel_size: float,
    z_mode: str = "constant",
    show_plot: bool = False,
) -> None:
    coords, z_values, rotations = parse_stage_locations(input_path)
    if not coords:
        raise ValueError("No StageLocation entries found in input file")

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    if pixel_size <= 0:
        raise ValueError("pixel_size must be positive")

    step_x = pixel_size if max_x >= min_x else -pixel_size
    step_y = pixel_size if max_y >= min_y else -pixel_size

    x_grid: List[float] = []
    val = min_x
    eps = pixel_size * 1e-6
    if step_x > 0:
        while val <= max_x + eps:
            x_grid.append(val)
            val += step_x
    else:
        while val >= max_x - eps:
            x_grid.append(val)
            val += step_x

    y_grid: List[float] = []
    val = min_y
    if step_y > 0:
        while val <= max_y + eps:
            y_grid.append(val)
            val += step_y
    else:
        while val >= max_y - eps:
            y_grid.append(val)
            val += step_y

    constant_z_attr = None
    if z_mode == "constant":
        # Use the first location's Z (all components) for all grid points.
        constant_z_attr = ", ".join(str(v) for v in z_values[0])

    root = ET.Element("StageLocations")
    index = 0
    plot_points = []
    for j, y in enumerate(y_grid):
        for i, x in enumerate(x_grid):
            if z_mode == "constant":
                z_attr = constant_z_attr
            else:
                interpolated = inverse_distance_interpolate(x, y, coords, z_values)
                z_attr = ", ".join(str(v) for v in interpolated)
            rotation = rotations[0] if rotations else "0"
            elem = ET.SubElement(root, "StageLocation")
            elem.set("index", str(index))
            elem.set("x", f"{x:.6f}")
            elem.set("y", f"{y:.6f}")
            elem.set("z", z_attr)
            elem.set("rotation", rotation)
            index += 1

            # For plotting: use the first Z component as the color value.
            try:
                first_z = float(z_attr.split(",")[0].strip())
            except (ValueError, IndexError):
                first_z = 0.0
            plot_points.append((x, y, first_z))

    tree = ET.ElementTree(root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    if show_plot and plot_points:
        _plot_grid(plot_points, coords, pixel_size)


def _plot_grid(
    points: List[Tuple[float, float, float]],
    sample_coords: List[Tuple[float, float]],
    pixel_size: float,
) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]

    fig, ax = plt.subplots()
    sc = ax.scatter(xs, ys, c=zs, cmap="viridis")
    plt.colorbar(sc, ax=ax, label="Z")

    for x, y, z in points:
        ax.text(x, y, f"({x:.0f}, {y:.0f})\n{z:.2f}", fontsize=6, ha="center", va="center")

    # Draw square FOVs centered on each original input location using pixel_size as side length.
    half = pixel_size / 2.0
    for sx, sy in sample_coords:
        rect = patches.Rectangle(
            (sx - half, sy - half),
            pixel_size,
            pixel_size,
            linewidth=1.0,
            edgecolor="red",
            facecolor="none",
        )
        ax.add_patch(rect)

    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_title("StageLocation Grid (red squares = FOV)")
    ax.set_aspect("equal", adjustable="box")
    ax.invert_yaxis()
    plt.tight_layout()
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a regular grid of StageLocation entries from an input locs.xy "
            "file, with optional Z interpolation and optional plotting."
        )
    )
    parser.add_argument(
        "-i",
        "--input",
        default="locs.xy",
        help="Path to input locs.xy XML file (default: locs.xy)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="new_loc.xy",
        help="Path to output XML file (default: new_loc.xy)",
    )
    parser.add_argument(
        "-p",
        "--pixel-size",
        type=float,
        required=True,
        help="Pixel size / grid spacing in stage units (positive float)",
    )
    parser.add_argument(
        "--z-mode",
        choices=["constant", "interpolate"],
        default="constant",
        help=(
            'How to handle Z values for grid points: "constant" uses the first '
            'location\'s Z for all points; "interpolate" uses inverse-distance '
            "weighting based on input locations."
        ),
    )
    parser.add_argument(
        "--show-plot",
        action="store_true",
        help=(
            "Display a matplotlib plot of the generated grid with XY and Z "
            "annotations."
        ),
    )
    args = parser.parse_args()

    generate_grid(args.input, args.output, args.pixel_size, args.z_mode, args.show_plot)


if __name__ == "__main__":
    main()
