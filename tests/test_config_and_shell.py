import json
import sys

import pytest

from agent_guard import config as config_mod
from agent_guard.shell import split_segments, substitutions, words


def test_defaults_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_GUARD_CONFIG", str(tmp_path / "missing.json"))
    config_mod._CACHE.clear()
    cfg = config_mod.load()
    assert cfg.source is None and cfg["language"] == "en" and cfg.on("force-push")


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11+")
def test_toml_config_merges_over_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('language = "ru"\n[rules]\nforce-push = false\n[git]\nprotected_branches = ["trunk"]\n')
    monkeypatch.setenv("AGENT_GUARD_CONFIG", str(path))
    config_mod._CACHE.clear()
    cfg = config_mod.load()
    assert cfg.source == str(path)
    assert cfg["language"] == "ru" and not cfg.on("force-push") and cfg.on("rm-recursive")
    assert cfg["git"]["protected_branches"] == ["trunk"] and cfg["git"]["agent_branch_prefixes"] == ["agent/"]


def test_xdg_lookup(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_GUARD_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "agent-guard").mkdir()
    (tmp_path / "agent-guard" / "config.json").write_text(json.dumps({"language": "ru"}))
    config_mod._CACHE.clear()
    assert config_mod.load()["language"] == "ru"


def test_broken_file_falls_back_to_defaults(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.json"
    path.write_text("{ not json")
    monkeypatch.setenv("AGENT_GUARD_CONFIG", str(path))
    config_mod._CACHE.clear()
    assert config_mod.load()["rules"]["force-push"] is True
    assert "ignoring" in capsys.readouterr().err


def test_unknown_keys_are_reported():
    assert config_mod.unknown_keys({"langauge": "en", "git": {"protected": []}, "rules": {"nope": True, "force-push": False}}) == [
        "langauge",
        "git.protected",
        "rules.nope",
    ]


def test_example_config_is_valid():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "examples" / "config.json").read_text())
    assert config_mod.unknown_keys(data) == []
    if sys.version_info >= (3, 11):
        import tomllib

        assert config_mod.unknown_keys(tomllib.loads((root / "examples" / "config.toml").read_text())) == []


def test_segments_ignore_quotes_and_heredocs():
    assert split_segments("a && b || c; d | e\nf") == ["a", "b", "c", "d", "e", "f"]
    assert split_segments("echo 'a; b' && c") == ["echo 'a; b'", "c"]
    assert split_segments("cat <<'EOF'\nrm -rf /\nEOF\nls") == ["cat", "ls"]


def test_wrappers_are_peeled():
    w = words("sudo -u dev nice -n 5 timeout -k 5 30s kubectl get pods")
    assert w.tokens[:2] == ["kubectl", "get"] and w.behind_wrapper
    w = words("FOO=1 kubectl get pods")
    assert w.tokens[0] == "kubectl" and not w.behind_wrapper


def test_substitutions():
    assert substitutions("P=$(kubectl delete pod x) && echo `id`") == ["kubectl delete pod x", "id"]
    assert substitutions("echo '$(not this)' $((1 + 2))") == []
