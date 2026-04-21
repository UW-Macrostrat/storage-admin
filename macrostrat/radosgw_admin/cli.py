"""
Client for the Ceph Object Gateway Admin Operations API.
"""

import argparse
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Union, NamedTuple

import humanize

# import radosgw  # type: ignore[import-untyped]
# import radosgw.user  # type: ignore[import-untyped]

from rgwadmin import RGWAdmin
from rgwadmin.exceptions import RGWAdminException
from rgwadmin.user import RGWCap, RGWKey, RGWUser


class UserError(RuntimeError):
    """
    A runtime error where a stack trace should not be necessary.
    """


# --------------------------------------------------------------------------


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


def is_system_user(uid: str) -> bool:
    """
    Returns True if the uid is for a user that should not be modified.
    """
    return bool(re.search(r"admin|rook-ceph|system", uid))


def simplify(xs: List[Any]) -> List[Any]:
    """
    Attempts to remove duplicate bucket policy statements.
    """
    return [json.loads(x) for x in sorted(set(json.dumps(x) for x in xs))]


def print_json(obj: Union[Dict[Any, Any], List[Any]]) -> None:
    print(json.dumps(obj, indent=2))


# --------------------------------------------------------------------------


def jsonify_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    return bucket  # Passthrough inheriting older structure


def jsonify_cap(cap: RGWCap) -> str:
    return f"{cap.type}={cap.perm}"


def jsonify_key(key: RGWKey) -> Dict[str, Any]:
    return {
        # "key_type": key.key_type, # Thios was only present in the old system
        "access_key": key.access_key,
        "secret_key": key.secret_key,
    }


def jsonify_user(user: RGWUser) -> Dict[str, Any]:
    return {
        "uid": user.user_id,
        "display_name": user.display_name,
        "email": user.email,
        "keys": [jsonify_key(RGWKey(**k)) for k in user.keys],
        "caps": [jsonify_cap(RGWCap(**c)) for c in user.caps],
    }


def get_users(args: argparse.Namespace) -> None:
    conn = get_connection()
    users = []

    for uid in conn.get_users():
        if args.include_system or not is_system_user(uid):
            ures = conn.get_user(uid)
            users.append(RGWUser(**ures))

    print_json([jsonify_user(u) for u in users])


def create_user(args: argparse.Namespace) -> None:
    conn = get_connection()
    user = conn.create_user(
        uid=args.uid,
        display_name=args.display_name,
        email=args.email,
        user_caps=args.caps,
    )

    print_json(jsonify_user(user))


def delete_user(args: argparse.Namespace) -> None:
    conn = get_connection()
    conn.remove_user(args.uid)


def get_quota(args: argparse.Namespace) -> None:
    conn = get_connection()
    bucket = json.loads(conn.get_quota(args.uid, "bucket"))
    user = json.loads(conn.get_quota(args.uid, "user"))

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


def set_quota(args: argparse.Namespace) -> None:
    if is_system_user(args.uid):
        raise UserError("Cannot modify a system user")

    conn = get_connection()

    conn.set_user_quota(
        args.uid,
        "bucket",
        max_size_kb=-1,
        max_objects=args.max_objects,
        enabled="False",
    )

    conn.set_user_quota(
        args.uid,
        "user",
        max_size_kb=args.max_size_gb * 1024 * 1024,
        max_objects=args.max_objects,
        enabled="True",
    )


def get_buckets(args: argparse.Namespace) -> None:
    conn = get_connection()
    buckets = conn.get_bucket(bucket=None, uid=args.uid, stats=True)

    print_json([jsonify_bucket(b) for b in buckets])


def get_bucket(args: argparse.Namespace) -> None:
    conn = get_connection()

    print_json(jsonify_bucket(conn.get_bucket(args.bucket_name)))


def create_bucket(args: argparse.Namespace) -> None:
    conn = get_connection()
    user = conn.get_user(args.uid)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    try:
        conn.request("PUT", f"/{args.bucket_name}")
        print("OK")
    except RGWAdminException as e:
        print("Error:", e)


