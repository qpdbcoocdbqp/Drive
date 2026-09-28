"""Compatibility entry point for the reorganized MCP server."""

try:
    from .mcp.skill_server import (  # noqa: F401
        MAX_FILES_PER_SKILL,
        MAX_VIEW_BYTES,
        READ_ONLY,
        SKILL_ROOTS_ENV,
        configure_skill_roots,
        main,
        server,
        skill_files,
        skill_instructions_resource,
        skill_list,
        skill_support_resource,
        skill_view,
    )
except ImportError:  # Executed directly by an older stdio configuration.
    from src.oai_skill.mcp.skill_server import (  # type: ignore[no-redef] # noqa: F401
        MAX_FILES_PER_SKILL,
        MAX_VIEW_BYTES,
        READ_ONLY,
        SKILL_ROOTS_ENV,
        configure_skill_roots,
        main,
        server,
        skill_files,
        skill_instructions_resource,
        skill_list,
        skill_support_resource,
        skill_view,
    )


if __name__ == "__main__":
    main()
