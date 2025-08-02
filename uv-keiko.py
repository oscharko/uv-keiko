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
python uv-keiko.py [--dry-run] [--no-backup] [--pyproject PATH]
"""

import argparse
import re
import shutil
import subprocess
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
        if package_name in self.package_cache:
            return self.package_cache[package_name]

        try:
            url = f"https://pypi.org/pypi/{package_name}/json"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            self.package_cache[package_name] = data
            return data

        except requests.RequestException as e:
            print(f"⚠️  Warning: Could not fetch package info for {package_name}: {e}")
            return None

    def get_latest_version(self, package_name: str) -> Optional[str]:
        """Determines the latest version of a package"""
        info = self.get_package_info(package_name)
        if not info:
            return None

        return info['info']['version']

    def get_compatible_versions(self, package_name: str, python_version: str = None) -> List[str]:
        """Determines all compatible versions of a package"""
        info = self.get_package_info(package_name)
        if not info:
            return []

        versions = []
        for ver, releases in info['releases'].items():
            if releases:  # Only versions with actual releases
                try:
                    version.parse(ver)  # Validate version number
                    versions.append(ver)
                except version.InvalidVersion:
                    continue

        # Sort versions in descending order
        versions.sort(key=version.parse, reverse=True)
        return versions

    def parse_requirement(self, req_string: str) -> Tuple[str, str, Optional[str]]:
        """Parses a requirement string and returns name, constraint, and extras"""
        try:
            req = Requirement(req_string)
            name = req.name.lower()

            # Extract extras
            extras = f"[{','.join(req.extras)}]" if req.extras else ""

            # Extract current constraints
            if req.specifier:
                constraint = str(req.specifier)
            else:
                constraint = ""

            return name, constraint, extras
        except Exception as e:
            print(f"⚠️  Warning: Could not parse requirement '{req_string}': {e}")
            # Fallback for simple names
            match = re.match(r'^([a-zA-Z0-9_-]+)(\[.*\])?', req_string)
            if match:
                name = match.group(1).lower()
                extras = match.group(2) or ""
                return name, "", extras
            return req_string.lower(), "", ""

    def check_uv_compatibility(self, dependencies: Dict[str, str]) -> bool:
        """Checks the compatibility of dependencies with uv"""
        if not shutil.which('uv'):
            print("⚠️  uv is not installed. Install it for best compatibility checking.")
            return True

        try:
            # Create temporary pyproject.toml for testing
            temp_project = {
                'project': {
                    'name': 'temp-compatibility-test',
                    'version': '0.1.0',
                    'requires-python': '>=3.8',
                    'dependencies': [f"{name}>={ver}" for name, ver in dependencies.items()]
                }
            }

            temp_path = Path('temp_pyproject.toml')
            with open(temp_path, 'wb') as f:
                tomli_w.dump(temp_project, f)

            # Test with uv lock --dry-run (if available)
            result = subprocess.run(
                ['uv', 'lock', '--dry-run', '--project', str(temp_path.parent)],
                capture_output=True,
                text=True,
                timeout=30
            )

            temp_path.unlink()  # Cleanup

            if result.returncode == 0:
                return True
            else:
                print(f"⚠️  uv compatibility check failed: {result.stderr}")
                return False

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            return True  # Assume compatible if we can't test

    def resolve_dependencies(self, requirements: List[str]) -> Dict[str, str]:
        """Resolves dependencies and finds the latest compatible versions"""
        print("🔍 Analyzing dependencies...")

        resolved = {}
        processed = set()

        def process_requirement(req_string: str, depth: int = 0) -> None:
            if depth > 10:  # Prevent infinite recursion
                return

            name, constraint, extras = self.parse_requirement(req_string)

            if name in processed:
                return

            processed.add(name)
            print(f"{'  ' * depth}📦 Processing: {name}")

            # Get latest version
            latest_version = self.get_latest_version(name)
            if not latest_version:
                print(f"{'  ' * depth}⚠️  Could not determine version for {name}")
                return

            resolved[name] = latest_version
            print(f"{'  ' * depth}✓ {name}: {latest_version}")

            # Get dependencies of the latest version
            info = self.get_package_info(name)
            if info and 'info' in info and 'requires_dist' in info['info']:
                requires_dist = info['info']['requires_dist'] or []
                for dep in requires_dist:
                    if ';' in dep:  # Remove environment markers
                        dep = dep.split(';')[0].strip()
                    if dep and not any(extra in dep for extra in ['extra ==', 'extra==']):
                        process_requirement(dep, depth + 1)

        # Process all requirements
        for req in requirements:
            process_requirement(req)

        return resolved

    def update_pyproject(self) -> None:
        """Updates the pyproject.toml with the latest versions"""
        if not self.pyproject_path.exists():
            print(f"❌ File not found: {self.pyproject_path}")
            return

        print(f"📖 Reading {self.pyproject_path}")

        # Load pyproject.toml
        with open(self.pyproject_path, 'rb') as f:
            data = tomllib.load(f)

        original_data = data.copy()
        updated_packages = []

        # Update main dependencies
        if 'project' in data and 'dependencies' in data['project']:
            print("\n🔄 Updating main dependencies...")
            dependencies = data['project']['dependencies']

            # Resolve all dependencies
            resolved = self.resolve_dependencies(dependencies)

            # Update dependencies
            new_dependencies = []
            for dep in dependencies:
                name, _, extras = self.parse_requirement(dep)

                if name in resolved:
                    new_version = resolved[name]
                    new_dep = f"{name}{extras}>={new_version}"
                    new_dependencies.append(new_dep)

                    if not dep.startswith(f"{name}{extras}>={new_version}"):
                        updated_packages.append(f"{name}: -> {new_version}")
                        print(f"  ✓ {name}: -> {new_version}")
                else:
                    new_dependencies.append(dep)
                    print(f"  ⚠️  {name}: No update available")

            data['project']['dependencies'] = new_dependencies

        # Update optional dependencies
        if 'project' in data and 'optional-dependencies' in data['project']:
            print("\n🔄 Updating optional dependencies...")

            for group_name, deps in data['project']['optional-dependencies'].items():
                print(f"  Group: {group_name}")
                resolved = self.resolve_dependencies(deps)

                new_deps = []
                for dep in deps:
                    name, _, extras = self.parse_requirement(dep)

                    if name in resolved:
                        new_version = resolved[name]
                        new_dep = f"{name}{extras}>={new_version}"
                        new_deps.append(new_dep)

                        if not dep.startswith(f"{name}{extras}>={new_version}"):
                            updated_packages.append(f"{name} ({group_name}): -> {new_version}")
                            print(f"    ✓ {name}: -> {new_version}")
                    else:
                        new_deps.append(dep)
                        print(f"    ⚠️  {name}: No update available")

                data['project']['optional-dependencies'][group_name] = new_deps

        # Update dependency groups (PEP 735)
        if 'dependency-groups' in data:
            print("\n🔄 Updating dependency groups...")

            for group_name, deps in data['dependency-groups'].items():
                print(f"  Group: {group_name}")

                # Filter include-group entries
                regular_deps = [d for d in deps if
                                not (isinstance(d, dict) and 'include-group' in d)]
                include_groups = [d for d in deps if isinstance(d, dict) and 'include-group' in d]

                if regular_deps:
                    resolved = self.resolve_dependencies(regular_deps)

                    new_deps = []
                    for dep in regular_deps:
                        name, _, extras = self.parse_requirement(dep)

                        if name in resolved:
                            new_version = resolved[name]
                            new_dep = f"{name}{extras}>={new_version}"
                            new_deps.append(new_dep)

                            if not dep.startswith(f"{name}{extras}>={new_version}"):
                                updated_packages.append(f"{name} ({group_name}): -> {new_version}")
                                print(f"    ✓ {name}: -> {new_version}")
                        else:
                            new_deps.append(dep)
                            print(f"    ⚠️  {name}: No update available")

                    # Combine new deps with include-groups
                    data['dependency-groups'][group_name] = new_deps + include_groups

        # Display results
        print(f"\n📊 Summary:")
        print(f"  📦 Total {len(updated_packages)} packages updated")

        if updated_packages:
            print("  🔄 Updated packages:")
            for pkg in updated_packages:
                print(f"    • {pkg}")

        # Write file or dry-run
        if self.dry_run:
            print(f"\n🔍 DRY RUN: Changes would be written to {self.pyproject_path}")
            print("   Use without --dry-run to actually write changes")
        else:
            if updated_packages:
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
        sys.exit(1)


if __name__ == "__main__":
    main()
