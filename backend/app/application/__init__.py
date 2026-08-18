"""Application layer: workflow orchestration and business rules.

Sits between the thin API routes (Presentation) and the persistence
mappings (Infrastructure). Services here own the transaction boundary
of each configuration change and every business rule PostgreSQL cannot
express declaratively.
"""
