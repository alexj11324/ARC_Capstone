"""Generate GeoTIFF flood depth rasters from SLOSH parquet data."""

import argparse
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely import wkt

NODATA = -9999.0


def slosh_to_raster(
    parquet_path: str,
    output_tif: str,
    category: int = 3,
    scenario: str = "mean",
    resolution: float = 0.001,
    crs: str = "EPSG:4326",
) -> str:
    """Rasterize SLOSH surge polygons to a GeoTIFF."""
    col = f"c{category}_{scenario}"
    table = pq.read_table(parquet_path, columns=["geometry_wkt", col])
    geom_wkts = table.column("geometry_wkt").to_pylist()
    values = table.column(col).to_pylist()

    shapes = []
    for w, v in zip(geom_wkts, values):
        if w is None or v is None:
            continue
        shapes.append((wkt.loads(w), float(v)))

    all_bounds = [s[0].bounds for s in shapes]
    minx = min(b[0] for b in all_bounds)
    miny = min(b[1] for b in all_bounds)
    maxx = max(b[2] for b in all_bounds)
    maxy = max(b[3] for b in all_bounds)

    width = int(np.ceil((maxx - minx) / resolution))
    height = int(np.ceil((maxy - miny) / resolution))
    transform = from_bounds(minx, miny, maxx, maxy, width, height)

    raster = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=NODATA,
        dtype="float32",
    )

    Path(output_tif).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        output_tif, "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype="float32", crs=crs,
        transform=transform, nodata=NODATA,
    ) as dst:
        dst.write(raster, 1)

    return output_tif


def batch_rasterize(
    parquet_path: str,
    output_dir: str,
    categories: list[int] | None = None,
    scenarios: list[str] | None = None,
    resolution: float = 0.001,
) -> list[str]:
    """Generate rasters for multiple category/scenario combinations."""
    categories = categories or [0, 1, 2, 3, 4, 5]
    scenarios = scenarios or ["mean", "high"]
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for cat in categories:
        for scn in scenarios:
            tif = str(out / f"slosh_c{cat}_{scn}.tif")
            try:
                slosh_to_raster(parquet_path, tif, cat, scn, resolution)
                paths.append(tif)
            except KeyError:
                pass  # column not present
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SLOSH parquet to GeoTIFF")
    parser.add_argument("--parquet", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--category", type=int, default=3)
    parser.add_argument("--scenario", default="mean")
    parser.add_argument("--resolution", type=float, default=0.001)
    args = parser.parse_args()
    path = slosh_to_raster(args.parquet, args.output, args.category, args.scenario, args.resolution)
    print(f"Wrote {path}")
