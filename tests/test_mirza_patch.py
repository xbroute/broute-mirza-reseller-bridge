from __future__ import annotations

from pathlib import Path

from scripts.mirza_patch import transform


def write_fixture(root: Path):
    (root / "request.php").write_text('''<?php
class CurlRequest {
    private $url;
    private $headers = [];
    private $timeout = null;
    private $authToken = null;
    private $cookie = null;
    public function setBearerToken($token) {
        $this->authToken = $token;
    }
    
    public function setCookie($cookieStr) {
        $this->cookie = $cookieStr;
    }
    private function execute($method, $data = null) {
        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    }
}
''')
    (root / "mirza_agent.php").write_text('''<?php
function get_panel_list(array $panel) {
    $url = $panel['url_panel'];
    $headers = array();
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
}
function extend_service_mirza(array $panel, int $data_limit_gb, int $time_day, string $username)
{
    $url = $panel['url_panel'];
    $headers = array();
    $data = json_encode(array(
        'actions' => "extend_service",
        'username' => $username,
        'data_limit_gb' => $data_limit_gb,
        'time_day' => $time_day,
    ));
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
}
function remove_service_mirza(array $panel, string $username) {
    $url = $panel['url_panel'];
    $headers = array();
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
}
''')
    (root / "panels.php").write_text('''<?php
$extend = extend_service_mirza($panel, $new_limit, $time_day, $username);
        } elseif ($panel['type'] == "mirza_agent") {
            return array(
                'status' => true,
                'msg' => 'successful'
            );
        } elseif ($panel['type'] == "rebecca") {
''')


def apply_result(result):
    for path, (text, changed) in result.items():
        if changed:
            path.write_text(text)


def test_patcher_is_idempotent_and_adds_only_agent_tls(tmp_path):
    write_fixture(tmp_path)
    first = transform(tmp_path)
    assert all(changed for _, changed in first.values())
    apply_result(first)
    second = transform(tmp_path)
    assert not any(changed for _, changed in second.values())
    request = (tmp_path / "request.php").read_text()
    agent = (tmp_path / "mirza_agent.php").read_text()
    panels = (tmp_path / "panels.php").read_text()
    assert "setTlsVerify" in request
    assert "CURLOPT_SSL_VERIFYHOST" in request
    assert "reset_usage_mirza" in agent
    assert "'method_extend' => $method_extend" in agent
    assert agent.count("$req->setTlsVerify(true);") == agent.count("$req = new CurlRequest($url);")
    assert "reset_usage_mirza($panel, $username)" in panels
    assert "$Method_extend);" in panels
