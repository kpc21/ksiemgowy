"""This module describes data structures used in ksiemgowy."""

import logging
import dateutil.parser
import sqlalchemy


import ksiemgowy.mbankmail
from ksiemgowy.mbankmail import MbankAction
from typing import Any, Dict, Iterator, Generator

LOGGER = logging.getLogger(__name__)


class KsiemgowyDB:
    """A class that groups together all models that describe the state of
    ksiemgowy."""

    def __init__(self, database_uri: str) -> None:
        """Initializes the database, creating tables if they don't exist."""
        self.database = sqlalchemy.create_engine(database_uri)
        metadata = sqlalchemy.MetaData(self.database)

        self.mbank_actions = sqlalchemy.Table(
            "mbank_actions",
            metadata,
            sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
            sqlalchemy.Column("mbank_action", sqlalchemy.JSON),
        )

        self.expenses = sqlalchemy.Table(
            "expenses",
            metadata,
            sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
            sqlalchemy.Column("mbank_action", sqlalchemy.JSON),
        )

        try:
            self.mbank_actions.create()
        except (
            sqlalchemy.exc.OperationalError,
            sqlalchemy.exc.ProgrammingError,
        ):
            pass

        try:
            self.expenses.create()
        except (
            sqlalchemy.exc.OperationalError,
            sqlalchemy.exc.ProgrammingError,
        ):
            pass

        self.in_acc_no_to_email = sqlalchemy.Table(
            "in_acc_no_to_email",
            metadata,
            sqlalchemy.Column(
                "in_acc_no", sqlalchemy.String, primary_key=True
            ),
            sqlalchemy.Column("email", sqlalchemy.String),
            sqlalchemy.Column(
                "notify_arrived", sqlalchemy.String, default="y"
            ),
            sqlalchemy.Column(
                "notify_overdue", sqlalchemy.String, default="y"
            ),
            sqlalchemy.Column("is_member", sqlalchemy.String, default="n"),
        )

        self.observed_email_ids = sqlalchemy.Table(
            "observed_email_ids",
            metadata,
            sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
            sqlalchemy.Column("imap_id", sqlalchemy.String, unique=True),
        )

        try:
            self.in_acc_no_to_email.create()
        except (
            sqlalchemy.exc.OperationalError,
            sqlalchemy.exc.ProgrammingError,
        ):
            pass

        try:
            self.observed_email_ids.create()
        except (
            sqlalchemy.exc.OperationalError,
            sqlalchemy.exc.ProgrammingError,
        ):
            pass

    def was_imap_id_already_handled(self, imap_id: str) -> bool:
        """Tells whether a given IMAP ID was already processed by ksiemgowy."""
        for entry in self.observed_email_ids.select().execute().fetchall():
            LOGGER.debug(
                "was_imap_id_already_handled: %r vs %r", imap_id, entry.imap_id
            )
            if entry.imap_id == imap_id:
                return True
        return False

    def mark_imap_id_already_handled(self, imap_id: str) -> None:
        """Marks a given IMAP ID as already processed by ksiemgowy."""
        LOGGER.debug("mark_imap_id_already_handled(%r)", imap_id)
        self.observed_email_ids.insert(None).execute(imap_id=imap_id)

    def acc_no_to_email(self, notification_type: str) -> Dict[Any, Any]:
        """Builds a mapping between banking accounts an e-mail addresses for
        people interested in a given type of a notification."""
        ret = {}
        for entry in self.in_acc_no_to_email.select().execute().fetchall():
            if entry["notify_" + notification_type] == "y":
                ret[entry["in_acc_no"]] = entry["email"]

        return ret

    def list_positive_transfers(self) -> Iterator[MbankAction]:
        """Returns a generator that lists all positive transfers that were
        observed so far."""
        for entry in self.mbank_actions.select().execute().fetchall():
            ret = entry.mbank_action
            ret["timestamp"] = dateutil.parser.parse(ret["timestamp"])
            ret["amount_pln"] = float(ret["amount_pln"].replace(",", "."))
            yield ksiemgowy.mbankmail.MbankAction(**ret)

    def add_positive_transfer(self, mbank_action: Dict[str, str]) -> None:
        """Adds a positive transfer to the database."""
        self.mbank_actions.insert(None).execute(mbank_action=mbank_action)

    def add_expense(self, mbank_action: MbankAction) -> None:
        """Adds an expense to the database."""
        self.expenses.insert(None).execute(mbank_action=mbank_action)

    def list_expenses(self) -> Generator[MbankAction, None, None]:
        """Returns a generator that lists all expenses transfers that were
        observed so far."""
        for entry in self.expenses.select().execute().fetchall():
            ret = entry.mbank_action
            ret["timestamp"] = dateutil.parser.parse(ret["timestamp"])
            ret["amount_pln"] = float(ret["amount_pln"].replace(",", "."))
            yield ksiemgowy.mbankmail.MbankAction(**ret)
