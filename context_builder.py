import argparse
import fnmatch
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile

# fmt: off
IGNORED_DIRS = {
    ".git", "__pycache__", ".idea", ".vscode", "node_modules", "vendor",
    ".venv", "venv", "env", ".uv", "dist", "build", "out", "target",
    "skills", "coverage", "*cache", ".next", ".nuxt"
}

IGNORED_FILES = {
    ".DS_Store", "context_builder.py", ".env", "*.lock", "*.log",
    "*.pyc", "*.pyo", "*.pyd", "*.html", "*.pdf", "*.zip", "*.tar.gz",
    "*.min.js", "*.min.css", "*.map",
    "*.svg", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.webp",
    "*.mp4", "*.mp3", "*.wav", "*.ttf", "*.woff", "*.woff2",
    "*.sqlite3-journal", "*.sqlite3-wal"
}
# fmt: on

INCLUDE_HIDDEN_FILES = {".env.example", ".gitignore", ".dockerignore"}
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
SQLITE_EXACT_FILENAMES = {"database"}
DATASET_EXTENSIONS = {".csv", ".tsv", ".jsonl"}

SYSTEM_PROMPT = """# SYSTEM INSTRUCTIONS & PROJECT CONTEXT

You are an Expert AI Developer and Architect. You have been provided with the complete context of a software project.

## Document Structure
1. **Project Tree**: An overview of the directory structure. Use this to understand the architecture and module boundaries.
2. **File Contents**: The source code and content of the project files.
3. **Database Schemas**: (If applicable) Extracted SQLite schemas with types and foreign keys.

## Guidelines for Answering
- **Analyze First**: Before writing or modifying code, briefly analyze the relevant files, dependencies, and how they interact (Chain of Thought).
- **Be Precise**: Provide exact, copy-pasteable code changes. Do not hallucinate file names, variables, or functions.
- **Context-Aware**: Strictly respect the existing code style, frameworks, typing conventions, and architectural patterns found in this context.
- **No Placeholders**: Unless explicitly asked, avoid writing lazy comments like `// implement logic here`. Write the actual code.
"""


