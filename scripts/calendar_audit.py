#!/usr/bin/env python3

import csv
import html
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZipFile


WEEKDAYS = [
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
]


def read_csv_from_zip(zip_file: ZipFile, filename: str):
    with zip_file.open(filename) as raw:
        text = raw.read().decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def consecutive_ranges(dates):
    if not dates:
        return []

    dates = sorted(dates)
    ranges = []

    start = dates[0]
    previous = dates[0]

    for current in dates[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            continue

        ranges.append((start, previous))
        start = current
        previous = current

    ranges.append((start, previous))
    return ranges


def format_date(value):
    return value.strftime("%d/%m/%Y")


def format_ranges(dates):
    return "; ".join(
        f"{format_date(start)} - {format_date(end)}"
        for start, end in consecutive_ranges(dates)
    )


def weekday_pattern(dates):
    values = sorted(
        {WEEKDAYS[d.weekday()] for d in dates},
        key=WEEKDAYS.index,
    )

    if len(values) == 7:
        return "Daily"

    if values == ["Mon", "Tue", "Wed", "Thu", "Fri"]:
        return "Mon-Fri"

    if values == ["Sat", "Sun"]:
        return "Sat-Sun"

    return ", ".join(values)


def get_trip_metadata(trips, routes, stop_times, stops):
    routes_by_id = {row["route_id"]: row for row in routes}
    stops_by_id = {row["stop_id"]: row for row in stops}

    stop_times_by_trip = defaultdict(list)

    for row in stop_times:
        stop_times_by_trip[row["trip_id"]].append(row)

    for values in stop_times_by_trip.values():
        values.sort(
            key=lambda row: int(row.get("stop_sequence", "0"))
        )

    metadata = defaultdict(
        lambda: {
            "route_short_names": set(),
            "route_long_names": set(),
            "origins": set(),
            "destinations": set(),
        }
    )

    for trip in trips:
        service_id = trip["service_id"]
        route_id = trip["route_id"]

        route = routes_by_id.get(route_id, {})
        route_short_name = route.get("route_short_name", "")
        route_long_name = route.get("route_long_name", "")

        if route_short_name:
            metadata[service_id]["route_short_names"].add(
                route_short_name
            )

        if route_long_name:
            metadata[service_id]["route_long_names"].add(
                route_long_name
            )

        times = stop_times_by_trip.get(trip["trip_id"], [])

        if times:
            first_stop = stops_by_id.get(times[0]["stop_id"], {})
            last_stop = stops_by_id.get(times[-1]["stop_id"], {})

            first_name = first_stop.get("stop_name", "")
            last_name = last_stop.get("stop_name", "")

            if first_name:
                metadata[service_id]["origins"].add(first_name)

            if last_name:
                metadata[service_id]["destinations"].add(last_name)

    return metadata


def generate_csv(rows, output_path):
    fieldnames = [
        "service_id",
        "route_short_name",
        "route_long_name",
        "origin",
        "destination",
        "first_date",
        "last_date",
        "number_of_operating_dates",
        "weekday_pattern",
        "date_ranges",
        "number_of_exception_dates",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_html(rows, output_path):
    headers = [
        "Service ID",
        "Route",
        "Route long name",
        "Origin",
        "Destination",
        "First date",
        "Last date",
        "Operating dates",
        "Weekday pattern",
        "Date ranges",
        "Exceptions",
    ]

    table_rows = []

    for row in rows:
        cells = [
            row["service_id"],
            row["route_short_name"],
            row["route_long_name"],
            row["origin"],
            row["destination"],
            row["first_date"],
            row["last_date"],
            row["number_of_operating_dates"],
            row["weekday_pattern"],
            row["date_ranges"],
            row["number_of_exception_dates"],
        ]

        table_rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in cells
            )
            + "</tr>"
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Italo Service Calendar Audit</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 24px;
    background: #f7f7f7;
    color: #222;
}}

h1 {{
    margin-bottom: 8px;
}}

.summary {{
    margin-bottom: 20px;
    color: #555;
}}

