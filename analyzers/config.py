"""Generic config/infra/manifest analyzer: evidence without language parsers.

Covers build manifests, lockfiles, container/infra definitions, CI, env key
names (never values), and generic properties/YAML/TOML/JSON config keys.
All technology names (postgres, redis, kafka, aws, ...) are DATA in meta —
never node kinds.
"""

import json
import os
import re

NAME = "config"
KIND = "config"
EXTENSIONS = {".properties", ".yml", ".yaml", ".toml", ".ini", ".cfg",
              ".env.example", ".json", ".xml"}
FILENAMES = {"Dockerfile", "docker-compose.yml", "docker-compose.prod.yml",
             "pom.xml", "package.json", "package-lock.json", "go.mod",
             "go.sum", "Cargo.toml", "Cargo.lock", "requirements.txt",
             "pyproject.toml", "Pipfile", "Pipfile.lock", ".env.example",
             "angular.json", "nginx.conf", "proxy.conf.json", "mvnw",
             "build.gradle", "build.gradle.kts", "settings.gradle",
             "Gemfile", "composer.json", "Package.swift", "pubspec.yaml",
             "mix.exs", "render-realm.mjs"}
PATH_HINTS = (".github/workflows", "docker", "keycloak", "deploy", "infra",
              "k8s", "kubernetes", "terraform")
PRIORITY = 40

DB_HINTS = {
    "postgresql": ("PostgreSQL", "database"), "postgres": ("PostgreSQL", "database"),
    "mysql": ("MySQL", "database"), "mariadb": ("MariaDB", "database"),
    "oracle": ("Oracle", "database"), "sqlserver": ("SQL Server", "database"),
    "mssql": ("SQL Server", "database"), "sqlite": ("SQLite", "database"),
    "mongodb": ("MongoDB", "database"), "mongo": ("MongoDB", "database"),
    "redis": ("Redis", "cache"), "memcached": ("Memcached", "cache"),
    "kafka": ("Kafka", "queue"), "rabbitmq": ("RabbitMQ", "queue"),
    "sqs": ("SQS", "queue"), "sns": ("SNS", "topic"),
    "keycloak": ("Keycloak", "auth"), "auth0": ("Auth0", "auth"),
    "okta": ("Okta", "auth"), "cognito": ("Cognito", "auth"),
    "s3": ("S3", "storage"), "mailpit": ("Mailpit", "mail"),
    "smtp": ("SMTP", "mail"), "sendgrid": ("Sendgrid", "mail"),
    "ses": ("SES", "mail"),
}

_MAX_ID = 512
_ID_WS = re.compile(r"\s+")


def _sid(text):
    """Single-line node/edge id capped at 512 chars."""
    s = _ID_WS.sub(" ", text).strip()
    return s if len(s) <= _MAX_ID else s[:_MAX_ID]


MANIFEST_DEP_RES = [
    (re.compile(r"<artifactId>([^<]+)</artifactId>"), "maven", "MEDIUM"),
    (re.compile(r'"([^"]+)"\s*:\s*"[^"]*"\s*[,}]'), "npm", "MEDIUM"),
    # [ \t] not \s after ^: \s would swallow newlines under re.M and
    # attribute the match to the blank lines above instead of its own line.
    (re.compile(r"^[ \t]*([\w./-]+)[ \t]+v?[\d.]+", re.M), "go", "LOW"),
    (re.compile(r"^[ \t]*([\w-]+)[ \t]*=[ \t]*\"[^\"]*\"", re.M), "cargo", "LOW"),
    (re.compile(r"^[ \t]*([\w_.-]+)[ \t]*(?:==|>=|~=|>|<)[^,\s]*", re.M), "pip", "LOW"),
]


def can_handle(path, text=None):
    base = os.path.basename(path)
    if base in FILENAMES:
        return True
    if any(h in path for h in PATH_HINTS):
        return True
    ext = os.path.splitext(path)[1].lower()
    return ext in EXTENSIONS


def _external(ctx, rel, line, label, role, detail, conf="MEDIUM"):
    nid = _sid(f"external:{label.lower().replace(' ', '-')}")
    ctx.node(nid, "external-service", label, line, conf,
             {"role": role, "detail": detail})
    return nid


