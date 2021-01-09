#!/usr/bin/env python

import argparse
import collections
import contextlib
import datetime
import dateutil.rrule
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import yaml

import schedule

import ksiemgowy.public_state

from yaml.representer import Representer

yaml.add_representer(collections.defaultdict, Representer.represent_dict)


LOGGER = logging.getLogger("homepage_updater")
HOMEPAGE_REPO = "hakierspejs/homepage"
DUES_FILE_PATH = "_data/dues.yml"
MEETUP_FILE_PATH = "_includes/next_meeting.txt"


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


def get_local_state_dues(db):

    last_updated = None
    observed_acc_numbers = set()
    observed_acc_owners = set()

    # manual correction because of various bugs/problems
    total_expenses = -1279.159
    monthly_expenses = collections.defaultdict(
        lambda: collections.defaultdict(float)
    )
    for action in db.list_expenses():
        total_expenses -= action.amount_pln
        month = f"{action.timestamp.year}-{action.timestamp.month:02d}"
        kategoria = "Pozostałe"
        if (
            action.in_desc == "09564e96eabee7aadd8ee3"
            "c4518ad9878cedb09ac31c2b7dc11ffe23ca3be4bb"
        ):
            kategoria = "Czynsz"
        if (
            action.out_acc_no == "62eb7121a7ba81754aa746762dbc364e9ed961b"
            "8d1cf61a94d6531c92c81e56f"
        ):
            kategoria = "Internet"
        monthly_expenses[month][kategoria] += action.amount_pln
        if last_updated is None or action.timestamp > last_updated:
            last_updated = action.timestamp

    now = datetime.datetime.now()
    month_ago = now - datetime.timedelta(days=31)
    total = 200
    num_subscribers = 1
    total_ever = 0

    monthly_income = collections.defaultdict(
        lambda: collections.defaultdict(float)
    )
    for action in db.list_mbank_actions():

        month = f"{action.timestamp.year}-{action.timestamp.month:02d}"
        monthly_income[month]["Suma"] += action.amount_pln

        total_ever += action.amount_pln
        if action.timestamp < month_ago:
            continue
        elif last_updated is None or action.timestamp > last_updated:
            last_updated = action.timestamp
        if (
            action.in_acc_no not in observed_acc_numbers
            and action.in_person not in observed_acc_owners
        ):
            num_subscribers += 1
            observed_acc_numbers.add(action.in_acc_no)
            observed_acc_owners.add(action.in_person)
        total += action.amount_pln

    first_200pln_d33tah_due_date = datetime.datetime(year=2020, month=6, day=7)
    for timestamp in dateutil.rrule.rrule(
        dateutil.rrule.MONTHLY,
        dtstart=first_200pln_d33tah_due_date,
        until=now,
    ):
        month = f"{timestamp.year}-{timestamp.month:02d}"
        monthly_income[month]["Suma"] += 200
        total_ever += 200

    extra_monthly_reservations = sum(
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

    monthly_expenses['2020-08']['Meetup'] += 294.36
    monthly_expenses['2020-10']['Remont'] += 1145
    monthly_expenses['2020-11']['Pozostałe'] += 139.80
    monthly_income['2020-04']['Suma'] += 200
    monthly_income['2020-05']['Suma'] += 100

    months = set(monthly_income.keys()).union(
            set(monthly_expenses.keys())
    )

    monthly_balance = {
        month: {
            "Suma": sum(x for x in monthly_income.get(month, {}).values())
            - sum(x for x in monthly_expenses.get(month, {}).values())
        }
        for month in months
    }

    balance_so_far = 0
    monthly_final_balance = collections.defaultdict(
        lambda: collections.defaultdict(float)
    )
    for month in sorted(months):
        _monthly_income = sum(monthly_income.get(month, {}).values())
        _monthly_expenses = sum(monthly_expenses.get(month, {}).values())
        balance_so_far += _monthly_income - _monthly_expenses
        monthly_final_balance[month]['Suma'] = balance_so_far

    last_updated_s = last_updated.strftime("%d-%m-%Y")
    ret = {
        "dues_total_lastmonth": total,
        "dues_last_updated": last_updated_s,
        "dues_num_subscribers": num_subscribers,
        "dues_so_far": total_ever,
        "dues_total_correction": total_expenses,
        "extra_monthly_reservations": extra_monthly_reservations,
        "monthly": {
            "Wydatki": monthly_expenses,
            "Przychody": monthly_income,
            "Bilans": monthly_balance,
            "Saldo": monthly_final_balance,
        },
    }
    LOGGER.debug("get_local_state_dues: ret=%r", ret)
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
    local_state = get_local_state_dues(db)
    upload_to_graphite(local_state)
    remote_state = get_remote_state_dues()
    has_changed = do_states_differ(remote_state, local_state)
    if has_changed and is_newer(remote_state, local_state):
        remote_state.update(local_state)
        update_remote_state(
            f"homepage/{DUES_FILE_PATH}", remote_state, git_env
        )


def maybe_update(db, deploy_key_path):
    with git_cloned(deploy_key_path) as git_env:
        maybe_update_dues(db, git_env)


def main(state):
    deploy_key_path = os.environ["DEPLOY_KEY_PATH"]
    schedule.every().hour.do(maybe_update, state, deploy_key_path)
    time.sleep(6.0)
    maybe_update(state, deploy_key_path)
    while True:
        time.sleep(1.0)
        schedule.run_pending()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="continuous")
    parser.add_argument("--loglevel", default="INFO")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=args.loglevel.upper())
    PUBLIC_DB_URI = os.environ["PUBLIC_DB_URI"]
    state = ksiemgowy.public_state.PublicState(PUBLIC_DB_URI)
    if args.mode == "continuous":
        main(state)
    elif args.mode == "get_local_state_dues":
        new_state = get_local_state_dues(state)
        print(serialize(new_state))
    else:
        sys.exit("ERROR: unknown mode: %s" % args.mode)
