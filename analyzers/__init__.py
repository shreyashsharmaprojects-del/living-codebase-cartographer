"""Analyzer registry: ordered dispatch + technology detection.

Detection is EVIDENCE-based: manifest filenames, lockfiles, config contents,
imports, and file-extension census. Each detected technology carries a
confidence (HIGH for manifest + import agreement, MEDIUM for manifest or
strong imports alone, LOW for extension presence only).
"""

from . import java, typescript, python, go, csharp, rust, sql, config, fallback

ANALYZERS = [java, typescript, python, go, csharp, rust, sql, config,
             fallback]
ANALYZERS.sort(key=lambda a: a.PRIORITY)

BY_NAME = {a.NAME: a for a in ANALYZERS}

try:
    CONFIG_FILENAMES = BY_NAME["config"].FILENAMES
except KeyError:
    CONFIG_FILENAMES = set()

# Manifest -> (ecosystem label, language/framework signal, confidence)
MANIFEST_SIGNALS = [
    ("pom.xml", "maven", "java", "HIGH"),
    ("build.gradle", "gradle", "java", "HIGH"),
    ("build.gradle.kts", "gradle", "kotlin/java", "HIGH"),
    ("package.json", "npm", "javascript", "MEDIUM"),
    ("angular.json", "angular", "angular", "HIGH"),
    ("requirements.txt", "pip", "python", "HIGH"),
    ("pyproject.toml", "python", "python", "HIGH"),
    ("Pipfile", "pipenv", "python", "HIGH"),
    ("go.mod", "go-modules", "go", "HIGH"),
    ("Cargo.toml", "cargo", "rust", "HIGH"),
    ("Gemfile", "bundler", "ruby", "HIGH"),
    ("composer.json", "composer", "php", "HIGH"),
    ("build.sbt", "sbt", "scala", "HIGH"),
    ("mix.exs", "mix", "elixir", "HIGH"),
    ("pubspec.yaml", "pub", "dart", "HIGH"),
    ("Package.swift", "spm", "swift", "HIGH"),
    ("*.csproj", "msbuild", "csharp", "HIGH"),
    ("*.sln", "msbuild", "csharp", "HIGH"),
    ("Dockerfile", "docker", "containers", "HIGH"),
    ("docker-compose.yml", "docker-compose", "containers", "HIGH"),
    ("docker-compose.prod.yml", "docker-compose", "containers", "HIGH"),
]

# Import/dependency substring -> (technology, role, confidence)
CONTENT_SIGNALS = [
    ("springframework", "spring-boot", "framework", "HIGH"),
    ("spring-boot", "spring-boot", "framework", "HIGH"),
    ("@angular/", "angular", "framework", "HIGH"),
    ("react", "react", "framework", "MEDIUM"),
    ("vue", "vue", "framework", "MEDIUM"),
    ("fastapi", "fastapi", "framework", "HIGH"),
    ("django", "django", "framework", "HIGH"),
    ("flask", "flask", "framework", "HIGH"),
    ("grpc", "grpc", "framework", "MEDIUM"),
    ("protobuf", "protobuf", "framework", "MEDIUM"),
    ("kafka", "kafka", "messaging", "MEDIUM"),
    ("flyway", "flyway", "migration-tool", "HIGH"),
    ("alembic", "alembic", "migration-tool", "HIGH"),
    ("typeorm", "typeorm", "orm", "MEDIUM"),
    ("prisma", "prisma", "orm", "MEDIUM"),
    ("sqlalchemy", "sqlalchemy", "orm", "MEDIUM"),
    ("hibernate", "hibernate/jpa", "orm", "HIGH"),
    ("keycloak", "keycloak", "auth", "MEDIUM"),
    ("jdbc:postgresql", "postgresql", "database", "HIGH"),
    ("jdbc:mysql", "mysql", "database", "HIGH"),
    ("jdbc:oracle", "oracle", "database", "HIGH"),
    ("postgresql", "postgresql", "database", "MEDIUM"),
    ("pg8000", "postgresql", "database", "MEDIUM"),
    ("psycopg", "postgresql", "database", "MEDIUM"),
    ("mysql", "mysql", "database", "MEDIUM"),
    ("mssql", "sql-server", "database", "MEDIUM"),
    ("sqlserver", "sql-server", "database", "MEDIUM"),
    ("sqlite", "sqlite", "database", "MEDIUM"),
    ("mongodb", "mongodb", "database", "MEDIUM"),
    ("pymongo", "mongodb", "database", "MEDIUM"),
    ("mongoose", "mongodb", "database", "MEDIUM"),
    ("redis", "redis", "cache", "MEDIUM"),
    ("memcached", "memcached", "cache", "MEDIUM"),
    ("aws-sdk", "aws", "cloud", "MEDIUM"),
    ("boto3", "aws", "cloud", "MEDIUM"),
    ("google-cloud", "gcp", "cloud", "MEDIUM"),
    ("kubernetes", "kubernetes", "infra", "MEDIUM"),
    ("terraform", "terraform", "infra", "MEDIUM"),
]


