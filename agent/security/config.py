"""
security/config.py — Centralized security policies and tool permissions.
"""

TOOL_POLICIES = {
    # Kafka mode: multi-user, untrusted input, production data
    "kafka": {
        "allowed_tools": [
            "hybrid_retrieval",
            "query_database",    # read-only, validated
            "analyze_csv",
            "generate_chart",
            "extract_pdf",
            "extract_doc",
            "TodoWrite",
            "load_skill",
        ],
        "denied_tools": [
            "bash",              # NEVER in multi-user mode
            "background_run",    # NEVER in multi-user mode
            "write_file",        # no filesystem writes in production
            "edit_file",         # no filesystem edits in production
        ],
        "max_tool_calls_per_session": 50,
        "max_tool_calls_per_minute": 20,
    },

    # CLI mode: single developer, local machine
    "cli": {
        "allowed_tools": [
            "bash_safe",         # constrained bash (replaces open bash)
            "read_file",
            "write_file",
            "edit_file",
            "query_database",
            "analyze_csv",
            "generate_chart",
            "extract_pdf",
            "extract_doc",
            "get_stock_price",
            "hybrid_retrieval",
            "TodoWrite",
            "load_skill",
            "compress",
            "background_run",
            "check_background",
        ],
        "denied_tools": [],
        "max_tool_calls_per_session": 200,
        "max_tool_calls_per_minute": 60,
    },
}

ARGUMENT_RULES = {
    "query_database": {
        "sql": {
            "type": "string",
            "max_length": 2000,
            "deny_patterns": [
                r"(?i)\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
                r"(?i)\bINTO\s+OUTFILE\b",
                r"(?i)\bLOAD_FILE\b",
                r"(?i);\s*--",                 # comment-based injection
            ],
            "require_patterns": [
                r"(?i)^\s*SELECT\b",           # must be a SELECT query
            ],
        },
    },
    "hybrid_retrieval": {
        "query": {
            "type": "string",
            "max_length": 500,
        },
        "top_k": {
            "type": "integer",
            "min": 1,
            "max": 20,
        },
    },
    "read_file": {
        "path": {
            "type": "string",
            "max_length": 500,
            "deny_patterns": [
                r"\.\./",                      # path traversal (../)
                r"\.\.$",                      # path traversal (..)
                r"%2e%2e",                     # URL-encoded traversal
                r"\\x2e\\x2e",                # hex-encoded traversal
                r"^/etc/",                     # system files
                r"^/proc/",
                r"^/sys/",
                r"^/dev/",
                r"^/var/log/",
                r"^/home/[^/]+/\.ssh/",        # SSH keys
                r"^/root/",
                r"\.env",                      # env files
                r"credentials",
                r"\.key$",
                r"\.pem$",
                r"\.p12$",
                r"id_rsa",
                r"id_ed25519",
                r"known_hosts",
                r"\.bash_history",
                r"\.zsh_history",
                r"shadow$",
                r"passwd$",
            ],
            "must_resolve_under": "WORKDIR",   # path must resolve under agent working directory
        },
    },
    "bash_safe": {
        "command": {
            "type": "string",
            "max_length": 1000,
        },
    },
}
