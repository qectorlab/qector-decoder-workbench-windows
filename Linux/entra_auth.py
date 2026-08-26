"""entra_auth.py - OPTIONAL Microsoft Entra ID sign-in for QECTOR Decoder Workbench.

Entra ID readiness, not Entra ID dependence.  The workbench is a fully
air-gapped application: no identity service is ever contacted and no
authentication is required to run any feature, at any tier.  Entra ID is an
opt-in convenience for online, Entra ID-managed enterprises that want SSO and
group-based entitlement gating.  It is off by default.

Configuration (any of, in priority order):

1. Environment: ``QECTOR_ENTRA_CLIENT_ID`` + ``QECTOR_ENTRA_TENANT``
   (optionally ``QECTOR_ENTRA_GROUP_ID`` to gate Enterprise features on a
   group claim, and ``QECTOR_ENTRA_SCOPES`` to override the default scope).
2. Encrypted config file ``~/.qector/entra.json`` (written by the GUI or CLI,
   encrypted at rest with the same machine-derived Fernet key used for licence
   keys - see ``utils.encrypt_license_key``).

Hard guarantees:

- **Zero egress by default.**  The ``msal`` dependency is imported lazily and
  only inside :func:`login`; no module-level import, no network call, no
  background token refresh unless the administrator explicitly configured the
  app AND the user runs ``login``.  The compliance attestation
  (``compliance.compliance_report``) therefore stays clean on air-gapped
  machines.
- **Air-gap mode disables Entra ID.**  When ``compliance.airgap_mode()`` is
  true (``QECTOR_AIRGAP`` / ``QECTOR_OFFLINE`` set, or a frozen bundle), every
  authentication function returns ``status="disabled"`` with a recorded
  reason.  An air-gapped lab cannot accidentally phone the identity
  provider.
- **Tokens at rest are encrypted.**  The MSAL token cache is persisted to
  ``~/.qector/entra_token_cache.bin`` wrapped in native OS Keychains (DPAPI 
  on Windows, SecretService on Linux, Keychain on macOS) or Fernet with the
  machine-derived key, so a copied cache file is useless off-machine.
- **No telemetry.**  Auth only; no usage metrics are ever sent.

States reported by :func:`posture`:

* ``disabled`` - not configured (default).  ``reason`` explains what would
  enable it.
* ``configured`` - client/tenant present, no active session yet.
* ``authenticated`` - an unexpired session exists; ``account`` holds the
  signed-in principal.
* ``failed`` - configuration or flow error; ``reason`` is honest.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

_CLIENT_ID_ENV = "QECTOR_ENTRA_CLIENT_ID"
_TENANT_ENV = "QECTOR_ENTRA_TENANT"
_GROUP_ENV = "QECTOR_ENTRA_GROUP_ID"
_SCOPES_ENV = "QECTOR_ENTRA_SCOPES"

ENTRA_AUTHORITY_ENDPOINTS = {
    "public": "https://login.microsoftonline.com/{tenant}",
    "us_government": "https://login.microsoftonline.us/{tenant}",
    "china": "https://login.chinacloudapi.cn/{tenant}",
    "custom": "{custom_authority}",
}
_DEFAULT_SCOPES = ["User.Read"]

CONFIG_FILE = "entra.json"
TOKEN_CACHE_FILE = "entra_token_cache.bin"

_MSAL = None
_MSAL_ERROR: Optional[str] = None
_msal_probed = False


def _data_dir() -> Path:
    try:
        from utils import get_data_dir
        return Path(get_data_dir())
    except Exception:
        return Path.home() / ".qector"


def _config_path() -> Path:
    return _data_dir() / CONFIG_FILE


def _cache_path() -> Path:
    return _data_dir() / TOKEN_CACHE_FILE


def _os_encrypt(plain: bytes) -> bytes:
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        Entropy = DATA_BLOB(0, None)
        Desc = ctypes.c_wchar_p("")
        In = DATA_BLOB(len(plain), ctypes.cast(ctypes.c_char_p(plain), ctypes.POINTER(ctypes.c_char)))
        Out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(ctypes.byref(In), Desc, ctypes.byref(Entropy), None, None, 0x01, ctypes.byref(Out)):
            encrypted = ctypes.string_at(Out.pbData, Out.cbData)
            ctypes.windll.kernel32.LocalFree(Out.pbData)
            return encrypted
        raise Exception("CryptProtectData failed")
    raise NotImplementedError("Fallback")


def _os_decrypt(cipher: bytes) -> bytes:
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        Entropy = DATA_BLOB(0, None)
        Desc = ctypes.c_wchar_p()
        In = DATA_BLOB(len(cipher), ctypes.cast(ctypes.c_char_p(cipher), ctypes.POINTER(ctypes.c_char)))
        Out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(In), ctypes.byref(Desc), ctypes.byref(Entropy), None, None, 0, ctypes.byref(Out)):
            decrypted = ctypes.string_at(Out.pbData, Out.cbData)
            if Desc:
                ctypes.windll.kernel32.LocalFree(Desc)
            ctypes.windll.kernel32.LocalFree(Out.pbData)
            return decrypted
        raise Exception("CryptUnprotectData failed")
    raise NotImplementedError("Fallback")


def _encrypt(data: str) -> str:
    try:
        if sys.platform == "win32":
            return "DPAPI:" + _os_encrypt(data.encode('utf-8')).hex()
        import keyring
        keyring.set_password("qector_entra", "token_cache", data)
        return "KEYRING:managed"
    except Exception:
        try:
            from utils import encrypt_license_key
            return encrypt_license_key(data)
        except Exception as exc:
            # FAIL CLOSED: never store tokens in reversible plaintext (the old
            # base64 fallback silently stored secrets effectively in cleartext
            # while docs claimed "tokens at rest are encrypted").
            raise RuntimeError(
                "No secure storage backend available (DPAPI/keyring/Fernet all "
                "failed); refusing to store Entra token cache in plaintext. "
                "Install 'keyring' or 'cryptography' to enable secure storage."
            ) from exc


def _decrypt(data: str) -> Optional[str]:
    try:
        if data.startswith("DPAPI:"):
            if sys.platform == "win32":
                return _os_decrypt(bytes.fromhex(data[6:])).decode('utf-8')
            return None
        if data.startswith("KEYRING:managed"):
            import keyring
            return keyring.get_password("qector_entra", "token_cache")
        if data.startswith("B64:"):
            # Legacy insecure format: still readable so existing installs don't
            # break, but warn loudly — it is effectively cleartext.
            import base64
            import warnings
            warnings.warn(
                "Entra token cache is stored in legacy base64 (cleartext) format; "
                "re-run `qector entra configure` to re-store it encrypted.",
                stacklevel=2,
            )
            return base64.b64decode(data[4:]).decode('utf-8')
        from utils import decrypt_license_key
        return decrypt_license_key(data)
    except Exception:
        return None


def _get_authority(cfg: dict[str, Any]) -> str:
    cloud = cfg.get("cloud", "public")
    if cloud == "custom":
        return cfg.get("custom_authority", "").format(tenant=cfg.get("tenant"))
    template = ENTRA_AUTHORITY_ENDPOINTS.get(cloud, ENTRA_AUTHORITY_ENDPOINTS["public"])
    return template.format(tenant=cfg.get("tenant") or "<tenant>")


def _load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {"configured": False}
    path = _config_path()
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw:
                plain = _decrypt(raw) if not raw.startswith("{") else raw
                if plain:
                    loaded = json.loads(plain)
                    if isinstance(loaded, dict):
                        cfg.update(loaded)
        except Exception:
            pass
    cfg["client_id"] = os.environ.get(_CLIENT_ID_ENV) or cfg.get("client_id")
    cfg["tenant"] = os.environ.get(_TENANT_ENV) or cfg.get("tenant")
    cfg["group_id"] = os.environ.get(_GROUP_ENV) or cfg.get("group_id")
    scopes_env = os.environ.get(_SCOPES_ENV)
    if scopes_env:
        cfg["scopes"] = [s.strip() for s in scopes_env.split(",") if s.strip()]
    cfg["configured"] = bool(cfg.get("client_id") and cfg.get("tenant"))
    return cfg


def _save_config(cfg: dict[str, Any]) -> tuple[bool, str]:
    try:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in cfg.items() if k not in {"configured"}}
        encrypted = _encrypt(json.dumps(payload))
        path.write_text(encrypted + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return True, str(path)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _clear_config() -> None:
    try:
        path = _config_path()
        if path.is_file():
            path.unlink()
    except Exception:
        pass


def _msal_available() -> tuple[Any, Optional[str]]:
    global _MSAL, _MSAL_ERROR, _msal_probed
    if not _msal_probed:
        _msal_probed = True
        try:
            import msal  # type: ignore[import-not-found]
            _MSAL = msal
            _MSAL_ERROR = None
        except Exception as exc:
            _MSAL = None
            _MSAL_ERROR = f"{type(exc).__name__}: {exc}"
    return _MSAL, _MSAL_ERROR


def airgapped() -> bool:
    try:
        from compliance import airgap_mode
        return airgap_mode()
    except Exception:
        return False


def posture() -> dict[str, Any]:
    cfg = _load_config()
    status = "disabled"
    reason: Optional[str] = None
    account: Optional[str] = None
    group_ok: Optional[bool] = None
    roles: list[str] = []
    overage = False

    if airgapped():
        reason = "mandatory air-gap policy hard-disables identity services"
    elif not cfg.get("configured"):
        reason = ("not configured; set QECTOR_ENTRA_CLIENT_ID and QECTOR_ENTRA_TENANT "
                  "(or `qector entra configure`) to enable optional SSO")
    else:
        status = "configured"
        cached = _read_cached_identity()
        if cached is not None:
            if cached.get("expires_at", 0) > time.time():
                status = "authenticated"
                account = cached.get("account") or cached.get("username")
                roles = cached.get("roles") or []
                groups = cached.get("groups") or []
                if "_OVERAGE_DETECTED_" in groups:
                    overage = True
                if cfg.get("group_id"):
                    group_ok = cfg["group_id"] in groups
            else:
                status = "configured"
                reason = "cached session expired; run `qector entra login` to refresh"

    msal, msal_err = _msal_available()
    return {
        "status": status,
        "reason": reason,
        "airgapped": airgapped(),
        "configured": cfg.get("configured", False),
        "client_id": cfg.get("client_id"),
        "tenant": cfg.get("tenant"),
        "group_id": cfg.get("group_id"),
        "group_entitlement_ok": group_ok,
        "account": account,
        "roles": roles,
        "overage": overage,
        "scopes": cfg.get("scopes") or _DEFAULT_SCOPES,
        "cloud": cfg.get("cloud", "public"),
        "msal_available": msal is not None,
        "msal_error": msal_err,
        "authority": _get_authority(cfg),
        "cache_path": str(_cache_path()),
        "config_path": str(_config_path()),
        "config_source": "environment" if os.environ.get(_CLIENT_ID_ENV) else ("file" if cfg.get("configured") else None),
    }


def configure(client_id: str, tenant: str, group_id: Optional[str] = None,
              scopes: Optional[list[str]] = None, cloud: str = "public",
              custom_authority: Optional[str] = None) -> dict[str, Any]:
    if airgapped():
        return {"ok": False, "status": "disabled",
                "reason": "mandatory air-gap policy disables Entra ID configuration"}
    client_id = (client_id or "").strip()
    tenant = (tenant or "").strip()
    if not client_id or not tenant:
        return {"ok": False, "reason": "client_id and tenant are required"}
    cfg = {
        "client_id": client_id,
        "tenant": tenant,
        "group_id": (group_id or "").strip() or None,
        "scopes": scopes or _DEFAULT_SCOPES,
        "cloud": cloud,
        "custom_authority": custom_authority,
    }
    ok, msg = _save_config(cfg)
    return {"ok": ok, "path": msg, "status": "configured"}


def _read_cached_identity() -> Optional[dict[str, Any]]:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        plain = _decrypt(raw) if not raw.startswith("{") else raw
        if not plain:
            return None
        data = json.loads(plain)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_cached_identity(data: dict[str, Any]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = _encrypt(json.dumps(data))
        path.write_text(encrypted + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def _clear_cache() -> None:
    try:
        path = _cache_path()
        if path.is_file():
            path.unlink()
    except Exception:
        pass


def _parse_claims(id_token: dict[str, Any]) -> tuple[list[str], list[str]]:
    if not isinstance(id_token, dict):
        return [], []
    groups = id_token.get("groups", [])
    if id_token.get("_claim_names", {}).get("groups"):
        groups.append("_OVERAGE_DETECTED_")
    
    roles = id_token.get("roles", [])
    if not isinstance(groups, list):
        groups = []
    if not isinstance(roles, list):
        roles = []
    
    return [str(g) for g in groups], [str(r) for r in roles]


def _build_msal_app(cfg: dict[str, Any], msal: Any, use_broker: bool = False) -> tuple[Any, Any]:
    cache = msal.SerializableTokenCache()
    cached_data = _read_cached_identity()
    if cached_data and "msal_cache" in cached_data:
        try:
            cache.deserialize(cached_data["msal_cache"])
        except Exception:
            pass

    authority = _get_authority(cfg)
    
    kwargs = {}
    if use_broker and sys.platform.startswith("win"):
        kwargs["enable_broker_on_windows"] = True

    app = msal.PublicClientApplication(
        cfg["client_id"],
        authority=authority,
        token_cache=cache,
        **kwargs
    )
    return app, cache


def _save_msal_result(result: dict[str, Any], cache: Any, scopes: list[str]) -> dict[str, Any]:
    cfg = _load_config()
    groups, roles = _parse_claims(result.get("id_token_claims", {}))
    acct = result.get("account")
    acct_name = acct.get("username") if isinstance(acct, dict) else acct
    
    cached = {
        "account": acct_name,
        "username": acct_name,
        "expires_at": time.time() + int(result.get("expires_in", 3600)),
        "groups": groups,
        "roles": roles,
        "login_hint": result.get("id_token_claims", {}).get("preferred_username"),
        "msal_cache": cache.serialize() if cache.has_state_changed else None,
    }
    
    # Preserve old msal_cache if no change
    old_cached = _read_cached_identity()
    if not cached["msal_cache"] and old_cached and "msal_cache" in old_cached:
        cached["msal_cache"] = old_cached["msal_cache"]
        
    _write_cached_identity(cached)
    group_ok = None
    if cfg.get("group_id"):
        group_ok = cfg["group_id"] in groups
        
    return {"ok": True, "status": "authenticated",
            "account": cached["account"], "group_entitlement_ok": group_ok,
            "roles": roles, "overage": "_OVERAGE_DETECTED_" in groups,
            "scopes": scopes, "authority": _get_authority(cfg)}


def login(flow: str = "browser", message_cb=None) -> dict[str, Any]:
    """Run interactive MSAL flows (PKCE Browser, Broker, Device).

    ``flow`` can be 'browser', 'broker', or 'device'.
    """
    if airgapped():
        return {"ok": False, "status": "disabled",
                "reason": "mandatory air-gap policy disables Entra ID sign-in"}
    cfg = _load_config()
    if not cfg.get("configured"):
        return {"ok": False, "status": "disabled",
                "reason": "not configured; run `qector entra configure` first"}
    msal, msal_err = _msal_available()
    if msal is None:
        return {"ok": False, "status": "failed",
                "reason": f"MSAL not available: {msal_err}"}

    scopes = cfg.get("scopes") or _DEFAULT_SCOPES
    app, cache = _build_msal_app(cfg, msal, use_broker=(flow == "broker"))
    
    # 1. Try silent authentication first (background refresh)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result and "access_token" in result:
            return _save_msal_result(result, cache, scopes)

    # 2. Interactive Flow
    try:
        if flow == "broker":
            if not sys.platform.startswith("win"):
                return {"ok": False, "status": "failed", "reason": "Broker flow is only supported on Windows"}
            result = app.acquire_token_interactive(scopes=scopes, parent_window_handle=app.CONSOLE_WINDOW_HANDLE)
            
        elif flow == "browser":
            # PKCE loopback flow — omit `port` so MSAL binds an ephemeral port
            # (a fixed port is open to local DoS / port squatting).
            result = app.acquire_token_interactive(scopes=scopes)
            
        else: # device
            device_flow = app.initiate_device_flow(scopes=scopes)
            if "user_code" not in device_flow:
                return {"ok": False, "status": "failed", "reason": str(device_flow.get("error", device_flow))}
            if message_cb is None:
                print(device_flow["message"])
            else:
                message_cb(device_flow["message"])
            result = app.acquire_token_by_device_flow(device_flow)

        if "access_token" not in result:
            return {"ok": False, "status": "failed",
                    "reason": f"token acquisition failed: {result.get('error_description', result.get('error', result))}"}
                    
        return _save_msal_result(result, cache, scopes)

    except Exception as exc:
        return {"ok": False, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}


def login_client_credentials(secret: Optional[str] = None, thumbprint: Optional[str] = None, private_key: Optional[str] = None) -> dict[str, Any]:
    """Run non-interactive Service Principal authentication."""
    if airgapped():
        return {"ok": False, "status": "disabled", "reason": "air-gap mode"}
    cfg = _load_config()
    if not cfg.get("configured"):
        return {"ok": False, "status": "disabled", "reason": "not configured"}
    msal, msal_err = _msal_available()
    if msal is None:
        return {"ok": False, "status": "failed", "reason": "MSAL not available"}
        
    authority = _get_authority(cfg)
    scopes = [f"{cfg['client_id']}/.default"]
    
    try:
        if secret:
            app = msal.ConfidentialClientApplication(
                cfg["client_id"], authority=authority, client_credential=secret
            )
        elif thumbprint and private_key:
            app = msal.ConfidentialClientApplication(
                cfg["client_id"], authority=authority, 
                client_credential={"thumbprint": thumbprint, "private_key": private_key}
            )
        else:
            return {"ok": False, "status": "failed", "reason": "Provide secret or certificate credentials"}
            
        result = app.acquire_token_for_client(scopes=scopes)
        if "access_token" not in result:
            return {"ok": False, "status": "failed", "reason": str(result.get("error", result))}

        # Deliberately do NOT return the raw access token: this dict flows
        # through MCP _json_safe and can be printed/logged by callers.
        expires_in = result.get("expires_in")
        return {"ok": True, "status": "authenticated", "sp_mode": True,
                "token_acquired": True, "expires_in": expires_in}
    except Exception as exc:
        return {"ok": False, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}


def logout() -> dict[str, Any]:
    """Forget the local session (no network contact is made)."""
    _clear_cache()
    return {"ok": True, "status": "disabled", "reason": "session cleared locally"}


def entitlement_ok() -> Optional[bool]:
    """True only when the signed-in user holds the configured entitlement group."""
    p = posture()
    if p["status"] != "authenticated":
        return None
    if not p.get("group_id"):
        return None
    return bool(p.get("group_entitlement_ok"))


def export_voucher(path: str) -> dict[str, Any]:
    """Export the current Entra ID session as an offline voucher."""
    p = posture()
    if p["status"] != "authenticated":
        return {"ok": False, "reason": "No active authenticated session to export"}
    
    cached = _read_cached_identity()
    if not cached:
        return {"ok": False, "reason": "Failed to read cached identity"}
        
    try:
        from utils import encrypt_license_key
        # We encrypt the voucher using the offline license key derived AES
        payload = json.dumps({"type": "qector_entra_voucher", "data": cached})
        voucher_data = encrypt_license_key(payload)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(voucher_data + "\n")
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}

def import_voucher(path: str) -> dict[str, Any]:
    """Import an offline Entra ID voucher."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            
        from utils import decrypt_license_key
        plain = decrypt_license_key(raw)
        payload = json.loads(plain)
        
        if payload.get("type") != "qector_entra_voucher":
            return {"ok": False, "reason": "Invalid voucher format"}
            
        _write_cached_identity(payload["data"])
        return {"ok": True, "status": "authenticated"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}

