"""Shared Skills V2 - physically decoupled from the legacy ``src/skills/`` tree.

This package is the only skill system used by the ``customer_ceshi`` link. It owns
its own registry, loader, manifests, adapters and upstream lock. It must never
import from ``skills.*`` (the legacy tree); the boundary is enforced by tests.
"""
