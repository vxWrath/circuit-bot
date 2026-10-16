import msgspec

__all__ = ("APIModel", "Model")


class Model(msgspec.Struct):
    """Base class for every model the bot passes around.

    Mirrors a source and nothing more - no clients, no I/O.  msgspec does the
    reading and writing, and reads strictly: a field is the shape it was
    declared or the payload is refused.
    """


class APIModel(Model):
    """A shape that mirrors a third-party payload, spelled as the source spells it.

    Inbound only: never inserted into Postgres, never a stable stored shape.
    Unknown fields are ignored, so an API that adds one doesn't break a decode,
    and a differently-spelled field carries its wire name via
    ``msgspec.field(name=...)``.
    """
