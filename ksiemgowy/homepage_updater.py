#!/usr/bin/env python

import collections
import contextlib
import datetime
import dateutil.rrule
import logging
import shutil
import socket
import subprocess
import time
import yaml
import os
import pickle
import sys
import difflib
import pprint

from yaml.representer import Representer

import ksiemgowy.public_state


yaml.add_representer(collections.defaultdict, Representer.represent_dict)


LOGGER = logging.getLogger("homepage_updater")
HOMEPAGE_REPO = "hakierspejs/homepage"
DUES_FILE_PATH = "_data/dues.yml"
MEETUP_FILE_PATH = "_includes/next_meeting.txt"
ACCOUNT_LABELS = {
    ("76561893"): "Konto Jacka",
    (
        "1f328d38b05ea11998bac3ee0a4a2c6c9595e6848d22f66a47aa4a68f3b781ed"
    ): "Konto Jacka",
    (
        "d66afcd5d08d61a5678dd3dd3fbb6b2f84985c5add8306e6b3a1c2df0e85f840"
    ): "Konto stowarzyszenia",
}

ACCOUNT_CORRECTIONS = {"Konto Jacka": -347.53, "Konto stowarzyszenia": -727.53}
MONTHLY_INCOME_CORRECTIONS = {
    "2020-04": {"Suma": 200},
    "2020-05": {"Suma": 100},
}

MONTHLY_EXPENSE_CORRECTIONS = {
    "2020-08": {"Meetup": 294.36},
    "2020-10": {"Remont": 1145},
    "2020-11": {"Pozostałe": 139.80},
    "2021-01": {
        "Drukarka HP": 314.00,
        "Meetup (za 6 mies.)": 285.43,
    },
    "2021-02": {"Domena": 55.34},
    "2021-05": {"Pozostałe": 200.0},
    "2021-07": {"Meetup (za 6 mies.)": 301.07},
    "2021-08": {"Zakupy": 840.04},
}


def serialize(d):
    # return json.dumps(d, indent=2)
    return yaml.dump(d)


def deserialize(d):
    # return json.loads(d)
    return yaml.safe_load(d)


def upload_value_to_graphite(h, metric, value):
    s = socket.socket()
    try:
        s.connect(h)
        now = int(time.time())
        buf = f"{metric} {value} {now}\n".encode()
        LOGGER.info("Sending %r to %r", buf, h)
        s.send(buf)
        s.close()
    except (ConnectionRefusedError, socket.timeout, TimeoutError) as e:
        LOGGER.exception(e)
    time.sleep(3.0)


def upload_to_graphite(d):
    h = ("graphite.hs-ldz.pl", 2003)
    upload_value_to_graphite(
        h, "hakierspejs.finanse.total_lastmonth", d["dues_total_lastmonth"]
    )
    upload_value_to_graphite(
        h, "hakierspejs.finanse.num_subscribers", d["dues_num_subscribers"]
    )


def get_empty_float_defaultdict():
    return collections.defaultdict(float)


def apply_corrections(
    balances_by_account_labels, monthly_income, monthly_expenses
):
    # Te hacki wynikają z bugów w powiadomieniach mBanku i braku powiadomień
    # związanych z przelewami własnymi:
    for account_name, value in ACCOUNT_CORRECTIONS.items():
        if account_name not in balances_by_account_labels:
            raise RuntimeError(
                "%r not in balances_by_account_labels" % account_name
            )
        balances_by_account_labels[account_name] += value

    balances_by_account_labels = dict(balances_by_account_labels)

    for month in MONTHLY_INCOME_CORRECTIONS:
        for label, value in MONTHLY_INCOME_CORRECTIONS[month].items():
            monthly_income[month][label] += value

    for month in MONTHLY_EXPENSE_CORRECTIONS:
        for label, value in MONTHLY_EXPENSE_CORRECTIONS[month].items():
            monthly_expenses[month][label] += value


