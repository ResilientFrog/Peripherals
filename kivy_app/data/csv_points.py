from pathlib import Path


def parse_uploaded_points(upload_file: Path, upload_dir: Path):
    log_file = upload_dir / "debug.log"

    def log(msg):
        print(msg)
        with open(log_file, "a") as fh:
            fh.write(msg + "\n")
            fh.flush()

    log("\n>>> parse_uploaded_points() CALLED")
    log(f"UPLOAD_FILE = {upload_file}")
    log(f"UPLOAD_FILE.exists() = {upload_file.exists()}")

    if not upload_file.exists():
        log(f"❌ File not found: {upload_file}")
        return []

    points = []
    encodings = ["utf-8", "latin-1", "cp1252", "iso-8859-1", "utf-16"]
    all_lines = None

    for encoding in encodings:
        try:
            log(f"📄 Trying encoding: {encoding}")
            with open(upload_file, "r", encoding=encoding) as fh:
                all_lines = fh.readlines()
            log(f"✅ Successfully read with {encoding}: {len(all_lines)} lines")
            break
        except Exception as exc:
            log(f"⚠️  {encoding} failed: {exc}")

    if all_lines is None:
        log("❌ Could not read file with any encoding")
        return []

    def to_float(value):
        if value is None:
            return None
        candidate = value.strip().rstrip(",").rstrip()
        if not candidate:
            return None
        try:
            return float(candidate)
        except ValueError:
            return None

    try:
        for line_num, line in enumerate(all_lines, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if ";" in line:
                parts = [p.strip() for p in line.split(";")]
            else:
                parts = [p.strip() for p in line.split(",")]

            parts = [p for p in parts if p != ""]
            if len(parts) < 2:
                continue

            parsed = False

            if len(parts) >= 6:
                x = to_float(parts[1])
                y = to_float(parts[2])
                name = parts[0] or f"P{len(points) + 1}"
                if x is not None and y is not None:
                    points.append({"name": name, "lat": y, "lon": x})
                    log(f"  ✓ {name}: ({x}, {y}) [survey]")
                    parsed = True

            if not parsed and len(parts) >= 3:
                lat = to_float(parts[1])
                lon = to_float(parts[2])
                name = parts[0] or f"P{len(points) + 1}"
                if lat is not None and lon is not None:
                    points.append({"name": name, "lat": lat, "lon": lon})
                    log(f"  ✓ {name}: ({lat}, {lon}) [name,lat,lon]")
                    parsed = True

            if not parsed:
                lat = to_float(parts[0])
                lon = to_float(parts[1])
                if lat is not None and lon is not None:
                    name = f"P{len(points) + 1}"
                    points.append({"name": name, "lat": lat, "lon": lon})
                    log(f"  ✓ {name}: ({lat}, {lon}) [lat,lon]")
                    parsed = True

            if not parsed:
                log(f"  ⚠️  Line {line_num}: unsupported format, skipped")
    except Exception as exc:
        import traceback

        log(f"❌ Error processing lines: {exc}")
        log(traceback.format_exc())
        return []

    log(f"<<< parse_uploaded_points() DONE: {len(points)} points\n")
    return points