def _bump(table, tech, role, conf):
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    cur = table.get(tech)
    if cur is None or rank[conf] > rank[cur["confidence"]]:
        table[tech] = {"role": role, "confidence": conf}


def detect_tech(files, read):
    """Run technology detection.

    files: list of (relpath, abspath). read(relpath) -> text (best effort).
    Returns a dict: {"languages": [...], "frameworks": [...],
    "databases": [...], "infrastructure": [...], "unsupported": [...]}.
    """
    import fnmatch
    import os
    tech = {}
    basenames = {os.path.basename(r) for r, _ in files}
    for pattern, eco, signal, conf in MANIFEST_SIGNALS:
        hit = (any(fnmatch.fnmatch(b, pattern) for b in basenames)
               or any(fnmatch.fnmatch(r, pattern) for r, _ in files))
        if hit:
            role = ("language" if signal in (
                "java", "python", "go", "rust", "ruby", "php", "scala",
                "elixir", "dart", "swift", "csharp", "kotlin/java",
                "javascript", "typescript") else "framework"
                if signal in ("angular",) else "tooling")
            _bump(tech, signal, role, conf)
            if eco:
                _bump(tech, eco, "build", conf)
    sampled = [r for r, _ in files
               if os.path.basename(r) in (
                   "package.json", "pom.xml", "requirements.txt",
                   "pyproject.toml", "go.mod", "Cargo.toml",
                   "application.properties", "application.yml",
                   "docker-compose.yml", "docker-compose.prod.yml")
               or r.endswith((".csproj", ".sln", "Gemfile"))]
    blob = ""
    for r in sampled[:12]:
        try:
            blob += "\n" + (read(r) or "")[:20000]
        except OSError:
            pass
    low = blob.lower()
    for needle, signal, role, conf in CONTENT_SIGNALS:
        if needle in low:
            _bump(tech, signal, role, conf)
    ext_lang = {".java": "java", ".py": "python", ".go": "go",
                ".rs": "rust", ".cs": "csharp", ".rb": "ruby",
                ".php": "php", ".swift": "swift", ".kt": "kotlin",
                ".scala": "scala", ".dart": "dart", ".ex": "elixir",
                ".ts": "typescript", ".tsx": "typescript",
                ".js": "javascript", ".jsx": "javascript",
                ".vue": "vue", ".svelte": "svelte", ".sql": "sql"}
    counts = {}
    for r, _ in files:
        lang = ext_lang.get(os.path.splitext(r)[1].lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    for lang, n in counts.items():
        _bump(tech, lang, "language", "LOW" if n < 3 else "MEDIUM")
    supported = {"java", "typescript", "javascript", "python", "go",
                 "csharp", "rust", "sql", "vue", "svelte"}
    out = {"languages": [], "frameworks": [], "databases": [],
           "infrastructure": [], "unsupported": []}
    for name, info in sorted(tech.items()):
        role = info["role"]
        entry = {"name": name, "confidence": info["confidence"]}
        if role == "language":
            out["languages"].append(entry)
            if name not in supported:
                out["unsupported"].append(
                    {"name": name,
                     "reason": "no dedicated analyzer; fallback used",
                     "confidence": "LOW"})
        elif role in ("framework", "orm", "migration-tool", "messaging"):
            out["frameworks"].append(entry)
        elif role in ("database", "cache"):
            out["databases"].append(entry)
        else:
            out["infrastructure"].append(entry)
    return out


def analyzers_for(path, text=None):
    """Return analyzers that claim this path, in priority order.

    Dedicated analyzers claim by extension/filename/path-hint. The
    fallback analyzer claims by exclusion: any source-looking file no
    dedicated analyzer claimed (graceful degradation, LOW only).
    """
    claimed = []
    for a in ANALYZERS:
        if a.NAME == "fallback":
            continue
        exts = getattr(a, "EXTENSIONS", set())
        names = getattr(a, "FILENAMES", set())
        hints = getattr(a, "PATH_HINTS", ())
        base = path.rsplit("/", 1)[-1]
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in base else ""
        selected = (ext in exts) or (base in names) or \
            any(h in path for h in hints)
        if selected:
            try:
                if a.can_handle(path, text):
                    claimed.append(a)
            except Exception:
                pass
    dedicated = [a for a in claimed if a.NAME != "fallback"]
    if dedicated:
        return dedicated
    if BY_NAME["fallback"].can_handle(path, text):
        return [BY_NAME["fallback"]]
    return []
