import contextlib

import unittest
import unittest.mock as mock

import ksiemgowy.__main__ as ksiemgowy_main
import ksiemgowy.models
from ksiemgowy.mbankmail import MbankAction


def run_immediately(_, fn, args, kwargs):
    fn(*args, **kwargs)


class EntrypointTestCase(unittest.TestCase):
    def setUp(self):

        """Generates a mock that fakes imaplib interface, returning e-mails
        from a given iterator. Not my proudest hack. Apologies!"""

        self.sent_messages = []
        self.incoming_messages = []

        def mail_fetch_mock(mail, _):
            return None, [(None, mail)]

        def return_messages(*args, **kwargs):
            return self.incoming_messages

        mail_connection_mock = mock.Mock()
        messages_mock = mock.Mock()
        messages_mock.split.side_effect = return_messages
        mail_connection_mock.search.return_value = (None, [messages_mock])
        mail_connection_mock.fetch.side_effect = mail_fetch_mock
        mail_mock = mock.Mock()
        mail_mock.imap_connect.return_value = mail_connection_mock

        def send_message_mock(msg):
            self.sent_messages.append(msg)

        @contextlib.contextmanager
        def smtp_login_mock(*args, **kwargs):
            server_mock = mock.Mock()
            server_mock.send_message.side_effect = send_message_mock
            yield server_mock

        mail_mock.smtp_login = smtp_login_mock

        self.config_mock = ksiemgowy_main.KsiemgowyConfig(
            database_uri="",
            deploy_key_path="",
            accounts=[
                ksiemgowy_main.KsiemgowyAccount(
                    mail_config=mail_mock, acc_number="81089394"
                )
            ],
            mbank_anonymization_key=b"",
        )
        self.database_mock = ksiemgowy.models.KsiemgowyDB("sqlite://")

    def run_entrypoint(self):

        ksiemgowy_main.main(
            self.config_mock,
            self.database_mock,
            mock.Mock(),
            run_immediately,
            mock.Mock(),
        )

    def test_entrypoint_doesnt_crash(self):

        with open(
            "docs/przykladowy_zalacznik_mbanku.eml",
            "rb",
        ) as f:
            self.incoming_messages = [f.read()]
            self.run_entrypoint()

    def test_entrypoint_sends_a_message(self):

        with open(
            "docs/przykladowy_zalacznik_mbanku.eml",
            "rb",
        ) as f:
            self.incoming_messages = [f.read()]
            self.run_entrypoint()
            self.assertNotEqual(len(self.sent_messages), 0)


    def test_build_confirmation_mail_copies_email_if_not_in_mapping(self):
        mbank_action = MbankAction(
            in_acc_no="a",
            out_acc_no="b",
            amount_pln="100",
            in_person="asd",
            in_desc="e",
            balance="100",
            timestamp="2021-09-09 22:39:11.099772",
            action_type="in_transfer",
        )
        msg = ksiemgowy_main.build_confirmation_mail(
            fromaddr="from@address",
            toaddr="to_address",
            mbank_action=mbank_action,
            emails={},
            mbank_anonymization_key=b"ad",
        )
        self.assertEqual(msg["To"], "to_address")
