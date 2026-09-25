#!/usr/bin/env python3

import csv
import io
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path


COMBINATION_ROUTE_RE = re.compile(r"^IT::Line:\d{4}-\d{4}-")

# Examples:
#   8192_#1 -> 8192
#   8192_#2 -> 8192
#   9980_#3 -> 9980
ROUTE_VARIANT_RE = re.compile(r"^(.+)_#\d+$")


def read_csv(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def write_csv(rows, fieldnames):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def normalize_route_short_name(route_short_name):
    match = ROUTE_VARIANT_RE.match(route_short_name)
    if match:
        return match.group(1)
    return route_short_name


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
                "Missing required GTFS files: "
                + ", ".join(sorted(missing))
            )

        routes = read_csv(zin.read("routes.txt"))
        trips = read_csv(zin.read("trips.txt"))
        stop_times = read_csv(zin.read("stop_times.txt"))

        calendar_dates = None
        if "calendar_dates.txt" in names:
            calendar_dates = read_csv(
                zin.read("calendar_dates.txt")
            )

        # ------------------------------------------------------------
        # STEP 1: Remove combination routes
        #
        # Example:
        #   IT::Line:9963-9944-1-1
        #
        # These represent the chained two-train-number services that
        # we do not want represented as direct GTFS trips.
        # ------------------------------------------------------------

        combination_route_ids = {
            row["route_id"]
            for row in routes
            if COMBINATION_ROUTE_RE.match(row["route_id"])
        }

        print(
            f"Combination route IDs found: "
            f"{len(combination_route_ids)}"
        )

        routes_without_combinations = [
            row
            for row in routes
            if row["route_id"] not in combination_route_ids
        ]

        removed_trip_ids = {
            row["trip_id"]
            for row in trips
            if row["route_id"] in combination_route_ids
        }

        trips_without_combinations = [
            row
            for row in trips
            if row["trip_id"] not in removed_trip_ids
        ]

        stop_times_without_combinations = [
            row
            for row in stop_times
            if row["trip_id"] not in removed_trip_ids
        ]

        # ------------------------------------------------------------
        # STEP 2: Merge route variants
        #
        # Examples:
        #   8192_#1 -> 8192
        #   8192_#2 -> 8192
        #   8192_#3 -> 8192
        #
        # We create ONE route record for each normalized public
        # route number.
        # ------------------------------------------------------------

        route_by_public_number = {}

        for row in routes_without_combinations:
            public_number = normalize_route_short_name(
                row["route_short_name"]
            )

            if public_number not in route_by_public_number:
                route_by_public_number[public_number] = row.copy()

        print(
            f"Routes after combination removal: "
            f"{len(routes_without_combinations)}"
        )

        print(
            f"Unique public route numbers after merge: "
            f"{len(route_by_public_number)}"
        )

        # Stable GTFS route ID based on the public route number.
        normalized_route_id = {
            public_number: f"IT::Line:{public_number}"
            for public_number in route_by_public_number
        }

        normalized_routes = []

        for public_number in sorted(
            route_by_public_number,
            key=lambda value: (
                int(value) if value.isdigit() else float("inf"),
                value,
            ),
        ):
            original = route_by_public_number[public_number]

            new_row = original.copy()
            new_row["route_id"] = normalized_route_id[public_number]
            new_row["route_short_name"] = public_number

            # Keep the existing route_long_name for now.
            # We will improve this separately to origin/destination.
            new_row["route_long_name"] = original.get(
                "route_long_name",
                public_number,
            )

            normalized_routes.append(new_row)

        # ------------------------------------------------------------
        # STEP 3: Re-point every remaining trip to its merged route
        # ------------------------------------------------------------

        normalized_trips = []

        for row in trips_without_combinations:
            public_number = normalize_route_short_name(
                next(
                    route["route_short_name"]
                    for route in routes_without_combinations
                    if route["route_id"] == row["route_id"]
                )
            )

            new_row = row.copy()
            new_row["route_id"] = normalized_route_id[public_number]

            normalized_trips.append(new_row)

        # ------------------------------------------------------------
        # STEP 4: Calendar dates
        #
        # Service IDs remain untouched because they belong to trips,
        # not routes.
        #
        # Remove calendar rows belonging to services that no longer
        # have any trip.
        # ------------------------------------------------------------

        normalized_calendar_dates = calendar_dates

        if calendar_dates is not None:
            remaining_service_ids = {
                row["service_id"]
                for row in normalized_trips
            }

            normalized_calendar_dates = [
                row
                for row in calendar_dates
                if row["service_id"] in remaining_service_ids
            ]

        print(
            f"Routes final: {len(normalized_routes)}"
        )
        print(
            f"Trips final: {len(normalized_trips)}"
        )
        print(
            f"Stop times final: {len(stop_times_without_combinations)}"
        )

        if calendar_dates is not None:
            print(
                f"Calendar dates final: "
                f"{len(normalized_calendar_dates)}"
            )

        # ------------------------------------------------------------
        # STEP 5: Write final ZIP
        # ------------------------------------------------------------

        output_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(
            output_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zout:

            for name in names:

                if name == "routes.txt":
                    data = write_csv(
                        normalized_routes,
                        routes[0].keys(),
                    )

                elif name == "trips.txt":
                    data = write_csv(
                        normalized_trips,
                        trips[0].keys(),
                    )

                elif name == "stop_times.txt":
                    data = write_csv(
                        stop_times_without_combinations,
                        stop_times[0].keys(),
                    )

                elif name == "calendar_dates.txt":
                    data = write_csv(
                        normalized_calendar_dates,
                        calendar_dates[0].keys(),
                    )

                else:
                    data = zin.read(name)

                zout.writestr(name, data)

    print(f"Created: {output_zip}")


if __name__ == "__main__":
    main()