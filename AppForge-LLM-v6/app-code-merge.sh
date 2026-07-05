#!/bin/bash
set -euo pipefail

APP_DIR="${1:-$(pwd)}"

if [ ! -d "$APP_DIR" ]; then
  echo "Error: app directory does not exist: $APP_DIR" >&2
  exit 1
fi

APP_DIR="$(cd "$APP_DIR" && pwd)"
APP_NAME="$(basename "$APP_DIR")"
OUT_FILE="${APP_DIR}/${APP_NAME}-source-code.md"
OUT_FILE_REL="$(basename "$OUT_FILE")"
MAX_FILE_BYTES="${APP_CODE_MERGE_MAX_FILE_BYTES:-1048576}"

case "$MAX_FILE_BYTES" in
  ''|*[!0-9]*)
    echo "Error: APP_CODE_MERGE_MAX_FILE_BYTES must be a positive integer" >&2
    exit 1
    ;;
esac

if [ "$MAX_FILE_BYTES" -le 0 ]; then
  echo "Error: APP_CODE_MERGE_MAX_FILE_BYTES must be greater than zero" >&2
  exit 1
fi

file_size() {
  wc -c < "$1" | tr -d ' '
}

should_include_file() {
  local rel="$1"
  local file="$2"
  local name="${rel##*/}"
  local lower_rel

  lower_rel="$(printf '%s' "$rel" | tr '[:upper:]' '[:lower:]')"

  case "$rel" in
    "$OUT_FILE_REL"|"app-code-merge.sh")
      return 1
      ;;
  esac

  case "$name" in
    ".env.example")
      ;;
    ".env"|".env."*|*.pem|*.key|*.crt|*.p12|"id_rsa"|"id_ed25519")
      return 1
      ;;
  esac

  case "$lower_rel" in
    *.ds_store|*.log|*.tmp|*.temp|*.cache|*.pyc|*.pyo|*.tsbuildinfo|*.map)
      return 1
      ;;
    *.zip|*.tar|*.tgz|*.gz|*.bz2|*.xz|*.7z|*.rar)
      return 1
      ;;
    *.png|*.jpg|*.jpeg|*.gif|*.webp|*.avif|*.ico|*.icns|*.svg|*.pdf)
      return 1
      ;;
    *.mp3|*.wav|*.flac|*.ogg|*.mp4|*.mov|*.avi|*.mkv)
      return 1
      ;;
    *.ttf|*.otf|*.woff|*.woff2|*.eot|*.wasm|*.so|*.dylib|*.dll|*.exe)
      return 1
      ;;
    *.sqlite|*.sqlite3|*.db|*.cube)
      return 1
      ;;
  esac

  if [ "$(file_size "$file")" -gt "$MAX_FILE_BYTES" ]; then
    return 1
  fi

  case "$name" in
    Dockerfile|Containerfile|Makefile|Procfile|Gemfile|Rakefile|Jenkinsfile|Brewfile)
      return 0
      ;;
    Pipfile|Pipfile.lock|pyproject.toml|poetry.lock|uv.lock)
      return 0
      ;;
    requirements.txt|requirements-*.txt|constraints.txt|constraints-*.txt)
      return 0
      ;;
    package.json|package-lock.json|pnpm-lock.yaml|yarn.lock|bun.lockb)
      return 0
      ;;
    tsconfig.json|tsconfig.*.json|vite.config.*|next.config.*|nuxt.config.*)
      return 0
      ;;
    svelte.config.*|tailwind.config.*|postcss.config.*|eslint.config.*|prettier.config.*)
      return 0
      ;;
    go.mod|go.sum|Cargo.toml|Cargo.lock|composer.json|composer.lock)
      return 0
      ;;
    .gitignore|.dockerignore|.npmrc|.editorconfig|.env.example)
      return 0
      ;;
  esac

  case "$lower_rel" in
    *.py|*.pyi|*.js|*.jsx|*.mjs|*.cjs|*.ts|*.tsx|*.vue|*.svelte)
      return 0
      ;;
    *.html|*.css|*.scss|*.sass|*.less|*.json|*.jsonc|*.yaml|*.yml|*.toml)
      return 0
      ;;
    *.ini|*.cfg|*.conf|*.md|*.mdx|*.txt|*.sh|*.bash|*.zsh|*.fish)
      return 0
      ;;
    *.sql|*.graphql|*.gql|*.rs|*.go|*.java|*.kt|*.kts|*.swift)
      return 0
      ;;
    *.c|*.h|*.cpp|*.hpp|*.cc|*.cs|*.php|*.rb|*.lua|*.r|*.dart)
      return 0
      ;;
    *.ex|*.exs|*.erl|*.hrl|*.clj|*.cljs|*.edn|*.scala|*.gradle)
      return 0
      ;;
  esac

  return 1
}

