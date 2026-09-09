#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


class PatchError(RuntimeError):
    pass


def _replace_once(text: str, old: str, new: str, *, label: str, already: str | None = None) -> tuple[str, bool]:
    if already and already in text:
        return text, False
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected exactly one upstream anchor, found {count}")
    return text.replace(old, new, 1), True


def patch_request_php(text: str) -> tuple[str, bool]:
    changed = False
    if "private $sslVerifyPeer" not in text:
        text, did = _replace_once(
            text,
            "    private $authToken = null;\n    private $cookie = null;",
            "    private $authToken = null;\n    private $sslVerifyPeer = false;\n    private $cookie = null;",
            label="request.php TLS property",
        )
        changed |= did

    if "function setTlsVerify" not in text:
        old = """    public function setBearerToken($token) {\n        $this->authToken = $token;\n    }\n    \n    public function setCookie($cookieStr) {"""
        new = """    public function setBearerToken($token) {\n        $this->authToken = $token;\n    }\n\n    public function setTlsVerify($verify = true) {\n        $this->sslVerifyPeer = (bool) $verify;\n    }\n    \n    public function setCookie($cookieStr) {"""
        text, did = _replace_once(text, old, new, label="request.php setTlsVerify method")
        changed |= did

    insecure = "        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);"
    secure = """        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, $this->sslVerifyPeer);\n        curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, $this->sslVerifyPeer ? 2 : 0);"""
    if insecure in text:
        text, did = _replace_once(text, insecure, secure, label="request.php TLS curl options")
        changed |= did
    elif "CURLOPT_SSL_VERIFYPEER, $this->sslVerifyPeer" not in text or "CURLOPT_SSL_VERIFYHOST" not in text:
        raise PatchError("request.php TLS curl options: compatible anchor not found")

    required = ("private $sslVerifyPeer", "function setTlsVerify", "CURLOPT_SSL_VERIFYHOST")
    if not all(x in text for x in required):
        raise PatchError("request.php post-condition failed")
    return text, changed


def _patch_agent_tls(text: str) -> tuple[str, bool]:
    raw = "$req = new CurlRequest($url);\n    $req->setHeaders($headers);"
    secured = "$req = new CurlRequest($url);\n    $req->setTlsVerify(true);\n    $req->setHeaders($headers);"
    before = text.count(raw)
    if before:
        text = text.replace(raw, secured)
    new_count = text.count("$req = new CurlRequest($url);")
    secure_count = text.count("$req->setTlsVerify(true);")
    if new_count == 0 or secure_count != new_count:
        raise PatchError(
            f"mirza_agent.php TLS post-condition failed: CurlRequest={new_count}, TLS={secure_count}"
        )
    return text, before > 0


def patch_mirza_agent_php(text: str) -> tuple[str, bool]:
    changed = False
    text, did = _patch_agent_tls(text)
    changed |= did

    old_sig = "function extend_service_mirza(array $panel, int $data_limit_gb, int $time_day, string $username)"
    new_sig = "function extend_service_mirza(array $panel, int $data_limit_gb, int $time_day, string $username, string $method_extend = \"resetVolumeTime\")"
    if new_sig not in text:
        text, did = _replace_once(text, old_sig, new_sig, label="mirza_agent.php extend signature")
        changed |= did

    start = text.find("function extend_service_mirza")
    end = text.find("function remove_service_mirza", start)
    if start < 0 or end < 0:
        raise PatchError("mirza_agent.php extend/remove function anchors not found")
    block = text[start:end]
    if "'method_extend' => $method_extend" not in block:
        old = "        'time_day' => $time_day,\n"
        if block.count(old) != 1:
            raise PatchError("mirza_agent.php method_extend payload anchor changed")
        block = block.replace(old, old + "        'method_extend' => $method_extend,\n", 1)
        text = text[:start] + block + text[end:]
        changed = True

    if "function reset_usage_mirza(" not in text:
        marker = "function remove_service_mirza(array $panel, string $username)"
        if text.count(marker) != 1:
            raise PatchError("mirza_agent.php reset helper insertion anchor changed")
        helper = r'''function reset_usage_mirza(array $panel, string $username)
{
    $url = $panel['url_panel'];
    $headers = array(
        'accept: application/json'
    );
    $data = json_encode(array(
        'actions' => "reset_usage",
        'username' => $username,
    ));
    $req = new CurlRequest($url);
    $req->setTlsVerify(true);
    $req->setHeaders($headers);
    $req->setBearerToken($panel['password_panel']);
    $response = $req->put($data);
    return $response;
}


'''
        text = text.replace(marker, helper + marker, 1)
        changed = True

    required = ("function reset_usage_mirza(", "'method_extend' => $method_extend", "$req->setTlsVerify(true);")
    if not all(x in text for x in required):
        raise PatchError("mirza_agent.php post-condition failed")
    return text, changed


