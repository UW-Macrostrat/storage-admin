import json
import re
from typing import List, Any, Union, Dict
from rich.console import Console
from rich.pretty import pprint

from rgwadmin import RGWCap, RGWKey, RGWUser

console = Console()


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
    # If we are in "human" mode, try
    pprint(obj, expand_all=True)


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


class UserError(RuntimeError):
    """
    A runtime error where a stack trace should not be necessary.
    """
