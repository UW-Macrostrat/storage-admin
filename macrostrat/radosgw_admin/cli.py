"""
Client for the Ceph Object Gateway Admin Operations API.
"""

import logging
import os
from contextvars import ContextVar

import humanize
import typer

from rgwadmin import RGWAdmin
from rgwadmin.exceptions import RGWAdminException
from rgwadmin.user import RGWUser
from typer import Typer, Option, Argument

from .utils import (
    is_system_user,
    simplify,
    print_json,
    jsonify_bucket,
    jsonify_user,
    UserError,
)


def get_connection(  # nosec hardcoded_password_default
    access_key: str = "",
    secret_key: str = "",
    admin_path: str = "/admin",
) -> RGWAdmin:
    # pylint: disable=raise-missing-from
    """
    Returns a connection object for the Ceph Object Gateway.
    """

    try:
        with open("/etc/htpheno/radosgw.json", encoding="utf-8") as fp:
            config = json.loads(fp.read())
        for k in ["RADOSGW_HOST", "RADOSGW_ACCESS_KEY", "RADOSGW_SECRET_KEY"]:
            os.environ.setdefault(k, config[k])
    except FileNotFoundError:
        pass

    try:
        host = os.environ["RADOSGW_HOST"]
    except KeyError:
        raise UserError("'RADOSGW_HOST' not set in the environment")

    try:
        access_key = access_key or os.environ["RADOSGW_ACCESS_KEY"]
    except KeyError:
        raise UserError("'RADOSGW_ACCESS_KEY' not set in the environment")

    try:
        secret_key = secret_key or os.environ["RADOSGW_SECRET_KEY"]
    except KeyError:
        raise UserError("'RADOSGW_SECRET_KEY' not set in the environment")

    admin = admin_path
    if admin_path.startswith("/"):
        admin = admin_path[1:]

    return RGWAdmin(access_key, secret_key, host, admin)


# --------------------------------------------------------------------------


def create_command(**kwargs) -> Typer:
    return Typer(no_args_is_help=True, **kwargs)


app = Typer(no_args_is_help=True)

import json
import sys
from enum import Enum


class OutputMode(str, Enum):
    human = "human"
    json = "json"
    auto = "auto"


def resolve_mode(mode: OutputMode, json_flag: bool | None) -> OutputMode:
    # Highest precedence: explicit --json / --no-json style flag
    if json_flag is True:
        return OutputMode.json
    if json_flag is False:
        return OutputMode.human

    # Next: explicit --output
    if mode != OutputMode.auto:
        return mode

    # Fallback: interactive => human, piped/redirected => json
    return OutputMode.human if sys.stdout.isatty() else OutputMode.json


output_mode_ctx: ContextVar[OutputMode] = ContextVar("output_mode", default=OutputMode.auto)


@app.callback()
def command_callback(
    output_mode: OutputMode = Option(OutputMode.auto, "--output"),
    json_out: bool | None = Option(None, "--json/--human", help="Output as JSON"),
    verbose: bool = Option(False, "--verbose", "-v", help="Be chatty"),
) -> None:
    """Callback to set whether we're in human-readable mode."""
    mode = resolve_mode(output_mode, json_out)
    output_mode_ctx.set(mode)

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


user_cmd = create_command(short_help="Manage users")


@user_cmd.command("get")
def get_users(
    include_system: bool = Option(False, "--system/--no-system", help="Include system users"),
) -> None:
    conn = get_connection()
    users = []

    for uid in conn.get_users():
        if include_system or not is_system_user(uid):
            ures = conn.get_user(uid)
            users.append(RGWUser(**ures))

    print_json([jsonify_user(u) for u in users])


uid_arg = Argument(..., help="User ID")
uid_option = Option(None, "--user", "-u", help="User ID")


@user_cmd.command("create")
def create_user(
    uid: str = uid_arg,
    display_name: str = Argument(..., help="Display name"),
    email: str | None = Option(None, "--email", help="Email address"),
    caps: str | None = Option(None, "--caps", help="User caps"),
) -> None:
    conn = get_connection()
    user = conn.create_user(
        uid=uid,
        display_name=display_name,
        email=email,
        user_caps=caps,
    )

    print_json(jsonify_user(user))


@user_cmd.command("delete")
def delete_user(uid: str = uid_arg) -> None:
    conn = get_connection()
    conn.remove_user(uid)


@user_cmd.command("get-quota")
def get_quota(
    uid: str = uid_arg,
) -> None:
    conn = get_connection()
    bucket = json.loads(conn.get_quota(uid, "bucket"))
    user = json.loads(conn.get_quota(uid, "user"))

    print_json(
        {
            "user": {
                "enabled": user["enabled"],
                "max_size": humanize.naturalsize(user["max_size"], gnu=True),
                "max_objects": humanize.intcomma(user["max_objects"]),
            },
            "bucket": {
                "enabled": bucket["enabled"],
                "max_size": humanize.naturalsize(bucket["max_size"], gnu=True),
                "max_objects": humanize.intcomma(bucket["max_objects"]),
            },
        }
    )


