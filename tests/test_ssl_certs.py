"""python.org Python on macOS has no CA bundle, so every HTTPS call fails with
'CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate'."""

import ssl

import pytest

from clipper import cli


def _paths(cafile=None, capath=None):
    return ssl.DefaultVerifyPaths(
        cafile=cafile,
        capath=capath,
        openssl_cafile_env="SSL_CERT_FILE",
        openssl_cafile="/nonexistent/cert.pem",
        openssl_capath_env="SSL_CERT_DIR",
        openssl_capath="/nonexistent/certs",
    )


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def test_uses_certifi_when_the_interpreter_has_no_bundle(monkeypatch):
    certifi = pytest.importorskip("certifi")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _paths())
    applied = cli.ensure_ca_certificates()
    assert applied == certifi.where()
    import os

    assert os.environ["SSL_CERT_FILE"] == certifi.where()
    assert os.path.isfile(certifi.where())


def test_leaves_a_working_system_bundle_alone(monkeypatch):
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _paths(cafile="/etc/ssl/cert.pem"))
    assert cli.ensure_ca_certificates() is None
    import os

    assert "SSL_CERT_FILE" not in os.environ


@pytest.mark.parametrize("var", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_never_overrides_an_explicit_setting(monkeypatch, var):
    # e.g. a corporate proxy's CA bundle the user configured on purpose
    monkeypatch.setenv(var, "/corp/ca-bundle.pem")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _paths())
    assert cli.ensure_ca_certificates() is None
    import os

    assert os.environ[var] == "/corp/ca-bundle.pem"


def test_without_certifi_it_does_nothing_rather_than_crash(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_certifi(name, *args, **kwargs):
        if name == "certifi":
            raise ImportError("no certifi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _paths())
    monkeypatch.setattr(builtins, "__import__", no_certifi)
    assert cli.ensure_ca_certificates() is None


def test_doctor_reports_the_certifi_fallback(monkeypatch, tmp_path, capsys):
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLIPPER_API_KEY", raising=False)
    # main() assigns the module-level flag; registering it with monkeypatch first
    # makes the original value come back after the test.
    monkeypatch.setattr(cli, "_CA_FALLBACK", None)
    monkeypatch.setattr(cli, "ensure_ca_certificates", lambda: "/x/certifi/cacert.pem")
    cli.main(["doctor"])
    assert "TLS: this Python has no CA bundle" in capsys.readouterr().out