def determine_category(action):
    kategoria = "Pozostałe"
    if (
        action.out_acc_no == "5c0de18baddf47952"
        "002df587685dea519f06b639051ea3e4749ef058f6782bf"
    ):
        if int(action.amount_pln) == 800:
            kategoria = "Czynsz"
        else:
            kategoria = (
                "Media (głównie prąd) i inne rozliczenia w zw. z lokalem"
            )
    if (
        action.out_acc_no == "62eb7121a7ba81754aa746762dbc364e9ed961b"
        "8d1cf61a94d6531c92c81e56f"
    ):
        kategoria = "Internet"
    if (
        action.out_acc_no == "8f8340d7434997c052cc56f0191ed23d12a16ab1"
        "f2cba091c433539c13b7049c"
    ):
        kategoria = "Księgowość"
    return kategoria


def apply_d33tah_dues(monthly_income):
    first_200pln_d33tah_due_date = datetime.datetime(year=2020, month=6, day=7)
    # After this day, this hack isn't requried anymore:
    last_200pln_d33tah_due_date = datetime.datetime(year=2021, month=5, day=5)
    for timestamp in dateutil.rrule.rrule(
        dateutil.rrule.MONTHLY,
        dtstart=first_200pln_d33tah_due_date,
        until=last_200pln_d33tah_due_date,
    ):
        month = f"{timestamp.year}-{timestamp.month:02d}"
        monthly_income[month]["Suma"] += 200


def apply_positive_transfers(now, last_updated):
    monthly_income = collections.defaultdict(get_empty_float_defaultdict)
    income_by_out_account = collections.defaultdict(float)
    observed_acc_numbers = set()
    observed_acc_owners = set()

    total = 0
    num_subscribers = 0
    month_ago = now - datetime.timedelta(days=31)
    for action in mbank_actions:
        income_by_out_account[action.out_acc_no] += action.amount_pln

        month = f"{action.timestamp.year}-{action.timestamp.month:02d}"
        monthly_income[month]["Suma"] += action.amount_pln

        if action.timestamp < month_ago:
            continue
        if last_updated is None or action.timestamp > last_updated:
            last_updated = action.timestamp
        if (
            action.in_acc_no not in observed_acc_numbers
            and action.in_person not in observed_acc_owners
        ):
            num_subscribers += 1
            observed_acc_numbers.add(action.in_acc_no)
            observed_acc_owners.add(action.in_person)
        total += action.amount_pln

    apply_d33tah_dues(monthly_income)

    return (
        total,
        num_subscribers,
        last_updated,
        income_by_out_account,
        monthly_income,
    )


def apply_expenses(expenses):
    last_updated = None
    monthly_expenses = collections.defaultdict(get_empty_float_defaultdict)
    expenses_by_out_account = collections.defaultdict(float)
    for action in expenses:
        expenses_by_out_account[action.in_acc_no] += action.amount_pln
        month = f"{action.timestamp.year}-{action.timestamp.month:02d}"
        category = determine_category(action)
        monthly_expenses[month][category] += action.amount_pln
        if last_updated is None or action.timestamp > last_updated:
            last_updated = action.timestamp
    return last_updated, monthly_expenses, expenses_by_out_account


def build_balances_by_account_labels(
    income_by_out_account, expenses_by_out_account
):
    balances_by_account_labels = collections.defaultdict(float)
    for acc_no, balance in income_by_out_account.items():
        balances_by_account_labels[ACCOUNT_LABELS[acc_no]] += balance
    for acc_no, balance in expenses_by_out_account.items():
        balances_by_account_labels[ACCOUNT_LABELS[acc_no]] -= balance
    return balances_by_account_labels


def build_monthly_final_balance(months, monthly_income, monthly_expenses):
    balance_so_far = 0
    monthly_final_balance = collections.defaultdict(
        get_empty_float_defaultdict
    )
    for month in sorted(months):
        _monthly_income = sum(monthly_income.get(month, {}).values())
        _monthly_expenses = sum(monthly_expenses.get(month, {}).values())
        balance_so_far += _monthly_income - _monthly_expenses
        monthly_final_balance[month]["Suma"] = balance_so_far
    return monthly_final_balance, balance_so_far