@user_cmd.command("set-quota")
def set_quota(
    uid: str = uid_arg,
    max_objects: int = Argument(..., help="Maximum number of objects"),
    max_size_gb: int = Argument(..., help="Maximum size in GB"),
) -> None:
    if is_system_user(uid):
        raise UserError("Cannot modify a system user")

    conn = get_connection()

    conn.set_user_quota(
        uid,
        "bucket",
        max_size_kb=-1,
        max_objects=max_objects,
        enabled="False",
    )

    conn.set_user_quota(
        uid,
        "user",
        max_size_kb=max_size_gb * 1024 * 1024,
        max_objects=max_objects,
        enabled="True",
    )


app.add_typer(user_cmd, name="user")

bucket_cmd = create_command()

bucket_name_arg = Argument(..., help="Bucket name")


@app.command("buckets")
def buckets_cmd() -> None:
    """List buckets"""
    conn = get_connection()
    buckets = conn.get_bucket(stats=False)
    print_json(buckets)


@app.command("users")
def users_cmd() -> None:
    """List users"""
    conn = get_connection()
    users = conn.get_users()
    print_json(users)


@bucket_cmd.command("get")
def get_buckets(
    name: str | None = typer.Argument(None, help="Bucket name"),
    uid: str | None = typer.Option(None, "--user", "-u", help="User ID"),
) -> None:
    """List all buckets."""
    conn = get_connection()
    buckets = conn.get_bucket(bucket=name, uid=uid, stats=True)
    print_json([jsonify_bucket(b) for b in buckets])


@bucket_cmd.command("create")
def create_bucket(uid: str = uid_arg, name: str = bucket_name_arg) -> None:
    """Create a new bucket."""
    conn = get_connection()
    user = conn.get_user(uid)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    try:
        conn.request("PUT", f"/{name}")
        print("OK")
    except RGWAdminException as e:
        print("Error:", e)


@bucket_cmd.command("get-policy", short_help="Get bucket policy")
def get_policy(name: str = bucket_name_arg) -> None:
    """Get a bucket's policy."""
    conn = get_connection()
    bucket = conn.get_bucket(name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    response = conn.request("GET", f"/{name}?policy")
    print(json.dumps(response, indent=2))


@bucket_cmd.command("allow-read")
def allow_read(
    bucket_name: str = bucket_name_arg,
    uid_of_reader: str | None = uid_option,
    public: bool = Option(False, "--public", help="Allow public read access"),
) -> None:
    """Allow a user (or the public) to read from a bucket."""
    if uid_of_reader is None and not public:
        raise UserError("Must specify a user ID or --public")

    conn = get_connection()
    bucket = conn.get_bucket(bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    response = conn.request("GET", f"/{bucket_name}?policy")

    principal = "*"
    if not public:
        principal = f"arn:aws:iam:::user/{uid_of_reader}"

    new_statements = [
        {
            "Action": [
                "s3:GetBucketLocation",
                "s3:ListBucket",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": [principal]},
            "Resource": [f"arn:aws:s3:::{bucket_name}"],
            "Sid": "",
        },
        {
            "Action": [
                "s3:GetObject",
                "S3:GetObjectVersion",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": [principal]},
            "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            "Sid": "",
        },
    ]

    new_policy = {
        "Statement": simplify(response.get("Statement", []) + new_statements),
        "Version": "2012-10-17",
    }

    try:
        conn.request(
            "PUT",
            f"/{bucket_name}?policy",
            data=json.dumps(new_policy),
        )
    except RGWAdminException as e:
        print("Error:", e)


@bucket_cmd.command("allow-write")
def allow_write(
    bucket_name: str = bucket_name_arg,
    uid_of_writer: str = uid_arg,
) -> None:
    """Allow a user to write to a bucket."""
    conn = get_connection()
    bucket = conn.get_bucket(bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    result = conn.request("GET", f"/{bucket_name}?policy")

    new_statements = [
        {
            "Action": [
                "s3:GetBucketLocation",
                "s3:ListBucketMultipartUploads",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": [f"arn:aws:iam:::user/{uid_of_writer}"]},
            "Resource": [f"arn:aws:s3:::{bucket_name}"],
            "Sid": "",
        },
        {
            "Action": [
                "s3:AbortMultipartUpload",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:ListMultipartUploadParts",
                "s3:PutObject",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": [f"arn:aws:iam:::user/{uid_of_writer}"]},
            "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            "Sid": "",
        },
    ]

    new_policy = {
        "Statement": simplify(result.get("Statement", []) + new_statements),
        "Version": "2012-10-17",
    }

    try:
        conn.request(
            "PUT",
            f"/{bucket_name}?policy",
            data=json.dumps(new_policy),
        )
    except RGWAdminException as e:
        print("Error:", e)


@bucket_cmd.command("make-private")
def make_private(bucket_name: str = bucket_name_arg) -> None:
    """Make a bucket private."""
    conn = get_connection()
    bucket = conn.get_bucket(bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    conn.request(
        "PUT",
        f"/{bucket_name}?policy",
        data="{}",
    )


app.add_typer(bucket_cmd, name="bucket", short_help="Manage buckets")


# --------------------------------------------------------------------------


def init_logging() -> None:
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s %(message)s",
        level=logging.ERROR,
        stream=sys.stderr,
    )


def entrypoint() -> None:
    init_logging()
    app()


if __name__ == "__main__":
    entrypoint()