class ProjectScanner:
    def __init__(self, source_dir, project_name=None, minify=False):
        self.source_dir = os.path.abspath(source_dir)
        self.project_name = project_name or os.path.basename(self.source_dir)
        self.minify = minify
        self.files_included = 0
        self.files_skipped = 0

    def matches_any_pattern(self, name, patterns):
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def is_binary(self, file_path):
        try:
            with open(file_path, "rb") as f:
                chunk = f.read(1024)
                if b"\x00" in chunk:
                    return True
        except Exception:
            return True
        return False

    def get_language_hint(self, filename):
        # fmt: off
        extension_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
            ".html": "html", ".css": "css", ".scss": "scss", ".json": "json", ".md": "markdown",
            ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".xml": "xml", ".csv": "csv", ".tsv": "tsv",
            ".rb": "ruby", ".java": "java", ".c": "c", ".cpp": "cpp", ".cs": "csharp", ".go": "go",
            ".php": "php", ".rs": "rust", ".sql": "sql", ".sh": "shell", ".ini": "ini", ".conf": "conf",
            ".dockerfile": "dockerfile", "Dockerfile": "dockerfile"
        }
        # fmt: on
        _, ext = os.path.splitext(filename)
        return extension_map.get(ext, extension_map.get(filename, ""))

    def safe_minify(self, content, ext):
        if not self.minify:
            return content
        ext = ext.lower()
        if ext == ".css":
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            content = re.sub(r"\s+", " ", content)
        elif ext in [".js", ".ts", ".jsx", ".tsx"]:
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            content = "\n".join([l for l in content.splitlines() if l.strip()])
        return content

    def get_sqlite_schema(self, db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            markdown_output = [f"# SQLite Schema: {os.path.basename(db_path)}\n"]
            for table_name_tuple in tables:
                table_name = table_name_tuple[0]
                if table_name.startswith("sqlite_"):
                    continue
                markdown_output.append(f"## Table: `{table_name}`")
                cursor.execute(f"PRAGMA table_info('{table_name}')")
                columns = cursor.fetchall()
                markdown_output.append("| Column | Type | Nullable | PK | Default |")
                markdown_output.append("|---|---|---|---|---|")
                for col in columns:
                    markdown_output.append(
                        f"| **{col[1]}** | {col[2]} | {'No' if col[3] else 'Yes'} | {'✅' if col[5] else ''} | {f'`{col[4]}`' if col[4] is not None else ''} |"
                    )
                cursor.execute(f"PRAGMA foreign_key_list('{table_name}')")
                fks = cursor.fetchall()
                if fks:
                    markdown_output.append("\n**Foreign Keys:**")
                    for fk in fks:
                        markdown_output.append(
                            f"- `{fk[3]}` references `{fk[2]}({fk[4]})`"
                        )
                markdown_output.append("\n---\n")
            conn.close()
            return (
                "\n".join(markdown_output)
                if len(markdown_output) > 1
                else "Database is empty."
            )
        except Exception as e:
            return f"SQLite Error: {e}"

    def parse_jupyter(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            output = [
                f"```{c['cell_type']}\n{''.join(c.get('source', []))}\n```"
                for c in data.get("cells", [])
                if c.get("cell_type") in ["code", "markdown"]
            ]
            return "\n\n".join(output)
        except Exception as e:
            return f"Error parsing Jupyter: {e}"

    def read_truncated_data(self, file_path, max_lines=15):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [l.rstrip("\n") for _, l in zip(range(max_lines), f)]
            if len(lines) >= max_lines:
                lines.append(f"\n... [TRUNCATED: Showing first {max_lines} lines] ...")
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading dataset: {e}"

    def generate_tree_structure(self):
        tree_lines = [f"# {self.project_name} Project Structure", "```"]
        for root, dirs, files in os.walk(self.source_dir, topdown=True):
            dirs[:] = [d for d in dirs if not self.matches_any_pattern(d, IGNORED_DIRS)]
            files_to_show = [
                f
                for f in files
                if not self.matches_any_pattern(f, IGNORED_FILES)
                and (
                    not f.startswith(".")
                    or self.matches_any_pattern(f, INCLUDE_HIDDEN_FILES)
                )
            ]

            level = root.replace(self.source_dir, "").count(os.sep)
            # Заменяем имя временной папки на реальное имя проекта для корня
            display_name = (
                self.project_name if root == self.source_dir else os.path.basename(root)
            )
            tree_lines.append(f"{' ' * 4 * level}{display_name}/")

            for f in sorted(files_to_show):
                tree_lines.append(f"{' ' * 4 * (level + 1)}{f}")
        tree_lines.append("```")
        return "\n".join(tree_lines)

    def process_directory(self):
        files_data = []
        for root, dirs, files in os.walk(self.source_dir, topdown=True):
            dirs[:] = [d for d in dirs if not self.matches_any_pattern(d, IGNORED_DIRS)]
            for file in sorted(files):
                if self.matches_any_pattern(file, IGNORED_FILES) or (
                    file.startswith(".")
                    and not self.matches_any_pattern(file, INCLUDE_HIDDEN_FILES)
                ):
                    self.files_skipped += 1
                    continue
                file_path = os.path.join(root, file)
                ext_lower = os.path.splitext(file)[1].lower()
                is_sqlite = (ext_lower in SQLITE_EXTENSIONS) or (
                    file in SQLITE_EXACT_FILENAMES
                )
                if not is_sqlite and self.is_binary(file_path):
                    self.files_skipped += 1
                    continue
                try:
                    if is_sqlite:
                        content, lang_hint = (
                            self.get_sqlite_schema(file_path),
                            "markdown",
                        )
                    elif ext_lower == ".ipynb":
                        content, lang_hint = self.parse_jupyter(file_path), "markdown"
                    elif ext_lower in DATASET_EXTENSIONS:
                        content, lang_hint = (
                            self.read_truncated_data(file_path),
                            self.get_language_hint(file),
                        )
                    else:
                        with open(
                            file_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = self.safe_minify(f.read(), ext_lower)
                        lang_hint = self.get_language_hint(file)
                    files_data.append(
                        {
                            "path": os.path.relpath(file_path, self.source_dir),
                            "language": lang_hint,
                            "content": content,
                        }
                    )
                    self.files_included += 1
                except Exception as e:
                    print(f"Error reading {file_path}: {e}")
                    self.files_skipped += 1
        return files_data


class SkillManager:
    def __init__(self, skills_dir="skills"):
        self.skills_dir = skills_dir
        self.available_skills = []
        self.selected_skills = set()
        self._load_skills()

    def _load_skills(self):
        if not os.path.exists(self.skills_dir):
            os.makedirs(self.skills_dir, exist_ok=True)
            return
        self.available_skills = sorted(
            [f for f in os.listdir(self.skills_dir) if f.endswith(".md")]
        )

    def toggle_skill(self, index):
        if 0 <= index < len(self.available_skills):
            skill = self.available_skills[index]
            if skill in self.selected_skills:
                self.selected_skills.remove(skill)
                return f"Removed: {skill}"
            self.selected_skills.add(skill)
            return f"Added: {skill}"
        return "Invalid selection"

    def get_compiled_skills(self):
        if not self.selected_skills:
            return ""
        output = ["# ACTIVE AGENT SKILLS & PERSONAS\n"]
        for s in sorted(list(self.selected_skills)):
            try:
                with open(os.path.join(self.skills_dir, s), "r", encoding="utf-8") as f:
                    output.append(f"## Skill Module: {s}\n{f.read()}\n---\n")
            except Exception as e:
                output.append(f"Error loading {s}: {e}")
        return "\n".join(output)


def format_output(tree, files_data, fmt, skills_content=""):
    if fmt == "json":
        return json.dumps(
            {
                "system_prompt": SYSTEM_PROMPT,
                "skills": skills_content,
                "project_tree": tree,
                "files": files_data,
            },
            indent=2,
            ensure_ascii=False,
        )
    elif fmt == "xml":
        xml = [
            f"<context>\n<system_prompt>{SYSTEM_PROMPT}</system_prompt>",
            f"<skills>{skills_content}</skills>" if skills_content else "",
            f"<project_tree><![CDATA[{tree}]]></project_tree>",
            "<files>",
        ]
        for f in files_data:
            xml.append(
                f'  <file path="{f["path"]}" language="{f["language"]}"><![CDATA[{f["content"].replace("]]>", "]]]]><![CDATA[>")}]]></file>'
            )
        xml.append("</files>\n</context>")
        return "\n".join(xml)
    md = [
        SYSTEM_PROMPT,
        skills_content,
        "\n# PROJECT CONTEXT START\n",
        tree,
        "\n# FILE CONTENTS\n",
    ]
    for f in files_data:
        md.append(
            f"\n---\n**File:** `{f['path']}`\n```{f['language']}\n{f['content']}\n```"
        )
    return "\n".join([p for p in md if p])


def is_git_url(url):
    hosts = ["github.com", "codeberg.org", "gitlab.com", "bitbucket.org"]
    return url.startswith("http") and (
        any(h in url.lower() for h in hosts) or url.endswith(".git")
    )


def run_generation(
    source_input, output_file, fmt="md", minify=False, skill_manager=None
):
    IGNORED_FILES.add(os.path.basename(output_file))
    temp_dir, source_dir, project_name = None, source_input, None

    if is_git_url(source_input):
        if not shutil.which("git"):
            return "ERROR: Git not found in PATH."
        print(f"Cloning {source_input}...")
        temp_dir = tempfile.TemporaryDirectory()
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", source_input, temp_dir.name],
                check=True,
                capture_output=True,
                text=True,
            )
            source_dir = temp_dir.name
            # Улучшенное определение имени проекта из URL
            project_name = source_input.rstrip("/").split("/")[-1].replace(".git", "")
        except subprocess.CalledProcessError as e:
            if temp_dir:
                temp_dir.cleanup()
            return f"ERROR: Git clone failed: {e.stderr}"

    if not os.path.exists(source_dir):
        return f"ERROR: Path '{source_dir}' not found."

    scanner = ProjectScanner(source_dir, project_name=project_name, minify=minify)
    try:
        files_data = scanner.process_directory()
        if not files_data:
            return "WARNING: No files found."
        final_text = format_output(
            scanner.generate_tree_structure(),
            files_data,
            fmt,
            skill_manager.get_compiled_skills() if skill_manager else "",
        )
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final_text)
        return f"SUCCESS! Saved to '{output_file}'\n📊 Stats: {os.path.getsize(output_file) / 1024:.2f} KB | ~{len(final_text) // 3.5:,.0f} tokens\n📁 Files: {scanner.files_included} included | {scanner.files_skipped} skipped"
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        if temp_dir:
            try:
                temp_dir.cleanup()
            except Exception:
                pass


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def get_input_with_cancel(prompt):
    try:
        val = input(f"{prompt} (Enter/'b' to cancel): ").strip()
        return None if not val or val.lower() == "b" else val
    except KeyboardInterrupt:
        return None


def interactive_mode(default_source_dir, default_output_file):
    sm, src, out, fmt, mini, msg = (
        SkillManager(),
        default_source_dir,
        default_output_file,
        "md",
        False,
        "Ready",
    )
    while True:
        clear_screen()
        print(
            f"==========================================\n   AI CONTEXT BUILDER & SKILL SELECTOR\n==========================================\nSource: {src}\nOutput: {out}\nFormat: {fmt.upper()} | Minify: {'ON' if mini else 'OFF'}\n------------------------------------------\nSTATUS: {msg}\n------------------------------------------\nAvailable Skills:"
        )
        for i, s in enumerate(sm.available_skills):
            print(f"  {i + 1}. {'[x]' if s in sm.selected_skills else '[ ]'} {s}")
        print(
            "------------------------------------------\nActions: [S]ource, [O]utput, [F]ormat, [M]inify, [G]enerate, [Q]uit\n=========================================="
        )
        c = input("Select: ").lower()
        if c == "q":
            break
        elif c == "s":
            new = get_input_with_cancel("Path/URL")
            if new:
                src, msg = new, "Source updated"
        elif c == "o":
            new = get_input_with_cancel("Filename")
            if new:
                out, msg = new, "Output updated"
        elif c == "f":
            fmts = ["md", "xml", "json"]
            fmt = fmts[(fmts.index(fmt) + 1) % 3]
            msg = f"Format: {fmt.upper()}"
        elif c == "m":
            mini = not mini
            msg = f"Minify: {'ON' if mini else 'OFF'}"
        elif c == "g":
            print("Generating...")
            msg = run_generation(src, out, fmt, mini, sm)
        elif c.isdigit():
            msg = sm.toggle_skill(int(c) - 1)
        else:
            msg = "Invalid option"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_source", nargs="?")
    parser.add_argument("pos_out", nargs="?")
    parser.add_argument("--dir", "-d")
    parser.add_argument("--out", "-o")
    parser.add_argument("--format", "-f", choices=["md", "xml", "json"], default="md")
    parser.add_argument("--minify", "-m", action="store_true")
    parser.add_argument("--interactive", "-i", action="store_true")
    args = parser.parse_args()
    src = args.dir or args.pos_source
    out = args.out or args.pos_out or f"project_context.{args.format}"
    if args.interactive or not src:
        interactive_mode(src or os.getcwd(), out)
    else:
        print(run_generation(src, out, args.format, args.minify))


if __name__ == "__main__":
    main()