def _format_status(p: Optional[dict[str, Any]] = None) -> str:
    p = p or posture()
    lines = ["QECTOR Decoder Workbench - Entra ID readiness", "=" * 46]
    lines.append(f"Status:            {p.get('status', '?')}")
    if p.get("reason"):
        lines.append(f"Reason:            {p['reason']}")
    lines.append(f"Air-gapped:        {bool(p.get('airgapped'))}")
    lines.append(f"Configured:        {bool(p.get('configured'))}")
    lines.append(f"Tenant:            {p.get('tenant') or '(none)'}")
    lines.append(f"Client ID:         {p.get('client_id') or '(none)'}")
    lines.append(f"Cloud Env:         {p.get('cloud', 'public')}")
    lines.append(f"Entitlement group: {p.get('group_id') or '(not set)'}")
    lines.append(f"Group OK:          {p.get('group_entitlement_ok') if p.get('group_entitlement_ok') is not None else '(n/a)'}")
    lines.append(f"Account:           {p.get('account') or '(signed out)'}")
    lines.append(f"MSAL available:    {bool(p.get('msal_available'))}"
                 + (f"  ({p['msal_error']})" if p.get("msal_error") else ""))
    lines.append(f"Authority:         {p.get('authority', '?')}")
    lines.append(f"Config:            {p.get('config_path', '?')}")
    lines.append(f"Token cache:       {p.get('cache_path', '?')} (OS encrypted)")
    return "\n".join(lines)


if __name__ == "__main__":
    print(_format_status())
