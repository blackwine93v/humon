"""Built-in tools. Each implements ``core.interfaces.Tool`` and imports only
``core.interfaces`` (plus sibling tool utilities). Tools declare the permissions
they need and never self-authorize — the policy engine decides (FR-6.1).
"""
