"""Account erasure (cloud/account.py): the GDPR Art. 17 delete.

The one thing worth testing without a database is coverage. The endpoint tells
the user everything is gone, so any table holding their rows that the delete
list forgets turns that sentence into a false statement — and a table added a
year from now is exactly the kind of thing nobody remembers to add here.
"""
import hashlib

from cloud import account
from cloud.database import Base
from cloud.models import AccountDeletion, User


def _tables_referencing_users():
    """Every table with a foreign key into ``users`` — the ground truth the
    delete list has to keep up with."""
    found = set()
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == User.__tablename__:
                found.add(table.name)
    return found


class TestCoverage:
    def test_every_user_owned_table_is_erased(self):
        listed = {m.__tablename__ for m in account.USER_OWNED_TABLES}
        missing = _tables_referencing_users() - listed
        assert not missing, (
            f"{missing} reference users.id but are not erased on account "
            f"deletion — add them to account.USER_OWNED_TABLES")

    def test_children_are_deleted_before_their_parents(self):
        # clip_expiry_warnings has an FK into user_videos, so deleting the
        # videos first would trip that constraint.
        order = [m.__tablename__ for m in account.USER_OWNED_TABLES]
        assert order.index("clip_expiry_warnings") < order.index("user_videos")

    def test_the_erasure_record_itself_is_not_erased(self):
        # It has no FK to users on purpose (the row it would point at is gone),
        # so it must not appear in the delete list either.
        assert AccountDeletion not in account.USER_OWNED_TABLES
        assert AccountDeletion.__tablename__ not in _tables_referencing_users()


class TestEmailFingerprint:
    def test_is_a_plain_sha256_of_the_normalised_address(self):
        assert account.email_fingerprint("user@example.com") == \
            hashlib.sha256(b"user@example.com").hexdigest()

    def test_does_not_contain_the_address(self):
        assert "user@example.com" not in account.email_fingerprint("user@example.com")

    def test_survives_the_aliasing_the_account_key_ignores(self):
        # Sign-up normalises Gmail dots and +tags, so the fingerprint has to
        # match the same way or a deleted user could not be found by the
        # address they actually typed.
        assert account.email_fingerprint("Foo.Bar+news@gmail.com") == \
            account.email_fingerprint("foobar@gmail.com")

    def test_different_addresses_differ(self):
        assert account.email_fingerprint("a@example.com") != \
            account.email_fingerprint("b@example.com")