def _db_hints_in(text):
    """Word-boundary hint scan: 'assets' must not claim SES, 'sessions'
    must not claim SNS/SQS, etc."""
    found = {}
    for word in set(re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", text.lower())):
        word = word.replace("_", "-").replace(".", "-")
        for hint, (label, role) in DB_HINTS.items():
            if word == hint or word.startswith(hint + "-") \
                    or word.endswith("-" + hint):
                found[label] = (role, word)
    return found


def _own_node(ctx, file_id, rel, line, nid, kind, name, conf, meta):
    """Node + file `defines` edge for symbols this file declares."""
    nid = _sid(nid)
    ctx.node(nid, kind, name, line, conf, meta)
    ctx.edge(file_id, nid, "defines", line, conf, {})
    return nid


def scan(ctx, path, text):
    rel = ctx.path
    base = os.path.basename(rel)
    lines = text.splitlines()
    file_id = _sid(f"file:{rel}")
    ctx.node(file_id, "file", base, 1, "HIGH", {"role": "source"})

    # --- Dockerfiles: base images + exposed ports as deployment evidence ---
    if base == "Dockerfile" or rel.endswith("Dockerfile"):
        dep_id = _own_node(ctx, file_id, rel, 1, f"config:{rel}",
                           "deployment-unit",
                           os.path.basename(os.path.dirname(rel)) + "/Dockerfile",
                           "MEDIUM", {"kind": "dockerfile"})
        for i, line in enumerate(lines, start=1):
            m = re.match(r"\s*FROM\s+(\S+)", line, re.I)
            if m:
                ctx.edge(dep_id,
                         _external(ctx, rel, i, f"image:{m.group(1)}",
                                   "container-base", m.group(1), "LOW"),
                         "depends-on", i, "LOW", {"via": "dockerfile"})
        return

    # --- compose files: services become external-service nodes ---
    if base in ("docker-compose.yml", "docker-compose.prod.yml",
                "compose.yml", "compose.yaml"):
        dep_id = _own_node(ctx, file_id, rel, 1, f"config:{rel}",
                           "deployment-unit", base, "HIGH",
                           {"kind": "compose"})
        # Only the `services:` block declares services; sibling top-level
        # blocks (volumes/networks/configs/secrets) never do.
        # [ \t] (not \s) for gaps around ':' and [^:\n] (not [^:]) for
        # the key: both would otherwise span line breaks under re.M — a
        # bare word line above a header (e.g. `foo` over `services:`)
        # would swallow the header and flip in_services wrongly.
        in_services = False
        for m in re.finditer(r"^( *)([^:\s][^:\n]*?):[ \t]*(?:\r?\n|$)",
                             text, re.M):
            indent = len(m.group(1))
            key = m.group(2).strip().strip("\"'")
            if indent == 0:
                in_services = (key == "services")
                continue
            if not in_services or indent != 2 or " " in key or "\t" in key:
                continue
            svc = key
            if svc in ("services", "volumes", "networks", "configs",
                       "secrets"):
                continue
            line = text[:m.start()].count("\n") + 1
            nid = _sid(f"external:{svc}")
            ctx.node(nid, "external-service", svc, line, "MEDIUM",
                     {"via": "docker-compose"})
            ctx.edge(dep_id, nid, "contains", line, "MEDIUM", {})
            ctx.edge(file_id, nid, "defines", line, "MEDIUM", {})
        return

    # --- CI workflows ---
    if rel.startswith(".github/workflows/"):
        _own_node(ctx, file_id, rel, 1, f"ci:{base}", "ci-job", base,
                  "MEDIUM", {})
        return

    # --- env key names only (never values) ---
    if base in (".env.example", ".env.sample", ".env.template"):
        for i, line in enumerate(lines, start=1):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                key = s.partition("=")[0].strip()
                if key:
                    _own_node(ctx, file_id, rel, i, f"env:{key}",
                              "environment", key, "HIGH",
                              {"note": "key name only; values never stored"})
        return

    # --- realm / auth templates ---
    if "realm" in rel and rel.endswith((".json", ".mjs")):
        _own_node(ctx, file_id, rel, 1, "auth:realm", "configuration",
                  "realm template", "HIGH", {"renderer": rel})
        return

    # --- key=value configs: generic keys + DB/auth/mail hints as data ---
    if rel.endswith((".properties", ".ini", ".cfg", ".conf")) or \
            base in ("application.properties", "application.yml",
                     "application.yaml"):
        cfg_id = _own_node(ctx, file_id, rel, 1, f"config:{rel}",
                           "configuration", base, "HIGH", {})
        for i, line in enumerate(lines, start=1):
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, _, value = s.partition("=")
            key, value = key.strip(), value.strip()
            if not key:
                continue
            key_id = _own_node(ctx, file_id, rel, i, f"config-key:{key}",
                               "configuration-key", key, "HIGH", {})
            ctx.edge(cfg_id, key_id, "configures",
                     i, "HIGH", {})
            low = value.lower()
            if "jdbc:" in low:
                # A JDBC URL names its dialect (postgresql/mysql/...);
                # anything else is not a database connection.
                m = re.search(r"jdbc:(\w+):", low)
                if m:
                    dialect = m.group(1).lower()
                    label = {"postgresql": "PostgreSQL", "mysql": "MySQL",
                             "mariadb": "MariaDB", "oracle": "Oracle",
                             "sqlserver": "SQL Server",
                             "sqlite": "SQLite"}.get(dialect,
                                                     dialect.upper() or "?")
                    nid = _external(ctx, rel, i, label, "database",
                                    "connection-url", "HIGH")
                    ctx.edge(cfg_id, nid, "depends-on", i, "HIGH", {})
            elif low.startswith(("postgres", "mysql", "mongodb", "sqlite")) \
                    and "://" in value:
                scheme = low.split(":", 1)[0].split("+", 1)[0]
                label = {"postgres": "PostgreSQL", "mysql": "MySQL",
                         "mongodb": "MongoDB", "sqlite": "SQLite"}.get(
                             scheme, scheme.upper())
                nid = _external(ctx, rel, i, label, "database",
                                "connection-url", "HIGH")
                ctx.edge(cfg_id, nid, "depends-on", i, "HIGH", {})
            for key_word in re.findall(r"[A-Za-z][\w.-]*", key.lower()):
                norm = key_word.replace("_", "-").replace(".", "-")
                for hint, (label, role) in DB_HINTS.items():
                    if norm == hint or norm.startswith(hint + "-") \
                            or norm.endswith("-" + hint):
                        if "uri" in key or "url" in key or "host" in key \
                                or "issuer" in key:
                            nid = _external(ctx, rel, i, label, role, key,
                                            "HIGH")
                            ctx.edge(cfg_id, nid, "depends-on", i,
                                     "HIGH", {})
                            break
            for env in re.findall(r"\$\{([^}:\s]+)", value):
                ctx.edge(key_id, _sid(f"env:{env}"),
                         "depends-on", i, "HIGH", {})
        return

    # --- YAML/TOML/JSON configs: shallow keys + image/db hints ---
    if rel.endswith((".yml", ".yaml", ".toml", ".json")):
        cfg_id = _own_node(ctx, file_id, rel, 1, f"config:{rel}",
                           "configuration", base, "MEDIUM", {})
        # Generated single-line blobs (e.g. bundled JSON) carry no line
        # structure to mine and can be tens of MB; hash-level scanning is
        # enough. (Core also skips >1MB files; this guard covers large
        # files below that threshold without crashing.)
        if len(lines) < 20 and len(text) > 200000:
            return
        seen = set()
        for i, line in enumerate(lines, start=1):
            m = re.match(r"^[ \t]*([\w.-]+)[ \t]*[:=][ \t]*(.*)$", line)
            if m and m.group(1) not in seen and len(m.group(1)) > 1:
                seen.add(m.group(1))
                if len(seen) > 60:
                    break
                key_id = _own_node(ctx, file_id, rel, i,
                                   f"config-key:{m.group(1)}",
                                   "configuration-key", m.group(1), "LOW",
                                   {"via": base})
                ctx.edge(cfg_id, key_id, "configures", i, "LOW", {})
        for label, (role, _word) in _db_hints_in(text).items():
            nid = _external(ctx, rel, 1, label, role, base, "LOW")
            ctx.edge(cfg_id, nid, "depends-on", 1, "LOW", {})
        # package.json scripts/entry points as generic handlers
        if base == "package.json":
            try:
                data = json.loads(text)
                handlers = list((data.get("scripts") or {}))[:40]
                for n, name in enumerate(handlers):
                    safe = _sid(f"handler:npm:{name}")
                    ctx.node(safe, "handler", name[:128], 1,
                             "MEDIUM", {"via": "npm-script"})
                    ctx.edge(file_id, safe, "defines", 1, "MEDIUM",
                             {"via": "npm-script"})
                    ctx.edge(cfg_id, safe, "contains", 1, "MEDIUM",
                             {"via": "npm-script"})
                for dep in list((data.get("dependencies") or {}))[:80]:
                    ctx.edge(cfg_id,
                             _sid(f"unresolved:module:npm:{dep}"),
                             "depends-on", 1, "MEDIUM", {"via": "npm"})
            except (ValueError, AttributeError):
                pass
        return

    # --- build manifests: dependency edges (generic depends-on) ---
    for rx, via, dep_conf in MANIFEST_DEP_RES:
        if base in ("pom.xml",) and via != "maven":
            continue
        hits = rx.findall(text)
        if hits:
            cfg_id = _own_node(ctx, file_id, rel, 1, f"config:{rel}",
                               "configuration", base, "MEDIUM",
                               {"kind": "manifest", "via": via})
            for h in hits[:80]:
                dep = h if isinstance(h, str) else h[0]
                dep = dep.strip()
                line = text.find(dep)
                ctx.edge(cfg_id,
                         _sid(f"unresolved:module:{via}:{dep}"),
                         "depends-on",
                         text[:line].count("\n") + 1 if line >= 0 else 1,
                         dep_conf, {"via": via})
            return
