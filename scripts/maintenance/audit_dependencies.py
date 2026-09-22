"""Audit installed core + API dependency closure against OSV (opt-in network).

Only public package names/versions are sent. No environment dump or pip install.
This is a known-advisory check, not a lock file or a complete supply-chain audit.
"""

import argparse
import json
import tomllib
from collections import deque
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from urllib.request import Request, urlopen

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT = Path(__file__).resolve().parents[2]


def inventory():
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]
    roots = project["dependencies"] + project["optional-dependencies"]["api"]
    queue = deque((Requirement(value), {""}) for value in roots)
    visited, packages, problems = set(), {}, set()
    while queue:
        requirement, parent_extras = queue.popleft()
        if requirement.marker and not any(
            requirement.marker.evaluate({"extra": extra}) for extra in parent_extras
        ):
            continue
        name = canonicalize_name(requirement.name)
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            problems.add(f"missing:{name}")
            continue
        if distribution.version not in requirement.specifier:
            problems.add(f"constraint_mismatch:{name}:{requirement.specifier}")
        packages[name] = distribution.version
        extras = requirement.extras | {""}
        key = (name, tuple(sorted(extras)))
        if key in visited:
            continue
        visited.add(key)
        for value in distribution.requires or []:
            queue.append((Requirement(value), extras))
    return packages, sorted(problems)


def query(packages):
    results = {name: set() for name in packages}
    pending = [
        {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
        for name, version in sorted(packages.items())
    ]
    # Follow per-package pagination; bounded failure is reported as incomplete.
    for _ in range(10):
        request = Request(
            "https://api.osv.dev/v1/querybatch",
            data=json.dumps({"queries": pending}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read(4_000_001))
        rows = data["results"]
        if len(rows) != len(pending):
            raise ValueError("Incomplete OSV response")
        next_queries = []
        for package, row in zip(pending, rows, strict=True):
            results[package["package"]["name"]].update(
                vulnerability["id"] for vulnerability in row.get("vulns", [])
            )
            if row.get("next_page_token"):
                next_queries.append({**package, "page_token": row["next_page_token"]})
        if not next_queries:
            return {name: sorted(ids) for name, ids in results.items() if ids}
        pending = next_queries
    raise ValueError("OSV pagination limit")


def main(args):
    packages, problems = inventory()
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "installed_core_plus_api_closure_not_full_environment_or_lock",
        "packages": packages,
        "inventory_problems": problems,
        "osv_status": "not_requested",
        "advisories": {},
    }
    if args.osv:
        try:
            report["advisories"] = query(packages)
            report["osv_status"] = "complete"
        except Exception as error:
            report["osv_status"] = "incomplete"
            report["error_type"] = type(error).__name__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({key: value for key, value in report.items() if key != "packages"})
    )
    return int(
        bool(problems or report["advisories"] or report["osv_status"] == "incomplete")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osv", action="store_true", help="Send names/versions to OSV")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT / "knowledge/results/dependency_audit.json",
    )
    raise SystemExit(main(parser.parse_args()))
