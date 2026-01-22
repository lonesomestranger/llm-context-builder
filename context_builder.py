import argparse
import fnmatch
import os
import sqlite3
import sys

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".idea",
    ".venv",
    "venv",
    ".uv",
    "node_modules",
    "dist",
    "build",
    ".vscode",
    "skills",
}
IGNORED_FILES = {
    ".DS_Store",
    "*.pyc",
    "*.log",
    "*.html",
    "context_builder.py",
    ".env",
    "uv.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
}
INCLUDE_HIDDEN_FILES = {".env.example", ".gitignore", ".dockerignore"}

SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
SQLITE_EXACT_FILENAMES = {"database"}


class ProjectScanner:
    def __init__(self, source_dir):
        self.source_dir = os.path.abspath(source_dir)
        self.project_name = os.path.basename(self.source_dir)

    def matches_any_pattern(self, name, patterns):
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def get_language_hint(self, filename):
        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
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
        }
        _, ext = os.path.splitext(filename)
        if ext in extension_map:
            return extension_map[ext]
        if filename in extension_map:
            return extension_map[filename]
        return ""

    def get_sqlite_schema(self, db_path):
        if not os.path.exists(db_path):
            return f"Error: Database file '{db_path}' not found."

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()

            markdown_output = []
            markdown_output.append(f"# SQLite Schema: {os.path.basename(db_path)}\n")

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
            if not markdown_output:
                return "Database is empty or contains no user tables."
            return "\n".join(markdown_output)

        except sqlite3.Error as e:
            return f"SQLite Error reading schema: {e}"

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
        all_files_content = []
        for root, dirs, files in os.walk(self.source_dir, topdown=True):
            dirs[:] = [d for d in dirs if not self.matches_any_pattern(d, IGNORED_DIRS)]

            for file in sorted(files):
                if self.matches_any_pattern(file, IGNORED_FILES):
                    continue

                if file.startswith(".") and not self.matches_any_pattern(
                    file, INCLUDE_HIDDEN_FILES
                ):
                    continue

                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, self.source_dir)

                _, ext = os.path.splitext(file)
                is_sqlite = (ext.lower() in SQLITE_EXTENSIONS) or (
                    file in SQLITE_EXACT_FILENAMES
                )

                try:
                    if is_sqlite:
                        content = self.get_sqlite_schema(file_path)
                        lang_hint = "markdown"
                    else:
                        with open(
                            file_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = f.read()
                        lang_hint = self.get_language_hint(file)

                    formatted_content = (
                        f"\n---\n\n"
                        f"**File:** `{relative_path}`\n\n"
                        f"```{lang_hint}\n"
                        f"{content}\n"
                        f"```"
                    )
                    all_files_content.append(formatted_content)
                except Exception as e:
                    print(f"Could not read file {file_path}: {e}")

        return "\n".join(all_files_content)


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
        output.append(
            "The following skills are active for this session. Adopt these roles and guidelines:\n"
        )

        for skill_file in sorted(list(self.selected_skills)):
            path = os.path.join(self.skills_dir, skill_file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                output.append(f"## Skill Module: {skill_file}\n{content}\n---\n")
            except Exception as e:
                output.append(f"Error loading skill {skill_file}: {e}")

        return "\n".join(output)


def clear_screen():
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\033[H\033[2J\033[3J")
        sys.stdout.flush()
        os.system("clear")


def get_input_with_cancel(prompt):
    try:
        user_input = input(f"{prompt} (or Press Enter/'b' to cancel): ").strip()
        if not user_input or user_input.lower() == "b":
            return None
        return user_input
    except KeyboardInterrupt:
        return None


def run_generation(source_dir, output_file, skill_manager=None):
    scanner = ProjectScanner(source_dir)
    IGNORED_FILES.add(os.path.basename(output_file))

    try:
        tree = scanner.generate_tree_structure()
        content = scanner.process_directory()

        final_output = []

        if skill_manager:
            skills_content = skill_manager.get_compiled_skills()
            if skills_content:
                final_output.append(skills_content)
                final_output.append("\n# PROJECT CONTEXT START\n")

        final_output.append(tree)
        final_output.append("\n# FILE CONTENTS\n")
        final_output.append(content)

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(final_output))

        size_kb = os.path.getsize(output_file) / 1024
        return f"SUCCESS! Saved to '{os.path.basename(output_file)}' ({size_kb:.2f} KB)"
    except Exception as e:
        return f"ERROR: {str(e)}"


def interactive_mode(default_source_dir, default_output_file):
    skill_manager = SkillManager()
    current_source_dir = default_source_dir
    output_file = default_output_file
    status_message = "Ready"

    while True:
        clear_screen()
        print("==========================================")
        print("   AI CONTEXT BUILDER & SKILL SELECTOR    ")
        print("==========================================")
        print(f"Source Directory: {current_source_dir}")
        print(f"Output File:      {output_file}")
        print("------------------------------------------")

        if status_message:
            print(f"STATUS: {status_message}")
            print("------------------------------------------")

        print("Available Skills (Toggle by number):")

        if not skill_manager.available_skills:
            print("  (No .md files found in ./skills folder)")

        for idx, skill in enumerate(skill_manager.available_skills):
            status = "[x]" if skill in skill_manager.selected_skills else "[ ]"
            print(f"  {idx + 1}. {status} {skill}")

        print("------------------------------------------")
        print("Actions:")
        print("  S. Change Source Directory")
        print("  O. Change Output Filename")
        print("  G. Generate Context File")
        print("  Q. Quit")
        print("==========================================")

        choice = input("Select option: ").strip().lower()

        if choice == "q":
            clear_screen()
            sys.exit(0)

        elif choice == "s":
            new_dir = get_input_with_cancel("Enter new source directory path")
            if new_dir:
                if os.path.isdir(new_dir):
                    current_source_dir = os.path.abspath(new_dir)
                    status_message = "Source directory updated."
                else:
                    status_message = f"Error: '{new_dir}' is not a directory."
            else:
                status_message = "Change directory cancelled."

        elif choice == "o":
            new_out = get_input_with_cancel("Enter output filename (e.g., context.md)")
            if new_out:
                output_file = new_out
                status_message = "Output filename updated."
            else:
                status_message = "Change filename cancelled."

        elif choice == "g":
            status_message = "Generating..."
            status_message = run_generation(
                current_source_dir, output_file, skill_manager
            )

        elif choice.isdigit():
            idx = int(choice) - 1
            msg = skill_manager.toggle_skill(idx)
            status_message = msg

        else:
            status_message = "Invalid option."


def print_usage_hint():
    print("\n--- Usage Hints ---")
    print("1. Interactive Mode (Menu):")
    print("   python context_builder.py")
    print("\n2. Quick Mode (Current Dir):")
    print("   python context_builder.py .")
    print("\n3. Specific Dir & Output:")
    print("   python context_builder.py /path/to/project my_context.md")
    print("\n4. Using Flags:")
    print("   python context_builder.py --dir /path/to/project --out my_context.md")
    print("-------------------\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pos_source", nargs="?", help="Source directory path")
    parser.add_argument("pos_out", nargs="?", help="Output filename")
    parser.add_argument("--dir", "-d", help="Source directory path")
    parser.add_argument("--out", "-o", help="Output filename")
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Force interactive mode"
    )

    args = parser.parse_args()

    source_dir = args.dir if args.dir else args.pos_source
    output_file = args.out if args.out else args.pos_out
    if not output_file:
        output_file = "project_context.md"

    should_run_interactive = args.interactive or (
        not source_dir and not args.pos_source
    )

    if should_run_interactive:
        start_dir = source_dir if source_dir else os.getcwd()
        interactive_mode(start_dir, output_file)
    else:
        if not os.path.isdir(source_dir):
            print(f"Error: Source directory '{source_dir}' not found.")
            print_usage_hint()
            sys.exit(1)

        print(f"Scanning project: {source_dir}")
        result_msg = run_generation(source_dir, output_file)
        print(result_msg)


if __name__ == "__main__":
    main()
