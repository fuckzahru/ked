#!/usr/bin/env python3
"""
ked - a cross-platform terminal text editor with syntax highlighting.

Requires:
    python -m pip install prompt_toolkit pygments

Usage:
    python ked.py [options] [file]

Options:
    -l, --lang NAME   force a language
    -h, --help        show help
"""

import sys
from pathlib import Path

try:
    from prompt_toolkit import Application
    from prompt_toolkit.application import run_in_terminal
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.lexers import PygmentsLexer
    from prompt_toolkit.styles import Style, merge_styles
    from prompt_toolkit.widgets import TextArea
except ImportError:
    print("Error: prompt_toolkit is required.", file=sys.stderr)
    print("Install it with:", file=sys.stderr)
    print("  python -m pip install prompt_toolkit", file=sys.stderr)
    sys.exit(1)

try:
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
    from pygments.util import ClassNotFound
    from pygments.styles import get_style_by_name
    from prompt_toolkit.styles.pygments import style_from_pygments_cls
    HAVE_PYGMENTS = True
except ImportError:
    HAVE_PYGMENTS = False


LANG_BY_EXT = {
    "py": "python", "pyw": "python", "pyi": "python",
    "js": "javascript", "mjs": "javascript", "cjs": "javascript", "jsx": "jsx",
    "ts": "typescript", "tsx": "tsx",
    "c": "c", "h": "c",
    "cpp": "cpp", "cc": "cpp", "cxx": "cpp",
    "hpp": "cpp", "hxx": "cpp", "hh": "cpp",
    "rs": "rust", "go": "go", "java": "java",
    "rb": "ruby", "php": "php", "pl": "perl", "lua": "lua",
    "sh": "bash", "bash": "bash", "zsh": "bash", "fish": "fish",
    "json": "json", "jsonc": "json",
    "yaml": "yaml", "yml": "yaml", "toml": "toml", "ini": "ini",
    "html": "html", "htm": "html", "xml": "xml", "svg": "xml", "vue": "vue",
    "css": "css", "scss": "scss", "sass": "sass", "less": "less",
    "sql": "sql",
    "md": "markdown", "markdown": "markdown", "rst": "rst", "tex": "latex",
    "cs": "csharp", "swift": "swift", "kt": "kotlin", "kts": "kotlin",
    "scala": "scala", "hs": "haskell", "ml": "ocaml",
    "ex": "elixir", "exs": "elixir", "erl": "erlang", "clj": "clojure",
    "r": "r", "dart": "dart", "zig": "zig", "nim": "nim",
}

LANG_ALIASES = {
    "python": "python", "py": "python",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "c": "c", "cpp": "cpp", "c++": "cpp", "cxx": "cpp",
    "rust": "rust", "rs": "rust",
    "go": "go", "java": "java",
    "ruby": "ruby", "rb": "ruby", "php": "php", "perl": "perl", "lua": "lua",
    "shell": "bash", "bash": "bash", "sh": "bash", "zsh": "bash",
    "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml", "ini": "ini",
    "html": "html", "xml": "xml",
    "css": "css", "scss": "scss", "sass": "sass", "less": "less",
    "sql": "sql",
    "markdown": "markdown", "md": "markdown", "rst": "rst",
    "latex": "latex", "tex": "latex",
    "csharp": "csharp", "cs": "csharp", "swift": "swift",
    "kotlin": "kotlin", "kt": "kotlin", "scala": "scala",
    "haskell": "haskell", "hs": "haskell", "ocaml": "ocaml", "ml": "ocaml",
    "elixir": "elixir", "erlang": "erlang", "clojure": "clojure",
    "r": "r", "dart": "dart", "zig": "zig", "nim": "nim",
    "text": "text", "plain": "text", "txt": "text",
}


