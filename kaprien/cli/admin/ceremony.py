#
# Ceremony
#
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional

import requests
import rich_click as click  # type: ignore
from click import ClickException
from rich import box, markdown, prompt, table  # type: ignore
from rich.console import Console  # type: ignore
from securesystemslib.exceptions import (  # type: ignore
    CryptoError,
    Error,
    FormatError,
    StorageError,
)
from securesystemslib.interface import (  # type: ignore
    import_ed25519_privatekey_from_file,
)

from kaprien.cli.admin import admin
from kaprien.helpers.tuf import RolesKeysInput, initialize_metadata

METADATA_CEREMONY_INTRO = """
# Metadata Initialization Ceremony

Create a new TUF Metadata initialization.

Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor
incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis
nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.
Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu
fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in
culpa qui officia deserunt mollit anim id est laborum.


**Steps**

1. Configure Roles
2. Load the Keys
3. Validate configuration

"""

STEP_1 = """
# STEP 1: [Optional] Configure the number of keys and the threshold

The TUF roles supports multiple keys and the threshold (quorun trust)
defines the minimal number of the keys required to take actions using
specific Role.

Reference: [TUF](https://theupdateframework.github.io/specification/latest/#goals-for-pki)


Skipping this Step will set all roles with number of keys as *one*
and threshold as *one*.

"""  # noqa

STEP_2 = """
# STEP 2: Load roles keys

The keys must to have a password and the file must to be accessible.

Depending of the Organization, each key has an owner. During the the key
loading process is important that the owner of the key insert the password.

The password or the key content is not shown in the screen.
"""

STEP_3 = """
# STEP 3: Validate configuration

The information below is the configuration done in the preview steps.
Check the number of keys, the threshold/quorun and type of key.

"""

PATHS_DELEGATION_MESSAGE = """
The role *targets* delegates `paths` to `bin`
role. See
[TUF Specification about Path Pattern](
    https://theupdateframework.github.io/specification/latest/#pathpattern
) for the paths pattern and the example.
"""

PATHS_EXAMPLE = """

Example:
--------

The Organization Example (https://example.com) has all files downloaded
`/downloads` path, meaning https://example.com/downloads/.

Additionally it has two sub-folders, `productA` and `productB` where the
clients can find all files (i.e.: `productA-v1.0.tar`, `productB-1.0.tar`), for
`productB`it has even a sub-folder, `updates` where clients can find update
files (i.e.: `servicepack-1.tar`, `servicepack-2.tar`)

In that case mapping all targets files paths as:
- https://example.com/downloads/ is `*`
- https://example.com/downloads/productA/ is `*/*`
- https://example.com/downloads/productB/ is `*/*` (same as above)
- https://example.com/downloads/productB/updates/ is `*/*/*`

Specific paths that role `targets` delegates are:
``*/productA/*, */productB/*, * /productB/updates/*``

Generic paths that role targets delegates are: ``*, */*, */*/*``
"""

console = Console()


class Roles(Enum):
    ROOT = "root"
    TARGETS = "targets"
    SNAPSHOT = "snapshot"
    TIMESTAMP = "timestamp"
    BIN = "bin"
    BINS = "bins"


@dataclass
class RoleSettings:
    expiration: int
    threshold: int
    keys: int
    offline_keys: bool


default_settings = {
    Roles.ROOT.value: RoleSettings(356, 1, 2, True),
    Roles.TARGETS.value: RoleSettings(365, 1, 2, True),
    Roles.SNAPSHOT.value: RoleSettings(1, 1, 1, False),
    Roles.TIMESTAMP.value: RoleSettings(1, 1, 1, False),
    Roles.BIN.value: RoleSettings(365, 1, 1, True),
    Roles.BINS.value: RoleSettings(1, 1, 1, False),
}


@dataclass
class Key:
    key: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class ServiceSettings:
    targets_base_url: str

    def to_dict(self):
        return asdict(self)


@dataclass
class PayloadSettings:
    roles: Dict[str, RolesKeysInput]
    service: ServiceSettings


class Methods(Enum):
    get = "get"
    post = "post"


OFFLINE_KEYS = {Roles.ROOT.value, Roles.TARGETS.value, Roles.BIN.value}

# generate the basic data structure
SETTINGS = PayloadSettings(
    roles={role.value: RolesKeysInput() for role in Roles},
    service=ServiceSettings(targets_base_url=""),
)


def _key_is_duplicated(key: Dict[str, Any]) -> bool:
    for role in SETTINGS.roles.values():
        if any(k for k in role.keys.values() if key == k.get("key")):
            return True
        if any(k for k in role.keys.values() if key == k.get("path")):
            return False

    return False


