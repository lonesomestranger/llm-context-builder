import argparse
import fnmatch
import json
import os
import re
import sqlite3
import subprocess
import sys
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
        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".jsx": "jsx",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".json": "json",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".md": "markdown",
            ".sh": "shell",
            ".rb": "ruby",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".cs": "csharp",
            ".go": "go",
            ".php": "php",
            ".rs": "rust",
            ".sql": "sql",
            ".xml": "xml",
            ".toml": "toml",
            ".dockerfile": "dockerfile",
            "Dockerfile": "dockerfile",
            ".ini": "ini",
            ".conf": "conf",
            ".csv": "csv",
            ".tsv": "tsv",
        }
        _, ext = os.path.splitext(filename)
        if ext in extension_map:
            return extension_map[ext]
        if filename in extension_map:
            return extension_map[filename]
        return ""

    def safe_minify(self, content, ext):
        if not self.minify:
            return content

        ext = ext.lower()
        if ext in [".css"]:
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            content = re.sub(r"\s+", " ", content)
        elif ext in [".js", ".ts", ".jsx", ".tsx"]:
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            content = "\n".join([line for line in content.splitlines() if line.strip()])
        return content

    def get_sqlite_schema(self, db_path):
        if not os.path.exists(db_path):
            return f"Error: Database file '{db_path}' not found."
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
                    cid, name, dtype, notnull, dflt_value, pk = col
                    is_nullable = "No" if notnull else "Yes"
                    is_pk = "✅" if pk else ""
                    dflt = f"`{dflt_value}`" if dflt_value is not None else ""
                    markdown_output.append(
                        f"| **{name}** | {dtype} | {is_nullable} | {is_pk} | {dflt} |"
                    )

                markdown_output.append("")
                cursor.execute(f"PRAGMA foreign_key_list('{table_name}')")
                fks = cursor.fetchall()

                if fks:
                    markdown_output.append("**Foreign Keys:**")
                    for fk in fks:
                        target_table = fk[2]
                        source_col = fk[3]
                        target_col = fk[4]
                        markdown_output.append(
                            f"- `{source_col}` references `{target_table}({target_col})`"
                        )
                    markdown_output.append("")
                markdown_output.append("---\n")

            conn.close()
            if len(markdown_output) == 1:
                return "Database is empty or contains no user tables."
            return "\n".join(markdown_output)
        except sqlite3.Error as e:
            return f"SQLite Error reading schema: {e}"

    def parse_jupyter(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            output = []
            for cell in data.get("cells", []):
                if cell.get("cell_type") in ["code", "markdown"]:
                    source = "".join(cell.get("source", []))
                    if source.strip():
                        output.append(f"```{cell['cell_type']}\n{source}\n```")
            return "\n\n".join(output)
        except Exception as e:
            return f"Error parsing Jupyter notebook: {e}"

    def read_truncated_data(self, file_path, max_lines=15):
        lines = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(
                            f"\n... [TRUNCATED: Showing first {max_lines} lines only to save context] ..."
                        )
                        break
                    lines.append(line.rstrip("\n"))
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
            indent = " " * 4 * level
            tree_lines.append(f"{indent}{os.path.basename(root)}/")
            subindent = " " * 4 * (level + 1)
            for f in sorted(files_to_show):
                tree_lines.append(f"{subindent}{f}")
        tree_lines.append("```")
        return "\n".join(tree_lines)

    def process_directory(self):
        files_data = []
        for root, dirs, files in os.walk(self.source_dir, topdown=True):
            dirs[:] = [d for d in dirs if not self.matches_any_pattern(d, IGNORED_DIRS)]

            for file in sorted(files):
                if self.matches_any_pattern(file, IGNORED_FILES):
                    self.files_skipped += 1
                    continue

                if file.startswith(".") and not self.matches_any_pattern(
                    file, INCLUDE_HIDDEN_FILES
                ):
                    self.files_skipped += 1
                    continue

                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, self.source_dir)
                _, ext = os.path.splitext(file)
                ext_lower = ext.lower()

                is_sqlite = (ext_lower in SQLITE_EXTENSIONS) or (
                    file in SQLITE_EXACT_FILENAMES
                )

                if not is_sqlite and self.is_binary(file_path):
                    self.files_skipped += 1
                    continue

                try:
                    if is_sqlite:
                        content = self.get_sqlite_schema(file_path)
                        lang_hint = "markdown"
                    elif ext_lower == ".ipynb":
                        content = self.parse_jupyter(file_path)
                        lang_hint = "markdown"
                    elif ext_lower in DATASET_EXTENSIONS:
                        content = self.read_truncated_data(file_path)
                        lang_hint = self.get_language_hint(file)
                    else:
                        with open(
                            file_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = f.read()
                        content = self.safe_minify(content, ext_lower)
                        lang_hint = self.get_language_hint(file)

                    files_data.append(
                        {
                            "path": relative_path,
                            "language": lang_hint,
                            "content": content,
                        }
                    )
                    self.files_included += 1
                except Exception as e:
                    print(f"Could not read file {file_path}: {e}")
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
            script_dir = os.path.dirname(os.path.abspath(__file__))
            potential_path = os.path.join(script_dir, "skills")
            if os.path.exists(potential_path):
                self.skills_dir = potential_path
            else:
                os.makedirs(self.skills_dir, exist_ok=True)
                return
        for f in os.listdir(self.skills_dir):
            if f.endswith(".md"):
                self.available_skills.append(f)
        self.available_skills.sort()

    def toggle_skill(self, index):
        if 0 <= index < len(self.available_skills):
            skill = self.available_skills[index]
            if skill in self.selected_skills:
                self.selected_skills.remove(skill)
                return f"Removed skill: {skill}"
            else:
                self.selected_skills.add(skill)
                return f"Added skill: {skill}"
        return "Invalid skill selection"

    def get_compiled_skills(self):
        if not self.selected_skills:
            return ""
        output = ["# ACTIVE AGENT SKILLS & PERSONAS\n"]
        for skill_file in sorted(list(self.selected_skills)):
            path = os.path.join(self.skills_dir, skill_file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    output.append(f"## Skill Module: {skill_file}\n{f.read()}\n---\n")
            except Exception as e:
                output.append(f"Error loading skill {skill_file}: {e}")
        return "\n".join(output)


def format_output(tree, files_data, fmt, skills_content=""):
    if fmt == "json":
        data = {
            "system_prompt": SYSTEM_PROMPT,
            "skills": skills_content,
            "project_tree": tree,
            "files": files_data,
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    elif fmt == "xml":
        xml_parts = ["<context>", f"<system_prompt>\n{SYSTEM_PROMPT}\n</system_prompt>"]
        if skills_content:
            xml_parts.append(f"<skills>\n{skills_content}\n</skills>")
        xml_parts.append(f"<project_tree>\n<![CDATA[\n{tree}\n]]>\n</project_tree>")
        xml_parts.append("<files>")
        for f in files_data:
            safe_content = f["content"].replace("]]>", "]]]]><![CDATA[>")
            xml_parts.append(
                f"""  <file path="{f["path"]}" language="{f["language"]}">\n<![CDATA[\n{safe_content}\n]]>\n  </file>"""
            )
        xml_parts.append("</files>\n</context>")
        return "\n".join(xml_parts)
    else:
        md_parts = [SYSTEM_PROMPT]
        if skills_content:
            md_parts.append(skills_content)
            md_parts.append("\n# PROJECT CONTEXT START\n")
        md_parts.append(tree)
        md_parts.append("\n# FILE CONTENTS\n")
        for f in files_data:
            md_parts.append(
                f"\n---\n\n**File:** `{f['path']}`\n\n```{f['language']}\n{f['content']}\n```"
            )
        return "\n".join(md_parts)


def is_github_url(url):
    return url.startswith("http") and "github.com" in url


def run_generation(
    source_input, output_file, fmt="md", minify=False, skill_manager=None
):
    IGNORED_FILES.add(os.path.basename(output_file))

    temp_dir = None
    if is_github_url(source_input):
        print(f"Cloning repository {source_input}...")
        temp_dir = tempfile.TemporaryDirectory()
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", source_input, temp_dir.name],
                check=True,
                capture_output=True,
            )
            source_dir = temp_dir.name
            project_name = source_input.rstrip("/").split("/")[-1].replace(".git", "")
        except subprocess.CalledProcessError as e:
            temp_dir.cleanup()
            return f"ERROR: Failed to clone repository. Is git installed? {e}"
    else:
        source_dir = source_input
        project_name = None

    scanner = ProjectScanner(source_dir, project_name=project_name, minify=minify)

    try:
        tree = scanner.generate_tree_structure()
        files_data = scanner.process_directory()
        skills_content = skill_manager.get_compiled_skills() if skill_manager else ""

        final_text = format_output(tree, files_data, fmt, skills_content)

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final_text)

        size_kb = os.path.getsize(output_file) / 1024
        estimated_tokens = len(final_text) // 3.5

        stats = (
            f"SUCCESS! Saved to '{os.path.basename(output_file)}'\n"
            f"📊 Stats: {size_kb:.2f} KB | ~{int(estimated_tokens):,} tokens\n"
            f"📁 Files: {scanner.files_included} included | {scanner.files_skipped} skipped"
        )
        return stats
    except Exception as e:
        return f"ERROR: {str(e)}"
    finally:
        if temp_dir:
            temp_dir.cleanup()


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def get_input_with_cancel(prompt):
    try:
        user_input = input(f"{prompt} (or Press Enter/'b' to cancel): ").strip()
        return None if not user_input or user_input.lower() == "b" else user_input
    except KeyboardInterrupt:
        return None


def interactive_mode(default_source_dir, default_output_file):
    skill_manager = SkillManager()
    current_source_dir = default_source_dir
    output_file = default_output_file
    fmt = "md"
    minify = False
    status_message = "Ready"

    while True:
        clear_screen()
        print("==========================================")
        print("   AI CONTEXT BUILDER & SKILL SELECTOR    ")
        print("==========================================")
        print(f"Source/Repo: {current_source_dir}")
        print(f"Output File: {output_file}")
        print(f"Format:      {fmt.upper()}")
        print(f"Minify:      {'ON' if minify else 'OFF'}")
        print("------------------------------------------")

        if status_message:
            print(f"STATUS:\n{status_message}")
            print("------------------------------------------")

        print("Available Skills (Toggle by number):")
        if not skill_manager.available_skills:
            print("  (No .md files found in ./skills folder)")
        for idx, skill in enumerate(skill_manager.available_skills):
            status = "[x]" if skill in skill_manager.selected_skills else "[ ]"
            print(f"  {idx + 1}. {status} {skill}")

        print("------------------------------------------")
        print("Actions:")
        print("  S. Change Source (Local Dir or GitHub URL)")
        print("  O. Change Output Filename")
        print("  F. Toggle Format (MD -> XML -> JSON)")
        print("  M. Toggle Minification (JS/CSS)")
        print("  G. Generate Context File")
        print("  Q. Quit")
        print("==========================================")

        choice = input("Select option: ").strip().lower()

        if choice == "q":
            clear_screen()
            sys.exit(0)
        elif choice == "s":
            new_dir = get_input_with_cancel("Enter local path or GitHub URL")
            if new_dir:
                current_source_dir = new_dir
                status_message = "Source updated."
        elif choice == "o":
            new_out = get_input_with_cancel("Enter output filename (e.g., context.md)")
            if new_out:
                output_file = new_out
                status_message = "Output filename updated."
        elif choice == "f":
            formats = ["md", "xml", "json"]
            fmt = formats[(formats.index(fmt) + 1) % len(formats)]
            status_message = f"Format changed to {fmt.upper()}."
        elif choice == "m":
            minify = not minify
            status_message = f"Minification turned {'ON' if minify else 'OFF'}."
        elif choice == "g":
            status_message = "Generating... (This may take a moment if cloning a repo)"
            print(status_message)
            status_message = run_generation(
                current_source_dir, output_file, fmt, minify, skill_manager
            )
        elif choice.isdigit():
            idx = int(choice) - 1
            status_message = skill_manager.toggle_skill(idx)
        else:
            status_message = "Invalid option."


def print_usage_hint():
    print("\n--- Usage Hints ---")
    print("1. Interactive Mode: python context_builder.py")
    print("2. Quick Mode:       python context_builder.py .")
    print(
        "3. GitHub Repo:      python context_builder.py https://github.com/user/repo context.xml --format xml"
    )
    print(
        "4. With Flags:       python context_builder.py -d /path -o out.json -f json --minify"
    )
    print("-------------------\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_source", nargs="?", help="Source directory or GitHub URL")
    parser.add_argument("pos_out", nargs="?", help="Output filename")
    parser.add_argument("--dir", "-d", help="Source directory or GitHub URL")
    parser.add_argument("--out", "-o", help="Output filename")
    parser.add_argument(
        "--format",
        "-f",
        choices=["md", "xml", "json"],
        default="md",
        help="Output format",
    )
    parser.add_argument(
        "--minify",
        "-m",
        action="store_true",
        help="Enable safe minification for JS/CSS",
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Force interactive mode"
    )

    args = parser.parse_args()

    source_dir = args.dir if args.dir else args.pos_source
    output_file = args.out if args.out else args.pos_out
    if not output_file:
        output_file = f"project_context.{args.format}"

    should_run_interactive = args.interactive or (
        not source_dir and not args.pos_source
    )

    if should_run_interactive:
        start_dir = source_dir if source_dir else os.getcwd()
        interactive_mode(start_dir, output_file)
    else:
        print(f"Scanning project: {source_dir}")
        result_msg = run_generation(source_dir, output_file, args.format, args.minify)
        print(result_msg)


if __name__ == "__main__":
    main()