def patch_panels_php(text: str) -> tuple[str, bool]:
    changed = False
    old_call = "$extend = extend_service_mirza($panel, $new_limit, $time_day, $username);"
    new_call = "$extend = extend_service_mirza($panel, $new_limit, $time_day, $username, $Method_extend);"
    if new_call not in text:
        text, did = _replace_once(text, old_call, new_call, label="panels.php extend call")
        changed |= did

    if "reset_usage_mirza($panel, $username)" not in text:
        old_block = '''        } elseif ($panel['type'] == "mirza_agent") {
            return array(
                'status' => true,
                'msg' => 'successful'
            );
        } elseif ($panel['type'] == "rebecca") {'''
        new_block = '''        } elseif ($panel['type'] == "mirza_agent") {
            $reset = reset_usage_mirza($panel, $username);
            if (!empty($reset['error'])) {
                return array(
                    'status' => false,
                    'msg' => 'error : ' . $reset['error']
                );
            }
            if (!empty($reset['status']) && !in_array($reset['status'], [200, 400])) {
                return array(
                    'status' => false,
                    'msg' => 'error code : ' . $reset['status']
                );
            }
            $resetBody = json_decode($reset['body'] ?? '', true);
            if (!is_array($resetBody) || empty($resetBody['status'])) {
                return array(
                    'status' => false,
                    'msg' => is_array($resetBody) ? ($resetBody['msg'] ?? 'reset failed') : 'reset failed'
                );
            }
            return array(
                'status' => true,
                'msg' => 'successful'
            );
        } elseif ($panel['type'] == "rebecca") {'''
        text, did = _replace_once(text, old_block, new_block, label="panels.php Mirza Agent reset branch")
        changed |= did

    if new_call not in text or "reset_usage_mirza($panel, $username)" not in text:
        raise PatchError("panels.php post-condition failed")
    return text, changed


def transform(root: Path) -> dict[Path, tuple[str, bool]]:
    paths = {
        root / "request.php": patch_request_php,
        root / "mirza_agent.php": patch_mirza_agent_php,
        root / "panels.php": patch_panels_php,
    }
    result: dict[Path, tuple[str, bool]] = {}
    for path, fn in paths.items():
        if not path.is_file():
            raise PatchError(f"Required Mirza file not found: {path}")
        text = path.read_text(encoding="utf-8")
        result[path] = fn(text)
    return result


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.broute-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed MirzaBot compatibility patcher for Broute Bridge")
    parser.add_argument("--root", required=True, type=Path, help="MirzaBot installation directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Verify that the installed Mirza version is patchable; do not write")
    mode.add_argument("--apply", action="store_true", help="Apply only the compatibility changes that are still missing")
    args = parser.parse_args()

    try:
        result = transform(args.root.resolve())
    except PatchError as exc:
        print(f"INCOMPATIBLE: {exc}", file=sys.stderr)
        print("No files were modified. Use broute-mirza-compat rollback if this followed an upstream update.", file=sys.stderr)
        return 3

    changed = [path.name for path, (_, did) in result.items() if did]
    if args.check:
        if changed:
            print("COMPATIBLE: patch is required for " + ", ".join(changed))
        else:
            print("COMPATIBLE: already patched")
        return 0

    for path, (text, did) in result.items():
        if did:
            _atomic_write(path, text)
    print("PATCHED: " + (", ".join(changed) if changed else "no changes needed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