language_for_file() {
  local rel="$1"
  local name="${rel##*/}"
  local lower_rel

  lower_rel="$(printf '%s' "$rel" | tr '[:upper:]' '[:lower:]')"

  case "$name" in
    Dockerfile|Containerfile) echo "dockerfile"; return ;;
    Makefile) echo "makefile"; return ;;
  esac

  case "$lower_rel" in
    *.py|*.pyi) echo "python" ;;
    *.js|*.jsx|*.mjs|*.cjs) echo "javascript" ;;
    *.ts|*.tsx) echo "typescript" ;;
    *.vue) echo "vue" ;;
    *.svelte) echo "svelte" ;;
    *.html) echo "html" ;;
    *.css) echo "css" ;;
    *.scss|*.sass) echo "scss" ;;
    *.json|*.jsonc) echo "json" ;;
    *.yaml|*.yml) echo "yaml" ;;
    *.toml) echo "toml" ;;
    *.md|*.mdx) echo "markdown" ;;
    *.sh|*.bash|*.zsh|*.fish) echo "bash" ;;
    *.sql) echo "sql" ;;
    *.rs) echo "rust" ;;
    *.go) echo "go" ;;
    *.java|*.kt|*.kts|*.gradle) echo "java" ;;
    *.swift) echo "swift" ;;
    *.c|*.h|*.cpp|*.hpp|*.cc) echo "cpp" ;;
    *.cs) echo "csharp" ;;
    *.php) echo "php" ;;
    *.rb) echo "ruby" ;;
    *.lua) echo "lua" ;;
    *.r) echo "r" ;;
    *.dart) echo "dart" ;;
    *.graphql|*.gql) echo "graphql" ;;
    *) echo "" ;;
  esac
}

LIST_FILE="$(mktemp "${TMPDIR:-/tmp}/app-code-merge.XXXXXX")"
trap 'rm -f "$LIST_FILE"' EXIT
: > "$LIST_FILE"

while IFS= read -r -d '' file; do
  rel="${file#"$APP_DIR"/}"
  if should_include_file "$rel" "$file"; then
    printf '%s\n' "$rel" >> "$LIST_FILE"
  fi
done < <(
  find "$APP_DIR" \
    \( -type d \( \
      -name .git -o \
      -name .hg -o \
      -name .svn -o \
      -name node_modules -o \
      -name bower_components -o \
      -name vendor -o \
      -name dist -o \
      -name build -o \
      -name out -o \
      -name target -o \
      -name coverage -o \
      -name .next -o \
      -name .nuxt -o \
      -name .svelte-kit -o \
      -name .vite -o \
      -name .turbo -o \
      -name .cache -o \
      -name __pycache__ -o \
      -name .pytest_cache -o \
      -name .mypy_cache -o \
      -name .ruff_cache -o \
      -name .venv -o \
      -name venv -o \
      -name env -o \
      -name .idea -o \
      -name .vscode -o \
      -name .codex -o \
      -name .agents \
    \) -prune \) -o \
    -type f -print0
)

LC_ALL=C sort -o "$LIST_FILE" "$LIST_FILE"

if [ ! -s "$LIST_FILE" ]; then
  echo "Error: no source files found in '$APP_DIR'" >&2
  exit 1
fi

FILE_COUNT="$(wc -l < "$LIST_FILE" | tr -d ' ')"

echo "Merging source code for: $APP_NAME"
echo "Source root: $APP_DIR"
echo "Output file: $OUT_FILE_REL"
echo "Files merged: $FILE_COUNT"
echo "Excluding dependency, cache, build output, archive, binary, and secret files"

{
  printf '# %s Source Code\n\n' "$APP_NAME"
  printf 'Source root: `%s`\n\n' "$APP_DIR"
  printf 'Generated by: `app-code-merge.sh`\n\n'
  printf 'Files merged: %s\n\n' "$FILE_COUNT"

  while IFS= read -r rel; do
    file="${APP_DIR}/${rel}"
    lang="$(language_for_file "$rel")"

    printf '## File: %s\n\n' "$rel"
    printf '````%s\n' "$lang"
    cat "$file"
    printf '\n````\n\n'
  done < "$LIST_FILE"
} > "$OUT_FILE"

echo "Created: $OUT_FILE_REL"
ls -lh "$OUT_FILE"
