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
python uv-keiko.py [--dry-run] [--no-backup] [--pyproject PATH]
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
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

    def extract_version_from_constraint(self, constraint: str) -> Optional[str]:
        """Extracts version number from a constraint string like '>=1.2.3'"""
        if not constraint:
            print(f"      DEBUG: No constraint found")
            return None

        # Handle common patterns: >=1.2.3, ==1.2.3, ~=1.2.3, etc.
        # Also handle complex constraints like ">=1.2.3,<2.0.0"

        # Split by comma and take the first constraint (usually the minimum version)
        first_constraint = constraint.split(',')[0].strip()

        # Extract version using regex
        match = re.search(r'[><=~!]*\s*([0-9]+(?:\.[0-9]+)*(?:\.?[0-9a-zA-Z-]+)*)',
                          first_constraint)
        if match:
            extracted = match.group(1)
            print(f"      DEBUG: Extracted '{extracted}' from constraint '{constraint}'")
            return extracted

        print(f"      DEBUG: Could not extract version from constraint '{constraint}'")
        return None

    def is_version_newer(self, new_version: str, old_version: Optional[str]) -> bool:
        """Checks if new_version is newer than old_version"""
        if not old_version:
            print(f"      DEBUG: No old version, considering update needed")
            return True

        try:
            new_parsed = version.parse(new_version)
            old_parsed = version.parse(old_version)
            is_newer = new_parsed > old_parsed
            print(f"      DEBUG: Comparing {new_version} > {old_version} = {is_newer}")
            return is_newer
        except version.InvalidVersion as e:
            print(f"      DEBUG: Version parsing error ({e}), assuming update needed")
            return True  # Assume it's newer if we can't parse

    def check_uv_compatibility(self, data: dict) -> bool:
        """Test if the current pyproject.toml configuration is compatible using UV"""
        if not shutil.which('uv'):
            print("⚠️  uv is not installed. Skipping compatibility check.")
            return True

        try:
            # Create temporary directory and write pyproject.toml there
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir) / 'pyproject.toml'

                with open(temp_path, 'wb') as f:
                    tomli_w.dump(data, f)

                print("🔍 Testing dependency compatibility with UV (including dev extras)...")

                # Test with uv sync --dry-run --all-extras (like make install does)
                result = subprocess.run(
                    ['uv', 'sync', '--dry-run', '--all-extras'],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if result.returncode == 0:
                    print("✅ Dependency compatibility check passed!")
                    return True
                else:
                    print("❌ Dependency compatibility check failed!")
                    print("UV Error:")
                    if result.stderr:
                        print(result.stderr)
                    if result.stdout:
                        print(result.stdout)
                    return False

        except Exception as e:
            print(f"⚠️  Error during compatibility check: {e}")
            return True  # Assume compatible if we can't test

    def auto_resolve_psutil_safety_conflict(self, data: dict) -> dict:
        """Automatically resolve the common psutil/safety conflict"""
        print("🔧 Auto-resolving psutil/safety conflict...")

        # Strategy 1: Remove safety completely (simplest solution)
        print(
            "🗑️ Removing safety from dev dependencies (security scanning can be done via pre-commit)")

        def remove_safety_from_deps(deps_list):
            new_deps = []
            for dep in deps_list:
                if isinstance(dep, str):
                    name, _, _, extras = self.parse_requirement(dep)
                    if name.lower() != 'safety':
                        new_deps.append(dep)
                    else:
                        print(f"  🗑️ Removed {name}: psutil version conflict")
                else:
                    new_deps.append(dep)
            return new_deps

        # Remove safety from optional dependencies
        if 'project' in data and 'optional-dependencies' in data['project']:
            for group_name, deps in data['project']['optional-dependencies'].items():
                data['project']['optional-dependencies'][group_name] = remove_safety_from_deps(deps)

        # Remove safety from dependency groups
        if 'dependency-groups' in data:
            for group_name, deps in data['dependency-groups'].items():
                new_deps = []
                for dep in deps:
                    if isinstance(dep, str):
                        name, _, _, extras = self.parse_requirement(dep)
                        if name.lower() != 'safety':
                            new_deps.append(dep)
                        else:
                            print(
                                f"  🗑️ Removed {name} from group[{group_name}]: psutil version conflict")
                    else:
                        new_deps.append(dep)
                data['dependency-groups'][group_name] = new_deps

        print("✅ Removed safety to resolve psutil conflict")
        print("💡 Consider using: ruff --select S (security rules) or bandit for security scanning")
        return data

    def auto_resolve_conflicts(self, data: dict, error_output: str) -> dict:
        """Automatically resolve common dependency conflicts"""
        print("\n🛠️ Attempting automatic conflict resolution...")

        # Check for psutil/safety conflict
        error_lower = error_output.lower()
        if "psutil" in error_lower and "safety" in error_lower:
            return self.auto_resolve_psutil_safety_conflict(data)

        # Add more conflict resolution patterns here in the future
        # elif "other_conflict_pattern" in error_lower:
        #     return self.resolve_other_conflict(data)

        print("⚠️  No automatic resolution available for this conflict")
        return data

    def apply_compatible_versions(self, data: dict, package_versions: Dict[str, str]) -> dict:
        """Apply compatible versions from UV lock file to pyproject.toml data"""
        print("🔄 Applying UV-resolved compatible versions...")

        # Update main dependencies
        if 'project' in data and 'dependencies' in data['project']:
            new_deps = []
            for dep in data['project']['dependencies']:
                if isinstance(dep, str):
                    original_name, normalized_name, _, extras = self.parse_requirement(dep)
                    if normalized_name in package_versions:
                        compatible_version = package_versions[normalized_name]
                        new_dep = f"{original_name}{extras}>={compatible_version}"
                        new_deps.append(new_dep)
                        print(f"  ✓ {original_name}: -> {compatible_version} (UV resolved)")
                    else:
                        new_deps.append(dep)
                else:
                    new_deps.append(dep)
            data['project']['dependencies'] = new_deps

        # Update optional dependencies
        if 'project' in data and 'optional-dependencies' in data['project']:
            for group_name, deps in data['project']['optional-dependencies'].items():
                new_deps = []
                for dep in deps:
                    if isinstance(dep, str):
                        original_name, normalized_name, _, extras = self.parse_requirement(dep)
                        if normalized_name in package_versions:
                            compatible_version = package_versions[normalized_name]
                            new_dep = f"{original_name}{extras}>={compatible_version}"
                            new_deps.append(new_dep)
                            print(
                                f"  ✓ {original_name} ({group_name}): -> {compatible_version} (UV resolved)")
                        else:
                            new_deps.append(dep)
                    else:
                        new_deps.append(dep)
                data['project']['optional-dependencies'][group_name] = new_deps

        # Update dependency groups
        if 'dependency-groups' in data:
            for group_name, deps in data['dependency-groups'].items():
                new_deps = []
                for dep in deps:
                    if isinstance(dep, str):
                        original_name, normalized_name, _, extras = self.parse_requirement(dep)
                        if normalized_name in package_versions:
                            compatible_version = package_versions[normalized_name]
                            new_dep = f"{original_name}{extras}>={compatible_version}"
                            new_deps.append(new_dep)
                            print(
                                f"  ✓ {original_name} (group[{group_name}]): -> {compatible_version} (UV resolved)")
                        else:
                            new_deps.append(dep)
                    elif isinstance(dep, dict) and 'include-group' in dep:
                        new_deps.append(dep)
                    else:
                        new_deps.append(dep)
                data['dependency-groups'][group_name] = new_deps

        return data

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
            print(f"      Original constraint: '{old_constraint}'")

            # Get latest version from PyPI
            latest_version = self.get_latest_version(normalized_name)

            if latest_version:
                # Extract current version from the dependency string
                old_version = self.extract_version_from_constraint(old_constraint)
                print(f"      Extracted old version: '{old_version}'")
                print(f"      Latest from PyPI: '{latest_version}'")

                # Check if this is actually an update needed
                is_update_needed = self.is_version_newer(latest_version, old_version)
                print(f"      Update needed: {is_update_needed}")

                # Always build new dependency string with latest version
                new_dep = f"{original_name}{extras}>={latest_version}"
                new_dependencies.append(new_dep)

                if is_update_needed:
                    updated_packages.append(
                        f"{original_name}: {old_version or 'none'} -> {latest_version}")
                    print(f"    ✅ {original_name}: {old_version or 'none'} -> {latest_version}")
                else:
                    print(f"    ✓ {original_name}: already latest ({latest_version})")
            else:
                # Keep original if we couldn't get version info
                new_dependencies.append(dep)
                print(f"    ⚠️  {original_name}: Could not fetch version, keeping original")

        print(f"🔄 Finished updating {group_name} dependencies")
        return new_dependencies, updated_packages

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

        print(f"\n🔍 Starting dependency updates...")

        # Update main dependencies
        if 'project' in data and 'dependencies' in data['project']:
            dependencies = data['project']['dependencies']
            if dependencies:
                print(f"\n📋 Found {len(dependencies)} main dependencies")
                new_deps, updated = self.update_dependency_list(dependencies, "main")
                data['project']['dependencies'] = new_deps
                all_updated_packages.extend(updated)
            else:
                print(f"\n📋 No main dependencies found")

        # Update optional dependencies
        if 'project' in data and 'optional-dependencies' in data['project']:
            print(f"\n🔄 Updating optional dependencies...")

            for group_name, deps in data['project']['optional-dependencies'].items():
                if deps:
                    print(f"\n  📋 Group '{group_name}' has {len(deps)} dependencies")
                    new_deps, updated = self.update_dependency_list(deps, f"optional[{group_name}]")
                    data['project']['optional-dependencies'][group_name] = new_deps
                    all_updated_packages.extend(updated)
                else:
                    print(f"\n  📋 Group '{group_name}' is empty")

        # Update dependency groups (PEP 735)
        if 'dependency-groups' in data:
            print(f"\n🔄 Updating dependency groups...")

            for group_name, deps in data['dependency-groups'].items():
                if deps:
                    print(f"\n  📋 Group '{group_name}' has {len(deps)} dependencies")
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
                        print(f"    📋 Group contains only include-groups, keeping as is")
                        data['dependency-groups'][group_name] = deps
                else:
                    print(f"\n  📋 Group '{group_name}' is empty")

        # Check compatibility with UV
        print(f"\n🔍 Checking dependency compatibility...")
        if not self.check_uv_compatibility(data):
            print(f"\n⚠️  Compatibility issues found! Attempting automatic resolution...")

            # Try automatic conflict resolution first
            try:
                # Try to let UV resolve the dependencies and extract compatible versions
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_project_path = Path(temp_dir) / "pyproject.toml"

                    # Write the conflicting config
                    with open(temp_project_path, 'wb') as f:
                        tomli_w.dump(data, f)

                    # Try uv lock --upgrade to get compatible versions
                    print("🔧 Using 'uv lock --upgrade' to find compatible versions...")
                    result = subprocess.run(
                        ['uv', 'lock', '--upgrade'],
                        cwd=temp_dir,
                        capture_output=True,
                        text=True,
                        timeout=120
                    )

                    if result.returncode == 0:
                        # Read the generated lock file to extract compatible versions
                        lock_path = Path(temp_dir) / "uv.lock"
                        if lock_path.exists():
                            print("✅ UV found compatible versions!")

                            # Parse lock file and update pyproject.toml with compatible versions
                            lock_content = lock_path.read_text()

                            # Extract versions from lock file
                            package_versions = {}
                            lines = lock_content.split('\n')
                            current_package = None

                            for i, line in enumerate(lines):
                                if 'name = "' in line:
                                    current_package = line.split('"')[1].lower()
                                elif 'version = "' in line and current_package:
                                    version_str = line.split('"')[1]
                                    package_versions[current_package] = version_str
                                    current_package = None

                            print(f"📋 Found {len(package_versions)} package versions in lock file")

                            # Update our data with compatible versions from lock file
                            data = self.apply_compatible_versions(data, package_versions)
                            all_updated_packages = []  # Reset since we're using UV's resolution

                            for pkg, ver in package_versions.items():
                                all_updated_packages.append(f"{pkg}: -> {ver} (UV resolved)")
                    else:
                        print(f"❌ UV could not resolve dependencies automatically")
                        if result.stderr:
                            error_output = result.stderr
                            print(error_output)

                            # Try automatic conflict resolution
                            resolved_data = self.auto_resolve_conflicts(data, error_output)

                            if resolved_data != data:
                                print("🔄 Testing resolved configuration...")
                                if self.check_uv_compatibility(resolved_data):
                                    print("✅ Automatic conflict resolution successful!")
                                    data = resolved_data
                                    # Add a note about the conflict resolution
                                    all_updated_packages.append(
                                        "CONFLICT RESOLVED: Removed safety package for psutil>=7.0.0 compatibility")
                                else:
                                    print(
                                        "❌ Automatic resolution didn't work, using manual suggestions")
                                    self.print_manual_resolution_suggestions(error_output)
                            else:
                                print("❌ No automatic resolution available")
                                self.print_manual_resolution_suggestions(error_output)

            except Exception as e:
                print(f"❌ Error during conflict resolution: {e}")
                print("📝 Proceeding with updated versions (manual resolution may be needed)")

        # Display results
        print(f"\n📊 Summary:")
        print(f"  📦 Total {len(all_updated_packages)} packages updated")

        if all_updated_packages:
            print("  🔄 Updated packages:")
            for pkg in all_updated_packages:
                print(f"    • {pkg}")
        else:
            print("  ✅ No packages needed updates")

        # Write file or dry-run
        if self.dry_run:
            print(f"\n🔍 DRY RUN: Changes would be written to {self.pyproject_path}")
            print("   Use without --dry-run to actually write changes")
        else:
            # Always write the file to update to latest versions
            self.create_backup()

            with open(self.pyproject_path, 'wb') as f:
                tomli_w.dump(data, f)

            print(f"\n✅ {self.pyproject_path} successfully updated with compatible versions!")
            print("💡 Run 'uv lock' to update the uv.lock file")
            print("💡 Run 'uv sync --extra dev' to sync the environment")

    def print_manual_resolution_suggestions(self, error_output: str):
        """Print manual resolution suggestions for conflicts"""
        error_lower = error_output.lower()
        if "psutil" in error_lower and "safety" in error_lower:
            print("\n🔧 Detected psutil/safety conflict!")
            print("💡 Manual fix suggestions:")
            print("   Option 1: Downgrade psutil to 6.1.x:")
            print("   - Change psutil>=7.0.0 to psutil>=6.1.0,<6.2.0 in pyproject.toml")
            print("   Option 2: Remove safety from dev dependencies:")
            print("   - Use ruff --select S or bandit for security scanning instead")
            print("   Option 3: Use older safety version:")
            print("   - Change safety>=3.6.0 to safety>=3.0.0,<3.6.0 in pyproject.toml")
        else:
            print("\n🛠️ Manual resolution may be needed for dependency conflicts")
            print("💡 Check the UV error output above for specific conflict details")


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
