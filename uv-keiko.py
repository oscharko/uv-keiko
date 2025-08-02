#!/usr/bin/env python3
"""
UV Keiko - Smart Dependency Updater

This script updates all packages in pyproject.toml to the latest available versions,
while respecting dependency constraints.

Prerequisites:
- Python 3.11+ (for tomllib)
- tomli-w: pip install tomli-w
- requests: pip install requests
- packaging: pip install packaging

Usage:
python keiko.py [--dry-run] [--no-backup] [--pyproject PATH]
"""

import argparse
import re
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import requests
    import tomli_w
    from packaging import version
    from packaging.requirements import Requirement
except ImportError as e:
    print(f"Error: Required library not installed: {e}")
    print("Install with: pip install requests tomli-w packaging")
    sys.exit(1)


class PackageUpdater:
    def __init__(self, pyproject_path: Path, dry_run: bool = False, backup: bool = True):
        self.pyproject_path = pyproject_path
        self.dry_run = dry_run
        self.backup = backup
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'UV-Keiko/1.0'})

        # Cache for PyPI requests
        self.package_cache: Dict[str, dict] = {}

    def create_backup(self) -> None:
        """Creates a backup of the pyproject.toml file"""
        if self.backup and self.pyproject_path.exists():
            backup_path = self.pyproject_path.with_suffix('.toml.backup')
            shutil.copy2(self.pyproject_path, backup_path)
            print(f"✓ Backup created: {backup_path}")

    def get_package_info(self, package_name: str) -> Optional[dict]:
        """Fetches package information from PyPI"""
        # Normalize package name for PyPI (lowercase, underscores to hyphens)
        normalized_name = package_name.lower().replace('_', '-')

        if normalized_name in self.package_cache:
            return self.package_cache[normalized_name]

        try:
            url = f"https://pypi.org/pypi/{normalized_name}/json"
            response = self.session.get(url, timeout=15)
            response.raise_for_status()

            data = response.json()
            self.package_cache[normalized_name] = data
            return data

        except requests.RequestException as e:
            print(f"⚠️  Warning: Could not fetch package info for {package_name}: {e}")
            return None

    def get_latest_version(self, package_name: str) -> Optional[str]:
        """Gets the latest stable version of a package from PyPI"""
        info = self.get_package_info(package_name)
        if not info:
            return None

        # Get the latest version from PyPI
        latest = info['info']['version']
        print(f"  📡 PyPI latest for {package_name}: {latest}")
        return latest

    def parse_requirement(self, req_string: str) -> Tuple[str, str, str, str]:
        """Parses a requirement string and returns original_name, normalized_name, constraint, extras"""
        try:
            req = Requirement(req_string.strip())
            original_name = req.name  # Keep original casing
            normalized_name = req.name.lower()

            # Extract extras
            extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""

            # Extract current constraints
            constraint = str(req.specifier) if req.specifier else ""

            return original_name, normalized_name, constraint, extras

        except Exception as e:
            print(f"⚠️  Warning: Could not parse requirement '{req_string}': {e}")
            # Fallback for simple names
            match = re.match(r'^([a-zA-Z0-9_-]+)(\[.*\])?', req_string.strip())
            if match:
                name = match.group(1)
                extras = match.group(2) or ""
                return name, name.lower(), "", extras
            return req_string.strip(), req_string.strip().lower(), "", ""

    def update_dependency_list(self, dependencies: List[str], group_name: str = "main") -> Tuple[
        List[str], List[str]]:
        """Updates a list of dependencies to their latest versions"""
        print(f"🔄 Updating {group_name} dependencies...")

        new_dependencies = []
        updated_packages = []

        for dep in dependencies:
            if not dep or not dep.strip():
                continue

            # Handle include-group entries (for dependency-groups)
            if isinstance(dep, dict) and 'include-group' in dep:
                new_dependencies.append(dep)
                continue

            # Parse the dependency
            original_name, normalized_name, old_constraint, extras = self.parse_requirement(dep)

            print(f"  📦 Processing: {original_name}")

            # Get latest version from PyPI
            latest_version = self.get_latest_version(normalized_name)

            if latest_version:
                # Build new dependency string with latest version
                new_dep = f"{original_name}{extras}>={latest_version}"
                new_dependencies.append(new_dep)

                # Check if this is actually an update
                old_version = self.extract_version_from_constraint(old_constraint)
                if self.is_version_newer(latest_version, old_version):
                    updated_packages.append(
                        f"{original_name}: {old_version or 'unknown'} -> {latest_version}")
                    print(f"    ✓ {original_name}: {old_version or 'unknown'} -> {latest_version}")
                else:
                    print(f"    ✓ {original_name}: already latest ({latest_version})")
            else:
                # Keep original if we couldn't get version info
                new_dependencies.append(dep)
                print(f"    ⚠️  {original_name}: Could not fetch version, keeping original")

        return new_dependencies, updated_packages

    def extract_version_from_constraint(self, constraint: str) -> Optional[str]:
        """Extracts version number from a constraint string like '>=1.2.3'"""
        if not constraint:
            return None

        # Handle common patterns: >=1.2.3, ==1.2.3, ~=1.2.3, etc.
        match = re.search(r'[><=~!]*\s*([0-9]+(?:\.[0-9]+)*(?:\.[0-9a-zA-Z-]+)*)', constraint)
        if match:
            return match.group(1)
        return None

    def is_version_newer(self, new_version: str, old_version: Optional[str]) -> bool:
        """Checks if new_version is newer than old_version"""
        if not old_version:
            return True

        try:
            return version.parse(new_version) > version.parse(old_version)
        except version.InvalidVersion:
            return True  # Assume it's newer if we can't parse

    def update_pyproject(self) -> None:
        """Updates the pyproject.toml with the latest versions"""
        if not self.pyproject_path.exists():
            print(f"❌ File not found: {self.pyproject_path}")
            return

        print(f"📖 Reading {self.pyproject_path}")

        # Load pyproject.toml
        with open(self.pyproject_path, 'rb') as f:
            data = tomllib.load(f)

        all_updated_packages = []

        # Update main dependencies
        if 'project' in data and 'dependencies' in data['project']:
            dependencies = data['project']['dependencies']
            if dependencies:
                new_deps, updated = self.update_dependency_list(dependencies, "main")
                data['project']['dependencies'] = new_deps
                all_updated_packages.extend(updated)

        # Update optional dependencies
        if 'project' in data and 'optional-dependencies' in data['project']:
            print(f"\n🔄 Updating optional dependencies...")

            for group_name, deps in data['project']['optional-dependencies'].items():
                if deps:
                    print(f"  Group: {group_name}")
                    new_deps, updated = self.update_dependency_list(deps, f"optional[{group_name}]")
                    data['project']['optional-dependencies'][group_name] = new_deps
                    all_updated_packages.extend(updated)

        # Update dependency groups (PEP 735)
        if 'dependency-groups' in data:
            print(f"\n🔄 Updating dependency groups...")

            for group_name, deps in data['dependency-groups'].items():
                if deps:
                    print(f"  Group: {group_name}")
                    # Filter out include-group entries and process only regular dependencies
                    regular_deps = [d for d in deps if
                                    not (isinstance(d, dict) and 'include-group' in d)]
                    include_groups = [d for d in deps if
                                      isinstance(d, dict) and 'include-group' in d]

                    if regular_deps:
                        new_deps, updated = self.update_dependency_list(regular_deps,
                                                                        f"group[{group_name}]")
                        # Combine updated deps with include-groups
                        data['dependency-groups'][group_name] = new_deps + include_groups
                        all_updated_packages.extend(updated)
                    else:
                        # Only include-groups, keep as is
                        data['dependency-groups'][group_name] = deps

        # Display results
        print(f"\n📊 Summary:")
        print(f"  📦 Total {len(all_updated_packages)} packages updated")

        if all_updated_packages:
            print("  🔄 Updated packages:")
            for pkg in all_updated_packages:
                print(f"    • {pkg}")

        # Write file or dry-run
        if self.dry_run:
            print(f"\n🔍 DRY RUN: Changes would be written to {self.pyproject_path}")
            print("   Use without --dry-run to actually write changes")
        else:
            if all_updated_packages:
                self.create_backup()

                with open(self.pyproject_path, 'wb') as f:
                    tomli_w.dump(data, f)

                print(f"\n✅ {self.pyproject_path} successfully updated!")
                print("💡 Run 'uv lock' to update the uv.lock file")
                print("💡 Run 'uv sync' to sync the environment")
            else:
                print(f"\n✅ All packages are already up to date!")


def main():
    parser = argparse.ArgumentParser(
        description="Updates all packages in pyproject.toml to the latest versions"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying files"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Don't create a backup of pyproject.toml"
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml (default: ./pyproject.toml)"
    )

    args = parser.parse_args()

    print("🦄 UV Keiko - Smart Dependency Updater")
    print("=" * 50)

    updater = PackageUpdater(
        pyproject_path=args.pyproject,
        dry_run=args.dry_run,
        backup=not args.no_backup
    )

    try:
        updater.update_pyproject()
    except KeyboardInterrupt:
        print("\n❌ Cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