.controls {{
    margin-bottom: 16px;
}}

input {{
    width: 100%;
    max-width: 500px;
    padding: 10px;
    font-size: 16px;
}}

.table-container {{
    overflow-x: auto;
    background: white;
}}

table {{
    border-collapse: collapse;
    width: 100%;
    min-width: 1500px;
}}

th, td {{
    border: 1px solid #ddd;
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
}}

th {{
    position: sticky;
    top: 0;
    background: #eee;
}}

tr:nth-child(even) {{
    background: #fafafa;
}}
</style>
</head>
<body>

<h1>Italo Service Calendar Audit</h1>

<div class="summary">
    {len(rows)} services generated from calendar_dates.txt.
</div>

<div class="controls">
    <input
        id="filter"
        type="search"
        placeholder="Filter service, route, origin, destination..."
        oninput="filterTable()"
    >
</div>

<div class="table-container">
<table id="audit">
<thead>
<tr>
{"".join(f"<th>{html.escape(header)}</th>" for header in headers)}
</tr>
</thead>
<tbody>
{''.join(table_rows)}
</tbody>
</table>
</div>

<script>
function filterTable() {{
    const query = document
        .getElementById("filter")
        .value
        .toLowerCase();

    const rows = document.querySelectorAll("#audit tbody tr");

    for (const row of rows) {{
        row.style.display =
            row.innerText.toLowerCase().includes(query)
                ? ""
                : "none";
    }}
}}
</script>

</body>
</html>
"""

    output_path.write_text(document, encoding="utf-8")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python scripts/calendar_audit.py "
            "<gtfs.zip> <output-directory>"
        )

    gtfs_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])

    output_dir.mkdir(parents=True, exist_ok=True)

    with ZipFile(gtfs_path) as zip_file:
        calendar_dates = read_csv_from_zip(
            zip_file,
            "calendar_dates.txt",
        )
        trips = read_csv_from_zip(zip_file, "trips.txt")
        routes = read_csv_from_zip(zip_file, "routes.txt")
        stop_times = read_csv_from_zip(
            zip_file,
            "stop_times.txt",
        )
        stops = read_csv_from_zip(zip_file, "stops.txt")

    metadata = get_trip_metadata(
        trips,
        routes,
        stop_times,
        stops,
    )

    operating_dates = defaultdict(list)
    exception_dates = defaultdict(list)

    for row in calendar_dates:
        service_id = row["service_id"]
        current_date = date.fromisoformat(row["date"])

        if row["exception_type"] == "1":
            operating_dates[service_id].append(current_date)

        elif row["exception_type"] == "2":
            exception_dates[service_id].append(current_date)

    rows = []

    for service_id in sorted(operating_dates):
        dates = sorted(set(operating_dates[service_id]))

        if not dates:
            continue

        info = metadata.get(
            service_id,
            {
                "route_short_names": set(),
                "route_long_names": set(),
                "origins": set(),
                "destinations": set(),
            },
        )

        rows.append(
            {
                "service_id": service_id,
                "route_short_name": " | ".join(
                    sorted(info["route_short_names"])
                ),
                "route_long_name": " | ".join(
                    sorted(info["route_long_names"])
                ),
                "origin": " | ".join(
                    sorted(info["origins"])
                ),
                "destination": " | ".join(
                    sorted(info["destinations"])
                ),
                "first_date": format_date(dates[0]),
                "last_date": format_date(dates[-1]),
                "number_of_operating_dates": len(dates),
                "weekday_pattern": weekday_pattern(dates),
                "date_ranges": format_ranges(dates),
                "number_of_exception_dates": len(
                    exception_dates.get(service_id, [])
                ),
            }
        )

    csv_path = output_dir / "service_calendar_audit.csv"
    html_path = output_dir / "index.html"

    generate_csv(rows, csv_path)
    generate_html(rows, html_path)

    print(f"Generated {csv_path}")
    print(f"Generated {html_path}")
    print(f"Audited {len(rows)} services")


if __name__ == "__main__":
    main()