def _load_key(filepath: str, password: str) -> Key:
    try:
        key = import_ed25519_privatekey_from_file(filepath, password)
        return Key(key=key)
    except CryptoError as err:
        return Key(
            error=(
                f":cross_mark: [red]Failed[/]: {str(err)} Check the"
                " password."
            )
        )

    except (StorageError, FormatError, Error) as err:
        return Key(error=f":cross_mark: [red]Failed[/]: {str(err)}")


def _configure_role(rolename: str, role: RolesKeysInput) -> None:
    # default reset when start configuration
    role.keys.clear()

    role.threshold = default_settings[rolename].threshold
    role.offline_keys = default_settings[rolename].offline_keys

    role.expiration = prompt.IntPrompt.ask(
        (
            f"\nWhat [green]Metadata expiration[/] for [cyan]{rolename}[/]"
            " role?(Days)"
        ),
        default=default_settings[rolename].expiration,
        show_default=True,
    )

    role.num_of_keys = prompt.IntPrompt.ask(
        (
            f"What is the [green]number of keys[/] for "
            f"[cyan]{rolename}[/] role?"
        ),
        default=default_settings[rolename].keys,
        show_default=True,
    )
    if role.num_of_keys > 1:
        role.threshold = prompt.IntPrompt.ask(
            (
                f"What is the key [green]threshold[/] for "
                f"[cyan]{rolename}[/] role signing?"
            ),
            default=default_settings[rolename].threshold,
            show_default=True,
        )
    else:
        role.threshold = 1
        console.print(
            f"The [green]threshold[/] for [cyan]{rolename}[/] is "
            "[cyan]1[/] (one) based on the number of keys "
            "([cyan]1[/])."
        )

    if rolename == Roles.TARGETS.value:
        console.print(markdown.Markdown(PATHS_DELEGATION_MESSAGE), width=100)
        show_example = prompt.Confirm.ask("Show example", default="y")
        if show_example:
            console.print(markdown.Markdown(PATHS_EXAMPLE), width=100)

        targets_base_url = click.prompt(
            "\nWhat is the Base URL (i.e.: https://www.example.com/downloads/)"
        )
        if targets_base_url.endswith("/") is False:
            targets_base_url = targets_base_url + "/"

        SETTINGS.service.targets_base_url = targets_base_url

        input_paths = prompt.Prompt.ask(
            f"\nWhat [green]paths[/] [cyan]{rolename}[/] delegates?",
            default="*, */*",
            show_default=True,
        )
        delegate_paths = [path.strip() for path in input_paths.split(",")]
        role.paths = delegate_paths

    elif rolename == Roles.BINS.value:
        role.number_hash_prefixes = prompt.IntPrompt.ask(
            f"[green]Number of hashed bins[/] for [cyan]{rolename}[/]?",
            default=64,
            show_default=True,
        )


def _configure_keys(rolename: str, role: RolesKeysInput) -> None:
    key_count = 1
    while len(role.keys) < role.num_of_keys:
        filepath = prompt.Prompt.ask(
            f"\nEnter {key_count}/{role.num_of_keys} the "
            f"[cyan]{rolename}[/]`s Key [green]path[/]"
        )

        password = click.prompt(
            f"Enter {key_count}/{role.num_of_keys} the "
            f"{rolename}`s Key password",
            hide_input=True,
        )
        key: Key = _load_key(filepath, password)

        if key.error:
            console.print(key.error)
            try_again = prompt.Confirm.ask("Try again?", default="y")
            if try_again:
                continue
            else:
                raise click.ClickException("Required key not validated.")

        if key.key is not None and _key_is_duplicated(key.key) is True:
            console.print(":cross_mark: [red]Failed[/]: Key is duplicated.")
            continue

        role.keys[f"{rolename}_{key_count}"] = {
            "filename": filepath.split("/")[-1],
            "password": password,
            "key": key.key,
        }
        console.print(
            ":white_check_mark: Key "
            f"{key_count}/{role.num_of_keys} [green]Verified[/]"
        )
        key_count += 1


