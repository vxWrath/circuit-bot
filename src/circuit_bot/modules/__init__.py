"""Feature modules: each one owns its tables, its shapes and its service.

Nothing here is imported by ``core`` - the dependency runs ``commands`` ->
``modules`` -> ``core``, so a module can be added or removed without the rest of
the bot noticing.
"""
