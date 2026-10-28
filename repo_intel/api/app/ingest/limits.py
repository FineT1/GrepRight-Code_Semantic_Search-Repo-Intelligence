"""What 'small and medium repo' means: size limits and file filters."""

MAX_COMMITS = 3000
MAX_FILES = 2000
MAX_UNITS = 6000                      # functions + line windows per repo
MAX_FILE_BYTES = 200_000
MAX_PATH_LEN = 500
MAX_REPO_BYTES = 500 * 1024 * 1024
CLONE_TIMEOUT = 300

ALLOWED_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}
MANIFESTS = {"requirements.txt", "package.json"}

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target",
             ".venv", "venv", "__pycache__", ".next", "coverage"}
SKIP_SUFFIXES = (".min.js", ".lock", ".map", "package-lock.json")

LANG = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
        ".tsx": "TypeScript", ".java": "Java", ".go": "Go", ".rs": "Rust", ".c": "C",
        ".h": "C", ".cpp": "C++", ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
        ".sql": "SQL", ".sh": "Shell", ".md": "Markdown"}
CODE_LANGS = set(LANG.values()) - {"Markdown"}