def get_policy(args: argparse.Namespace) -> None:
    conn = get_connection()
    bucket = conn.get_bucket(args.bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    response = conn.request("GET", f"/{args.bucket_name}?policy")
    print(json.dumps(response, indent=2))


def allow_read(args: argparse.Namespace) -> None:
    conn = get_connection()
    bucket = conn.get_bucket(args.bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    response = conn.request("GET", f"/{args.bucket_name}?policy")

    new_statements = [
        {
            "Action": [
                "s3:GetBucketLocation",
                "s3:ListBucket",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": [f"arn:aws:iam:::user/{args.uid_of_reader}"]},
            "Resource": [f"arn:aws:s3:::{args.bucket_name}"],
            "Sid": "",
        },
        {
            "Action": [
                "s3:GetObject",
                "S3:GetObjectVersion",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": [f"arn:aws:iam:::user/{args.uid_of_reader}"]},
            "Resource": [f"arn:aws:s3:::{args.bucket_name}/*"],
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
            f"/{args.bucket_name}?policy",
            data=json.dumps(new_policy),
        )
    except RGWAdminException as e:
        print("Error:", e)


def allow_public_read(args: argparse.Namespace) -> None:
    conn = get_connection()
    bucket = conn.get_bucket(args.bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    response = conn.request("GET", f"/{args.bucket_name}?policy")

    new_statements = [
        {
            "Action": [
                "s3:GetBucketLocation",
                "s3:ListBucket",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Resource": [f"arn:aws:s3:::{args.bucket_name}"],
            "Sid": "",
        },
        {
            "Action": [
                "s3:GetObject",
                "S3:GetObjectVersion",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Resource": [f"arn:aws:s3:::{args.bucket_name}/*"],
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
            f"/{args.bucket_name}?policy",
            data=json.dumps(new_policy),
        )
    except RGWAdminException as e:
        print("Error:", e)


def allow_write(args: argparse.Namespace) -> None:
    conn = get_connection()
    bucket = conn.get_bucket(args.bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    result = conn.request("GET", f"/{args.bucket_name}?policy")

    new_statements = [
        {
            "Action": [
                "s3:GetBucketLocation",
                "s3:ListBucketMultipartUploads",
            ],
            "Effect": "Allow",
            "Principal": {"AWS": [f"arn:aws:iam:::user/{args.uid_of_writer}"]},
            "Resource": [f"arn:aws:s3:::{args.bucket_name}"],
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
            "Principal": {"AWS": [f"arn:aws:iam:::user/{args.uid_of_writer}"]},
            "Resource": [f"arn:aws:s3:::{args.bucket_name}/*"],
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
            f"/{args.bucket_name}?policy",
            data=json.dumps(new_policy),
        )
    except RGWAdminException as e:
        print("Error:", e)


def make_private(args: argparse.Namespace) -> None:
    conn = get_connection()
    bucket = conn.get_bucket(args.bucket_name)
    user = conn.get_user(bucket.owner)

    conn = get_connection(
        access_key=user.keys[0].access_key,
        secret_key=user.keys[0].secret_key,
        admin_path="",
    )

    conn.request(
        "PUT",
        f"/{args.bucket_name}?policy",
        data="{}",
    )


# --------------------------------------------------------------------------


def init_logging() -> None:
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s %(message)s",
        level=logging.ERROR,
        stream=sys.stderr,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="be chatty",
    )
    parser.set_defaults(func=None)
    parser.set_defaults(verbose=False)

    subparsers = parser.add_subparsers()

    get_users_parser = subparsers.add_parser("get-users")
    get_users_parser.add_argument("--include-system", action="store_true")
    get_users_parser.set_defaults(func=get_users)

    create_user_parser = subparsers.add_parser("create-user")
    create_user_parser.add_argument("uid")
    create_user_parser.add_argument("display_name")
    create_user_parser.add_argument("--email")
    create_user_parser.add_argument("--caps")
    create_user_parser.set_defaults(func=create_user)

    delete_user_parser = subparsers.add_parser("delete-user")
    delete_user_parser.add_argument("uid")
    delete_user_parser.set_defaults(func=delete_user)

    get_quota_parser = subparsers.add_parser("get-quota")
    get_quota_parser.add_argument("uid")
    get_quota_parser.set_defaults(func=get_quota)

    set_quota_parser = subparsers.add_parser("set-quota")
    set_quota_parser.add_argument("uid")
    set_quota_parser.add_argument("max_size_gb", type=int)
    set_quota_parser.add_argument("max_objects", type=int, nargs="?", default=10_000_000)
    set_quota_parser.set_defaults(func=set_quota)

    get_buckets_parser = subparsers.add_parser("get-buckets")
    get_buckets_parser.add_argument("uid", nargs="?", default=None)
    get_buckets_parser.set_defaults(func=get_buckets)

    get_bucket_parser = subparsers.add_parser("get-bucket")
    get_bucket_parser.add_argument("bucket_name")
    get_bucket_parser.set_defaults(func=get_bucket)

    create_bucket_parser = subparsers.add_parser("create-bucket")
    create_bucket_parser.add_argument("uid")
    create_bucket_parser.add_argument("bucket_name")
    create_bucket_parser.set_defaults(func=create_bucket)

    get_policy_parser = subparsers.add_parser("get-policy")
    get_policy_parser.add_argument("bucket_name")
    get_policy_parser.set_defaults(func=get_policy)

    allow_read_parser = subparsers.add_parser("allow-read")
    allow_read_parser.add_argument("bucket_name")
    allow_read_parser.add_argument("uid_of_reader")
    allow_read_parser.set_defaults(func=allow_read)

    allow_public_read_parser = subparsers.add_parser("allow-public-read")
    allow_public_read_parser.add_argument("bucket_name")
    allow_public_read_parser.set_defaults(func=allow_public_read)

    allow_write_parser = subparsers.add_parser("allow-write")
    allow_write_parser.add_argument("bucket_name")
    allow_write_parser.add_argument("uid_of_writer")
    allow_write_parser.set_defaults(func=allow_write)

    make_private_parser = subparsers.add_parser("make-private")
    make_private_parser.add_argument("bucket_name")
    make_private_parser.set_defaults(func=make_private)

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.func:
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        args.func(args)
    else:
        raise UserError("No action specified on the command line")


def entrypoint() -> None:
    try:
        init_logging()
        main()
    except UserError as exn:
        print("ERROR:", *exn.args)
        sys.exit(1)
    except Exception:  # pylint: disable=broad-except
        logging.exception("Uncaught exception")
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