def _request_server(
    server: str,
    url: str,
    method: Methods,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[requests.Response]:

    if method == Methods.get:
        response = requests.get(f"{server}/{url}", json=payload)
        return response

    elif method == Methods.post:
        response = requests.post(f"{server}/{url}", json=payload)
        return response

    else:
        raise ValueError("Invalid Method")


@admin.command()
@click.option(
    "-b",
    "--bootstrap",
    "server",
    help=(
        "Bootstrap a Kaprien Server using the Repository Metadata after"
        "Ceremony"
    ),
    required=False,
)
def ceremony(server):
    """
    Start a new Metadata Ceremony.
    """
    if server:
        try:
            response = _request_server(
                server, "api/v1/bootstrap/", Methods.get
            )
        except requests.exceptions.ConnectionError:
            raise ClickException(f"Failed to connect to {server}")

        if response.status_code != 200:
            raise ClickException(
                f"Error: {response.status_code} | {response.text}"
            )

        json_response = response.json()
        if json_response.get("bootstrap") is True:
            raise ClickException(f"{json_response.get('message')}")

    console.print(markdown.Markdown(METADATA_CEREMONY_INTRO), width=100)
    start_ceremony = prompt.Confirm.ask("\nDo you want start the ceremony?")

    if start_ceremony is False:
        raise click.ClickException("Ceremony aborted.")

    console.print(markdown.Markdown(STEP_1), width=80)
    for rolename, role in SETTINGS.roles.items():
        _configure_role(rolename, role)

    console.print(markdown.Markdown(STEP_2), width=100)
    start_ceremony = prompt.Confirm.ask(
        "\nReady to start loading the keys? Passwords will be "
        "required for keys"
    )
    if start_ceremony is False:
        raise click.ClickException("Ceremony aborted.")

    for rolename, role in SETTINGS.roles.items():
        _configure_keys(rolename, role)

    console.print(markdown.Markdown(STEP_3), width=100)

    for rolename, role in SETTINGS.roles.items():
        while True:
            role_table = table.Table()
            role_table.add_column(
                "ROLE SUMMARY",
                style="yellow",
                justify="center",
                vertical="middle",
            )
            role_table.add_column("KEYS", justify="center", vertical="middle")
            keys_table = table.Table(box=box.MINIMAL)
            keys_table.add_column(
                "path", justify="right", style="cyan", no_wrap=True
            )
            keys_table.add_column("id", justify="center")
            keys_table.add_column("verified", justify="center")
            for key in role.keys.values():
                keys_table.add_row(
                    key.get("filename"),
                    key.get("key").get("keyid"),
                    ":white_heavy_check_mark:",
                )

            if role.offline_keys is True:
                key_type = "[red]offline[/red]"
            else:
                key_type = "[green]online[/]"

            role_table.add_row(
                (
                    f"Role: [cyan]{rolename}[/]"
                    f"\nNumber of Keys: {len(role.keys)}"
                    f"\nThreshold: {role.threshold}"
                    f"\nKeys Type: {key_type}"
                    f"\nRole Expiration: {role.expiration} days"
                ),
                keys_table,
            )

            if rolename == Roles.TARGETS.value:
                delegations_row = (
                    f"\n{SETTINGS.service.targets_base_url}".join(
                        ["", *role.paths]
                    )
                )
                role_table.add_row(
                    (
                        "\n"
                        "\n[orange1]DELEGATIONS[/]"
                        f"\n[aquamarine3]{rolename} -> bin[/]"
                        f"{delegations_row}"
                    ),
                    "",
                )

            if rolename == Roles.BINS.value:
                role_table.add_row(
                    (
                        "\n"
                        "\n[orange1]DELEGATIONS[/]"
                        f"\n[aquamarine3]{rolename} -> bins[/]"
                        f"\nNumber bins: {role.number_hash_prefixes}"
                    ),
                    "",
                )

            console.print(role_table)
            confirm_config = prompt.Confirm.ask(
                f"Configuration correct for {rolename}?"
            )
            if not confirm_config:
                # reconfigure role and keys
                _configure_role(rolename, role)
                _configure_keys(rolename, role)
            else:
                break

    metadata = initialize_metadata(SETTINGS.roles)

    json_payload: Dict[str, Any] = dict()

    json_payload["settings"] = {"service": SETTINGS.service.to_dict()}
    for role, data in SETTINGS.roles.items():
        if data.offline_keys is True:
            data.keys.clear()

        if "roles" not in json_payload["settings"]:
            json_payload["settings"]["roles"] = {role: data.to_dict()}
        else:
            json_payload["settings"]["roles"][role] = data.to_dict()

    json_payload["metadata"] = {
        key: data.to_dict() for key, data in metadata.items()
    }

    while True:
        if server:
            response = _request_server(
                server, "api/v1/bootstrap", Methods.post, json_payload
            )

            if response.status_code != 201:
                raise ClickException(response.text)
            else:
                console.print("Ceremony and Bootstrap done.")
                break
        else:
            break
