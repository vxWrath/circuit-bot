import msgspec

__all__ = ("APIModel", "Model")


class Model(msgspec.Struct):
    """Base class for every model the bot passes around.

    A model mirrors a source and nothing more: a row mirrors a table, an API
    payload mirrors a response.  Models hold no clients and do no I/O, so they
    stay cheap to build, easy to test, and safe to encode.

    A model is a :class:`msgspec.Struct`, so it declares its fields and nothing
    else::

        class PlayerRow(PlayerTable):
            snowflake: int
            name: str

    Reading and writing go through msgspec directly - ``msgspec.convert`` for
    a mapping, ``msgspec.json.decode`` for bytes, ``msgspec.to_builtins`` and
    ``msgspec.json.encode`` back out - so a model carries no encoding of its
    own to keep in step with its fields.

    Reading is strict, because msgspec is: a field is the shape it was declared
    or the payload is refused.  ``"12"`` is not an ``int``, ``12.9`` does not
    truncate into one, and ``"false"`` is not a ``bool``.  A model is read back
    from somewhere it was written earlier - a row, a cache entry - and a value
    that no longer fits is a bug worth hearing about, not one to coerce into
    looking right::

        msgspec.convert(record, type=PlayerRow)
        # ValidationError: Expected `int`, got `str` - at `$.snowflake`

    Struct options are declared where the class is and are *not* inherited, so
    a shape whose defaulted field comes before a required one says so itself::

        class NewPlayer(PlayerTable, kw_only=True):
            id: int | None = None
            snowflake: int

    Anything the bot doesn't own the spelling of belongs on :class:`APIModel`
    instead.  A model belongs to exactly one of the two branches.
    """


class APIModel(Model):
    """A shape that mirrors a third-party payload, spelled as the source spells it.

    Inbound only: never inserted into Postgres, never a stable stored shape.
    Unknown fields are ignored, so an API that adds one doesn't break a decode -
    the right trade for someone else's payload.

    A field the source spells differently carries that spelling as the wire
    name, and a payload decodes straight into the shape it arrives as::

        class RobloxUserIngest(APIModel):
            id: int
            username: str = msgspec.field(name="name")
            display_name: str = msgspec.field(name="displayName")

        ingested = msgspec.json.decode(body, type=RobloxUserIngest)

    An ingest shape is how a rename is carried: the wire spelling lives on the
    field, the name the rest of the bot uses lives on the model it is converted
    into, and ``msgspec.json.encode`` on that model writes the bot's own
    spelling back out rather than the source's::

        user = msgspec.convert(ingested, RobloxUser, from_attributes=True)

    ``from_attributes=True`` is what lets one struct convert into another;
    without it msgspec expects a mapping and refuses the struct.
    """
