"""Browser administration (ADR-0014): the channel between the API and the administration agent.

The API (``NT SERVICE\\ArciApi``) never writes the configuration, the secret key or the vault.
It writes one Ed25519-signed request per change into a protected inbox; the agent
``ArciAdmin`` (``NT SERVICE\\ArciAdmin``, no listener, no administrator rights) checks and
applies it, and hands evidence actions to the LOCAL SERVICE tasks as signed task requests.

* :mod:`arcilogger_core.admin.contract` — names, limits, the folder layout and every file format;
* :mod:`arcilogger_core.admin.messages` — the Turkish and English texts of the codes and states;
* :mod:`arcilogger_core.admin.envelope` — signing and verifying request files;
* :mod:`arcilogger_core.admin.keys` — the channel's key files and ``keys init``;
* :mod:`arcilogger_core.admin.rules` — what the browser may change and every check of it (the API
  imports it for its immediate answer, the agent runs it again authoritatively);
* :mod:`arcilogger_core.admin.tomledit` — the narrow editor of ``arcilogger.toml``;
* :mod:`arcilogger_core.admin.roots`, :mod:`~arcilogger_core.admin.zamane` — uploaded root
  certificates and Zamane clients;
* :mod:`arcilogger_core.admin.policy` — ``policy.json`` as the agent and the API use it;
* :mod:`arcilogger_core.admin.agent` — the agent ``ArciAdmin``, with
  :mod:`~arcilogger_core.admin.apply` (the transaction), :mod:`~arcilogger_core.admin.store`
  (its files), :mod:`~arcilogger_core.admin.dispatch` (task requests),
  :mod:`~arcilogger_core.admin.probe` (connection tests) and
  :mod:`~arcilogger_core.admin.winapi` (Windows calls);
* :mod:`arcilogger_core.admin.systemplan` — ApplySystem's input-free plan;
* :mod:`arcilogger_core.admin.testing` — fakes for the tests of every package.

This package never imports the database, the web stack, the worker or the licensing package:
the agent and the tasks run without them. Section numbers in its documentation ("contract
2.4.3") are those of the Wave 1 administration contract that ADR-0014 summarises.
"""
