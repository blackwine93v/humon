"""Persistence layer — a single SQLite file in WAL mode (NFR-6).

Used only by ``humon.core`` (layering rule). Everything lives in one file so a
backup is literally "copy one file".
"""
