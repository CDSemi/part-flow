"""PF-A3.4 integration harness (runs inside the isolated dind container as uid 0; never on a host).

Entry: ``python3 -B -m harness --root /srv/pfa34 --evidence /pfa34/evidence [--scenario …] [--rows …]``.
Every lifecycle or install effect on a pf-managed instance goes through the installed launcher at a scripted
terminal; the harness acts on pf-managed resources only as a recorded external fault or a read-only oracle.
"""
