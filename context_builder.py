import argparse
import fnmatch
import os
import sqlite3

IGNORED_DIRS = {".git", "__pycache__", ".idea", ".venv", "venv", ".uv"}
IGNORED_FILES = {
    ".DS_Store",
    "*.pyc",
    "*.log",
    "*.html",
    "context_builder.py",
    ".env",
    "uv.lock",
}
INCLUDE_HIDDEN_FILES = {".env.example"}

SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
SQLITE_EXACT_FILENAMES = {"database"}

def matches_any_pattern(name, patterns):
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False

def get_language_hint(filename):
    extension_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
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
    }
    _, ext = os.path.splitext(filename)
    if ext in extension_map:
        return extension_map[ext]
    if filename in extension_map:
        return extension_map[filename]
    return ""

def get_sqlite_schema(db_path):
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
            
            if table_name.startswith('sqlite_'):
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
                
                markdown_output.append(f"| **{name}** | {dtype} | {is_nullable} | {is_pk} | {dflt} |")
            
            markdown_output.append("")

            cursor.execute(f"PRAGMA foreign_key_list('{table_name}')")
            fks = cursor.fetchall()
            
            if fks:
                markdown_output.append("**Foreign Keys:**")
                for fk in fks:
                    target_table = fk[2]
                    source_col = fk[3]
                    target_col = fk[4]
                    markdown_output.append(f"- `{source_col}` references `{target_table}({target_col})`")
                markdown_output.append("")

            markdown_output.append("---\n")

        conn.close()
        if not markdown_output:
            return "Database is empty or contains no user tables."
        return "\n".join(markdown_output)

    except sqlite3.Error as e:
        return f"SQLite Error reading schema: {e}"

def generate_tree_structure(startpath, project_name):
    tree_lines = [f"# {project_name} Project Structure", "```"]
    for root, dirs, files in os.walk(startpath, topdown=True):
        dirs[:] = [d for d in dirs if not matches_any_pattern(d, IGNORED_DIRS)]

        files_to_show = [
            f
            for f in files
            if not matches_any_pattern(f, IGNORED_FILES)
            and (not f.startswith(".") or matches_any_pattern(f, INCLUDE_HIDDEN_FILES))
        ]

        level = root.replace(startpath, "").count(os.sep)
        indent = " " * 4 * (level)
        tree_lines.append(f"{indent}{os.path.basename(root)}/")
        subindent = " " * 4 * (level + 1)
        for f in sorted(files_to_show):
            tree_lines.append(f"{subindent}{f}")
    tree_lines.append("```")
    return "\n".join(tree_lines)

def process_directory(source_dir):
    all_files_content = []
    for root, dirs, files in os.walk(source_dir, topdown=True):
        dirs[:] = [d for d in dirs if not matches_any_pattern(d, IGNORED_DIRS)]

        for file in sorted(files):
            if matches_any_pattern(file, IGNORED_FILES):
                continue

            if file.startswith(".") and not matches_any_pattern(
                file, INCLUDE_HIDDEN_FILES
            ):
                continue

            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, source_dir)
            
            _, ext = os.path.splitext(file)
            is_sqlite = (ext.lower() in SQLITE_EXTENSIONS) or (file in SQLITE_EXACT_FILENAMES)

            try:
                if is_sqlite:
                    content = get_sqlite_schema(file_path)
                    lang_hint = "markdown"
                else:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    lang_hint = get_language_hint(file)

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_dir", type=str, help="The source directory of the project."
    )
    parser.add_argument(
        "output_file",
        type=str,
        nargs="?",
        default="project_context.md",
        help="The name of the output markdown file.",
    )

    args = parser.parse_args()

    source_dir = os.path.abspath(args.source_dir)
    output_file = args.output_file
    IGNORED_FILES.add(os.path.basename(output_file))

    if not os.path.isdir(source_dir):
        print(f"Error: Source directory '{source_dir}' not found.")
        return

    project_name = os.path.basename(source_dir)

    print(f"Processing project: {project_name}")
    print(f"Source directory: {source_dir}")

    tree_structure = generate_tree_structure(source_dir, project_name)
    print("Generated directory tree...")

    combined_content = process_directory(source_dir)
    print("Aggregated file contents...")

    final_output = f"{tree_structure}\n\n# File Contents\n{combined_content}"

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final_output)
        print(f"\nSuccessfully created '{output_file}'")
        print(f"Full path: {os.path.abspath(output_file)}")
    except Exception as e:
        print(f"Error writing to output file: {e}")

if __name__ == "__main__":
    main()
    