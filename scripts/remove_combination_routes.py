#!/usr/bin/env python3

import csv
import io
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date
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


def sort_public_route_number(value):
    if value.isdigit():
        return (0, int(value), value)
    return (1, value)


def build_service_id_metadata(calendar_dates):
    """
    Build metadata from actual operating dates.

    exception_type:
        1 = service operates
        2 = service does not operate

    For each service_id:
        operating_dates = all type 1 dates minus all type 2 dates

    Returns:
        {
            service_id: {
                "dates": set[date],
                "first_date": date,
                "last_date": date,
                "weekday_mask": str,
                "new_service_id": str,
            }
        }
    """

    type_1_dates = defaultdict(set)
    type_2_dates = defaultdict(set)

    for row in calendar_dates:
        service_id = row["service_id"]
        current_date = date.fromisoformat(row["date"])

        if row["exception_type"] == "1":
            type_1_dates[service_id].add(current_date)

        elif row["exception_type"] == "2":
            type_2_dates[service_id].add(current_date)

    metadata = {}

    for service_id in set(type_1_dates) | set(type_2_dates):
        operating_dates = (
            type_1_dates[service_id]
            - type_2_dates[service_id]
        )

        if not operating_dates:
            metadata[service_id] = {
                "dates": set(),
                "first_date": None,
                "last_date": None,
                "weekday_mask": None,
                "new_service_id": service_id,
            }
            continue

        first_date = min(operating_dates)
        last_date = max(operating_dates)

        # Monday -> Sunday.
        weekday_bits = []

        for weekday in range(7):
            present = any(
                current_date.weekday() == weekday
                for current_date in operating_dates
            )
            weekday_bits.append("1" if present else "0")

        weekday_mask = "".join(weekday_bits)

        new_service_id = (
            f"{service_id}_"
            f"{first_date.isoformat()}_"
            f"{last_date.isoformat()}_"
            f"{weekday_mask}"
        )

        metadata[service_id] = {
            "dates": operating_dates,
            "first_date": first_date,
            "last_date": last_date,
            "weekday_mask": weekday_mask,
            "new_service_id": new_service_id,
        }

    return metadata


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
        # These are the chained two-train-number services that we do
        # not want represented as direct GTFS trips.
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
        # One GTFS route is created for each public route number.
        # Trips remain separate.
        # ------------------------------------------------------------

        route_by_public_number = {}

        # Also keep a direct lookup from original route_id to
        # normalized public route number.
        public_number_by_route_id = {}

        for row in routes_without_combinations:
            public_number = normalize_route_short_name(
                row["route_short_name"]
            )

            public_number_by_route_id[row["route_id"]] = (
                public_number
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

        normalized_route_id = {
            public_number: f"IT::Line:{public_number}"
            for public_number in route_by_public_number
        }

        normalized_routes = []

        for public_number in sorted(
            route_by_public_number,
            key=sort_public_route_number,
        ):
            original = route_by_public_number[public_number]

            new_row = original.copy()

            new_row["route_id"] = normalized_route_id[
                public_number
            ]

            new_row["route_short_name"] = public_number

            # Keep the existing route_long_name for now.
            # We will change this separately to origin/destination.
            new_row["route_long_name"] = original.get(
                "route_long_name",
                public_number,
            )

            normalized_routes.append(new_row)

        # ------------------------------------------------------------
        # STEP 3: Re-point trips to merged routes
        # ------------------------------------------------------------

        normalized_trips = []

        for row in trips_without_combinations:
            original_route_id = row["route_id"]

            try:
                public_number = public_number_by_route_id[
                    original_route_id
                ]
            except KeyError:
                raise SystemExit(
                    "Could not find route mapping for "
                    f"trip {row['trip_id']}: "
                    f"{original_route_id}"
                )

            new_row = row.copy()

            new_row["route_id"] = normalized_route_id[
                public_number
            ]

            normalized_trips.append(new_row)

        # ------------------------------------------------------------
        # STEP 4: Keep only calendar dates belonging to remaining
        # services.
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

        # ------------------------------------------------------------
        # STEP 5: Enrich service_id from actual operating dates
        #
        # New format:
        #
        #   <original_service_id>_
        #   <first_date>_
        #   <last_date>_
        #   <weekday_mask>
        #
        # Example:
        #
        #   IT::DayType:8111--1-1-1-1
        #
        # becomes:
        #
        #   IT::DayType:8111--1-1-1-1_
        #   2026-09-24_
        #   2026-09-28_
        #   1001111
        #
        # Weekday order:
        #   Monday Tuesday Wednesday Thursday Friday Saturday Sunday
        #
        # exception_type=1 dates are operating dates.
        # exception_type=2 dates are removed from them.
        # ------------------------------------------------------------

        normalized_service_id = {}

        if normalized_calendar_dates is not None:
            service_metadata = build_service_id_metadata(
                normalized_calendar_dates
            )

            for service_id, info in service_metadata.items():
                new_service_id = info["new_service_id"]

                normalized_service_id[service_id] = (
                    new_service_id
                )

                if info["first_date"] is not None:
                    print(
                        f"Service {service_id}"
                        f" -> {new_service_id}"
                    )
                else:
                    print(
                        f"WARNING: Service {service_id} "
                        "has no operating dates (exception_type=1). "
                        "Keeping original service_id."
                    )

            # Rename service_id in calendar_dates.
            renamed_calendar_dates = []

            for row in normalized_calendar_dates:
                old_service_id = row["service_id"]

                new_row = row.copy()

                new_row["service_id"] = normalized_service_id.get(
                    old_service_id,
                    old_service_id,
                )

                renamed_calendar_dates.append(new_row)

            normalized_calendar_dates = renamed_calendar_dates

            # Rename service_id in trips.
            renamed_trips = []

            for row in normalized_trips:
                old_service_id = row["service_id"]

                new_row = row.copy()

                new_row["service_id"] = normalized_service_id.get(
                    old_service_id,
                    old_service_id,
                )

                renamed_trips.append(new_row)

            normalized_trips = renamed_trips

        # ------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------

        print()
        print(
            f"Routes final: "
            f"{len(normalized_routes)}"
        )

        print(
            f"Trips final: "
            f"{len(normalized_trips)}"
        )

        print(
            f"Stop times final: "
            f"{len(stop_times_without_combinations)}"
        )

        if calendar_dates is not None:
            print(
                f"Calendar dates final: "
                f"{len(normalized_calendar_dates)}"
            )

        # ------------------------------------------------------------
        # STEP 6: Write final ZIP
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

    print()
    print(f"Created: {output_zip}")


if __name__ == "__main__":
    main()