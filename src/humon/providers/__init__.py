"""LLM providers. Each implements ``core.interfaces.LLMProvider`` and imports
only ``core.interfaces``. Heavy SDKs are imported lazily inside each provider so
the core stays installable without them.
"""
