"""Scoped control transport primitive; provision dedicated key outside this code.

Not a substitute for verified Telegram update/DB ownership at signer, or live
admission lock at recorder. Signature authenticates the dispatcher, not a human.
"""
import hashlib
import hmac
import json
import time

class Rejected(ValueError):
    pass


def canonical(method, path, body):
    return json.dumps([method, path, body], sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sign(key, method, path, body):
    if len(key) < 32:
        raise Rejected('key_unconfigured')
    return hmac.new(key, canonical(method, path, body), hashlib.sha256).hexdigest()


def verify(key, method, path, body, signature, allowed_ids, now=None):
    if method != 'POST' or path not in {'/control/prepare','/control/confirm','/control/cancel','/control/status','/control/events'}:
        raise Rejected('route_denied')
    if set(body) != {'issued_at','request_id','user_id','session_id','generation','ownership_verified'}:
        raise Rejected('invalid_fields')
    # identity/ownership must be asserted only by the authenticated dispatcher
    # after existing DB ownership lookup, never copied from callback/request.
    if not isinstance(body['user_id'],str) or body['user_id'] not in allowed_ids or body['ownership_verified'] is not True:
        raise Rejected('access_denied')
    if type(body['issued_at']) not in (int,float) or not abs((time.time() if now is None else now)-body['issued_at']) <= 30:
        raise Rejected('expired')
    if any(not isinstance(body[k],str) or not 1 <= len(body[k]) <= 64 for k in ['request_id','session_id','generation']):
        raise Rejected('invalid_identifier')
    if not isinstance(signature,str) or not hmac.compare_digest(sign(key,method,path,body),signature):
        raise Rejected('invalid_signature')
    return body
