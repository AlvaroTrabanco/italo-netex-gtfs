#!/usr/bin/env python3

import csv
import io
import re
import sys
import zipfile
from pathlib import Path


COMBINATION_ROUTE_RE = re.compile(r"^IT::Line:\d{4}-\d{4}-")


def read_csv(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def write_csv(rows, fieldnames):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python scripts/remove_combination_routes.py "
            "input.zip output.zip"
        )

    input_zip = Path(sys.argv[1])
    output_zip = Path(sys.argv[2])

    with zipfile.ZipFile(input_zip, "r") as zin:
        names = zin.namelist()

        required = {
            "routes.txt",
            "trips.txt",
            "stop_times.txt",
        }

        missing = required - set(names)
        if missing:
            raise SystemExit(
                f"Missing required GTFS files: {', '.join(sorted(missing))}"
            )

        routes = read_csv(zin.read("routes.txt"))
        trips = read_csv(zin.read("trips.txt"))
        stop_times = read_csv(zin.read("stop_times.txt"))

        calendar_dates = None
        if "calendar_dates.txt" in names:
            calendar_dates = read_csv(
                zin.read("calendar_dates.txt")
            )

        # Identify combination routes:
        # IT::Line:9963-9944-...
        combination_route_ids = {
            row["route_id"]
            for row in routes
            if COMBINATION_ROUTE_RE.match(row["route_id"])
        }

        print(
            f"Found {len(combination_route_ids)} combination route IDs."
        )

        # Remove combination routes.
        filtered_routes = [
            row
            for row in routes
            if row["route_id"] not in combination_route_ids
        ]

        # Remove trips belonging to those routes.
        removed_trip_ids = {
            row["trip_id"]
            for row in trips
            if row["route_id"] in combination_route_ids
        }

        filtered_trips = [
            row
            for row in trips
            if row["trip_id"] not in removed_trip_ids
        ]

        # Remove stop_times belonging to removed trips.
        filtered_stop_times = [
            row
            for row in stop_times
            if row["trip_id"] not in removed_trip_ids
        ]

        # Only remove calendar services if they are no longer referenced
        # by any remaining trip.
        filtered_calendar_dates = calendar_dates

        if calendar_dates is not None:
            remaining_service_ids = {
                row["service_id"]
                for row in filtered_trips
            }

            filtered_calendar_dates = [
                row
                for row in calendar_dates
                if row["service_id"] in remaining_service_ids
            ]

        print(f"Routes before: {len(routes)}")
        print(f"Routes after:  {len(filtered_routes)}")
        print(f"Trips before:  {len(trips)}")
        print(f"Trips after:   {len(filtered_trips)}")
        print(f"Trips removed: {len(removed_trip_ids)}")
        print(
            f"Stop times before: {len(stop_times)}"
        )
        print(
            f"Stop times after:  {len(filtered_stop_times)}"
        )

        if calendar_dates is not None:
            print(
                f"Calendar dates before: {len(calendar_dates)}"
            )
            print(
                f"Calendar dates after:  {len(filtered_calendar_dates)}"
            )

        output_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(
            output_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zout:

            for name in names:
                if name == "routes.txt":
                    data = write_csv(
                        filtered_routes,
                        routes[0].keys(),
                    )
                elif name == "trips.txt":
                    data = write_csv(
                        filtered_trips,
                        trips[0].keys(),
                    )
                elif name == "stop_times.txt":
                    data = write_csv(
                        filtered_stop_times,
                        stop_times[0].keys(),
                    )
                elif name == "calendar_dates.txt":
                    data = write_csv(
                        filtered_calendar_dates,
                        calendar_dates[0].keys(),
                    )
                else:
                    data = zin.read(name)

                zout.writestr(name, data)

    print(f"Created: {output_zip}")


if __name__ == "__main__":
    main()