def detect_lexer(path, first_line, lang_name=None):
    if not HAVE_PYGMENTS:
        return None
    if lang_name:
        name = LANG_ALIASES.get(lang_name.lower(), lang_name.lower())
        try:
            return get_lexer_by_name(name)
        except ClassNotFound:
            return None
    if path:
        ext = Path(path).suffix.lower().lstrip(".")
        if ext in LANG_BY_EXT:
            try:
                return get_lexer_by_name(LANG_BY_EXT[ext])
            except ClassNotFound:
                pass
        try:
            return get_lexer_for_filename(path)
        except ClassNotFound:
            pass
    if first_line.startswith("#!"):
        for token in first_line[2:].split():
            token = token.split("/")[-1]
            alias = LANG_ALIASES.get(token)
            if alias:
                try:
                    return get_lexer_by_name(alias)
                except ClassNotFound:
                    pass
    return None


class KedApp:
    def __init__(self, path=None, lang_name=None):
        self.path = None
        self.dirty = False
        content = ""
        first_line = ""

        if path:
            p = Path(path).expanduser()
            self.path = str(p.resolve())
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    content = content.replace("\r\n", "\n").replace("\r", "\n")
                except OSError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)
                if content:
                    first_line = content.split("\n", 1)[0]

        lexer = detect_lexer(self.path, first_line, lang_name)

        self.text_area = TextArea(
            text=content,
            scrollbar=True,
            line_numbers=True,
            wrap_lines=False,
            multiline=True,
            focusable=True,
            lexer=PygmentsLexer(lexer.__class__) if lexer else None,
        )
        self.text_area.buffer.on_text_changed += self._mark_dirty

        self.message = "Ctrl+S save  Ctrl+Q quit  Ctrl+O open  Ctrl+F find  Ctrl+G goto"
        self.message_bar = Window(
            FormattedTextControl(lambda: [("class:msg", f" {self.message}")]),
            height=1,
            style="class:msgbar",
        )
        self.status_bar = Window(
            FormattedTextControl(self._status_text),
            height=1,
            style="class:status",
        )

        self.kb = KeyBindings()
        self._setup_keybindings()

        base_style = Style.from_dict({
            "status": "reverse",
            "msg":    "fg:#ffd75f",
            "msgbar": "bg:#333333",
        })
        if HAVE_PYGMENTS:
            try:
                self.style = merge_styles(
                    [base_style, style_from_pygments_cls(get_style_by_name("monokai"))]
                )
            except Exception:
                self.style = base_style
        else:
            self.style = base_style

        body = HSplit([self.text_area, self.status_bar, self.message_bar])
        self.app = Application(
            layout=Layout(body, focused_element=self.text_area),
            key_bindings=self.kb,
            style=self.style,
            full_screen=True,
            mouse_support=True,
        )

    # -- state ------------------------------------------------------------
    def _mark_dirty(self, _buf):
        self.dirty = True

    def _status_text(self):
        try:
            doc = self.text_area.buffer.document
            row = doc.cursor_position_row + 1
            col = doc.cursor_position_col + 1
        except Exception:
            row, col = 1, 1
        name = Path(self.path).name if self.path else "[No Name]"
        return f" {name}{' [+]' if self.dirty else ''}   Ln {row}, Col {col}"

    def _set_message(self, text):
        self.message = text
        try:
            self.app.invalidate()
        except Exception:
            pass

    # -- keybindings ------------------------------------------------------
    def _setup_keybindings(self):
        kb = self.kb

        @kb.add("c-s")
        def _save(event):
            self.save()

        @kb.add("c-q")
        def _quit(event):
            if self.dirty:
                self._prompt("Save before quit? (y/n): ", self._handle_quit)
            else:
                event.app.exit()

        @kb.add("c-o")
        def _open(event):
            self._prompt("Open: ", self.open_file)

        @kb.add("c-f")
        def _find(event):
            self._prompt("Find: ", self.do_find)

        @kb.add("c-g")
        def _goto(event):
            self._prompt("Go to line: ", self.do_goto)

        @kb.add("c-z")
        def _undo(event):
            event.current_buffer.undo()

        @kb.add("c-y")
        def _redo(event):
            event.current_buffer.redo()

        @kb.add("enter")
        def _enter(event):
            buf = event.current_buffer
            if buf is not self.text_area.buffer:
                buf.insert_text("\n")
                return
            line = buf.document.current_line
            indent = line[: len(line) - len(line.lstrip())]
            if line.rstrip().endswith(":"):
                indent += "    "
            buf.insert_text("\n" + indent)

        @kb.add("tab")
        def _tab(event):
            event.current_buffer.insert_text("    ")

    # -- prompting (fixed) ------------------------------------------------
    def _prompt(self, label, callback):
        # `run_in_terminal` now returns a scheduled Task on recent
        # prompt_toolkit versions. Wrapping in an async function and using
        # `await` works regardless of whether it returns a coroutine or a
        # Task, and gives `create_background_task` a fresh coroutine.
        async def do_prompt():
            try:
                result = await run_in_terminal(lambda: input(label))
                callback(result)
            except (KeyboardInterrupt, EOFError):
                pass
        self.app.create_background_task(do_prompt())

    def _handle_quit(self, text):
        t = text.strip().lower()
        if t == "y":
            if self.path:
                self._do_save(self.path)
                if not self.dirty:
                    self.app.exit()
            else:
                self._prompt("Save as: ", self._do_save_and_quit)
        elif t == "n":
            self.app.exit()

    def _do_save_and_quit(self, path):
        self._do_save(path)
        if not self.dirty:
            self.app.exit()

    # -- file ops ---------------------------------------------------------
    def save(self):
        if not self.path:
            self._prompt("Save as: ", self._do_save)
        else:
            self._do_save(self.path)

    def _do_save(self, path):
        path = (path or "").strip()
        if not path:
            return
        try:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self.text_area.text, encoding="utf-8")
            self.path = str(p.resolve())
            self.dirty = False
            self._set_message(f"Saved {p.name}")
        except OSError as e:
            self._set_message(f"Save failed: {e}")

    def open_file(self, path):
        path = (path or "").strip()
        if not path:
            return
        try:
            p = Path(path).expanduser()
            content = p.read_text(encoding="utf-8", errors="replace")
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            self.text_area.text = content
            self.path = str(p.resolve())
            self.dirty = False
            self._set_message(f"Opened {p.name}")
        except OSError as e:
            self._set_message(f"Open failed: {e}")

    # -- find / goto ------------------------------------------------------
    def do_find(self, text):
        text = (text or "").strip()
        if not text:
            return
        buf = self.text_area.buffer
        start = buf.document.cursor_position + 1
        idx = buf.text.find(text, start)
        if idx < 0:
            idx = buf.text.find(text)
        if idx >= 0:
            buf.cursor_position = idx
            self._set_message(f"Found at position {idx}")
        else:
            self._set_message(f"Not found: {text}")

    def do_goto(self, text):
        try:
            line = int((text or "").strip()) - 1
        except ValueError:
            self._set_message("Invalid line number")
            return
        doc = self.text_area.buffer.document
        line = max(0, min(line, doc.line_count - 1))
        self.text_area.buffer.cursor_position = (
            doc.translate_row_col_to_index(line, 0)
        )

    # -- run --------------------------------------------------------------
    def run(self):
        self.app.run()


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        return

    path = None
    lang = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-l", "--lang"):
            i += 1
            if i >= len(args):
                print("Error: -l needs a value", file=sys.stderr)
                sys.exit(2)
            lang = args[i]
        elif not a.startswith("-"):
            path = a
        i += 1

    if not HAVE_PYGMENTS:
        print(
            "Note: install pygments for syntax highlighting: "
            "python -m pip install pygments",
            file=sys.stderr,
        )

    KedApp(path=path, lang_name=lang).run()


if __name__ == "__main__":
    main()