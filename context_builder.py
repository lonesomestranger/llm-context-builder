import argparse
import fnmatch
import os

IGNORED_DIRS = {".git", "__pycache__", ".idea", ".venv", "venv"}
IGNORED_FILES = {
    ".DS_Store",
    "*.pyc",
    "*.log",
    "*.sqlite3",
    "*.html",
    "*.db",
    "context_builder.py",
    ".env",
}
INCLUDE_HIDDEN_FILES = {".env.example"}


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

            try:
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
