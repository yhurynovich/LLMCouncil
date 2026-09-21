"""User and data processing utilities with security hardening.

Addresses all findings from the LLM Council code review:
- SQL injection prevention via parameterized queries
- Portable cursor/resource management (DB-API 2.0 compliant)
- Input validation for all public functions
- Explicit column selection (no SELECT *)
- Error handling and logging
- Type hints and docstrings
"""
import logging
from collections.abc import Iterable, Sequence
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Reject these types even though str/bytes are Sequence subtypes —
# treating them as key/value *pairs* would iterate character-by-character.
_REJECTED_SEQUENCE_TYPES = (str, bytes, bytearray)


def process_data(data: Iterable[Sequence[Any]]) -> dict[Any, Any]:
    """Convert an iterable of (key, value) pairs into a dictionary.

    Args:
        data: An iterable where each item is a sequence with exactly
              two elements (key, value).

    Returns:
        A dictionary mapping each key to its corresponding value.

    Raises:
        TypeError: If data is not iterable, or an item is not a
                   valid (non-string) sequence.
        ValueError: If an item does not have exactly 2 elements, a key
                    is not hashable, or a duplicate key is encountered.
    """
    if not isinstance(data, Iterable):
        raise TypeError("data must be an iterable of key/value pairs")

    result: dict[Any, Any] = {}
    for i, item in enumerate(data):
        if isinstance(item, _REJECTED_SEQUENCE_TYPES) or not isinstance(item, Sequence):
            raise TypeError(
                f"Item at index {i} must be a non-string sequence (list or tuple), "
                f"got {type(item).__name__}"
            )

        if len(item) != 2:
            raise ValueError(
                f"Item at index {i} must have exactly 2 elements (key, value), "
                f"got {len(item)}: {item!r}"
            )

        key, value = item[0], item[1]

        try:
            hash(key)
        except TypeError:
            raise ValueError(f"Key at index {i} is not hashable: {key!r}") from None

        if key in result:
            raise ValueError(f"Duplicate key {key!r} at index {i}")

        result[key] = value
    return result


def fetch_user(
    conn: Any,
    user_id: int,
    db_error: type[Exception] = Exception,
) -> Optional[dict[str, Any]]:
    """Fetch a single user by ID using a parameterized query.

    Args:
        conn: A DB-API 2.0 compliant connection object.
        user_id: The user's primary key (must be a non-negative integer).
        db_error: The database error class to catch (e.g. sqlite3.Error,
                  psycopg2.Error). Defaults to Exception so the function
                  works without knowing the driver, but callers should
                  pass the driver-specific error class for precise
                  catching.

    Returns:
        A dict with keys 'id', 'username', 'email', or None if not found.

    Raises:
        ValueError: If conn is None or user_id is not a non-negative integer.
        RuntimeError: On database errors.
        TypeError: If db_error is not an exception class.
    """
    if conn is None:
        raise ValueError("conn must be a database connection")

    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise ValueError("user_id must be an integer")
    if user_id < 0:
        raise ValueError("user_id must be non-negative")

    # Validate db_error early
    if not (isinstance(db_error, type) and issubclass(db_error, Exception)):
        raise TypeError("db_error must be an Exception subclass")

    query = "SELECT id, username, email FROM users WHERE id = %s"

    cursor = None
    row = None
    columns = None
    try:
        cursor = conn.cursor()
        cursor.execute(query, (user_id,))
        row = cursor.fetchone()
        if cursor.description is not None:
            columns = [desc[0] for desc in cursor.description]
    except db_error as e:
        # Rollback if the connection supports it (for write reuse)
        if hasattr(conn, "rollback"):
            try:
                conn.rollback()
            except Exception:
                logger.warning("Rollback failed for user %s", user_id, exc_info=True)
        logger.exception("Database error fetching user %s", user_id)
        raise RuntimeError(f"Failed to fetch user {user_id}") from e
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                logger.warning("Failed to close cursor for user %s", user_id, exc_info=True)

    if row is None:
        return None
    if columns is None:
        raise RuntimeError("cursor.description unavailable after SELECT")
    if len(columns) != len(row):
        raise RuntimeError(f"Column/row length mismatch: {len(columns)} vs {len(row)}")
    return dict(zip(columns, row))