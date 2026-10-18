# ked

A small terminal text editor with syntax highlighting, written in Python with `curses`. It runs directly in your terminal, edits plain files, and highlights code for many common languages.

## Usage:
    python3 ked.py [options] [file]

## Options:
    -l, --lang NAME   force a language (python, javascript, rust, ...)
    -h, --help        show help
    
## Features
- Single-file editor with no third-party dependencies on Unix-like systems
- Syntax highlighting for Python, JavaScript, TypeScript, C, C++, Rust, Go, Java, JSON, HTML, CSS, Shell, SQL, YAML, Markdown, and plain text
- Automatic language detection by file extension, or by shebang when there is no extension
- Manual language override with -l / --lang
- Undo / redo with grouping for rapid typing
- Find, go-to-line, save, save-as, and open-file prompts
- Auto-indent: copies leading indentation on new lines; adds one indent after a Python line ending in :
- Horizontal and vertical scrolling
- Status bar with filename, dirty marker, language, and cursor position
- Works with new files: opening a non-existent path starts an empty buffer

Requirements
- Python 3.8 or newer
- curses module
==Linux/macOS: usually included with Python==
==Windows: install windows-curses==

'python -m pip install windows-curses'

A terminal at least 20 columns wide and 4 rows tall. Smaller terminals show a "Terminal too small" message.
