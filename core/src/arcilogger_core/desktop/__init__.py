"""ARCILOGGER Durum: the small read-only status window on the server's desktop (Wave 5 contract
§5, decisions I1-I5; owner request of 2026-11-02).

* :mod:`.status`: what is shown and how it is judged (no Tk, fully testable);
* :mod:`.winapi`: the Windows calls (services, elevation, one instance, flashing);
* :mod:`.window`: the tkinter view;
* ``python -m arcilogger_core.desktop``: the entry point (:mod:`.__main__`).
"""