def build_monthly_balance(months, monthly_income, monthly_expenses):
    return {
        month: {
            "Suma": sum(x for x in monthly_income.get(month, {}).values())
            - sum(x for x in monthly_expenses.get(month, {}).values())
        }
        for month in months
    }


def build_extra_monthly_reservations(now):
    return sum(
        [
            200
            for _ in dateutil.rrule.rrule(
                dateutil.rrule.MONTHLY,
                # https://pad.hs-ldz.pl/aPQpWcUbTvWwEdwsxB0ulQ#Kwestia-sk%C5%82adek
                dtstart=datetime.datetime(year=2020, month=11, day=24),
                until=now,
            )
        ]
    )


def get_local_state_dues(now, expenses, mbank_actions):

    last_updated, monthly_expenses, expenses_by_out_account = apply_expenses(
        expenses
    )

    (
        total,
        num_subscribers,
        last_updated,
        income_by_out_account,
        monthly_income,
    ) = apply_positive_transfers(now, last_updated)

    extra_monthly_reservations = build_extra_monthly_reservations(now)

    balances_by_account_labels = build_balances_by_account_labels(
        income_by_out_account, expenses_by_out_account
    )

    apply_corrections(
        balances_by_account_labels, monthly_income, monthly_expenses
    )

    months = set(monthly_income.keys()).union(set(monthly_expenses.keys()))

    monthly_final_balance, balance_so_far = build_monthly_final_balance(
        months, monthly_income, monthly_expenses
    )

    ret = {
        "dues_total_lastmonth": total,
        "dues_last_updated": last_updated.strftime("%d-%m-%Y"),
        "dues_num_subscribers": num_subscribers,
        "extra_monthly_reservations": extra_monthly_reservations,
        "balance_so_far": balance_so_far,
        "balances_by_account_labels": balances_by_account_labels,
        "monthly": {
            "Wydatki": monthly_expenses,
            "Przychody": monthly_income,
            "Bilans": build_monthly_balance(
                months, monthly_income, monthly_expenses
            ),
            "Saldo": monthly_final_balance,
        },
    }
    LOGGER.debug("get_local_state_dues: ret=%r", ret)
    LOGGER.debug(
        "get_local_state_dues: "
        "income_by_out_account=%r"
        "expenses_by_out_account=%r",
        income_by_out_account,
        expenses_by_out_account,
    )
    return ret


def get_remote_state_dues():
    try:
        with open(f"homepage/{DUES_FILE_PATH}") as f:
            ret = deserialize(f.read())
        return ret
    except FileNotFoundError:
        return {}


def ssh_agent_import_key_and_build_env_and_setup_git(deploy_key_path):
    env = {}
    for line in subprocess.check_output(["ssh-agent", "-c"]).split(b"\n"):
        s = line.decode().split()
        if len(s) == 3 and s[0] == "setenv" and s[-1].endswith(";"):
            env[s[1]] = s[2].rstrip(";")
    subprocess.check_call(["ssh-add", deploy_key_path], env=env)
    subprocess.check_call(["ssh-add", "-l"], env=env)
    return env


def set_up_git_identity(username, email, cwd):
    subprocess.check_call(["git", "config", "user.email", email], cwd=cwd)
    subprocess.check_call(["git", "config", "user.name", username], cwd=cwd)


@contextlib.contextmanager
def git_cloned(deploy_key_path):
    cwd = "homepage"
    try:
        env = ssh_agent_import_key_and_build_env_and_setup_git(deploy_key_path)
        git_url = f"git@github.com:{HOMEPAGE_REPO}.git"
        env["GIT_SSH_COMMAND"] = " ".join(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
            ]
        )
        subprocess.check_call(["git", "clone", git_url, cwd], env=env)
        set_up_git_identity("ksiemgowy", "d33tah+ksiemgowy@gmail.com", cwd)
        yield env
    finally:
        try:
            shutil.rmtree(cwd)
        except FileNotFoundError:
            pass


