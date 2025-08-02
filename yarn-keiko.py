#!/usr/bin/env python3
"""
Yarn Keiko - Smart Frontend Dependency Updater

This script updates all packages in package.json to the latest available versions,
while respecting dependency constraints.

Prerequisites:
- Python 3.11+
- requests: pip install requests
- packaging: pip install packaging
- yarn: npm install -g yarn (or use npm instead)

Usage:
python yarn-keiko.py [--dry-run] [--no-backup] [--package-json PATH] [--use-npm]
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import requests
    from packaging import version
except ImportError as e:
    print(f"Error: Required library not installed: {e}")
    print("Install with: pip install requests packaging")
    sys.exit(1)


class PackageUpdater:
    def __init__(self, package_json_path: Path, dry_run: bool = False, backup: bool = True, use_npm: bool = False):
        self.package_json_path = package_json_path
        self.dry_run = dry_run
        self.backup = backup
        self.use_npm = use_npm
        self.package_manager = "npm" if use_npm else "yarn"
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Yarn-Keiko/1.0'})

        # Cache for npm registry requests
        self.package_cache: Dict[str, dict] = {}

    def create_backup(self) -> None:
        """Creates a backup of the package.json file"""
        if self.backup and self.package_json_path.exists():
            backup_path = self.package_json_path.with_suffix('.json.backup')
            shutil.copy2(self.package_json_path, backup_path)
            print(f"✓ Backup created: {backup_path}")

    def get_package_info(self, package_name: str) -> Optional[dict]:
        """Fetches package information from npm registry"""
        if package_name in self.package_cache:
            return self.package_cache[package_name]

        try:
            # Handle scoped packages (e.g., @types/node)
            encoded_name = package_name.replace('/', '%2F')
            url = f"https://registry.npmjs.org/{encoded_name}"
            response = self.session.get(url, timeout=15)
            response.raise_for_status()

            data = response.json()
            self.package_cache[package_name] = data
            return data

        except requests.RequestException as e:
            print(f"⚠️  Warning: Could not fetch package info for {package_name}: {e}")
            return None

    def get_latest_version(self, package_name: str) -> Optional[str]:
        """Gets the latest stable version of a package from npm registry"""
        info = self.get_package_info(package_name)
        if not info:
            return None

        # Get the latest version from npm registry
        latest = info.get('dist-tags', {}).get('latest')
        if latest:
            print(f"  📡 npm latest for {package_name}: {latest}")
            return latest

        return None

    def parse_version_constraint(self, constraint: str) -> Tuple[str, Optional[str]]:
        """Parses a version constraint and returns the operator and version"""
        if not constraint:
            return "", None

        # Handle common patterns: ^1.2.3, ~1.2.3, >=1.2.3, 1.2.3, etc.
        match = re.match(r'^([~^>=<]*)(.+)$', constraint.strip())
        if match:
            operator = match.group(1)
            version_str = match.group(2)
            return operator, version_str

        return "", constraint

    def extract_version_from_constraint(self, constraint: str) -> Optional[str]:
        """Extracts version number from a constraint string like '^1.2.3'"""
        if not constraint:
            print(f"      DEBUG: No constraint found")
            return None

        # Remove operators and get just the version
        _, version_str = self.parse_version_constraint(constraint)
        if version_str:
            print(f"      DEBUG: Extracted '{version_str}' from constraint '{constraint}'")
            return version_str

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

    def check_package_manager_compatibility(self, data: dict) -> bool:
        """Test if the current package.json configuration is compatible using yarn/npm"""
        if not shutil.which(self.package_manager):
            print(f"⚠️  {self.package_manager} is not installed. Skipping compatibility check.")
            return True

        try:
            # Create temporary directory and write package.json there
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir) / 'package.json'

                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)

                print(f"🔍 Testing dependency compatibility with {self.package_manager}...")

                # Test with yarn install --dry-run or npm install --dry-run
                cmd = [self.package_manager, 'install', '--dry-run']
                if self.use_npm:
                    # npm doesn't have --dry-run for install, use audit dry-run instead
                    result = subprocess.run(
                        [self.package_manager, 'audit', '--dry-run'],
                        cwd=temp_dir,
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    # For npm, we'll consider it successful if audit doesn't fail catastrophically
                    if result.returncode in [0, 1]:  # 1 is common for audit warnings
                        print("✅ Dependency compatibility check passed!")
                        return True
                else:
                    result = subprocess.run(
                        cmd,
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
                    print(f"{self.package_manager.title()} Error:")
                    if result.stderr:
                        print(result.stderr)
                    if result.stdout:
                        print(result.stdout)
                    return False

        except Exception as e:
            print(f"⚠️  Error during compatibility check: {e}")
            return True  # Assume compatible if we can't test

    def auto_resolve_common_conflicts(self, data: dict, error_output: str) -> dict:
        """Automatically resolve common dependency conflicts"""
        print("🔧 Auto-resolving common frontend conflicts...")

        error_lower = error_output.lower()

        # React version conflicts
        if "react" in error_lower and ("peer" in error_lower or "version" in error_lower):
            print("🔧 Detected React version conflict, trying to align versions...")
            return self.align_react_versions(data)

        # TypeScript conflicts
        if "typescript" in error_lower or "@types" in error_lower:
            print("🔧 Detected TypeScript version conflict, trying to resolve...")
            return self.resolve_typescript_conflicts(data)

        # ESLint conflicts
        if "eslint" in error_lower:
            print("🔧 Detected ESLint conflict, trying to resolve...")
            return self.resolve_eslint_conflicts(data)

        print("⚠️  No automatic resolution available for this conflict")
        return data

    def align_react_versions(self, data: dict) -> dict:
        """Align React and React-related package versions"""
        print("🔄 Aligning React ecosystem versions...")

        # Get React version to align others with
        react_version = None
        if 'dependencies' in data and 'react' in data['dependencies']:
            react_constraint = data['dependencies']['react']
            react_version = self.extract_version_from_constraint(react_constraint)

        if react_version:
            print(f"📌 Using React {react_version} as base version")

            # Align react-dom
            if 'dependencies' in data and 'react-dom' in data['dependencies']:
                data['dependencies']['react-dom'] = f"^{react_version}"
                print(f"  ✓ Aligned react-dom to ^{react_version}")

            # Align @types/react and @types/react-dom if they exist
            if 'devDependencies' in data:
                if '@types/react' in data['devDependencies']:
                    # Use latest types for the React version
                    types_info = self.get_package_info('@types/react')
                    if types_info:
                        latest_types = types_info.get('dist-tags', {}).get('latest')
                        if latest_types:
                            data['devDependencies']['@types/react'] = f"^{latest_types}"
                            print(f"  ✓ Updated @types/react to ^{latest_types}")

                if '@types/react-dom' in data['devDependencies']:
                    types_info = self.get_package_info('@types/react-dom')
                    if types_info:
                        latest_types = types_info.get('dist-tags', {}).get('latest')
                        if latest_types:
                            data['devDependencies']['@types/react-dom'] = f"^{latest_types}"
                            print(f"  ✓ Updated @types/react-dom to ^{latest_types}")

        return data

    def resolve_typescript_conflicts(self, data: dict) -> dict:
        """Resolve TypeScript and @types conflicts"""
        print("🔄 Resolving TypeScript conflicts...")

        # Often @types packages conflict with each other
        # Strategy: Use latest stable TypeScript and align @types packages
        if 'devDependencies' in data and 'typescript' in data['devDependencies']:
            ts_info = self.get_package_info('typescript')
            if ts_info:
                latest_ts = ts_info.get('dist-tags', {}).get('latest')
                if latest_ts:
                    data['devDependencies']['typescript'] = f"^{latest_ts}"
                    print(f"  ✓ Updated TypeScript to ^{latest_ts}")

        return data

    def resolve_eslint_conflicts(self, data: dict) -> dict:
        """Resolve ESLint plugin conflicts"""
        print("🔄 Resolving ESLint conflicts...")

        # ESLint plugins often have peer dependency conflicts
        # Strategy: Use latest ESLint and compatible plugin versions
        if 'devDependencies' in data:
            eslint_packages = [pkg for pkg in data['devDependencies'].keys() if 'eslint' in pkg.lower()]

            for pkg in eslint_packages:
                if pkg == 'eslint':
                    # Update main ESLint to latest
                    eslint_info = self.get_package_info('eslint')
                    if eslint_info:
                        latest_eslint = eslint_info.get('dist-tags', {}).get('latest')
                        if latest_eslint:
                            data['devDependencies']['eslint'] = f"^{latest_eslint}"
                            print(f"  ✓ Updated ESLint to ^{latest_eslint}")

        return data

    def auto_resolve_conflicts(self, data: dict, error_output: str) -> dict:
        """Automatically resolve common dependency conflicts"""
        print("\n🛠️ Attempting automatic conflict resolution...")
        return self.auto_resolve_common_conflicts(data, error_output)

    def apply_compatible_versions(self, data: dict, package_versions: Dict[str, str]) -> dict:
        """Apply compatible versions from lock file to package.json data"""
        print("🔄 Applying package manager resolved compatible versions...")

        # Update dependencies
        if 'dependencies' in data:
            for pkg_name in data['dependencies']:
                if pkg_name in package_versions:
                    compatible_version = package_versions[pkg_name]
                    data['dependencies'][pkg_name] = f"^{compatible_version}"
                    print(f"  ✓ {pkg_name}: -> ^{compatible_version} (resolved)")

        # Update devDependencies
        if 'devDependencies' in data:
            for pkg_name in data['devDependencies']:
                if pkg_name in package_versions:
                    compatible_version = package_versions[pkg_name]
                    data['devDependencies'][pkg_name] = f"^{compatible_version}"
                    print(f"  ✓ {pkg_name} (dev): -> ^{compatible_version} (resolved)")

        # Update peerDependencies (but don't change them automatically)
        # peerDependencies are usually left as-is

        # Update optionalDependencies
        if 'optionalDependencies' in data:
            for pkg_name in data['optionalDependencies']:
                if pkg_name in package_versions:
                    compatible_version = package_versions[pkg_name]
                    data['optionalDependencies'][pkg_name] = f"^{compatible_version}"
                    print(f"  ✓ {pkg_name} (optional): -> ^{compatible_version} (resolved)")

        return data

    def update_dependency_group(self, dependencies: Dict[str, str], group_name: str = "dependencies") -> Tuple[
        Dict[str, str], List[str]]:
        """Updates a group of dependencies to their latest versions"""
        print(f"🔄 Updating {group_name}...")

        new_dependencies = {}
        updated_packages = []

        for pkg_name, current_constraint in dependencies.items():
            print(f"  📦 Processing: {pkg_name}")
            print(f"      Current constraint: '{current_constraint}'")

            # Get latest version from npm registry
            latest_version = self.get_latest_version(pkg_name)

            if latest_version:
                # Extract current version from the constraint
                old_version = self.extract_version_from_constraint(current_constraint)
                print(f"      Extracted old version: '{old_version}'")
                print(f"      Latest from npm: '{latest_version}'")

                # Check if this is actually an update needed
                is_update_needed = self.is_version_newer(latest_version, old_version)
                print(f"      Update needed: {is_update_needed}")

                # Use ^ prefix for semantic versioning (most common in frontend)
                new_constraint = f"^{latest_version}"
                new_dependencies[pkg_name] = new_constraint

                if is_update_needed:
                    updated_packages.append(
                        f"{pkg_name}: {old_version or 'none'} -> {latest_version}")
                    print(f"    ✅ {pkg_name}: {old_version or 'none'} -> {latest_version}")
                else:
                    print(f"    ✓ {pkg_name}: already latest ({latest_version})")
            else:
                # Keep original if we couldn't get version info
                new_dependencies[pkg_name] = current_constraint
                print(f"    ⚠️  {pkg_name}: Could not fetch version, keeping original")

        print(f"🔄 Finished updating {group_name}")
        return new_dependencies, updated_packages

    def update_package_json(self) -> None:
        """Updates the package.json with the latest versions"""
        if not self.package_json_path.exists():
            print(f"❌ File not found: {self.package_json_path}")
            return

        print(f"📖 Reading {self.package_json_path}")

        # Load package.json
        with open(self.package_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        all_updated_packages = []

        print(f"\n🔍 Starting dependency updates...")

        # Update main dependencies
        if 'dependencies' in data and data['dependencies']:
            print(f"\n📋 Found {len(data['dependencies'])} main dependencies")
            new_deps, updated = self.update_dependency_group(data['dependencies'], "dependencies")
            data['dependencies'] = new_deps
            all_updated_packages.extend(updated)
        else:
            print(f"\n📋 No main dependencies found")

        # Update devDependencies
        if 'devDependencies' in data and data['devDependencies']:
            print(f"\n📋 Found {len(data['devDependencies'])} dev dependencies")
            new_deps, updated = self.update_dependency_group(data['devDependencies'], "devDependencies")
            data['devDependencies'] = new_deps
            all_updated_packages.extend(updated)
        else:
            print(f"\n📋 No dev dependencies found")

        # Update optionalDependencies if they exist
        if 'optionalDependencies' in data and data['optionalDependencies']:
            print(f"\n📋 Found {len(data['optionalDependencies'])} optional dependencies")
            new_deps, updated = self.update_dependency_group(data['optionalDependencies'], "optionalDependencies")
            data['optionalDependencies'] = new_deps
            all_updated_packages.extend(updated)

        # Check compatibility with package manager
        print(f"\n🔍 Checking dependency compatibility...")
        if not self.check_package_manager_compatibility(data):
            print(f"\n⚠️  Compatibility issues found! Attempting automatic resolution...")

            # Try automatic conflict resolution
            try:
                # Create a test directory and try to install
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_package_path = Path(temp_dir) / "package.json"

                    # Write the conflicting config
                    with open(temp_package_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2)

                    # Try package manager install to get compatible versions
                    print(f"🔧 Using '{self.package_manager} install' to find compatible versions...")

                    if self.use_npm:
                        install_cmd = ['npm', 'install', '--package-lock-only']
                    else:
                        install_cmd = ['yarn', 'install', '--mode=update-lockfile']

                    result = subprocess.run(
                        install_cmd,
                        cwd=temp_dir,
                        capture_output=True,
                        text=True,
                        timeout=120
                    )

                    if result.returncode == 0:
                        print(f"✅ {self.package_manager.title()} found compatible versions!")

                        # Read the generated lock file to extract compatible versions
                        if self.use_npm:
                            lock_file = Path(temp_dir) / "package-lock.json"
                        else:
                            lock_file = Path(temp_dir) / "yarn.lock"

                        if lock_file.exists():
                            print(f"📋 Reading {lock_file.name} for compatible versions...")

                            # Parse lock file and extract versions
                            package_versions = {}

                            if self.use_npm:
                                # Parse package-lock.json
                                with open(lock_file, 'r', encoding='utf-8') as f:
                                    lock_data = json.load(f)
                                    if 'packages' in lock_data:
                                        for path, info in lock_data['packages'].items():
                                            if path.startswith('node_modules/'):
                                                pkg_name = path[13:]  # Remove 'node_modules/'
                                                if 'version' in info:
                                                    package_versions[pkg_name] = info['version']
                            else:
                                # Parse yarn.lock (simplified)
                                with open(lock_file, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                    # Simple regex to extract package@version
                                    matches = re.findall(r'^"?([^@\s]+)@.*?:\s*version\s+"([^"]+)"', content,
                                                         re.MULTILINE)
                                    for pkg_name, pkg_version in matches:
                                        package_versions[pkg_name] = pkg_version

                            if package_versions:
                                print(f"📋 Found {len(package_versions)} package versions in lock file")
                                # Update our data with compatible versions from lock file
                                data = self.apply_compatible_versions(data, package_versions)
                                all_updated_packages = []  # Reset since we're using resolved versions
                                for pkg, ver in list(package_versions.items())[:10]:  # Show first 10
                                    all_updated_packages.append(f"{pkg}: -> {ver} (resolved)")
                                if len(package_versions) > 10:
                                    all_updated_packages.append(f"... and {len(package_versions) - 10} more packages")
                    else:
                        print(f"❌ {self.package_manager.title()} could not resolve dependencies automatically")
                        if result.stderr:
                            error_output = result.stderr
                            print(error_output)

                            # Try automatic conflict resolution
                            resolved_data = self.auto_resolve_conflicts(data, error_output)

                            if resolved_data != data:
                                print("🔄 Testing resolved configuration...")
                                if self.check_package_manager_compatibility(resolved_data):
                                    print("✅ Automatic conflict resolution successful!")
                                    data = resolved_data
                                    all_updated_packages.append("CONFLICT RESOLVED: Applied automatic fixes")
                                else:
                                    print("❌ Automatic resolution didn't work, using manual suggestions")
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
            print(f"\n🔍 DRY RUN: Changes would be written to {self.package_json_path}")
            print("   Use without --dry-run to actually write changes")
        else:
            # Always write the file to update to latest versions
            self.create_backup()

            with open(self.package_json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write('\n')  # Add trailing newline

            print(f"\n✅ {self.package_json_path} successfully updated with compatible versions!")
            if self.use_npm:
                print("💡 Run 'npm install' to update the package-lock.json and install packages")
                print("💡 Run 'npm audit' to check for security vulnerabilities")
            else:
                print("💡 Run 'yarn install' to update the yarn.lock and install packages")
                print("💡 Run 'yarn audit' to check for security vulnerabilities")

    def print_manual_resolution_suggestions(self, error_output: str):
        """Print manual resolution suggestions for conflicts"""
        error_lower = error_output.lower()

        if "react" in error_lower:
            print("\n🔧 Detected React ecosystem conflict!")
            print("💡 Manual fix suggestions:")
            print("   - Check React and React-DOM versions are aligned")
            print("   - Update @types/react and @types/react-dom to compatible versions")
            print("   - Consider using 'yarn resolutions' or 'npm overrides' for peer dependency issues")
        elif "typescript" in error_lower:
            print("\n🔧 Detected TypeScript conflict!")
            print("💡 Manual fix suggestions:")
            print("   - Ensure TypeScript version is compatible with @types packages")
            print("   - Check if any packages require older TypeScript versions")
        elif "eslint" in error_lower:
            print("\n🔧 Detected ESLint conflict!")
            print("💡 Manual fix suggestions:")
            print("   - Update ESLint plugins to versions compatible with main ESLint")
            print("   - Check peer dependency requirements")
        else:
            print("\n🛠️ Manual resolution may be needed for dependency conflicts")
            print("💡 Check the package manager error output above for specific conflict details")
            print("💡 Consider using 'yarn resolutions' (Yarn) or 'overrides' (npm) for peer dependency conflicts")


def main():
    parser = argparse.ArgumentParser(
        description="Updates all packages in package.json to the latest versions"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying files"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Don't create a backup of package.json"
    )
    parser.add_argument(
        "--package-json",
        type=Path,
        default=Path("package.json"),
        help="Path to package.json (default: ./package.json)"
    )
    parser.add_argument(
        "--use-npm",
        action="store_true",
        help="Use npm instead of yarn for compatibility checks"
    )

    args = parser.parse_args()

    print("🦄 Yarn Keiko - Smart Frontend Dependency Updater")
    print("=" * 50)

    updater = PackageUpdater(
        package_json_path=args.package_json,
        dry_run=args.dry_run,
        backup=not args.no_backup,
        use_npm=args.use_npm
    )

    try:
        updater.update_package_json()
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
