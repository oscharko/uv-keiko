# UV Keiko

<div align="center">

**The intelligent dependency updater for your UV projects**

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

*Keiko (恵子) - "wise child" - because your dependencies deserve intelligent updates*

</div>

---

## 🌟 What is UV Keiko?

UV Keiko is a smart dependency updater for Python projects using [UV](https://github.com/astral-sh/uv). While UV doesn't yet have a built-in `uv upgrade --all` command, Keiko fills that gap by intelligently updating all your dependencies to their latest compatible versions while respecting dependency constraints.

### ✨ Why Keiko?

- 🧠 **Smart Resolution**: Analyzes dependency trees to ensure compatibility
- 🔄 **Complete Coverage**: Updates main dependencies, optional dependencies, and dependency groups
- 🛡️ **Safe Updates**: Automatic backups and dry-run mode
- 🚀 **UV Native**: Designed specifically for UV projects
- 📦 **PyPI Direct**: Fetches latest versions directly from PyPI
- 🎯 **Extras Preservation**: Maintains package extras like `fastapi[standard]`

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/uv-keiko.git
cd uv-keiko

# Install dependencies
pip install requests tomli-w packaging

```

### Basic Usage

```bash
# Update all dependencies (with backup)
python uv-keiko.py

# Preview changes without modifying files
python uv-keiko.py --dry-run

# Update without creating backup
python uv-keiko.py --no-backup

# Update specific pyproject.toml
python uv-keiko.py --pyproject /path/to/your/pyproject.toml
```

## 📋 Prerequisites

- Python 3.11+ (for `tomllib` support)
- UV installed and available in PATH
- Required Python packages:
  ```bash
  pip install requests tomli-w packaging
  ```

## 🔧 How It Works

1. **📖 Reads** your `pyproject.toml` and extracts all dependencies
2. **🔍 Analyzes** dependency trees recursively from PyPI
3. **🧮 Resolves** compatible versions respecting constraints
4. **✏️ Updates** all dependency sections:
   - `project.dependencies`
   - `project.optional-dependencies` 
   - `dependency-groups` (PEP 735)
5. **💾 Saves** updated `pyproject.toml` with latest versions

## 🎯 Example

**Before** (`pyproject.toml`):
```toml
[project]
dependencies = [
    "fastapi>=0.100.0",
    "uvicorn>=0.20.0",
    "httpx>=0.25.0"
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "black>=22.0.0"
]
```

**After running Keiko**:
```toml
[project]
dependencies = [
    "fastapi>=0.104.1",
    "uvicorn>=0.24.0", 
    "httpx>=0.27.0"
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.3",
    "black>=23.11.0"
]
```

## 📊 Sample Output

```bash
🚀 UV Keiko - Smart Dependency Updater
==================================================
📖 Reading pyproject.toml
🔍 Analyzing dependencies...
  📦 Processing: fastapi
  ✓ fastapi: 0.104.1
  📦 Processing: uvicorn  
  ✓ uvicorn: 0.24.0
  📦 Processing: httpx
  ✓ httpx: 0.27.0

🔄 Updating main dependencies...
  ✓ fastapi: -> 0.104.1
  ✓ uvicorn: -> 0.24.0
  ✓ httpx: -> 0.27.0

📊 Summary:
  📦 Total 3 packages updated
  🔄 Updated packages:
    • fastapi: -> 0.104.1
    • uvicorn: -> 0.24.0  
    • httpx: -> 0.27.0

✓ Backup created: pyproject.toml.backup
✅ pyproject.toml successfully updated!
💡 Run 'uv lock' to update your lockfile
💡 Run 'uv sync' to sync your environment
```

## 🛠️ Command Line Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview changes without modifying files |
| `--no-backup` | Skip creating backup file |
| `--pyproject PATH` | Specify custom pyproject.toml path |
| `--help` | Show help message |

## 📜 License

This project is licensed under the MIT License.