def update_remote_state(filepath, new_state, env):
    with open(filepath, "w") as f:
        f.write(serialize(new_state))
    subprocess.check_call(
        ["git", "commit", "-am", "dues: autoupdate"], cwd="homepage", env=env
    )
    subprocess.check_call(["git", "push"], cwd="homepage", env=env)


def do_states_differ(remote_state, local_state):
    for k in local_state:
        if local_state.get(k) != remote_state.get(k):
            return True
    return False


def is_newer(remote_state, local_state):
    local_modified = datetime.datetime.strptime(
        local_state["dues_last_updated"], "%d-%m-%Y"
    )
    remote_modified = datetime.datetime.strptime(
        remote_state["dues_last_updated"], "%d-%m-%Y"
    )
    return local_modified > remote_modified


def maybe_update_dues(db, git_env):
    now = datetime.datetime.now()
    local_state = get_local_state_dues(
        now, db.list_expenses(), db.list_mbank_actions()
    )
    upload_to_graphite(local_state)
    remote_state = get_remote_state_dues()
    has_changed = do_states_differ(remote_state, local_state)
    if has_changed and is_newer(remote_state, local_state):
        LOGGER.info("maybe_update_dues: updating dues")
        remote_state.update(local_state)
        update_remote_state(
            f"homepage/{DUES_FILE_PATH}", remote_state, git_env
        )
    LOGGER.info("maybe_update_dues: done")


def maybe_update(db, deploy_key_path):
    with git_cloned(deploy_key_path) as git_env:
        maybe_update_dues(db, git_env)


def build_args():
    config = yaml.load(
        open(
            os.environ.get("KSIEMGOWYD_CFG_FILE", "/etc/ksiemgowy/config.yaml")
        )
    )
    ret = []
    public_db_uri = config["PUBLIC_DB_URI"]
    for account in config["ACCOUNTS"]:
        imap_login = account["IMAP_LOGIN"]
        imap_server = account["IMAP_SERVER"]
        imap_password = account["IMAP_PASSWORD"]
        acc_no = account["ACC_NO"]
        ret.append(
            [
                imap_login,
                imap_password,
                imap_server,
                acc_no,
                public_db_uri,
            ]
        )
    return ret


def compare_dicts(d1, d2):
    return "\n" + "\n".join(
        difflib.ndiff(
            pprint.pformat(d1).splitlines(), pprint.pformat(d2).splitlines()
        )
    )


if __name__ == "__main__":
    try:
        with open("testdata/input.pickle", "rb") as f:
            now = pickle.load(f)
            expenses = pickle.load(f)
            mbank_actions = pickle.load(f)
    except FileNotFoundError:
        args = build_args()
        public_db_uri = args[0][-1]
        db = ksiemgowy.public_state.PublicState(public_db_uri)
        now = datetime.datetime.now()
        expenses = list(db.list_expenses())
        mbank_actions = list(db.list_mbank_actions())
        with open("testdata/input.pickle", "wb") as f:
            pickle.dump(now, f)
            pickle.dump(expenses, f)
            pickle.dump(mbank_actions, f)
    try:
        with open("testdata/expected_output.pickle", "rb") as f:
            expected_output = pickle.load(f)
            local_state = get_local_state_dues(now, expenses, mbank_actions)
            if local_state == expected_output:
                print("Test passed")
            else:
                print(compare_dicts(local_state, expected_output))
                sys.exit("ERROR: test not passed.")
    except FileNotFoundError:
        local_state = get_local_state_dues(now, expenses, mbank_actions)
        with open("testdata/expected_output.pickle", "wb") as f:
            pickle.dump(local_state, f)
